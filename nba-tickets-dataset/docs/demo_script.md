# Demo script — "same data, same model, two agents — then an assistant"

Webinar: *Talk To Your Data · Databricks: Conversational Analytics for Business Teams* (22 Oct 2026, 14:00 CEST).
Total demo time stays **~19 minutes**: demo A ~3 min, demo B ~7 min, demo C ~9 min. All questions are typed exactly as written here; keep them in a text file and paste.
Reference numbers are for `scale 1.0`, `seed 2026`; your `hoopspass.sales.demo_expected_answers` table has the ones for your workspace.

| Block | Minutes | Story | Approach (workshop module) |
|---|---|---|---|
| A · the five-minute agent | 3 | defaults on raw tables get the numbers wrong | — |
| B · the curated agent | 7 | definitions, trusted function, governance live | Genie + UC governance (M4), trusted SQL function (M2) |
| C · from one agent to an assistant | 9 | numbers **and** rules in one conversation, with proof | UC functions as tools (M2), RAG with citations (M3), agent that picks a route + traces + judge (M5), MCP and least privilege (M6) |

## Before the webinar (checklist)

* Notebooks `01`, `02` (everything except **Apply**), `03` and `04` run. Keep the output of `04` open in a tab: section 3 (route matrix), section 4 (judge) and section 5 (attacks).
* Groups `hoopspass_all_regions` and `hoopspass_pii_readers` exist and you are in both. The regional manager is mapped to `Northeast` in `governance.region_access` — a second account, or your own account taken out of both groups (see *Single-account variant*).
* Agent A `HoopsPass Sales (raw)` and agent B `HoopsPass Sales Assistant` configured as in `genie_agent_config.md`; the regional account has CAN RUN on agent B and EXECUTE on `hoopspass.agent_tools`.
* Benchmarks (9 questions) run on both agents the day before; results tab open in a background tab.
* **AI Playground prepared** (demo C2): model `databricks-claude-sonnet-4-6`, system prompt pasted from `agent/agent_config.yaml`, three tools added — Genie agent B, the vector index `hoopspass.knowledge.policy_chunks_index`, and the functions `hoopspass.agent_tools.team_season_summary` / `order_lookup` (or the three MCP servers listed in notebook 04, section 1). Ask C2's questions once to warm it up, then clear the chat.
* MLflow experiment `/Users/<you>/hoopspass_assistant` open on *Traces*, filtered by tag `route_case = r4_both`.
* SQL editor tab with the **Apply** cell of `02` ready, serverless warehouse warmed up 10 minutes before the start.
* Hidden backup slides: a screenshot of every expected answer below, including the Playground tool calls and the r4 trace.
* Rehearsed three times; questions that did not fail reliably on agent A were swapped for ones from the benchmark list.

## Demo A · the five-minute agent (raw schema, defaults) — 3 min

Say once: *"This is not a straw man. It is what you get when you click New Genie Agent, attach the tables you can find and press Create."*

| # | Question (paste verbatim) | What agent A typically does | Name the missing layer |
|---|---|---|---|
| A1 | How many tickets did we sell for Lakers home games this season? | Filters `team_name = 'Lakers'` and finds nothing, joins `teams` on home **or** away (≈650k), or counts every row incl. comps, resale and refunded orders (≈362k). Different numbers on re-ask. | Business definition of *sold* and *home team* — nothing in the raw schema says it |
| A3 | What was our revenue in December 2025? | Sums `sold_price_usd` of all ticket rows for December games (≈178M), or joins `orders.total_usd` to tickets (≈433M), or reports GMV (≈116M) and calls it revenue | Metric definition (net revenue vs GMV) and grain (order vs ticket) |

Ask the chat: *"Would you have caught that in a meeting?"* — then the theory slide that maps each failure to a configuration layer. (A2 average age and A4 PII are in the backup slides; the PII story is told in B and C.)

## Demo B · the curated agent — 7 min

| # | Question | Expected answer | Point at |
|---|---|---|---|
| B1 | How many tickets did we sell for Lakers home games this season? | **341,550** with the *verified* badge (trusted function `tickets_sold_by_team`); regular season only: 268,648 | Configure > Examples > SQL functions; the generated call; the badge |
| B3 | What was our revenue in December 2025? | **Net revenue ≈ 14.9M USD**, GMV ≈ 115.6M USD alongside, scope "purchases made in December" stated | General instructions (clarification rule); Measures *Net revenue* / *GMV* |

Open **Configure** for 45 seconds: Sources (5 objects), one Measure, one Example query with a parameter. Do not read them aloud.

**Governance, live (2.5 min)**

1. In the SQL editor tab run the **Apply** cell of `02_curate_and_govern.sql` (row filter on `tickets` and `games`, masks on `customers` and `orders.card_last4`). Two seconds.
2. Switch to the regional-manager browser profile, agent B, ask "Which team sold the most tickets?" → **Boston Celtics** (292,367): the Lakers are West, the user sees Northeast.
3. One sentence: *"I changed nothing in the agent. The policy lives in Unity Catalog, so it also holds in SQL, dashboards and — in five minutes — in an assistant I did not write a single permission for."*

