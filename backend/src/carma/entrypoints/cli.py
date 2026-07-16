"""Console scripts: carma-migrate and carma-load-gtfs."""

import argparse
import os
from pathlib import Path

import psycopg

from carma.adapters.gtfs_static import load_gtfs_zip
from carma.adapters.migrations import apply_migrations


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    return url


def migrate() -> None:
    parser = argparse.ArgumentParser(
        prog="carma-migrate",
        description="Apply pending SQL migrations to the database in DATABASE_URL.",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=Path(os.environ.get("CARMA_MIGRATIONS_DIR", "migrations")),
        help="directory with numbered *.sql files (default: $CARMA_MIGRATIONS_DIR or ./migrations)",
    )
    args = parser.parse_args()
    with psycopg.connect(_database_url()) as conn:
        applied = apply_migrations(conn, args.migrations_dir)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("schema up to date, nothing to apply")


def load_gtfs() -> None:
    parser = argparse.ArgumentParser(
        prog="carma-load-gtfs",
        description="Full reload of the static GTFS tables from a feed zip.",
    )
    parser.add_argument("zip_path", type=Path, help="path to a static GTFS zip")
    args = parser.parse_args()
    if not args.zip_path.is_file():
        raise SystemExit(f"no such file: {args.zip_path}")
    with psycopg.connect(_database_url()) as conn:
        report = load_gtfs_zip(conn, args.zip_path)
    for table, count in report.rows_loaded.items():
        print(f"{table}: {count} rows")
