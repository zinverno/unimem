"""Shared fixtures built on :mod:`tests.unit.contracts.builders`."""

from typing import Any

import pytest

from core.contracts import CaptureEnvelope, ContentObject
from tests.unit.contracts.builders import make_content_object, make_envelope


@pytest.fixture
def envelope() -> CaptureEnvelope:
    """A valid webpage capture envelope."""
    return make_envelope()


@pytest.fixture
def envelope_data(envelope: CaptureEnvelope) -> dict[str, Any]:
    """The same envelope as plain JSON-compatible data."""
    return envelope.model_dump(mode="json")


@pytest.fixture
def content_object() -> ContentObject:
    """A valid content object."""
    return make_content_object()
