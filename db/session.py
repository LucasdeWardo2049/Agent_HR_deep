"""PostgreSQL session storage for AgentOS."""

from functools import cache

from agno.db.postgres import PostgresDb

from db.url import db_url

DB_ID = "agentos-db"


@cache
def get_postgres_db() -> PostgresDb:
    """Return one shared AgentOS database object."""
    return PostgresDb(id=DB_ID, db_url=db_url)
