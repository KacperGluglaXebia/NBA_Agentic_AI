-- Databricks notebook source
-- MAGIC %md
-- MAGIC # HoopsPass · 02 · Curate and govern
-- MAGIC
-- MAGIC Run after `01_generate_nba_tickets`. This notebook builds what the **well-configured agent (B)** stands on:
-- MAGIC
-- MAGIC 1. two curated views (`v_ticket_sales` at ticket grain, `v_game_sales` at game grain) that bake the business definitions into SQL,
-- MAGIC 2. a table-valued SQL function to attach to Genie as a **trusted asset**,
-- MAGIC 3. governance: a region mapping table, a **row filter** on `tickets`/`games` and **column masks** on PII,
-- MAGIC 4. the expected answers for the demo questions (`demo_expected_answers`),
-- MAGIC 5. a cleanup cell that removes filter and masks (run it after the webinar, or if you need prompt matching on the fact tables again).
-- MAGIC
-- MAGIC Change the two lines below if you used another catalog name in notebook 01.

-- COMMAND ----------

USE CATALOG hoopspass;
USE SCHEMA sales;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 1. Curated views
-- MAGIC `v_ticket_sales`: one row per ticket, codes decoded, home team resolved, `is_sold` precomputed, **no PII columns**.

-- COMMAND ----------

CREATE OR REPLACE VIEW v_ticket_sales
COMMENT 'One row per ticket (seat x game) with codes decoded and the home team resolved. is_sold = order COMPLETED and not complimentary. Use this view for counting tickets and summing ticket-level money (sold_price_usd, service_fee_usd, commission_usd). No PII.'
AS
SELECT t.ticket_id,
       t.order_id,
       t.game_id,
       g.game_date,
       g.game_type,
       g.playoff_round,
       t.home_team_id,
       home.team_name      AS home_team_name,
       home.nickname       AS home_team_nickname,
       home.team_code      AS home_team_code,
       home.sales_region,
       away.team_name      AS away_team_name,
       o.customer_id,
       o.order_ts,
       o.order_type,
       o.status          AS order_status,
       o.channel_code,
       ch.label          AS channel_label,
       o.payment_method,
       o.promo_code,
       t.price_tier_code,
       pt.label          AS price_tier_label,
       t.ticket_type_code,
       tt.label          AS ticket_type_label,
       t.section, t.row_label, t.seat_number,
       t.face_value_usd,
       t.sold_price_usd,
       t.service_fee_usd,
       t.commission_usd,
       t.scanned_at,
       (o.status = 'COMPLETED' AND t.ticket_type_code <> 'COMP') AS is_sold,
       (o.status = 'COMPLETED' AND t.ticket_type_code <> 'COMP' AND t.scanned_at IS NULL) AS is_no_show
FROM tickets t
JOIN orders o   ON o.order_id = t.order_id
JOIN games g    ON g.game_id = t.game_id
JOIN teams home   ON home.team_id = g.home_team_id
JOIN teams away   ON away.team_id = g.away_team_id
LEFT JOIN ref_codes ch ON ch.code_type = 'channel_code'     AND ch.code = CAST(o.channel_code AS STRING)
LEFT JOIN ref_codes pt ON pt.code_type = 'price_tier_code'  AND pt.code = t.price_tier_code
LEFT JOIN ref_codes tt ON tt.code_type = 'ticket_type_code' AND tt.code = t.ticket_type_code;

COMMENT ON COLUMN v_ticket_sales.is_sold IS 'TRUE when the ticket counts as a sale: order COMPLETED and ticket_type_code <> COMP. Filter on it for tickets sold, revenue, average price.';
COMMENT ON COLUMN v_ticket_sales.home_team_name IS 'The team HoopsPass sold the ticket for (home side). "Tickets sold for the Lakers" filters on this column.';
COMMENT ON COLUMN v_ticket_sales.sold_price_usd IS 'Price paid for the seat (0 for COMP). Average ticket price = AVG over is_sold AND ticket_type_code <> RSL.';
COMMENT ON COLUMN v_ticket_sales.service_fee_usd IS 'HoopsPass fee on the ticket. Net revenue = service_fee_usd + commission_usd over sold tickets.';
COMMENT ON COLUMN v_ticket_sales.order_ts IS 'Purchase timestamp (UTC). "Sales in December" = order_ts in December; "December games" = game_date in December.';

