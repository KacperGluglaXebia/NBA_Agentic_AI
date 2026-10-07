# Databricks notebook source
# MAGIC %md
# MAGIC # HoopsPass · 01 · Generate the NBA ticket-sales dataset
# MAGIC
# MAGIC Creates two copies of the same synthetic data:
# MAGIC
# MAGIC | Schema | Purpose | Metadata |
# MAGIC |---|---|---|
# MAGIC | `<catalog>.sales_raw` | **Agent A** (the "five minutes with defaults" agent) | no comments, no keys, plus a few clutter tables |
# MAGIC | `<catalog>.sales` | **Agent B** (curated by an SME) | table + column comments from `generator/semantic_layer.py`; keys, views and governance come in notebook `02` |
# MAGIC
# MAGIC Runs end-to-end on a serverless notebook in **3–6 minutes** at `scale = 1.0` (≈7.3M tickets, 2.2M orders, 306k customers, 1,325 games).
# MAGIC Use `scale = 0.1` for a quick rehearsal. Re-running is idempotent (`CREATE OR REPLACE`).
# MAGIC
# MAGIC **Prerequisites:** import this repository as a *Git folder* (the notebook imports `../generator/*.py`), `CREATE CATALOG`/`CREATE SCHEMA` rights,
# MAGIC and a UC volume is created below for the parquet staging files.

# COMMAND ----------

dbutils.widgets.text("catalog", "hoopspass", "Catalog")
dbutils.widgets.text("scale", "1.0", "Scale (1.0 = full season)")
dbutils.widgets.text("seed", "2026", "Random seed")

CATALOG = dbutils.widgets.get("catalog")
SCALE = float(dbutils.widgets.get("scale"))
SEED = int(dbutils.widgets.get("seed"))
CURATED = f"{CATALOG}.sales"
RAW = f"{CATALOG}.sales_raw"
VOLUME = f"/Volumes/{CATALOG}/sales/staging"
STAGING = f"{VOLUME}/hoopspass_seed{SEED}_scale{SCALE}"
print(f"catalog={CATALOG} scale={SCALE} seed={SEED}\nstaging={STAGING}")

# COMMAND ----------

import os, sys, shutil, time
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "generator")))
import nba_tickets_generator as gen
import semantic_layer as sem

spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CURATED} COMMENT 'HoopsPass curated sales data (Agent B)'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {RAW} COMMENT 'HoopsPass raw extracts, undocumented on purpose (Agent A)'")
spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.governance COMMENT 'Row filters, column masks and access mapping tables'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.sales.staging COMMENT 'Parquet staging files produced by 01_generate_nba_tickets'")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Generate parquet files into the staging volume
# MAGIC The generator yields pandas DataFrames in dependency order; fact tables arrive in batches of 40 games.
# MAGIC Writing parquet with pyarrow keeps nullable integers, dates and timestamps intact, and `CREATE TABLE AS SELECT` from the volume is the fastest way into Delta.

# COMMAND ----------

if os.path.exists(STAGING):
    shutil.rmtree(STAGING)
os.makedirs(STAGING, exist_ok=True)

t0 = time.time()
parts: dict[str, int] = {}
for name, df in gen.generate_all(scale=SCALE, seed=SEED, progress=lambda msg: print(f"  {msg}")):
    d = os.path.join(STAGING, name)
    os.makedirs(d, exist_ok=True)
    parts[name] = parts.get(name, 0) + 1
    df.to_parquet(os.path.join(d, f"part-{parts[name]:04d}.parquet"), index=False,
                  coerce_timestamps="us", allow_truncated_timestamps=True)  # Spark cannot read TIMESTAMP(NANOS)
