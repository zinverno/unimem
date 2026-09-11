"""Fixtures for the API tests: a real pipeline over fake backends.

The default stack is the genuine one — ``CaptureIntake``, ``ProcessorRouter``,
``TextProcessor``, ``WebpageProcessor``, ``PdfProcessor``,
``ProcessingOrchestrator`` — with only the three stores replaced. So a test that
posts an envelope really does encode, hash, store, route, decode, extract, parse,
and normalize it; what it does not do is touch a disk.

:func:`build_client` is the seam for the tests that need something else: a
failing store, a refusing processor, an empty router. Everything it takes has a
sensible default, so a test names only what it is actually testing.
"""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.intake import CaptureIntake
from core.processing import (
    PdfProcessor,
    ProcessingOrchestrator,
    Processor,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from tests.unit.api.doubles import (
    FakeCaptureRecordStore,
    FakeContentObjectStore,
    FakeRawObjectStore,
)
from unimem_api import create_app


class Stack:
    """The wired application plus the doubles underneath it.

    A test asserting on durable state reaches through ``record_store`` and
    ``content_store`` rather than through another HTTP call, so "the API left
    this behind" is checked against the store rather than against the API's own
    account of itself.
    """

    def __init__(
        self,
        app: FastAPI,
        client: TestClient,
        raw_store: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        content_store: FakeContentObjectStore,
    ) -> None:
        self.app = app
        self.client = client
        self.raw_store = raw_store
        self.record_store = record_store
        self.content_store = content_store


def build_stack(
    *,
    raw_store: FakeRawObjectStore | None = None,
    record_store: FakeCaptureRecordStore | None = None,
    content_store: FakeContentObjectStore | None = None,
    processors: list[Processor] | None = None,
) -> Stack:
    """Wire an app over the supplied doubles, defaulting everything unspecified."""
    raw_store = raw_store if raw_store is not None else FakeRawObjectStore()
    record_store = record_store if record_store is not None else FakeCaptureRecordStore()
    content_store = content_store if content_store is not None else FakeContentObjectStore()
    processors = (
        processors
        if processors is not None
        # The same three the composition root registers, in the same order — so
        # "the default stack is the genuine one" keeps meaning that.
        else [TextProcessor(raw_store), WebpageProcessor(raw_store), PdfProcessor(raw_store)]
    )

    intake = CaptureIntake(raw_store, record_store)
    orchestrator = ProcessingOrchestrator(ProcessorRouter(processors), record_store, content_store)
    app = create_app(
        intake=intake,
        orchestrator=orchestrator,
        record_store=record_store,
        content_store=content_store,
        raw_store=raw_store,
    )
    return Stack(app, TestClient(app), raw_store, record_store, content_store)


@pytest.fixture
def stack() -> Iterator[Stack]:
    """The default stack: real orchestration, fake storage."""
    built = build_stack()
    with built.client:
        yield built


@pytest.fixture
def client(stack: Stack) -> TestClient:
    return stack.client


def served_paths(app: FastAPI) -> set[str]:
    """The URL paths an app actually serves.

    ``Starlette.routes`` is typed as ``BaseRoute``, which has no ``path`` — only
    the routed subclasses do — so the attribute is read defensively rather than
    asserted into existence.
    """
    return {path for route in app.routes if (path := getattr(route, "path", ""))}