-- COMMAND ----------

-- MAGIC %md
-- MAGIC `v_game_sales`: one row per game with the allocation and per-game totals. **Sell-through lives here** (sold / allocated is a game-level ratio).

-- COMMAND ----------

CREATE OR REPLACE VIEW v_game_sales
COMMENT 'One row per game: schedule, score, arena-wide attendance, HoopsPass allocation and HoopsPass per-game totals. Sell-through rate = SUM(primary_tickets_sold) / SUM(allocated_seats). attendance is the arena-wide figure (all vendors) and must not be compared with tickets_sold.'
AS
WITH s AS (
  SELECT game_id,
         SUM(CASE WHEN is_sold THEN 1 ELSE 0 END)                                   AS tickets_sold,
         SUM(CASE WHEN is_sold AND ticket_type_code <> 'RSL' THEN 1 ELSE 0 END)      AS primary_tickets_sold,
         SUM(CASE WHEN ticket_type_code = 'COMP' AND order_status = 'COMPLETED' THEN 1 ELSE 0 END) AS comps,
         SUM(CASE WHEN is_sold THEN sold_price_usd + service_fee_usd ELSE 0 END)     AS gmv_usd,
         SUM(CASE WHEN is_sold THEN service_fee_usd + commission_usd ELSE 0 END)     AS net_revenue_usd,
         SUM(CASE WHEN is_no_show THEN 1 ELSE 0 END)                                 AS no_shows
  FROM v_ticket_sales GROUP BY game_id)
SELECT g.game_id, g.season, g.game_type, g.playoff_round, g.game_date, g.tipoff_local, g.series_game_no,
       g.home_team_id, home.team_name AS home_team_name, home.team_code AS home_team_code, home.sales_region,
       g.away_team_id, away.team_name AS away_team_name,
       g.arena_id, a.arena_name, a.city AS arena_city, a.state AS arena_state, a.country AS arena_country, a.capacity,
       g.is_national_tv, g.home_points, g.away_points, g.attendance, g.allocated_seats,
       COALESCE(s.tickets_sold, 0) AS tickets_sold, COALESCE(s.primary_tickets_sold, 0) AS primary_tickets_sold,
       COALESCE(s.comps, 0) AS comps, COALESCE(s.gmv_usd, 0) AS gmv_usd, COALESCE(s.net_revenue_usd, 0) AS net_revenue_usd,
       COALESCE(s.no_shows, 0) AS no_shows,
       ROUND(COALESCE(s.primary_tickets_sold, 0) / g.allocated_seats, 4) AS sell_through_rate
FROM games g
JOIN teams home ON home.team_id = g.home_team_id
JOIN teams away ON away.team_id = g.away_team_id
JOIN arenas a ON a.arena_id = g.arena_id
LEFT JOIN s ON s.game_id = g.game_id;

COMMENT ON COLUMN v_game_sales.sell_through_rate IS 'Per-game sell-through. For a period or a team use SUM(primary_tickets_sold)/SUM(allocated_seats), never AVG of this column.';
COMMENT ON COLUMN v_game_sales.attendance IS 'Arena-wide attendance (all vendors). HoopsPass sold only allocated_seats of them.';

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 2. Trusted asset: table-valued SQL function
-- MAGIC Attach it in Genie under *Configure > Examples > SQL functions*. Genie passes the user's team name; the SQL inside is fixed, so the answer gets the **verified** badge.

-- COMMAND ----------

