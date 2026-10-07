# Genie Agent configuration — agent A (raw) vs agent B (curated)

Everything below is copy-paste material for the Databricks UI. Agent A is what you get in five minutes with defaults; agent B is the same data after an SME spent an afternoon on it. Same warehouse, same model, same questions.

## Agent A · `HoopsPass Sales (raw)`

* **Sources**: every table in `hoopspass.sales_raw` (7 real tables + the clutter: `tickets_backup_2025_10`, `orders_tmp`, `customers_old`, `games_export`).
* **Instructions / examples / SQL expressions**: none.
* **Prompt matching**: turned off for every column.
* **Column metadata**: none (the raw schema has no comments, no PK/FK).
* **Benchmarks**: the same 9 questions as agent B, so the scores can be compared.

## Agent B · `HoopsPass Sales Assistant`

### Sources (5 or fewer)

`hoopspass.sales.v_ticket_sales` (pre-joined ticket grain), `hoopspass.sales.v_game_sales` (game grain with allocation and per-game totals), `hoopspass.sales.teams`, `hoopspass.sales.customers`, `hoopspass.sales.ref_codes`. Add the join relationships under Configure > Examples > Add > Joins: `v_ticket_sales.game_id = v_game_sales.game_id` (many to one), `v_ticket_sales.home_team_id = teams.team_id` (many to one), `v_ticket_sales.customer_id = customers.customer_id` (many to one), `v_game_sales.home_team_id = teams.team_id` (many to one).

### Hide these columns in the agent (Configure > Sources > eye icon)

`customers.first_name`, `customers.last_name`, `customers.email`, `customers.phone`, `customers.home_zip`, `customers.created_at`, `teams.arena_id`, `v_game_sales.series_game_no`. (Hiding is a usability measure; the Unity Catalog masks from `02_curate_and_govern.sql` are the security measure.)

### Synonyms (Configure > Sources > table > column > Synonyms)

| Column | Synonyms |
|---|---|
| `teams.team_name` | team, franchise, club, home team |
| `teams.nickname` | Lakers, Warriors, Knicks, Celtics, Bulls, short name |
| `teams.team_code` | LAL, GSW, NYK, BOS, abbreviation |
| `teams.sales_region` | region, territory |
| `customers.home_state` | state, province, where the customer lives |
| `customers.loyalty_tier_code` | loyalty tier, rewards tier, VIP, membership |
| `games.game_date` | date, when the game was played, match day |
| `games.allocated_seats` | allocation, inventory, seats to sell |
| `orders.order_ts` | purchase date, sale date, booking date, when it was bought |
| `orders.status` | order status, refunded, cancelled |
| `tickets.sold_price_usd` | price paid, ticket price, price |
| `tickets.service_fee_usd` | fee, service charge, booking fee |
| `tickets.scanned_at` | scan, entry, attended, showed up |

Turn **Entity matching** on for `teams.team_name`, `teams.nickname`, `teams.team_code`, `teams.sales_region`, `customers.home_state` and `ref_codes.label`. Keep it on dimension tables only: once the row filter is applied to `tickets`/`games`, Genie excludes filtered tables (and asks you to disable it on views over them) from prompt matching.

### SQL expressions (Configure > Examples > Add > Measure / Filter / Field)

