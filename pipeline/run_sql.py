#!/usr/bin/env python3
"""
Execute the named queries in sql/queries.sql against the DuckDB database and
write the results to data/processed/sql_results.md.

The SQL file is the analytical layer: the Python pipeline prepares the star
schema and the replay model, and these queries produce the published tables.
"""
from __future__ import annotations

import pathlib
import re

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "processed" / "f1.duckdb"
SQL_PATH = ROOT / "sql" / "queries.sql"
OUT_PATH = ROOT / "data" / "processed" / "sql_results.md"


def split_queries(text: str) -> list[tuple[str, str]]:
    blocks = re.split(r"^-- name: ", text, flags=re.MULTILINE)[1:]
    out = []
    for block in blocks:
        name, _, body = block.partition("\n")
        # drop comment lines, keep the statement
        stmt = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("--")
        ).strip().rstrip(";")
        if stmt:
            out.append((name.strip(), stmt))
    return out


def main() -> int:
    if not DB_PATH.exists():
        raise SystemExit("database missing - run pipeline/build_db.py first")

    con = duckdb.connect(str(DB_PATH), read_only=True)
    queries = split_queries(SQL_PATH.read_text(encoding="utf-8"))

    lines = ["# SQL results", "", f"Generated from `{DB_PATH.name}`.", ""]
    for name, stmt in queries:
        df = con.execute(stmt).df()
        lines.append(f"## {name}")
        lines.append("")
        lines.append(df.to_markdown(index=False))
        lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"ran {len(queries)} queries -> {OUT_PATH}")

    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
