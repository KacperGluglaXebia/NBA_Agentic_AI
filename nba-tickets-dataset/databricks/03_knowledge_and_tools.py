# Databricks notebook source
# MAGIC %md
# MAGIC # HoopsPass · 03 · Knowledge and agent tools
# MAGIC
# MAGIC Run after `02_curate_and_govern`. This notebook prepares what the **assistant** (demo C) stands on, on top of Genie agent B:
# MAGIC
# MAGIC 1. the HoopsPass policy PDFs (`knowledge/pdf`, rendered from `knowledge/policies/*.md`) copied into the volume `<catalog>.knowledge.policies`,
# MAGIC    which is the source of the **Knowledge Assistant** (no-code RAG),
# MAGIC 2. the same PDFs as a **hand-built RAG index**: `ai_parse_document` → one chunk per policy section → AI Search (Vector Search) index with managed embeddings,
# MAGIC    used by the code agent in notebook 04,
# MAGIC 3. two **Unity Catalog functions as agent tools** in their own schema `<catalog>.agent_tools`.
# MAGIC
# MAGIC **Why a separate schema for tools.** The managed MCP server `/api/2.0/mcp/functions/<catalog>/agent_tools` exposes every function in the schema, and nothing else.
# MAGIC The row filter and the masks live in `governance`, the Genie trusted function in `sales`, so an agent connected to `agent_tools` gets exactly two read-only tools (least privilege).
# MAGIC
# MAGIC **Prerequisites:** the repository imported as a Git folder (this notebook reads `../knowledge/pdf`), an AI Search endpoint (widget `search_endpoint`), `CREATE SCHEMA` on the catalog.

# COMMAND ----------

dbutils.widgets.text("catalog", "hoopspass", "Catalog")
dbutils.widgets.text("search_endpoint", "retail_rag_search", "AI Search endpoint")
dbutils.widgets.text("embedding_endpoint", "databricks-gte-large-en", "Embedding endpoint")

CATALOG = dbutils.widgets.get("catalog")
SEARCH_ENDPOINT = dbutils.widgets.get("search_endpoint")
EMBEDDING_ENDPOINT = dbutils.widgets.get("embedding_endpoint")
KNOWLEDGE = f"{CATALOG}.knowledge"
TOOLS = f"{CATALOG}.agent_tools"
VOLUME_PATH = f"/Volumes/{CATALOG}/knowledge/policies"
DOCS_TABLE = f"{KNOWLEDGE}.policy_docs"
CHUNKS_TABLE = f"{KNOWLEDGE}.policy_chunks"
SEARCH_INDEX = f"{KNOWLEDGE}.policy_chunks_index"
print(f"catalog={CATALOG}\nvolume={VOLUME_PATH}\nindex={SEARCH_INDEX} on endpoint {SEARCH_ENDPOINT}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Policy PDFs into a volume

# COMMAND ----------

import os, shutil
from pathlib import Path

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {KNOWLEDGE} COMMENT 'HoopsPass policy documents: source of the Knowledge Assistant and of the RAG index'")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {KNOWLEDGE}.policies COMMENT 'HoopsPass policy PDFs (synthetic), rendered from knowledge/policies/*.md'")

src = Path(os.getcwd()).parent / "knowledge" / "pdf"
pdfs = sorted(src.glob("*.pdf"))
assert pdfs, f"No PDFs in {src}: import the repository as a Git folder, or run generator/render_policies.py"
for pdf in pdfs:
    shutil.copy(pdf, f"{VOLUME_PATH}/{pdf.name}")
print(f"{len(pdfs)} PDFs copied to {VOLUME_PATH}")
display(dbutils.fs.ls(VOLUME_PATH))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Hand-built RAG: parse, chunk, index
# MAGIC `ai_parse_document` turns each PDF into layout elements. We keep the text and cut it at the numbered section headings
# MAGIC (`1. General rule`, `2. Change of mind` ...), so one chunk = one policy section = one citation, prefixed with the document title.

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE {DOCS_TABLE}
COMMENT 'One row per policy PDF: parsed text from ai_parse_document'
AS
WITH parsed AS (
  SELECT path, ai_parse_document(content) AS doc
  FROM read_files('{VOLUME_PATH}/*.pdf', format => 'binaryFile')
)
SELECT regexp_extract(path, '([^/]+)\\\\.pdf$', 1) AS file_name,
       path,
       concat_ws('\\n', transform(try_cast(doc:document:elements AS ARRAY<VARIANT>),
                                  e -> try_cast(e:content AS STRING))) AS text