CREATE OR REPLACE FUNCTION tickets_sold_by_team(team STRING)
RETURNS TABLE (team_name STRING, tickets_sold BIGINT, gmv_usd DECIMAL(18,2), net_revenue_usd DECIMAL(18,2))
COMMENT 'Tickets sold (order COMPLETED, not complimentary) for the HOME games of one team in season 2025-26, with GMV and HoopsPass net revenue. The team can be given as full name (Los Angeles Lakers), nickname (Lakers) or code (LAL). Use for any "how many tickets did we sell for <team>" question.'
RETURN
  SELECT home_team_name AS team_name,
         COUNT(*)                                        AS tickets_sold,
         CAST(SUM(sold_price_usd + service_fee_usd) AS DECIMAL(18,2))  AS gmv_usd,
         CAST(SUM(service_fee_usd + commission_usd) AS DECIMAL(18,2))  AS net_revenue_usd
  FROM v_ticket_sales
  WHERE is_sold
    AND (lower(home_team_name) = lower(team) OR lower(home_team_nickname) = lower(team) OR lower(home_team_code) = lower(team))
  GROUP BY home_team_name;

SELECT * FROM tickets_sold_by_team('Lakers');

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 3. Governance
-- MAGIC
-- MAGIC **Groups** (create them first under *Settings > Identity and access > Groups*): `hoopspass_all_regions` and `hoopspass_pii_readers`. Put **yourself** in both.
-- MAGIC The second demo account (regional manager) is in neither group and gets its region from the mapping table below.
-- MAGIC
-- MAGIC *Demo timing:* apply this section **live**, after the first questions to agent B. Once a row filter is on `tickets`/`games`, Genie excludes those tables from prompt matching.

-- COMMAND ----------

CREATE TABLE IF NOT EXISTS hoopspass.governance.region_access (
  account_email STRING COMMENT 'Databricks account e-mail (current_user())',
  sales_region  STRING COMMENT 'teams.sales_region the user may see'
) COMMENT 'Row-level security mapping: which sales region each regional user may see. Users in group hoopspass_all_regions bypass it.';

-- >>> replace with the second demo account's e-mail. 'Northeast' makes the Lakers (West) disappear for that user,
--     which is the most visible effect in the demo; the Celtics become the top team.
MERGE INTO hoopspass.governance.region_access AS tgt
USING (SELECT 'regional.manager@example.com' AS account_email, 'Northeast' AS sales_region) AS src
ON tgt.account_email = src.account_email AND tgt.sales_region = src.sales_region
WHEN NOT MATCHED THEN INSERT *;

SELECT * FROM hoopspass.governance.region_access;

-- COMMAND ----------

-- Row filter: all regions for the group, otherwise only the regions mapped to the current user.
CREATE OR REPLACE FUNCTION hoopspass.governance.region_row_filter(home_team_id BIGINT)
RETURNS BOOLEAN
COMMENT 'Row filter for HoopsPass fact tables: members of hoopspass_all_regions see everything; other users see the sales regions mapped to them in governance.region_access.'
RETURN is_account_group_member('hoopspass_all_regions')
    OR EXISTS (SELECT 1
               FROM hoopspass.governance.region_access ra
               JOIN hoopspass.sales.teams t ON t.sales_region = ra.sales_region
               WHERE ra.account_email = current_user() AND t.team_id = home_team_id);

-- Column masks: real values only for hoopspass_pii_readers.
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_name(v STRING)  RETURNS STRING
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE '***' END;
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_email(v STRING) RETURNS STRING
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE CONCAT('***@', split_part(v, '@', 2)) END;
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_phone(v STRING) RETURNS STRING
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE CONCAT('(***) ***-', right(v, 4)) END;
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_zip(v STRING)   RETURNS STRING
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE CONCAT(left(v, 3), '**') END;
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_dob(v DATE)     RETURNS DATE
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE make_date(year(v), 1, 1) END;
CREATE OR REPLACE FUNCTION hoopspass.governance.mask_last4(v STRING) RETURNS STRING
  RETURN CASE WHEN is_account_group_member('hoopspass_pii_readers') THEN v ELSE '****' END;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ### Apply (the live moment of the demo)
-- MAGIC One statement per policy. Nothing changes in the agent; the next answer from the regional account is already different.

-- COMMAND ----------