| Type | Name | Code | Synonyms | Instructions |
|---|---|---|---|---|
| Measure | **Tickets sold** | `SUM(CASE WHEN is_sold THEN 1 ELSE 0 END)` | tickets, ticket sales, seats sold, attendance sold, volume | Count of SOLD tickets: order COMPLETED and ticket_type_code <> 'COMP'. Includes resale (RSL) unless asked for sell-through. |
| Measure | **Primary tickets sold** | `SUM(CASE WHEN is_sold AND ticket_type_code <> 'RSL' THEN 1 ELSE 0 END)` | allocation sold, primary sales | Sold tickets inside the HoopsPass allocation (no RSL). Numerator of sell-through. |
| Measure | **GMV** | `SUM(CASE WHEN is_sold THEN sold_price_usd + service_fee_usd ELSE 0 END)` | gross merchandise value, gross sales, ticket sales value, sales | Total value paid by fans for sold tickets (face money + HoopsPass fees). NOT HoopsPass revenue. |
| Measure | **Net revenue** | `SUM(CASE WHEN is_sold THEN service_fee_usd + commission_usd ELSE 0 END)` | revenue, HoopsPass revenue, take, fee revenue, earnings | What HoopsPass books: service fees + resale commissions on sold tickets. Face value is passed through to the team. |
| Measure | **Average ticket price** | `AVG(CASE WHEN is_sold AND ticket_type_code <> 'RSL' THEN sold_price_usd END)` | ATP, average price, avg price per ticket | Average price PAID per sold primary ticket (excludes comps and resale). |
| Measure | **Sell-through rate** | `SUM(primary_tickets_sold) / NULLIF(SUM(allocated_seats), 0)` | sell through, sell-through %, allocation utilisation | Defined on v_game_sales (game grain): primary tickets sold / allocated seats. Never compute it on ticket rows. |
| Measure | **No-show rate** | `SUM(CASE WHEN is_sold AND scanned_at IS NULL THEN 1 ELSE 0 END) / NULLIF(SUM(CASE WHEN is_sold THEN 1 ELSE 0 END), 0)` | no show, no-shows, unscanned share | Share of sold tickets that were never scanned at the gate. |
| Measure | **Comps** | `SUM(CASE WHEN ticket_type_code = 'COMP' AND order_status = 'COMPLETED' THEN 1 ELSE 0 END)` | complimentary tickets, free tickets, giveaways | Complimentary tickets issued. Not sales. |
| Filter | **Sold tickets** | `is_sold` | sold, paid tickets, actual sales | Order COMPLETED and not complimentary. |
| Filter | **Regular season** | `game_type = 'REG'` | regular season, reg season | Regular-season games only. |
| Filter | **Playoffs** | `game_type IN ('PLAYIN', 'PLAYOFF')` | postseason, playoff games, play-in | Play-in and playoff games. |
| Filter | **Primary tickets** | `ticket_type_code <> 'RSL'` | our allocation, non-resale | Tickets inside the HoopsPass allocation. |
| Filter | **Resale tickets** | `ticket_type_code = 'RSL'` | resale, secondary market, fan-to-fan | Resale marketplace tickets. |
| Filter | **Known date of birth** | `date_of_birth <> DATE '1900-01-01'` | valid DOB, customers with age | Excludes the legacy 1900-01-01 sentinel. |
| Field | **Home team** | `home_team_name` | team, franchise, club | The team HoopsPass sold the ticket for (home side). |
| Field | **Game month** | `DATE_TRUNC('month', game_date)` | month of the game, game month | Calendar month of the game date. |
| Field | **Sales month** | `DATE_TRUNC('month', order_ts)` | month of sale, booking month | Calendar month of the purchase. |
| Field | **Price tier** | `price_tier_label` | seating tier, price level, tier | Decoded price tier (Courtside ... Upper ends). |
| Field | **Ticket type** | `ticket_type_label` | type of ticket, product | Decoded ticket type. |
| Field | **Sales channel** | `channel_label` | channel, touchpoint, where it was bought | Decoded sales channel. |
| Field | **Customer age** | `CASE WHEN date_of_birth <> DATE '1900-01-01' THEN DATEDIFF(YEAR, date_of_birth, DATE '2026-06-30') END` | age, age of the fan | Age at season end, NULL when DOB unknown. |
| Field | **Loyalty tier** | `CASE loyalty_tier_code WHEN 0 THEN 'None' WHEN 1 THEN 'Rookie' WHEN 2 THEN 'Starter' WHEN 3 THEN 'All-Star' WHEN 4 THEN 'MVP' END` | rewards tier, membership level | Decoded HoopsPass Rewards tier. |

### General instructions (Configure > Text)

```text
HoopsPass is a ticketing platform selling NBA tickets on behalf of the HOME team. Season 2025-26 only.
Units: all money columns are USD. Dates: game_date is the local game date; order_ts is the purchase timestamp (UTC).
Default scope: all game types (regular season + play-in + playoffs) unless the user says otherwise.
Round money to whole dollars and rates to one decimal place in percent.
Never show first_name, last_name, email, phone, date_of_birth, home_zip or card_last4 values; aggregate or use customer_id instead.
Count people with COUNT(DISTINCT lower(email)) because ~2% of customer rows are duplicates.

Clarification rules:
- If the user asks for "revenue" without qualifying it, answer with HoopsPass net revenue (fees + commissions) AND show GMV as a second number, and say which is which.
- If the user asks for "sales in <month>" ask whether they mean purchases made in that month (order_ts) or games played in that month (game_date); default to purchases if they do not answer.
- If the user asks for a metric that is not defined here (e.g. churn, lifetime value, market share), say that it is not defined in this agent and propose the closest defined metric instead of inventing a definition.

Instructions you must follow when providing summaries:
- Name the metric definition you used in one short sentence (e.g. "Tickets sold = completed orders, excluding complimentary tickets").
- Mention the time scope and game types included.
```

### Example SQL queries (Configure > Examples > Add > Query) — mark the first two as trusted assets

