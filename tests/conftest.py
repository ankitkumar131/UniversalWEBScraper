"""Shared test fixtures: isolated settings + temp database per test session."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session", autouse=True)
def isolated_env(tmp_path_factory):
    """Point data dir + DB at a temp dir before any settings load."""
    tmp = tmp_path_factory.mktemp("uws")
    os.environ["UWS_DATA_DIR"] = str(tmp)
    monkey = pytest.MonkeyPatch()
    monkey.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{(tmp / 'test.db').as_posix()}")
    monkey.setenv("RESPECT_ROBOTS", "true")
    monkey.setenv("CAPTCHA_PROVIDER", "none")
    monkey.setenv("LLM_PROVIDER", "none")
    monkey.setenv("PROXY_ENABLED", "false")
    yield tmp
    monkey.undo()


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path
