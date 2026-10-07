"""Render docs/data_dictionary.md and docs/genie_agent_config.md from semantic_layer.py.

    python render_docs.py --data ./out_full --docs ../docs
"""
from __future__ import annotations

import argparse
import glob
import os

import semantic_layer as sem


def row_counts(data: str | None) -> dict[str, int]:
    if not data:
        return {}
    import duckdb
    con = duckdb.connect()
    out = {}
    for t in ["teams", "arenas", "games", "customers", "orders", "tickets", "ref_codes"]:
        files = sorted(glob.glob(os.path.join(data, t, "part-*.parquet")))
        if files:
            out[t] = con.execute(f"SELECT COUNT(*) FROM read_parquet({files!r})").fetchone()[0]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=None, help="generated parquet directory (for row counts)")
    ap.add_argument("--docs", required=True)
    args = ap.parse_args()
    os.makedirs(args.docs, exist_ok=True)
    with open(os.path.join(args.docs, "data_dictionary.md"), "w", encoding="utf-8") as f:
        f.write(sem.render_data_dictionary(row_counts(args.data)))
    with open(os.path.join(args.docs, "genie_agent_config.md"), "w", encoding="utf-8") as f:
        f.write(sem.render_genie_config())
    print("rendered data_dictionary.md and genie_agent_config.md")


if __name__ == "__main__":
    main()
