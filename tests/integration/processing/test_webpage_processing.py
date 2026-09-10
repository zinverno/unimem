"""A webpage capture through the real processing layer, end to end below HTTP.

Real ``CaptureIntake``, real ``ProcessorRouter`` holding both real processors,
real ``ProcessingOrchestrator``, real ``LocalRawObjectStore`` on a real
directory, real SQLite. Nothing is stubbed except the passage of time, which is
not stubbed either.

The question this file answers is the one two processors make askable for the
first time: **with a text processor and a webpage processor registered together,
does each capture reach exactly one of them, and does the webpage lifecycle end
in a durable canonical object?**
"""

from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureSource,
    CaptureSourceType,
    CaptureStatus,
    ContentType,
    ProvenanceSourceType,
)
from core.intake import CaptureIntake
from core.persistence import (
    ContentObjectNotFoundError,
    SqliteCaptureRecordStore,
    SqliteContentObjectStore,
)
from core.processing import (
    AmbiguousProcessorError,
    NoProcessorError,
    ProcessingInputError,
    ProcessingOrchestrator,
    Processor,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from core.storage import LocalRawObjectStore
from tests.unit.processing.builders import CAPTURED_AT

PAGE = (
    "<!doctype html><html><head><title>Example &amp; Test</title>"
    "<style>.x { display: none; }</style></head><body><main>"
    "<h1>Hello</h1><p>First <strong>paragraph</strong>.</p>"
    '<script>window.secret = "not content"</script>'
    "<p>Second paragraph.</p></main></body></html>"
)


class Stack:
    """The real processing stack over one temporary directory."""

    def __init__(self, data_dir: Path) -> None:
        database = data_dir / "unimem.sqlite3"
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(database)
        self.content_store = SqliteContentObjectStore(database)
        self.intake = CaptureIntake(self.raw_store, self.record_store)
        #: Both real processors, in the order the composition root registers them.
        self.router = ProcessorRouter(
            [TextProcessor(self.raw_store), WebpageProcessor(self.raw_store)]
        )
        self.orchestrator = ProcessingOrchestrator(
            self.router, self.record_store, self.content_store
        )


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def envelope(**overrides: object) -> CaptureEnvelope:
    fields: dict[str, object] = {
        "id": "cap_web_int_01",
        "source": CaptureSource(
            type=CaptureSourceType.BROWSER,
            provider="chromium",
            url="https://example.com/article",
        ),
        "payload": CapturePayload(
            type=CapturePayloadType.WEBPAGE, mime_type="text/html", html=PAGE
        ),
        "context": CaptureContext(captured_at=CAPTURED_AT, device="laptop"),
    }
    return CaptureEnvelope(**(fields | overrides))  # type: ignore[arg-type]


def text_envelope(**overrides: object) -> CaptureEnvelope:
    return envelope(
        id="cap_text_int_01",
        payload=CapturePayload(
            type=CapturePayloadType.TEXT, mime_type="text/plain", text="plain words"
        ),
        **overrides,
    )


class TestRoutingWithTwoRealProcessors:
    def test_a_text_capture_selects_the_text_processor(self, stack: Stack) -> None:
        stored = stack.intake.accept(text_envelope())

        assert stack.router.select(stored).name == "text"

    def test_a_webpage_capture_selects_the_webpage_processor(self, stack: Stack) -> None:
        stored = stack.intake.accept(envelope())

        assert stack.router.select(stored).name == "webpage"

    def test_neither_claims_the_other_s_capture(self, stack: Stack) -> None:
        """Exactly-one-match holds because each refuses, not because of ordering."""
        page = stack.intake.accept(envelope())
        text = stack.intake.accept(text_envelope())
        processors: list[Processor] = [
            TextProcessor(stack.raw_store),
            WebpageProcessor(stack.raw_store),
        ]

        assert [p.name for p in processors if p.supports(page)] == ["webpage"]
        assert [p.name for p in processors if p.supports(text)] == ["text"]

    def test_registration_order_does_not_decide(self, stack: Stack) -> None:
        """Reverse the list and both answers are identical: no first-match behaviour."""
        reversed_router = ProcessorRouter(
            [WebpageProcessor(stack.raw_store), TextProcessor(stack.raw_store)]
        )
        page = stack.intake.accept(envelope())
        text = stack.intake.accept(text_envelope())

        assert reversed_router.select(page).name == stack.router.select(page).name
        assert reversed_router.select(text).name == stack.router.select(text).name

    def test_a_duplicated_processor_is_ambiguous_not_first_match(self, stack: Stack) -> None:
        """Proof there is no silent precedence: an overlap is refused outright."""
        router = ProcessorRouter(
            [WebpageProcessor(stack.raw_store), WebpageProcessor(stack.raw_store)]
        )
        page = stack.intake.accept(envelope())

        with pytest.raises(AmbiguousProcessorError):
            router.select(page)

    def test_an_unregistered_payload_type_still_has_no_fallback(self, stack: Stack) -> None:
        """No generic processor was introduced along the way."""
        router = ProcessorRouter([TextProcessor(stack.raw_store)])
        page = stack.intake.accept(envelope())

        with pytest.raises(NoProcessorError):
            router.select(page)


class TestTheWebpageLifecycle:
    def test_intake_leaves_it_stored(self, stack: Stack) -> None:
        stored = stack.intake.accept(envelope())

        assert stored.status is CaptureStatus.STORED
        assert stack.record_store.get("cap_web_int_01").status is CaptureStatus.STORED

    def test_processing_leaves_it_complete(self, stack: Stack) -> None:
        stack.intake.accept(envelope())

        stack.orchestrator.process("cap_web_int_01")

        assert stack.record_store.get("cap_web_int_01").status is CaptureStatus.COMPLETE

    def test_the_canonical_object_is_durable(self, stack: Stack) -> None:
        stack.intake.accept(envelope())

        content = stack.orchestrator.process("cap_web_int_01")

        assert stack.content_store.get_for_capture("cap_web_int_01").id == content.id

    def test_the_durable_object_is_a_web_object(self, stack: Stack) -> None:
        stack.intake.accept(envelope())
        stack.orchestrator.process("cap_web_int_01")

        stored = stack.content_store.get_for_capture("cap_web_int_01")

        assert stored.type is ContentType.WEB
        assert stored.segments[0].provenance.source_type is ProvenanceSourceType.HTML

    def test_the_content_survives_a_round_trip_byte_identically(self, stack: Stack) -> None:
        stack.intake.accept(envelope())
        content = stack.orchestrator.process("cap_web_int_01")

        stored = stack.content_store.get_for_capture("cap_web_int_01")

        assert stored.model_dump_json() == content.model_dump_json()

    def test_the_exact_html_is_still_in_raw_storage(self, stack: Stack) -> None:
        stack.intake.accept(envelope())
        stack.orchestrator.process("cap_web_int_01")

        record = stack.record_store.get("cap_web_int_01")
        assert record.raw_object is not None

        assert stack.raw_store.read_bytes(record.raw_object) == PAGE.encode("utf-8")

    def test_the_segment_is_extracted_text_not_markup(self, stack: Stack) -> None:
        stack.intake.accept(envelope())
        stack.orchestrator.process("cap_web_int_01")

        (segment,) = stack.content_store.get_for_capture("cap_web_int_01").segments

        assert segment.text == "Hello\n\nFirst paragraph.\n\nSecond paragraph."

    def test_no_extra_lifecycle_state_was_introduced(self, stack: Stack) -> None:
        """The webpage path uses the states that already existed and no others."""
        stack.intake.accept(envelope())
        stack.orchestrator.process("cap_web_int_01")
        record = stack.record_store.get("cap_web_int_01")

        assert record.status in set(CaptureStatus)
        assert record.status is CaptureStatus.COMPLETE
        assert record.error is None


class TestAFailingWebpage:
    """A page with nothing to extract follows the existing truthful failure rules."""

    @staticmethod
    def script_only() -> CaptureEnvelope:
        return envelope(
            payload=CapturePayload(
                type=CapturePayloadType.WEBPAGE,
                mime_type="text/html",
                html="<html><body><script>var x = 1;</script></body></html>",
            )
        )

    def test_it_raises_the_processing_error(self, stack: Stack) -> None:
        stack.intake.accept(self.script_only())

        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process("cap_web_int_01")

    def test_the_capture_is_durably_failed(self, stack: Stack) -> None:
        """A verdict about the capture, so a terminal state is honest here."""
        stack.intake.accept(self.script_only())
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process("cap_web_int_01")

        assert stack.record_store.get("cap_web_int_01").status is CaptureStatus.FAILED

    def test_no_content_object_is_stored(self, stack: Stack) -> None:
        stack.intake.accept(self.script_only())
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process("cap_web_int_01")

        with pytest.raises(ContentObjectNotFoundError):
            stack.content_store.get_for_capture("cap_web_int_01")

    def test_the_raw_original_is_still_there(self, stack: Stack) -> None:
        """Nothing is rolled back: the submitted bytes outlive the failed processing."""
        stack.intake.accept(self.script_only())
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process("cap_web_int_01")

        record = stack.record_store.get("cap_web_int_01")
        assert record.raw_object is not None
        assert b"<script>" in stack.raw_store.read_bytes(record.raw_object)


class TestTwoCapturesOfOnePage:
    def test_each_gets_its_own_content_object(self, stack: Stack) -> None:
        stack.intake.accept(envelope(id="cap_web_a"))
        stack.intake.accept(envelope(id="cap_web_b"))

        first = stack.orchestrator.process("cap_web_a")
        second = stack.orchestrator.process("cap_web_b")

        assert first.id != second.id
        assert first.segments[0].id != second.segments[0].id
        assert first.assets[0].id != second.assets[0].id

    def test_but_one_raw_object_on_disk(self, stack: Stack, tmp_path: Path) -> None:
        stack.intake.accept(envelope(id="cap_web_a"))
        stack.intake.accept(envelope(id="cap_web_b"))

        first = stack.orchestrator.process("cap_web_a")
        second = stack.orchestrator.process("cap_web_b")

        assert first.original.sha256 == second.original.sha256
        objects = [path for path in (tmp_path / "raw").rglob("*") if path.is_file()]
        assert len(objects) == 1


class TestBothModalitiesTogether:
    def test_one_stack_serves_a_text_and_a_webpage_capture(self, stack: Stack) -> None:
        stack.intake.accept(text_envelope())
        stack.intake.accept(envelope())

        text_content = stack.orchestrator.process("cap_text_int_01")
        page_content = stack.orchestrator.process("cap_web_int_01")

        assert text_content.type is ContentType.TEXT
        assert page_content.type is ContentType.WEB
        assert text_content.processing[0].processor == "text"
        assert page_content.processing[0].processor == "webpage"

    def test_the_text_path_is_semantically_unchanged(self, stack: Stack) -> None:
        """Adding a processor did not alter what a text capture produces."""
        stack.intake.accept(text_envelope())

        content = stack.orchestrator.process("cap_text_int_01")

        assert content.segments[0].text == "plain words"
        assert content.segments[0].provenance.source_type is ProvenanceSourceType.ORIGINAL
        assert content.processing[0].processor_version == "0.2"
