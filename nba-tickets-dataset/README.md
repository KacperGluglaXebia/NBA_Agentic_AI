# HoopsPass — synthetic NBA ticket-sales dataset for a Genie Agent demo

Dataset, generator and Databricks notebooks for the Xebia webinar **Talk To Your Data · Databricks: Conversational Analytics for Business Teams** (22 Oct 2026).
The story: *same data, same model, two Genie Agents* — one created with defaults on raw tables, one curated by a subject-matter expert. The data carries realistic traps so that the difference is visible on every question.

**HoopsPass Inc. is fictional.** The 30 NBA teams and their arenas are public reference data (names, cities, approximate capacities); every customer, order, ticket, price, score and schedule is synthetic and generated with a fixed seed. Phones use the fictional `555-01xx` range, e-mails the reserved `example.com/.net/.org` domains. No logos or trademarks are included.

## What is in the box

| Path | What |
|---|---|
| `generator/nba_tickets_generator.py` | The generator (numpy/pandas, deterministic). `--scale 1.0` = full season ≈ 7.3M tickets, 2.2M orders, 306k customers, 1,325 games; runs in ~30 s locally, 3–6 min on a serverless notebook including Delta writes. |
| `generator/semantic_layer.py` | Single source of truth: Unity Catalog comments, metric/filter/field definitions, synonyms, general instructions, example queries, benchmarks. Used by notebook 01 and by the docs renderer. |
| `generator/validate_dataset.py` | 34 invariant checks (schedule, integrity, money, and "the traps actually bite") with DuckDB. |
| `generator/render_docs.py` | Renders `docs/data_dictionary.md` and `docs/genie_agent_config.md` from `semantic_layer.py`. |
| `databricks/01_generate_nba_tickets.py` | Databricks notebook: generates parquet into a UC volume, loads `<catalog>.sales` (curated: comments, PK/FK) and `<catalog>.sales_raw` (undocumented + clutter tables). |
| `databricks/02_curate_and_govern.sql` | Databricks SQL notebook: curated views `v_ticket_sales` / `v_game_sales`, trusted table function, region mapping table, row filter, column masks, `demo_expected_answers`, cleanup. |
| `databricks/03_knowledge_and_tools.py` | Databricks notebook: policy PDFs into the volume `<catalog>.knowledge.policies`, hand-built RAG (`ai_parse_document` → one chunk per policy section → AI Search index `knowledge.policy_chunks_index`), and the agent tools `agent_tools.team_season_summary` / `agent_tools.order_lookup` in their own schema. |
| `databricks/04_code_agent.py` | Databricks notebook: the assistant as code — tools from three managed MCP servers (Genie agent B, the policy index, `agent_tools`), route matrix with MLflow traces, LLM judge (`mlflow.genai`), least-privilege attacks, logging to Unity Catalog as a `ResponsesAgent`, optional deployment. |
| `agent/` | `hoopspass_agent.py` (LangGraph agent + `ResponsesAgent`, models from code), `agent_config.yaml` (model, system prompt), `requirements.txt`. |
| `knowledge/policies/*.md` | Eight synthetic HoopsPass policies (refunds, fees, resale, season tickets, rewards, gate entry, allocation and comps, privacy), consistent with the generator's rules. `generator/render_policies.py` renders them to `knowledge/pdf/`. |
| `docs/data_dictionary.md` | Every table and column, business definitions, the six traps and which configuration layer fixes each. |
| `docs/genie_agent_config.md` | Copy-paste configuration for agent A (raw) and agent B (curated): sources, hidden columns, synonyms, SQL expressions, instructions, example queries, benchmarks. |
| `docs/demo_script.md` | The three demo blocks (raw agent, curated agent, assistant) question by question, with expected answers and what to point at. |
| `sample/` | CSV extract at `--scale 0.01` (all tables, small) for a quick look without Databricks. |

## Run it on Databricks (Premium workspace)

1. Import this repository as a **Git folder** (Workspace → Create → Git folder) so the notebooks can import `../generator`.
2. Open `databricks/01_generate_nba_tickets`, set the widgets (`catalog` = `hoopspass`, `scale` = `1.0`, `seed` = `2026`) and **Run all** on serverless. It creates the catalog, the schemas `sales`, `sales_raw`, `governance`, a staging volume, loads the tables and documents the curated schema.
3. Create two account groups: `hoopspass_all_regions` and `hoopspass_pii_readers`; add yourself to both. Create (or pick) a second user that is in neither — the "regional manager".
4. Open `databricks/02_curate_and_govern`, put the second user's e-mail into the `MERGE` statement, and run everything **except** the *Apply* cell (you run that one live during the demo). Check `demo_expected_answers`.
5. Create the two Genie Agents exactly as described in `docs/genie_agent_config.md`, give the regional user CAN RUN on agent B, and run the benchmarks on both agents the day before.
6. Run `databricks/03_knowledge_and_tools` (widget `search_endpoint` = an existing AI Search endpoint) and then `databricks/04_code_agent`. Keep 04's output: demo C shows its route matrix, traces and judge scores.
7. In AI Playground, prepare the no-code assistant described in `docs/demo_script.md` (demo C).
8. Rehearse with `docs/demo_script.md`.

To rehearse cheaply use `scale = 0.1` (same traps, ~730k tickets); switch back to `1.0` for the recording.

## Run it locally

```bash
pip install numpy pandas pyarrow duckdb
python generator/nba_tickets_generator.py --out ./out --scale 0.05 --format parquet
python generator/validate_dataset.py --data ./out
python generator/render_docs.py --data ./out --docs ./docs
```

## The traps, in one paragraph

Codes without meaning (`ticket_type_code`, `channel_code`, `price_tier_code`, `loyalty_tier_code`) decoded only in `ref_codes`; `orders.total_usd` at order grain next to `tickets` at seat grain (fan-out), `games.attendance` arena-wide next to HoopsPass's own `allocated_seats`, season orders with `game_id NULL`; tickets sold *for* the home team; *sold* means order COMPLETED and not complimentary; *revenue* is fees plus commissions, not GMV; *sell-through* is sold divided by allocation, not capacity; `date_of_birth = 1900-01-01` for 12% of customers; 2% duplicate customer rows; Toronto is not in the United States; and PII that must be masked while a regional manager must see only her region. See `docs/data_dictionary.md`.

## Season calendar baked in

Regular season 2025-10-21 → 2026-04-12 with the All-Star break (13–18 Feb), no games on 24 Dec and five on 25 Dec; play-in 14–17 Apr; playoffs from 18 Apr with best-of-seven series in the 2-2-1-1-1 format; data cut-off 30 Jun 2026. Season packages went on sale 4 Aug 2025, single-game tickets 15 Sep 2025.

## Licence

Code and synthetic data: use freely inside Xebia and for the webinar. Team and arena names belong to their owners and are used as factual reference data only.