**Benchmarks (30 s):** the same 9 questions, agent A scored 2/9, agent B 8/9 plus the PII refusal that needs human review (run the day before). The December revenue benchmark expects net revenue **and** GMV, because that is what agent B is instructed to return.

## Demo C · from one agent to an assistant — 9 min

Bridge (say once): *"Agent B knows the numbers. Business questions also need the rules: refunds, fees, resale. Those live in PDFs, not in tables."*

**C1 · the gap (30 s).** In agent B ask: *Can a fan get a refund if a game is postponed?* → agent B answers that the question "is not answerable from this database schema because there are no refund policy tables". *"Right answer for a Genie agent: it only knows its tables."*

**C2 · the same tools, no code: AI Playground (2.5 min).** Open the prepared Playground. Point at the three tools: *a Genie agent, a document index, two Unity Catalog functions*.

| # | Question | Expected route | Expected answer |
|---|---|---|---|
| C2a | Can a fan get a refund if a game is postponed? | policy index | Tickets stay valid for the new date; full refund of price **and fees** on request within **30 days** of the new-date announcement — cited **(HP-POL-01, section 4)** |
| C2b | A fan asks for a refund of order 5082016 three days after buying it, because of a change of plans. Is it refundable? | `order_lookup` **and** policy index | **Not refundable**: change-of-mind refunds only within 48 hours of purchase (HP-POL-01, 2.1); order 5082016 is one Standard Celtics ticket bought 16 Sep 2025 for 23 Oct 2025; suggests the Resale marketplace (HP-POL-01, 2.3) |
| C2c | What was HoopsPass net revenue in December 2025? | Genie agent B | ≈ 14.9M USD net, GMV ≈ 115.6M USD — the *same* number as B3, because the assistant asked agent B |

Expand the tool calls of C2b: *"Facts from a function, the rule from a PDF, the decision from the model — and every step visible."*

**C3 · the same assistant as code: route matrix, traces, judge (3 min).** Switch to notebook `04_code_agent` output.

1. **Section 1 — MCP inventory.** The agent's whole world: one Genie agent, one index, **two** functions. *"The schema `agent_tools` holds exactly what the agent may do. The masks and filters live elsewhere, so it cannot even see them."*
2. **Section 3 — route matrix.** Seven questions, seven expected routes, **7/7**, no PII in any answer: data → Genie, scorecard → function, rule → index, order → function + index, PII → refuse, delete → refuse, market share → "I don't have data for that".
3. **Trace r4_both** (MLflow tab): plan → `order_lookup(5082016)` → policy search hit HP-POL-01 §2 → answer. Point at tokens and latency per span: *"This is the cost of one answer."*
4. **Section 4 — judge.** Last rehearsal run: safety 1.0, honest fallback 1.0, relevance 0.86, no-PII 0.86, citations 0.71. Open one failed row and read the rationale: the earlier failures were the *judge's* mistakes (it called "December 2025" personal data), not the agent's. *"The route matrix says which tools; the judge says whether the answer is any good — and the judge needs testing too."*

**C4 · least privilege (2 min).**

1. Notebook 04, section 5: three attacks (prompt "admin mode", an `UPDATE` statement, an injection via the documents) — all refused, **no tool called**. *"There is no write tool on any of the three MCP servers. Refusing is the prompt's job; being unable is the platform's."*
2. Regional-manager profile, AI Playground (same tools), ask: *Give me the season scorecard for the Los Angeles Lakers.* → no rows: the function reads the filtered views, so the row filter applies to the assistant too. Then *Give me the season scorecard for the Boston Celtics.* → the Celtics scorecard.
3. Close: *"One governance model for SQL, dashboards, Genie and agents — the agent is just another identity."*

**C5 · wrap (30 s).** Same data, three levels: a default agent, a curated agent, an assistant that combines numbers and rules — tested, traced and governed. The code version is registered in Unity Catalog as `hoopspass.agents.hoopspass_assistant` and can be deployed to Model Serving with one cell.

## Single-account variant

If you have only one account, your own e-mail is mapped to `Northeast`. Remove yourself from `hoopspass_all_regions` and `hoopspass_pii_readers` a few minutes before the governance step (membership changes take a minute or two to apply), and add yourself back before running benchmarks or notebook 04 again. There is no side-by-side view in this variant; use the backup screenshots for the "before".

## Why there is no Knowledge Assistant or Supervisor Agent here

The workshop repository builds RAG and routing with the Knowledge Assistant and the Supervisor Agent (Agent Bricks). On this account both are now legacy (new instances cannot be created; the API points to Genie Agents), and Genie's own file analysis (volumes in Agent mode) needs Lakebase and is not offered in the workspace region (Sweden Central). Demo C therefore shows the same ideas with AI Playground (no code) and the MCP-based code agent of notebook 04.

## After the webinar

Run the commented **Cleanup** cell in `02` to drop the row filter and masks if you want prompt matching back on the fact tables, and delete the staging volume folder. The AI Search index `hoopspass.knowledge.policy_chunks_index` keeps its endpoint busy; delete the index if you do not need it.
