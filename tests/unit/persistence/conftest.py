"""Fixtures shared by the persistence tests."""

from pathlib import Path

import pytest

from core.contracts import CaptureRecord
from core.persistence import SqliteCaptureRecordStore
from tests.unit.persistence.builders import make_record


@pytest.fixture
def database(tmp_path: Path) -> Path:
    """A database file that does not exist yet."""
    return tmp_path / "captures.sqlite3"


@pytest.fixture
def store(database: Path) -> SqliteCaptureRecordStore:
    return SqliteCaptureRecordStore(database)


@pytest.fixture
def record() -> CaptureRecord:
    """A fully populated capture record."""
    return make_record()
