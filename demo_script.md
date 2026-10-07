# Demo script — "same data, same model, two agents"

Webinar: *Talk To Your Data · Databricks: Conversational Analytics for Business Teams* (22 Oct 2026, 14:00 CEST).
Demo A runs ~7 minutes (min 15–22 of the run-of-show), demo B ~12 minutes (min 26–38). All questions are typed exactly as written here; keep them in a text file and paste.
Reference numbers are for `scale 1.0`, `seed 2026`; your `hoopspass.sales.demo_expected_answers` table has the ones for your workspace.

## Before the webinar (checklist)

* Notebooks `01` and `02` run; `02` section 3 (**Apply**) NOT yet executed — you run it live. Prompt matching on the fact tables works only while no row filter is active.
* Groups `hoopspass_all_regions` and `hoopspass_pii_readers` exist, you are in both; the second account (regional manager) is in neither and is mapped to `Northeast` in `governance.region_access`.
* Agent A `HoopsPass Sales (raw)` and agent B `HoopsPass Sales Assistant` configured as in `genie_agent_config.md`; the regional account has CAN RUN on agent B.
* Benchmarks (9 questions) run on both agents the day before; results tab open in a background tab.
* Second browser profile logged in as the regional account, agent B already open, conversation history cleared.
* SQL editor tab with the **Apply** cell of `02` ready, serverless warehouse warmed up 10 minutes before the start.
* Hidden backup slides: a screenshot of every expected answer below.
* Rehearsed three times; questions that did not fail reliably on agent A were swapped for ones from the benchmark list.

## Demo A · the five-minute agent (raw schema, defaults)

Say once: *"This is not a straw man. It is what you get when you click New Genie Agent, attach the tables you can find and press Create."*

| # | Question (paste verbatim) | What agent A typically does | Name the missing layer |
|---|---|---|---|
| A1 | How many tickets did we sell for Lakers home games this season? | Joins `teams` on home **or** away (≈650k), or counts every row incl. comps, resale and refunded orders (≈362k). Different numbers on re-ask. | Business definition of *sold* and *home team* — nothing in the raw schema says it |
| A2 | What is the average age of our customers? | 58.3 years: averages over the 1900-01-01 sentinel rows | Data-quality knowledge that lives in people's heads, not in the table |
| A3 | What was our revenue in December 2025? | Sums `sold_price_usd` of all ticket rows for December games (≈178M), or joins `orders.total_usd` to tickets (≈433M), or reports GMV (≈116M) and calls it revenue | Metric definition (net revenue vs GMV) and grain (order vs ticket) |
| A4 | Show me the names and emails of our top 10 customers by spend. | Prints names and e-mails, ranks duplicate accounts separately | Governance: nothing stops it, because nothing was configured |

Ask the chat: *"Which of these four would you have caught in a meeting?"* — then move to the theory block that maps each failure to a configuration layer.

## Demo B · the curated agent (same questions first)

| # | Question | Expected answer | Point at |
|---|---|---|---|
| B1 | How many tickets did we sell for Lakers home games this season? | **341,550** with the *verified* badge (trusted function `tickets_sold_by_team`); regular season only: 268,648 | Configure > Examples > SQL functions; the generated call; the badge |
| B2 | What is the average age of our customers? | **47.5**, summary says unknown DOBs were excluded and people were de-duplicated | Column comment on `date_of_birth`; Filter *Known date of birth* |
| B3 | What was our revenue in December 2025? | **Net revenue ≈ 14.9M USD**, GMV ≈ 116.2M USD shown alongside, scope "purchases made in December" stated | General instructions (clarification rule); Measures *Net revenue* / *GMV* |
| B4 | Show me the names and emails of our top 10 customers by spend. | Declines the PII columns (hidden in the agent), offers the top 10 by `customer_id` with spend | Hidden columns in Sources — and say that hiding is convenience, the mask comes next |

Open **Configure** for 60 seconds: Sources (5 objects, descriptions), one Measure, one Example query with a parameter, the General instructions. Do not read them aloud; the slide already summarised them.

## Demo B · what agent A cannot do at all

**Visualisation and follow-up (bullet 4 of the landing page)**

* "Compare average ticket price by price tier as a bar chart" → bar chart, Courtside on top.
* "Only for playoff games" → the chart re-renders with the filter applied.
* "Save this to a dashboard" → the Save to dashboard flow.

**Governance, live (bullet 5)**

1. In the SQL editor tab run the **Apply** cell of `02_curate_and_govern.sql` (row filter on `tickets` and `games`, masks on `customers` and `orders.card_last4`). Two seconds.
2. Switch to the regional-manager browser profile, agent B, ask B1 again → **no Lakers rows** (Lakers are West, the user sees Northeast). Ask "Which team sold the most tickets?" → **Boston Celtics**.
3. Ask "Show me the emails of our top 5 customers" from that profile → `***@example.com`.
4. One sentence: *"I changed nothing in the agent. The policy lives in Unity Catalog, so it also holds in SQL, dashboards and any other agent."*

**Benchmarks and Monitor**

* Benchmarks tab: the same 9 questions, agent A scored x/9, agent B 9/9 (run the day before; a live run takes minutes).
* Monitor tab: weekly digest, thumbs up/down, *Request review* — this is how the SME finds the next definition to add.

## After the webinar

Run the commented **Cleanup** cell in `02` to drop the row filter and masks if you want prompt matching back on the fact tables, and delete the staging volume folder.