FROM parsed
""")
display(spark.sql(f"SELECT file_name, length(text) AS chars, left(text, 120) AS start FROM {DOCS_TABLE} ORDER BY file_name"))

# COMMAND ----------

import re

SECTION = re.compile(r"^\s*(\d{1,2})\.\s+([A-Z][^\n]{2,80})$", re.M)   # "3. Cancelled games"
DOC_ID = re.compile(r"HP-POL-\d+")

rows = []
for r in spark.table(DOCS_TABLE).collect():
    text = (r.text or "").replace("\r", "")
    lines = [l for l in text.splitlines() if l.strip()]
    title = next((l.strip("# ").strip() for l in lines if l.strip().startswith("HoopsPass") and "Inc." not in l), r.file_name)
    doc_id = (DOC_ID.search(text) or [r.file_name])[0]
    marks = list(SECTION.finditer(text))
    assert marks, f"No numbered sections found in {r.file_name}: check the ai_parse_document output"
    intro = text[:marks[0].start()].strip()
    pieces = [("0", "Scope", intro)] + [
        (m.group(1), m.group(2).strip(), text[m.end(): (marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip())
        for i, m in enumerate(marks)]
    for no, heading, body in pieces:
        if len(body) < 40:
            continue
        rows.append({"chunk_id": f"{doc_id}-{int(no):02d}", "doc_id": doc_id, "title": title, "section": f"{no}. {heading}" if no != "0" else heading,
                     "source_file": f"{r.file_name}.pdf",
                     "chunk_text": f"{title} ({doc_id}), section {no}. {heading}\n{body}" if no != "0" else f"{title} ({doc_id})\n{body}"})

chunks = spark.createDataFrame(rows)
chunks.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(CHUNKS_TABLE)
spark.sql(f"ALTER TABLE {CHUNKS_TABLE} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
spark.sql(f"COMMENT ON TABLE {CHUNKS_TABLE} IS 'One row per policy section: the unit of retrieval and citation for the RAG index'")
print(f"{len(rows)} chunks from {spark.table(DOCS_TABLE).count()} documents")
display(spark.table(CHUNKS_TABLE).select("chunk_id", "title", "section", "chunk_text").orderBy("chunk_id"))

# COMMAND ----------

# MAGIC %md
# MAGIC Delta Sync index with **managed embeddings**: AI Search calls the embedding endpoint itself, on every sync.

# COMMAND ----------

import time
from databricks.sdk import WorkspaceClient

# REST rather than SDK dataclasses: the request shape is stable across SDK versions on serverless.
w = WorkspaceClient()
API = "/api/2.0/vector-search/indexes"


def index_status() -> dict | None:
    try:
        return w.api_client.do("GET", f"{API}/{SEARCH_INDEX}").get("status", {})
    except Exception as e:
        if "does not exist" in str(e).lower() or "not found" in str(e).lower():
            return None
        raise


if index_status() is None:
    w.api_client.do("POST", API, body={
        "name": SEARCH_INDEX, "endpoint_name": SEARCH_ENDPOINT, "primary_key": "chunk_id", "index_type": "DELTA_SYNC",
        "delta_sync_index_spec": {
            "source_table": CHUNKS_TABLE, "pipeline_type": "TRIGGERED",
            "columns_to_sync": ["chunk_id", "doc_id", "title", "section", "source_file", "chunk_text"],
            "embedding_source_columns": [{"name": "chunk_text", "embedding_model_endpoint_name": EMBEDDING_ENDPOINT}]}})
    print(f"{SEARCH_INDEX} created on {SEARCH_ENDPOINT}")
else:
    w.api_client.do("POST", f"{API}/{SEARCH_INDEX}/sync")
    print(f"{SEARCH_INDEX} exists: sync started")

for _ in range(60):   # the first build takes a few minutes
    status = index_status() or {}
    if status.get("ready"):
        break
    time.sleep(20)
print(f"ready={status.get('ready')} rows={status.get('indexed_row_count')} {status.get('message', '')}")

# COMMAND ----------

hits = w.api_client.do("POST", f"{API}/{SEARCH_INDEX}/query", body={
    "columns": ["chunk_id", "section", "chunk_text"], "num_results": 3,
    "query_text": "Can I get my money back if a game is postponed?"})
for row in hits["result"]["data_array"]:
    print(row[0], "|", row[1], "|", row[2][:140].replace("\n", " "), "| score", round(row[-1], 3))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Unity Catalog functions as agent tools
# MAGIC The `COMMENT` of a function and of each parameter is the tool description the model reads. Both functions read the curated views,
# MAGIC so the row filter and the masks of `02` apply to them too: a regional manager's agent cannot look up an order outside her region.

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {TOOLS} COMMENT 'Read-only functions exposed to AI agents (managed MCP server: /api/2.0/mcp/functions/{CATALOG}/agent_tools). Nothing else lives here.'")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {TOOLS}.team_season_summary(
  team STRING COMMENT 'Team full name, nickname or code, e.g. Los Angeles Lakers, Lakers or LAL')
RETURNS TABLE (team_name STRING, home_games BIGINT, tickets_sold BIGINT, net_revenue_usd DECIMAL(18,2), gmv_usd DECIMAL(18,2),
               avg_ticket_price_usd DECIMAL(10,2), sell_through_pct DOUBLE, no_show_pct DOUBLE)
COMMENT 'Season 2025-26 scorecard for the HOME games of one team, all game types: tickets sold (COMPLETED, not complimentary), HoopsPass net revenue (fees + commissions), GMV, average ticket price of sold primary tickets, sell-through (primary sold / allocated seats, in percent) and no-show rate (unscanned among sold, in percent). Use for "how did <team> do this season" questions.'
RETURN
  WITH tm AS (
    SELECT team_id, team_name FROM {CATALOG}.sales.teams
    WHERE lower(team_season_summary.team) IN (lower(team_name), lower(nickname), lower(team_code))),
  g AS (
    SELECT home_team_id, COUNT(*) AS home_games, SUM(primary_tickets_sold) AS primary_sold, SUM(allocated_seats) AS allocated
    FROM {CATALOG}.sales.v_game_sales WHERE home_team_id IN (SELECT team_id FROM tm) GROUP BY home_team_id),
  t AS (
    SELECT home_team_id,
           SUM(CASE WHEN is_sold THEN 1 ELSE 0 END) AS sold,
           SUM(CASE WHEN is_sold THEN service_fee_usd + commission_usd ELSE 0 END) AS net,
           SUM(CASE WHEN is_sold THEN sold_price_usd + service_fee_usd ELSE 0 END) AS gmv,
           AVG(CASE WHEN is_sold AND ticket_type_code <> 'RSL' THEN sold_price_usd END) AS atp,
           SUM(CASE WHEN is_no_show THEN 1 ELSE 0 END) AS no_shows
    FROM {CATALOG}.sales.v_ticket_sales WHERE home_team_id IN (SELECT team_id FROM tm) GROUP BY home_team_id)
  SELECT tm.team_name, g.home_games, t.sold,
         CAST(t.net AS DECIMAL(18,2)), CAST(t.gmv AS DECIMAL(18,2)), CAST(t.atp AS DECIMAL(10,2)),
         ROUND(100.0 * g.primary_sold / g.allocated, 1), ROUND(100.0 * t.no_shows / t.sold, 1)
  FROM tm JOIN g ON g.home_team_id = tm.team_id JOIN t ON t.home_team_id = tm.team_id
""")

