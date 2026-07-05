"""Initialize database schema.

Creates the pgvector extension (if missing) then all ORM tables.
Uses a sync psycopg3 connection so it can run before async stack starts.

Usage (after .env configured & Postgres running):
    python -m app.db.init_schema
"""
from __future__ import annotations

import sys

import psycopg
from sqlalchemy import create_engine

from app.core.config import settings
from app.models import Base


def ensure_extension(dsn: str) -> None:
    # Connect to maintenance db and ensure pgvector is installed.
    # psycopg3 needs a plain postgresql:// dsn (no sqlalchemy dialect prefix).
    plain = dsn.replace("+psycopg", "").replace("+asyncpg", "")
    conn = psycopg.connect(plain, autocommit=True)
    try:
        with conn.cursor() as cur:
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                print("[init] pgvector extension ready")
            except psycopg.errors.PermissionDenied:
                print(
                    "[init] ERROR: current user lacks SUPERUSER/CREATEDB to "
                    "CREATE EXTENSION. Run as postgres user once:\n"
                    "    sudo -u postgres psql -d "
                    f"{settings.db_url.rsplit('/', 1)[-1]} -c "
                    "'CREATE EXTENSION IF NOT EXISTS vector;'"
                )
                raise
    finally:
        conn.close()


def main() -> int:
    # asyncpg URL -> sync sqlalchemy dialect for DDL
    sync_url = settings.db_url.replace("+asyncpg", "+psycopg")
    db_name = sync_url.rsplit("/", 1)[-1]
    print(f"[init] initializing schema on db={db_name}")

    try:
        ensure_extension(settings.db_url)
    except Exception as e:
        print(f"[init] ensure_extension failed: {e}")
        # extension may already exist (installed by superuser); try to proceed
        # because CREATE EXTENSION IF NOT EXISTS still requires privs the app
        # user lacks. Verify presence instead of creating.
        try:
            plain = settings.db_url.replace("+psycopg","").replace("+asyncpg","")
            conn = psycopg.connect(plain, autocommit=True)
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
                if cur.fetchone() is None:
                    print("[init] extension vector NOT installed; abort")
                    conn.close()
                    return 1
            conn.close()
            print("[init] extension vector already installed (by superuser)")
        except Exception as e2:
            print(f"[init] verify extension failed: {e2}")
            return 1

    engine = create_engine(sync_url, echo=False, future=True)
    Base.metadata.create_all(engine)
    print("[init] all tables created")
    return 0


if __name__ == "__main__":
    sys.exit(main())