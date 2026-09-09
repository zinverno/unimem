"""Fixtures shared by the processing tests."""

import pytest

from core.processing import TextProcessor
from tests.unit.processing.doubles import InMemoryRawObjectStore


@pytest.fixture
def store() -> InMemoryRawObjectStore:
    """An empty raw object store that is not the local backend."""
    return InMemoryRawObjectStore()


@pytest.fixture
def processor(store: InMemoryRawObjectStore) -> TextProcessor:
    """A text processor reading through the in-memory store."""
    return TextProcessor(store)
