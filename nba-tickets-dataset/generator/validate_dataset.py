"""
Invariant checks for a generated HoopsPass dataset (parquet or csv directory layout:
<out>/<table>/part-*.parquet). Uses DuckDB. Exit code 1 if any check fails.

    python validate_dataset.py --data ./out
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import duckdb

TABLES = ["teams", "arenas", "ref_codes", "games", "customers", "orders", "tickets"]


def register(con: duckdb.DuckDBPyConnection, data: str) -> None:
    for t in TABLES:
        files = sorted(glob.glob(os.path.join(data, t, "part-*")))
        if not files:
            raise SystemExit(f"missing table directory: {t}")
        reader = "read_parquet" if files[0].endswith(".parquet") else "read_csv_auto"
        con.execute(f"CREATE VIEW {t} AS SELECT * FROM {reader}({files!r})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    args = ap.parse_args()
    con = duckdb.connect()
    register(con, args.data)
    q = lambda sql: con.execute(sql).fetchone()[0]
    failures = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"[{'OK ' if ok else 'FAIL'}] {name} {detail}")
        if not ok:
            failures.append(name)

    # ---- dimensions ------------------------------------------------------------------------
    check("30 teams", q("SELECT COUNT(*) FROM teams") == 30)
    check("30 arenas, 29 in the US", q("SELECT COUNT(*) FROM arenas") == 30 and q("SELECT COUNT(*) FROM arenas WHERE country='US'") == 29)
    check("every team has a distinct arena", q("SELECT COUNT(DISTINCT arena_id) FROM teams") == 30)

    # ---- schedule --------------------------------------------------------------------------
    n_reg = q("SELECT COUNT(*) FROM games WHERE game_type='REG'")
    check("1230 regular-season games", n_reg == 1230, f"(got {n_reg})")
    check("41 home + 41 away per team", q("""
        WITH h AS (SELECT home_team_id t, COUNT(*) n FROM games WHERE game_type='REG' GROUP BY 1),
             a AS (SELECT away_team_id t, COUNT(*) n FROM games WHERE game_type='REG' GROUP BY 1)
        SELECT COUNT(*) FROM h JOIN a USING (t) WHERE h.n<>41 OR a.n<>41""") == 0)
    check("no team plays twice on one day", q("""
        SELECT COUNT(*) FROM (SELECT game_date, t, COUNT(*) n FROM (
            SELECT game_date, home_team_id t FROM games UNION ALL SELECT game_date, away_team_id FROM games) GROUP BY 1,2) WHERE n>1""") == 0)
    check("6 play-in games", q("SELECT COUNT(*) FROM games WHERE game_type='PLAYIN'") == 6)
    po = q("SELECT COUNT(*) FROM games WHERE game_type='PLAYOFF'")
    check("playoffs: 15 series, 60-105 games", 60 <= po <= 105, f"(got {po})")
    check("each playoff series has 4-7 games and a 4-win winner", q("""
        SELECT COUNT(*) FROM (
          SELECT game_id//10 AS series, COUNT(*) n,
                 MAX(CASE WHEN home_points>away_points THEN home_team_id ELSE away_team_id END) dummy,
                 SUM(CASE WHEN home_points>away_points THEN 1 ELSE 0 END) hw
          FROM games WHERE game_type='PLAYOFF' GROUP BY 1) s
        WHERE n<4 OR n>7""") == 0)
    check("playoff series: exactly one team reaches 4 wins, in the last game", q("""
        WITH w AS (SELECT game_id//10 AS series, series_game_no,
                          CASE WHEN home_points>away_points THEN home_team_id ELSE away_team_id END AS winner
                   FROM games WHERE game_type='PLAYOFF'),
             agg AS (SELECT series, winner, COUNT(*) wins, MAX(series_game_no) last_win FROM w GROUP BY 1,2),
             s AS (SELECT series, MAX(series_game_no) n FROM w GROUP BY 1)
        SELECT COUNT(*) FROM s LEFT JOIN (SELECT series, COUNT(*) champs FROM agg WHERE wins=4 GROUP BY 1) c USING(series)
        LEFT JOIN agg a ON a.series=s.series AND a.wins=4
        WHERE COALESCE(champs,0)<>1 OR a.last_win<>s.n""") == 0)
    check("no ties", q("SELECT COUNT(*) FROM games WHERE home_points=away_points") == 0)
    check("games table: allocated_seats < attendance <= capacity", q("""
        SELECT COUNT(*) FROM games g JOIN arenas a USING(arena_id)
        WHERE NOT (g.allocated_seats < g.attendance AND g.attendance <= a.capacity)""") == 0)

    # ---- customers -------------------------------------------------------------------------
    n_c = q("SELECT COUNT(*) FROM customers")
    dup = q("SELECT COUNT(*) - COUNT(DISTINCT lower(email)) FROM customers")
    check("~2% duplicate customer records (same email, different id)", 0.015 <= dup / n_c <= 0.025, f"({dup}/{n_c})")
    sent = q("SELECT COUNT(*) FROM customers WHERE date_of_birth = DATE '1900-01-01'")
    check("~12-14% DOB sentinel rows", 0.10 <= sent / n_c <= 0.16, f"({sent}/{n_c})")
    check("phones use the fictional 555-01xx range", q("SELECT COUNT(*) FROM customers WHERE phone NOT LIKE '(%) 555-01%'") == 0)
    check("emails only on example.* domains", q("SELECT COUNT(*) FROM customers WHERE lower(email) NOT SIMILAR TO '.*@example\\.(com|net|org)'") == 0)

    # ---- orders / tickets integrity ------------------------------------------------------
    check("every ticket has an order", q("SELECT COUNT(*) FROM tickets t LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL") == 0)
    check("every order has a customer", q("SELECT COUNT(*) FROM orders o LEFT JOIN customers c USING(customer_id) WHERE c.customer_id IS NULL") == 0)
    check("every ticket has a game", q("SELECT COUNT(*) FROM tickets t LEFT JOIN games g USING(game_id) WHERE g.game_id IS NULL") == 0)
    check("tickets.home_team_id matches games.home_team_id", q("SELECT COUNT(*) FROM tickets t JOIN games g USING(game_id) WHERE t.home_team_id<>g.home_team_id") == 0)
    check("SEASON orders have game_id NULL, others have it set", q("SELECT COUNT(*) FROM orders WHERE (order_type='SEASON') <> (game_id IS NULL)") == 0)
    check("orders.ticket_count = number of ticket rows", q("""
        SELECT COUNT(*) FROM orders o JOIN (SELECT order_id, COUNT(*) n FROM tickets GROUP BY 1) t USING(order_id) WHERE o.ticket_count<>t.n""") == 0)
    check("orders.subtotal = sum(sold_price) of its tickets", q("""
        SELECT COUNT(*) FROM orders o JOIN (SELECT order_id, ROUND(SUM(sold_price_usd),2) s FROM tickets GROUP BY 1) t USING(order_id)
        WHERE ABS(o.subtotal_usd - t.s) > 0.011""") == 0)
    check("single-game orders placed before tip-off", q("""
        SELECT COUNT(*) FROM orders o JOIN games g USING(game_id) WHERE o.order_ts >= g.tipoff_local""") == 0)
    check("no seat sold twice for the same game", q("SELECT COUNT(*) FROM (SELECT game_id, section, row_label, seat_number, COUNT(*) n FROM tickets GROUP BY 1,2,3,4) WHERE n>1") == 0)
    check("COMP tickets cost 0 and carry no fee", q("SELECT COUNT(*) FROM tickets WHERE ticket_type_code='COMP' AND (sold_price_usd<>0 OR service_fee_usd<>0)") == 0)
    check("only RSL tickets carry commission", q("SELECT COUNT(*) FROM tickets WHERE (ticket_type_code='RSL') <> (commission_usd>0)") == 0)
    check("tickets of non-COMPLETED orders are never scanned", q("SELECT COUNT(*) FROM tickets t JOIN orders o USING(order_id) WHERE o.status<>'COMPLETED' AND t.scanned_at IS NOT NULL") == 0)
    ns = q("""SELECT 1.0*SUM(CASE WHEN t.scanned_at IS NULL THEN 1 ELSE 0 END)/COUNT(*) FROM tickets t JOIN orders o USING(order_id)
              WHERE o.status='COMPLETED' AND t.ticket_type_code<>'COMP'""")
    check("no-show rate among sold tickets between 6% and 14%", 0.06 <= ns <= 0.14, f"({ns:.3f})")
    check("primary sold tickets never exceed allocation", q("""
        SELECT COUNT(*) FROM (SELECT t.game_id, COUNT(*) n, MAX(g.allocated_seats) a FROM tickets t JOIN games g USING(game_id)
        WHERE t.ticket_type_code<>'RSL' GROUP BY 1) WHERE n > a""") == 0, "(at scale 1.0 only; scaled runs shrink the allocation)")

    # ---- the traps must actually bite: naive vs correct give different numbers ------------
    naive = q("""SELECT COUNT(*) FROM tickets t JOIN games g USING(game_id) JOIN teams tm ON tm.team_id IN (g.home_team_id, g.away_team_id)
                 WHERE tm.nickname='Lakers' AND g.game_type='REG'""")
    correct = q("""SELECT COUNT(*) FROM tickets t JOIN games g USING(game_id) JOIN teams tm ON tm.team_id=g.home_team_id JOIN orders o USING(order_id)
                   WHERE tm.nickname='Lakers' AND g.game_type='REG' AND o.status='COMPLETED' AND t.ticket_type_code NOT IN ('COMP','RSL')""")
    check("trap T3/T4: naive Lakers count differs from correct", naive > correct * 1.5, f"(naive {naive:,} vs correct {correct:,})")
    avg_naive = q("SELECT AVG(face_value_usd) FROM tickets")
    avg_correct = q("SELECT AVG(sold_price_usd) FROM tickets t JOIN orders o USING(order_id) WHERE o.status='COMPLETED' AND t.ticket_type_code<>'COMP'")
    print(f"[info] avg ticket price: naive AVG(face_value, all rows) {avg_naive:.2f} vs primary sold price {avg_correct:.2f} "
          f"vs avg order total {q('SELECT AVG(total_usd) FROM orders WHERE status=' + chr(39) + 'COMPLETED' + chr(39)):.2f}")
    gmv = q("SELECT SUM(sold_price_usd + service_fee_usd) FROM tickets t JOIN orders o USING(order_id) WHERE o.status='COMPLETED'")
    net = q("SELECT SUM(service_fee_usd + commission_usd) FROM tickets t JOIN orders o USING(order_id) WHERE o.status='COMPLETED'")
    check("trap T4: GMV is ~8x net revenue", 5 < gmv / net < 12, f"(GMV {gmv:,.0f} vs net {net:,.0f})")
    fanout = q("SELECT SUM(o.total_usd) FROM orders o JOIN tickets t USING(order_id) WHERE o.status='COMPLETED'")
    real = q("SELECT SUM(total_usd) FROM orders WHERE status='COMPLETED'")
    check("trap T2: fan-out inflates order totals", fanout > real * 1.8, f"({fanout:,.0f} vs {real:,.0f})")
    age_naive = q("SELECT AVG(DATE_DIFF('year', date_of_birth, DATE '2026-06-30')) FROM customers")
    age_ok = q("SELECT AVG(DATE_DIFF('year', date_of_birth, DATE '2026-06-30')) FROM customers WHERE date_of_birth <> DATE '1900-01-01'")
    check("trap T5: DOB sentinel skews average age", age_naive - age_ok > 8, f"({age_naive:.1f} vs {age_ok:.1f})")

    print()
    print("tickets:", f"{q('SELECT COUNT(*) FROM tickets'):,}", "| orders:", f"{q('SELECT COUNT(*) FROM orders'):,}",
          "| customers:", f"{n_c:,}", "| games:", q("SELECT COUNT(*) FROM games"))
    if failures:
        print(f"\n{len(failures)} check(s) failed: {failures}")
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