ALTER TABLE tickets SET ROW FILTER hoopspass.governance.region_row_filter ON (home_team_id);
ALTER TABLE games   SET ROW FILTER hoopspass.governance.region_row_filter ON (home_team_id);

ALTER TABLE customers ALTER COLUMN first_name    SET MASK hoopspass.governance.mask_name;
ALTER TABLE customers ALTER COLUMN last_name     SET MASK hoopspass.governance.mask_name;
ALTER TABLE customers ALTER COLUMN email         SET MASK hoopspass.governance.mask_email;
ALTER TABLE customers ALTER COLUMN phone         SET MASK hoopspass.governance.mask_phone;
ALTER TABLE customers ALTER COLUMN home_zip      SET MASK hoopspass.governance.mask_zip;
ALTER TABLE customers ALTER COLUMN date_of_birth SET MASK hoopspass.governance.mask_dob;
ALTER TABLE orders    ALTER COLUMN card_last4    SET MASK hoopspass.governance.mask_last4;

-- what the current user sees now
SELECT sales_region, COUNT(*) AS ticket_rows FROM v_ticket_sales GROUP BY sales_region ORDER BY 1;
SELECT customer_id, first_name, email, phone, date_of_birth, home_zip FROM customers LIMIT 5;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 4. Expected answers for the demo questions
-- MAGIC Stored in `demo_expected_answers` so you can compare agent A and agent B against the same numbers (and paste them into Genie **Benchmarks**).
-- MAGIC Run this cell **as a member of `hoopspass_all_regions`** (the row filter would otherwise shrink the numbers).

-- COMMAND ----------

CREATE OR REPLACE TABLE demo_expected_answers
COMMENT 'Reference answers for the webinar demo questions; naive = what an undocumented agent typically computes, correct = business definition.'
AS
SELECT 'Q1 Lakers tickets' AS question, 'naive: all ticket rows, Lakers home OR away' AS variant, CAST(COUNT(*) AS DOUBLE) AS value
  FROM tickets t JOIN games g ON g.game_id = t.game_id JOIN teams tm ON tm.team_id IN (g.home_team_id, g.away_team_id) WHERE tm.nickname = 'Lakers'
UNION ALL SELECT 'Q1 Lakers tickets', 'semi: all ticket rows, Lakers home (incl. COMP, RSL, refunded)', COUNT(*)
  FROM tickets t JOIN teams tm ON tm.team_id = t.home_team_id WHERE tm.nickname = 'Lakers'
UNION ALL SELECT 'Q1 Lakers tickets', 'correct: home games, sold (all game types)', COUNT(*)
  FROM v_ticket_sales WHERE is_sold AND home_team_nickname = 'Lakers'
UNION ALL SELECT 'Q1 Lakers tickets', 'correct: home games, sold, regular season', COUNT(*)
  FROM v_ticket_sales WHERE is_sold AND home_team_nickname = 'Lakers' AND game_type = 'REG'
UNION ALL SELECT 'Q2 average age', 'naive: all rows incl. 1900-01-01', ROUND(AVG(DATEDIFF(YEAR, date_of_birth, DATE '2026-06-30')), 1) FROM customers
UNION ALL SELECT 'Q2 average age', 'correct: known DOB, de-duplicated by email', ROUND(AVG(a), 1)
  FROM (SELECT lower(email) e, MIN(DATEDIFF(YEAR, date_of_birth, DATE '2026-06-30')) a FROM customers WHERE date_of_birth <> DATE '1900-01-01' GROUP BY lower(email))
UNION ALL SELECT 'Q3 December 2025 revenue', 'naive: SUM(sold_price) of all ticket rows for December games', ROUND(SUM(sold_price_usd))
  FROM v_ticket_sales WHERE game_date BETWEEN '2025-12-01' AND '2025-12-31'
UNION ALL SELECT 'Q3 December 2025 revenue', 'naive: SUM(orders.total_usd) after joining tickets (fan-out), December purchases', ROUND(SUM(o.total_usd))
  FROM orders o JOIN tickets t ON t.order_id = o.order_id WHERE o.order_ts >= '2025-12-01' AND o.order_ts < '2026-01-01'
