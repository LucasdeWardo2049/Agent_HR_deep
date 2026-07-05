"""
.env Loader
===========

Hydrates `os.environ` from `<repo>/.env` without pulling in `python-dotenv`.
Call `load_dotenv()` before importing modules that read environment variables at import time.
Pre-existing values in the shell take precedence — the file only fills in what's missing.
"""

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".env"


def load_dotenv(path: Path = ENV_FILE) -> None:
    """Load `path` into `os.environ`. Existing values are not overwritten."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if value[:1] in ('"', "'"):
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing != -1 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
