"""
Database URL
============
"""

from app.settings import get_settings


def build_db_url() -> str:
    """Build database URL from environment variables."""
    return get_settings().database_url


db_url = build_db_url()
