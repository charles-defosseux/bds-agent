import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from backend.reset import reset

BASE_SEED = Path(__file__).resolve().parent.parent / "seeds" / "base.json"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh database from seeds/base.json; the tools read BDS_DB_PATH at every call."""
    path = tmp_path / "bds.db"
    reset(BASE_SEED, path)
    monkeypatch.setenv("BDS_DB_PATH", str(path))
    return path


def error_of(exc_info: pytest.ExceptionInfo) -> dict:
    """The typed error (code, message, hint, retryable) carried by a ToolError."""
    assert isinstance(exc_info.value, ToolError)
    return json.loads(str(exc_info.value))
