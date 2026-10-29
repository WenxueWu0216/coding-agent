"""Run the analytics queries in queries/ and print the results.

  python queries.py                         run every query
  python queries.py slowest_tools freshness run some
  python queries.py compare_runs --param a=RUN_A --param b=RUN_B
"""
import argparse
import sys
from pathlib import Path
from typing import Any

import loader

QUERIES_DIR = Path(__file__).resolve().parent / "queries"


def load_sql(name: str, db: str) -> str:
    return (QUERIES_DIR / f"{name}.sql").read_text().replace("{db}", db)


def run(client, name: str, db: str, params: dict[str, str]) -> tuple[list[str], list[tuple]]:
    r = client.query(load_sql(name, db), parameters=params or None)
    return list(r.column_names), r.result_rows


def table(cols: list[str], rows: list[tuple]) -> str:
    def fmt(v: Any) -> str:
        if isinstance(v, list):
            return ",".join(map(str, v))
        return "" if v is None else str(v)

    cells = [cols] + [[fmt(v) for v in r] for r in rows]
    widths = [max(len(c[i]) for c in cells) for i in range(len(cols))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(row, widths)).rstrip() for row in cells]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("names", nargs="*", help="query names (file names in queries/ without .sql)")
    ap.add_argument("--db", default=loader.DEFAULT_DB)
    ap.add_argument("--url", default=loader.DEFAULT_URL)
    ap.add_argument("--param", action="append", default=[], help="name=value for parameterized queries")
    args = ap.parse_args()

    params = dict(p.split("=", 1) for p in args.param)
    names = args.names or sorted(p.stem for p in QUERIES_DIR.glob("*.sql"))
    client = loader.connect(args.url)
    for name in names:
        if name == "compare_runs" and not {"a", "b"} <= params.keys():
            print(f"== {name}: skipped (needs --param a=RUN --param b=RUN)\n")
            continue
        cols, rows = run(client, name, args.db, params)
        print(f"== {name} ({len(rows)} rows)\n{table(cols, rows)}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