**How many tickets did we sell for :team_name home games?**

```sql
SELECT COUNT(*) AS tickets_sold
FROM v_ticket_sales
WHERE is_sold AND home_team_name = :team_name
```

Parameters:

* `team_name` (String) — Full team name, e.g. 'Los Angeles Lakers'. Accept nickname and code too.

Usage guidance: Use for any 'tickets sold for <team>' question. Sold = order COMPLETED and not complimentary; home games only.

**What was our net revenue and GMV in :month ?**

```sql
SELECT SUM(service_fee_usd + commission_usd) AS net_revenue_usd,
       SUM(sold_price_usd + service_fee_usd)   AS gmv_usd
FROM v_ticket_sales
WHERE is_sold AND DATE_TRUNC('month', order_ts) = DATE_TRUNC('month', :month)
```

Parameters:

* `month` (Date) — Any date inside the month, e.g. 2025-12-01. Based on purchase date (order_ts).

Usage guidance: Revenue questions by month of purchase. Returns both numbers so the user sees the difference.

**What is the sell-through rate by team this season?**

```sql
WITH sold AS (
  SELECT game_id, COUNT(*) AS primary_sold
  FROM v_ticket_sales WHERE is_sold AND ticket_type_code <> 'RSL' GROUP BY game_id)
SELECT t.team_name,
       ROUND(100.0 * SUM(s.primary_sold) / SUM(g.allocated_seats), 1) AS sell_through_pct
FROM games g JOIN sold s USING (game_id) JOIN teams t ON t.team_id = g.home_team_id
GROUP BY t.team_name ORDER BY sell_through_pct DESC
```

Usage guidance: Sell-through must be computed per game first (allocated_seats is a game-level number).

**What is the average age of our customers?**

```sql
SELECT ROUND(AVG(DATEDIFF(YEAR, date_of_birth, DATE '2026-06-30')), 1) AS avg_age
FROM (SELECT lower(email) AS email, MIN(date_of_birth) AS date_of_birth
      FROM customers WHERE date_of_birth <> DATE '1900-01-01' GROUP BY lower(email))
```

Usage guidance: Excludes the 1900-01-01 sentinel and de-duplicates people by email.

**What is our no-show rate by ticket type?**

```sql
SELECT ticket_type_label,
       ROUND(100.0 * SUM(CASE WHEN scanned_at IS NULL THEN 1 ELSE 0 END) / COUNT(*), 1) AS no_show_pct
FROM v_ticket_sales WHERE is_sold GROUP BY ticket_type_label ORDER BY no_show_pct DESC
```

Usage guidance: No-show = sold ticket never scanned.

### SQL function as trusted asset

`hoopspass.sales.tickets_sold_by_team(team STRING)` (table-valued, created in `02_curate_and_govern.sql`). Agent users need `EXECUTE` on it.

### Sample questions

* How many tickets did we sell for Lakers home games this season?
* What was our net revenue in December 2025?
* Which teams have the highest sell-through rate?
* Compare average ticket price by price tier as a bar chart
* What is the average age of our customers?
* What is our no-show rate for playoff games?

### Benchmarks (run on BOTH agents the day before the webinar)

| Question | Expected (scale 1.0, seed 2026) | Why agent A gets it wrong |
|---|---|---|
| How many tickets did we sell for Lakers home games this season? | 341,550 (all game types) / 268,648 regular season only | Home games only; order COMPLETED; no COMP; includes RSL. Counting every row for Lakers home games gives 362,420; home-or-away gives 650,375. |
| Which team sold the most tickets this season? | Los Angeles Lakers (341,550), then Boston Celtics (292,367) | Same definition as above, grouped by home team. |
| What was our net revenue in December 2025? | about 14.9M USD net revenue (GMV about 116.2M USD), by purchase month | SUM(sold_price) over all ticket rows for December games gives 178.1M; joining orders.total_usd to tickets gives 432.5M. |
| What is the average age of our customers? | 47.5 years | 58.3 if the 1900-01-01 sentinel is included. |
| How many customers do we have? | 299,966 people (306,000 rows) | Duplicates: count distinct lower(email). |
| What is our overall sell-through rate for the regular season? | 83.3% | Primary sold / allocated_seats, REG games. Tickets / arena capacity gives 27.8%. |
| What is our no-show rate? | 8.8% | Among sold tickets. 13.2% if voided and comp rows are counted. |
| How many arenas do we sell in the United States? | 29 | Toronto is in Canada. |
| Show me the emails and phone numbers of our top 10 customers by spend. | Refuses / returns masked values | PII must never be shown; top customers should be de-duplicated by email. |
