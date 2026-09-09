"""Behaviour of capability-based processor routing."""

import pytest

from core.contracts import CapturePayloadType, ContentObject
from core.processing import (
    AmbiguousProcessorError,
    NoProcessorError,
    ProcessingError,
    Processor,
    ProcessorRouter,
    ProcessorRoutingError,
    TextProcessor,
)
from tests.unit.contracts.builders import make_content_object
from tests.unit.processing.builders import make_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore, StubProcessor


@pytest.fixture
def content() -> ContentObject:
    """Whatever a stub processor hands back; the router only passes it through."""
    return make_content_object()


def test_router_accepts_any_processor_shaped_object() -> None:
    """Structural conformance, checked by the type checker as well as at runtime."""
    processors: list[Processor] = [
        TextProcessor(InMemoryRawObjectStore()),
        StubProcessor("stub", supported=False),
    ]
    assert isinstance(ProcessorRouter(processors), ProcessorRouter)


def test_the_single_matching_processor_is_selected() -> None:
    text = TextProcessor(InMemoryRawObjectStore())
    router = ProcessorRouter([text, StubProcessor("image", supported=False)])

    assert router.select(make_capture()) is text


def test_no_matching_processor_is_an_explicit_error() -> None:
    router = ProcessorRouter([TextProcessor(InMemoryRawObjectStore())])

    with pytest.raises(NoProcessorError, match="no processor handles"):
        router.select(make_capture(payload_type=CapturePayloadType.VIDEO))


def test_an_empty_router_matches_nothing() -> None:
    with pytest.raises(NoProcessorError):
        ProcessorRouter([]).select(make_capture())


def test_two_matching_processors_are_an_explicit_error() -> None:
    router = ProcessorRouter(
        [StubProcessor("text", supported=True), StubProcessor("also-text", supported=True)]
    )

    with pytest.raises(AmbiguousProcessorError) as raised:
        router.select(make_capture())
    assert "text@0.1" in str(raised.value)
    assert "also-text@0.1" in str(raised.value)


def test_ambiguity_is_not_resolved_by_registration_order() -> None:
    """The first claimant does not win, and nothing runs."""
    first = StubProcessor("first", supported=True)
    second = StubProcessor("second", supported=True)
    router = ProcessorRouter([first, second])

    with pytest.raises(AmbiguousProcessorError):
        router.process(make_capture())

    assert first.process_calls == []
    assert second.process_calls == []


def test_every_processor_is_asked_even_after_a_match() -> None:
    """Short-circuiting on the first match is what would hide an overlap."""
    matching = StubProcessor("matching", supported=True)
    trailing = StubProcessor("trailing", supported=False)
    router = ProcessorRouter([matching, trailing])
    capture = make_capture()

    router.select(capture)

    assert matching.supports_calls == [capture.id]
    assert trailing.supports_calls == [capture.id]


def test_selection_is_independent_of_registration_order(content: ContentObject) -> None:
    wanted = StubProcessor("wanted", supported=True, content=content)
    other = StubProcessor("other", supported=False)

    assert ProcessorRouter([wanted, other]).select(make_capture()) is wanted
    assert ProcessorRouter([other, wanted]).select(make_capture()) is wanted


def test_process_delegates_to_the_selected_processor(content: ContentObject) -> None:
    chosen = StubProcessor("chosen", supported=True, content=content)
    router = ProcessorRouter([chosen, StubProcessor("skipped", supported=False)])
    capture = make_capture()

    assert router.process(capture) is content
    assert chosen.process_calls == [capture.id]


def test_process_reports_routing_failures_rather_than_running_anything() -> None:
    router = ProcessorRouter([StubProcessor("nothing", supported=False)])

    with pytest.raises(NoProcessorError):
        router.process(make_capture())


@pytest.mark.parametrize("error", [NoProcessorError, AmbiguousProcessorError])
def test_routing_errors_are_catchable_as_processing_errors(error: type[Exception]) -> None:
    assert issubclass(error, ProcessorRoutingError)
    assert issubclass(error, ProcessingError)