spark.sql(f"""
CREATE OR REPLACE FUNCTION {TOOLS}.order_lookup(
  order_id BIGINT COMMENT 'HoopsPass order number, e.g. 5001261')
RETURNS TABLE (order_id BIGINT, customer_id BIGINT, order_type STRING, order_status STRING, order_ts TIMESTAMP,
               sales_channel STRING, promo_code STRING, tickets BIGINT, ticket_types STRING,
               ticket_price_usd DECIMAL(18,2), service_fees_usd DECIMAL(18,2),
               first_game_date DATE, last_game_date DATE, home_team STRING, game_types STRING,
               days_purchase_to_first_game INT)
COMMENT 'Facts about one order, without personal data: type (SINGLE, GROUP, SEASON, RESALE), status (COMPLETED, REFUNDED, CANCELLED, CHARGEBACK), purchase time, channel, number and types of tickets, amounts paid, the game(s) and home team, and the days between purchase and the first game. Combine with the refund and resale policies to answer "can this order be refunded" questions. Returns no rows if the order does not exist or is outside the user''s region.'
RETURN
  SELECT v.order_id, MIN(v.customer_id), MIN(v.order_type), MIN(v.order_status), MIN(v.order_ts), MIN(v.channel_label), MIN(v.promo_code),
         COUNT(*), concat_ws(', ', array_sort(collect_set(v.ticket_type_label))),
         CAST(SUM(v.sold_price_usd) AS DECIMAL(18,2)), CAST(SUM(v.service_fee_usd) AS DECIMAL(18,2)),
         MIN(v.game_date), MAX(v.game_date), MIN(v.home_team_name), concat_ws(', ', array_sort(collect_set(v.game_type))),
         datediff(MIN(v.game_date), CAST(MIN(v.order_ts) AS DATE))
  FROM {CATALOG}.sales.v_ticket_sales v
  WHERE v.order_id = order_lookup.order_id
  GROUP BY v.order_id
""")

display(spark.sql(f"SELECT * FROM {TOOLS}.team_season_summary('Lakers')"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Demo orders for the refund questions
# MAGIC Three real order numbers for the "can this order be refunded" questions in demo C. Paste them into `docs/demo_script.md` if they differ.

# COMMAND ----------

demo_orders = spark.sql(f"""
SELECT 'single game, bought >= 7 days ahead' AS case, MIN(order_id) AS order_id FROM {CATALOG}.sales.v_ticket_sales
 WHERE order_type = 'SINGLE' AND order_status = 'COMPLETED' AND home_team_nickname = 'Celtics' AND datediff(game_date, CAST(order_ts AS DATE)) BETWEEN 20 AND 40
UNION ALL
SELECT 'resale purchase', MIN(order_id) FROM {CATALOG}.sales.v_ticket_sales
 WHERE order_type = 'RESALE' AND order_status = 'COMPLETED' AND home_team_nickname = 'Celtics'
UNION ALL
SELECT 'season package', MIN(order_id) FROM {CATALOG}.sales.v_ticket_sales
 WHERE order_type = 'SEASON' AND order_status = 'COMPLETED' AND home_team_nickname = 'Celtics'
""")
display(demo_orders)
for r in demo_orders.collect():
    display(spark.sql(f"SELECT * FROM {TOOLS}.order_lookup({r.order_id})"))