print(f"generated in {time.time() - t0:,.0f}s: {parts}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Load the curated schema (`sales`) and the raw schema (`sales_raw`)
# MAGIC Same rows in both. The raw copy gets no comments and no constraints – that is the point.

# COMMAND ----------

TABLES = ["teams", "arenas", "ref_codes", "games", "customers", "orders", "tickets"]

# a few explicit casts so both schemas have clean, boring types
CASTS = {
    "games": "CAST(game_id AS BIGINT) AS game_id, season, game_type, playoff_round, CAST(game_date AS DATE) AS game_date, "
             "CAST(tipoff_local AS TIMESTAMP) AS tipoff_local, CAST(home_team_id AS BIGINT) AS home_team_id, CAST(away_team_id AS BIGINT) AS away_team_id, "
             "CAST(arena_id AS BIGINT) AS arena_id, CAST(series_game_no AS INT) AS series_game_no, is_national_tv, CAST(home_points AS INT) AS home_points, "
             "CAST(away_points AS INT) AS away_points, CAST(attendance AS INT) AS attendance, CAST(allocated_seats AS INT) AS allocated_seats",
    "customers": "CAST(customer_id AS BIGINT) AS customer_id, first_name, last_name, email, phone, CAST(date_of_birth AS DATE) AS date_of_birth, home_state, "
                 "home_country, home_zip, CAST(loyalty_tier_code AS INT) AS loyalty_tier_code, CAST(favorite_team_id AS BIGINT) AS favorite_team_id, "
                 "CAST(signup_date AS DATE) AS signup_date, marketing_opt_in, CAST(created_at AS TIMESTAMP) AS created_at",
    "orders": "CAST(order_id AS BIGINT) AS order_id, CAST(customer_id AS BIGINT) AS customer_id, CAST(game_id AS BIGINT) AS game_id, order_type, "
              "CAST(order_ts AS TIMESTAMP) AS order_ts, CAST(channel_code AS INT) AS channel_code, status, payment_method, card_last4, promo_code, "
              "CAST(ticket_count AS INT) AS ticket_count, CAST(subtotal_usd AS DECIMAL(12,2)) AS subtotal_usd, CAST(fees_usd AS DECIMAL(12,2)) AS fees_usd, "
              "CAST(total_usd AS DECIMAL(12,2)) AS total_usd",
    "tickets": "CAST(ticket_id AS BIGINT) AS ticket_id, CAST(order_id AS BIGINT) AS order_id, CAST(game_id AS BIGINT) AS game_id, CAST(home_team_id AS BIGINT) AS home_team_id, "
               "section, row_label, CAST(seat_number AS INT) AS seat_number, price_tier_code, ticket_type_code, CAST(face_value_usd AS DECIMAL(10,2)) AS face_value_usd, "
               "CAST(sold_price_usd AS DECIMAL(10,2)) AS sold_price_usd, CAST(service_fee_usd AS DECIMAL(10,2)) AS service_fee_usd, "
               "CAST(commission_usd AS DECIMAL(10,2)) AS commission_usd, CAST(scanned_at AS TIMESTAMP) AS scanned_at",
}

for t in TABLES:
    cols = CASTS.get(t, "*")
    src = f"SELECT {cols} FROM parquet.`{STAGING}/{t}`"
    spark.sql(f"CREATE OR REPLACE TABLE {CURATED}.{t} AS {src}")
    spark.sql(f"CREATE OR REPLACE TABLE {RAW}.{t} AS SELECT * FROM {CURATED}.{t}")
    n = spark.table(f"{CURATED}.{t}").count()
    print(f"{t:<10} {n:>12,} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Clutter for the raw schema
# MAGIC Real workspaces are full of `_backup`, `_tmp` and `_export` tables. An author who "attaches everything" hands them to the agent too.

# COMMAND ----------

spark.sql(f"CREATE OR REPLACE TABLE {RAW}.tickets_backup_2025_10 AS SELECT t.* FROM {RAW}.tickets t JOIN {RAW}.games g USING (game_id) WHERE g.game_date < '2025-11-01'")
spark.sql(f"CREATE OR REPLACE TABLE {RAW}.orders_tmp AS SELECT * FROM {RAW}.orders TABLESAMPLE (5 PERCENT) REPEATABLE ({SEED})")
spark.sql(f"CREATE OR REPLACE TABLE {RAW}.customers_old AS SELECT customer_id, first_name, last_name, email, home_state AS state, loyalty_tier_code AS tier FROM {RAW}.customers WHERE signup_date < '2020-01-01'")
spark.sql(f"""CREATE OR REPLACE TABLE {RAW}.games_export AS
  SELECT game_id AS GameID, date_format(game_date, 'MM/dd/yyyy') AS GameDate, home.team_name AS HomeTeam, away.team_name AS AwayTeam,
         CONCAT(home_points, '-', away_points) AS FinalScore, attendance AS Attendance
  FROM {RAW}.games g JOIN {RAW}.teams home ON home.team_id = g.home_team_id JOIN {RAW}.teams away ON away.team_id = g.away_team_id""")
for t in ["tickets_backup_2025_10", "orders_tmp", "customers_old", "games_export"]:
    print(f"{t:<24} {spark.table(f'{RAW}.{t}').count():>12,} rows")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Document the curated schema (Unity Catalog comments)
# MAGIC Genie reads table and column comments. The texts live in `generator/semantic_layer.py` so the data dictionary, the agent configuration and the catalog never drift apart.

# COMMAND ----------

def q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")

for t, comment in sem.TABLE_COMMENTS.items():
    spark.sql(f"COMMENT ON TABLE {CURATED}.{t} IS '{q(comment)}'")
    for c, cc in sem.COLUMN_COMMENTS[t].items():
        spark.sql(f"ALTER TABLE {CURATED}.{t} ALTER COLUMN {c} COMMENT '{q(cc)}'")
    print(f"documented {t} ({len(sem.COLUMN_COMMENTS[t])} columns)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Primary and foreign keys (informational) on the curated schema
# MAGIC Genie imports PK/FK pairs as join relationships automatically. The raw schema gets none.

# COMMAND ----------

ddl = [
    f"ALTER TABLE {CURATED}.teams     ALTER COLUMN team_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.arenas    ALTER COLUMN arena_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.games     ALTER COLUMN game_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.customers ALTER COLUMN customer_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.orders    ALTER COLUMN order_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.tickets   ALTER COLUMN ticket_id SET NOT NULL",
    f"ALTER TABLE {CURATED}.teams     ADD CONSTRAINT teams_pk PRIMARY KEY (team_id)",
    f"ALTER TABLE {CURATED}.arenas    ADD CONSTRAINT arenas_pk PRIMARY KEY (arena_id)",
    f"ALTER TABLE {CURATED}.games     ADD CONSTRAINT games_pk PRIMARY KEY (game_id)",
    f"ALTER TABLE {CURATED}.customers ADD CONSTRAINT customers_pk PRIMARY KEY (customer_id)",
    f"ALTER TABLE {CURATED}.orders    ADD CONSTRAINT orders_pk PRIMARY KEY (order_id)",
    f"ALTER TABLE {CURATED}.tickets   ADD CONSTRAINT tickets_pk PRIMARY KEY (ticket_id)",
    f"ALTER TABLE {CURATED}.teams     ADD CONSTRAINT teams_arena_fk FOREIGN KEY (arena_id) REFERENCES {CURATED}.arenas(arena_id)",
    f"ALTER TABLE {CURATED}.games     ADD CONSTRAINT games_home_fk FOREIGN KEY (home_team_id) REFERENCES {CURATED}.teams(team_id)",
    f"ALTER TABLE {CURATED}.games     ADD CONSTRAINT games_away_fk FOREIGN KEY (away_team_id) REFERENCES {CURATED}.teams(team_id)",
    f"ALTER TABLE {CURATED}.games     ADD CONSTRAINT games_arena_fk FOREIGN KEY (arena_id) REFERENCES {CURATED}.arenas(arena_id)",
    f"ALTER TABLE {CURATED}.orders    ADD CONSTRAINT orders_customer_fk FOREIGN KEY (customer_id) REFERENCES {CURATED}.customers(customer_id)",
    f"ALTER TABLE {CURATED}.orders    ADD CONSTRAINT orders_game_fk FOREIGN KEY (game_id) REFERENCES {CURATED}.games(game_id)",
    f"ALTER TABLE {CURATED}.tickets   ADD CONSTRAINT tickets_order_fk FOREIGN KEY (order_id) REFERENCES {CURATED}.orders(order_id)",
    f"ALTER TABLE {CURATED}.tickets   ADD CONSTRAINT tickets_game_fk FOREIGN KEY (game_id) REFERENCES {CURATED}.games(game_id)",
    f"ALTER TABLE {CURATED}.tickets   ADD CONSTRAINT tickets_home_fk FOREIGN KEY (home_team_id) REFERENCES {CURATED}.teams(team_id)",
]
for stmt in ddl:
    try:
        spark.sql(stmt)
        print("OK ", stmt.split(CURATED + ".")[1][:60])
    except Exception as e:  # re-runs: constraint already exists
        print("-- ", stmt.split(CURATED + ".")[1][:60], "|", str(e)[:80])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Sanity check
# MAGIC Numbers to compare with `docs/data_dictionary.md` (scale 1.0, seed 2026): 7.28M ticket rows, 6.1M tickets sold, 2.22M orders, 306,000 customer rows / 299,958 people, 1,325 games.

# COMMAND ----------

display(spark.sql(f"""
SELECT 'ticket rows' AS metric, COUNT(*) AS value FROM {CURATED}.tickets
UNION ALL SELECT 'tickets sold (COMPLETED, not COMP)', COUNT(*) FROM {CURATED}.tickets t JOIN {CURATED}.orders o USING (order_id) WHERE o.status = 'COMPLETED' AND t.ticket_type_code <> 'COMP'
UNION ALL SELECT 'orders', COUNT(*) FROM {CURATED}.orders
UNION ALL SELECT 'customer rows', COUNT(*) FROM {CURATED}.customers
UNION ALL SELECT 'customers (distinct email)', COUNT(DISTINCT lower(email)) FROM {CURATED}.customers
UNION ALL SELECT 'games', COUNT(*) FROM {CURATED}.games
UNION ALL SELECT 'games REG', COUNT(*) FROM {CURATED}.games WHERE game_type = 'REG'
"""))

# COMMAND ----------

# MAGIC %md
# MAGIC Next: run **`02_curate_and_govern`** (views, trusted function, row filter, column masks, expected answers). Staging parquet files can be deleted afterwards: `dbutils.fs.rm(STAGING, True)`.
