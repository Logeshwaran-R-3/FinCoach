"""CLI entry point: `python -m fincoach --check` smoke-tests the DB."""

import argparse
import sys
import tempfile
from pathlib import Path

from fincoach.storage import db


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fincoach")
    parser.add_argument(
        "--check",
        action="store_true",
        help="migrate a temp DB and report table counts",
    )
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.migrate(Path(tmp) / "check.db")
        try:
            counts = db.table_counts(conn)
        finally:
            conn.close()
    for name in db.TABLES:
        print(f"{name}: {counts[name]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