UNION ALL SELECT 'Q3 December 2025 revenue', 'GMV: completed orders placed in December', ROUND(SUM(total_usd))
  FROM orders WHERE status = 'COMPLETED' AND order_ts >= '2025-12-01' AND order_ts < '2026-01-01'
UNION ALL SELECT 'Q3 December 2025 revenue', 'correct: HoopsPass net revenue (fees + commissions), December purchases', ROUND(SUM(service_fee_usd + commission_usd))
  FROM v_ticket_sales WHERE is_sold AND order_ts >= '2025-12-01' AND order_ts < '2026-01-01'
UNION ALL SELECT 'Q3 December 2025 revenue', 'alt: net revenue for games played in December', ROUND(SUM(service_fee_usd + commission_usd))
  FROM v_ticket_sales WHERE is_sold AND game_date BETWEEN '2025-12-01' AND '2025-12-31'
UNION ALL SELECT 'Q5 customers', 'naive: customer rows', COUNT(*) FROM customers
UNION ALL SELECT 'Q5 customers', 'correct: distinct people (lower(email))', COUNT(DISTINCT lower(email)) FROM customers
UNION ALL SELECT 'Q6 sell-through REG', 'correct: primary sold / allocated (%)', ROUND(100.0 * SUM(primary_tickets_sold) / SUM(allocated_seats), 1) FROM v_game_sales WHERE game_type = 'REG'
UNION ALL SELECT 'Q6 sell-through REG', 'naive: tickets rows / arena capacity (%)', ROUND(100.0 * SUM(tickets_sold + comps) / SUM(capacity), 1) FROM v_game_sales WHERE game_type = 'REG'
UNION ALL SELECT 'Q7 no-show', 'correct: unscanned among sold (%)', ROUND(100.0 * SUM(CASE WHEN is_no_show THEN 1 ELSE 0 END) / SUM(CASE WHEN is_sold THEN 1 ELSE 0 END), 1) FROM v_ticket_sales
UNION ALL SELECT 'Q7 no-show', 'naive: NULL scanned_at over all rows (%)', ROUND(100.0 * SUM(CASE WHEN scanned_at IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) FROM tickets
UNION ALL SELECT 'Q8 US arenas', 'naive: all arenas', COUNT(*) FROM arenas
UNION ALL SELECT 'Q8 US arenas', 'correct: country = US', COUNT(*) FROM arenas WHERE country = 'US';

SELECT * FROM demo_expected_answers ORDER BY question, variant;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 5. Top team, for the "which team sold the most" benchmark

-- COMMAND ----------

SELECT home_team_name, COUNT(*) AS tickets_sold, ROUND(SUM(service_fee_usd + commission_usd)) AS net_revenue_usd
FROM v_ticket_sales WHERE is_sold GROUP BY home_team_name ORDER BY tickets_sold DESC LIMIT 10;

-- COMMAND ----------

-- MAGIC %md
-- MAGIC ## 6. Cleanup (after the webinar, or to re-enable prompt matching on the fact tables)
-- MAGIC Policies stay on the tables until you drop them. `DROP ROW FILTER` on a table that has none fails, which is fine.

-- COMMAND ----------

-- ALTER TABLE tickets DROP ROW FILTER;
-- ALTER TABLE games   DROP ROW FILTER;
-- ALTER TABLE customers ALTER COLUMN first_name    DROP MASK;
-- ALTER TABLE customers ALTER COLUMN last_name     DROP MASK;
-- ALTER TABLE customers ALTER COLUMN email         DROP MASK;
-- ALTER TABLE customers ALTER COLUMN phone         DROP MASK;
-- ALTER TABLE customers ALTER COLUMN home_zip      DROP MASK;
-- ALTER TABLE customers ALTER COLUMN date_of_birth DROP MASK;
-- ALTER TABLE orders    ALTER COLUMN card_last4    DROP MASK;
-- SELECT COUNT(*) AS ticket_rows_visible FROM tickets;  -- 7,279,785 at scale 1.0 when no filter is active