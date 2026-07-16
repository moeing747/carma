from pathlib import Path
from typing import Any

import psycopg

# Arbitrary app-wide key serializing concurrent migration runners.
_ADVISORY_LOCK_KEY = 0x_CA12_A001


def apply_migrations(conn: psycopg.Connection[Any], directory: Path) -> list[str]:
    """Apply pending ``*.sql`` files in filename order; returns those applied.

    Idempotent: applied filenames are recorded in ``schema_migrations`` and
    skipped on later runs. Each file runs in its own transaction, so a failing
    migration leaves earlier ones applied and itself fully rolled back.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"migrations directory not found: {directory}")
    with conn.transaction():
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " filename TEXT PRIMARY KEY,"
            " applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
    applied: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        with conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_ADVISORY_LOCK_KEY,))
            already = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE filename = %s", (path.name,)
            ).fetchone()
            if already is not None:
                continue
            # bytes, not str: psycopg types str queries as LiteralString.
            conn.execute(path.read_bytes())
            conn.execute("INSERT INTO schema_migrations (filename) VALUES (%s)", (path.name,))
            applied.append(path.name)
    return applied
