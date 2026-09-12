"""A DOCX document through the real processing layer, end to end below HTTP.

Real ``CaptureIntake``, real ``ProcessorRouter`` holding all four real
processors, real ``ProcessingOrchestrator``, real ``LocalRawObjectStore`` on a
real directory, real SQLite. Nothing is stubbed.

Three things are asked here that only the real stack can answer. **Does a second
document format run on the lifecycle that already existed** — ``stored`` to
``processing`` to ``complete``, with the canonical object durable before the
capture is allowed to claim completion, and no state added. **Does a DOCX whose
body says nothing fail truthfully**, leaving a ``failed`` capture, no content
object, and the exact original bytes still on disk. And **can one stack hold both
document processors at once** without either capture becoming ambiguous — which
is the question Phase 3 PR 1 deferred and this PR is here to answer.
"""

import io
from pathlib import Path

import pytest

from core.contracts import (
    CaptureContext,
    CaptureEnvelope,
    CapturePayload,
    CapturePayloadType,
    CaptureRecord,
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
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    DocxProcessor,
    PdfProcessor,
    ProcessingInputError,
    ProcessingOrchestrator,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
from core.storage import LocalRawObjectStore
from tests import docxs, pdfs
from tests.unit.processing.builders import CAPTURED_AT

CAPTURE_ID = "cap_docx_int_01"


class Stack:
    """The real processing stack over one temporary directory."""

    def __init__(self, data_dir: Path) -> None:
        database = data_dir / "unimem.sqlite3"
        self.raw_store = LocalRawObjectStore(data_dir / "raw")
        self.record_store = SqliteCaptureRecordStore(database)
        self.content_store = SqliteContentObjectStore(database)
        self.intake = CaptureIntake(self.raw_store, self.record_store)
        #: All four real processors, in the order the composition root uses.
        self.router = ProcessorRouter(
            [
                TextProcessor(self.raw_store),
                WebpageProcessor(self.raw_store),
                PdfProcessor(self.raw_store),
                DocxProcessor(self.raw_store),
            ]
        )
        self.orchestrator = ProcessingOrchestrator(
            self.router, self.record_store, self.content_store
        )

    def stage(self, data: bytes, *, mime_type: str = DOCX_MIME_TYPE) -> str:
        """Put bytes in the raw store the way an upload would, and return the ref.

        This is the *acquisition* half of the flow, done here directly because
        the HTTP surface is not what this file is testing. What it produces is
        the same logical reference the upload route hands a client — and it is
        the same call for either document format, which is the point.
        """
        raw_object = self.raw_store.store_stream(io.BytesIO(data), mime_type=mime_type)
        assert raw_object.ref is not None
        return raw_object.ref


@pytest.fixture
def stack(tmp_path: Path) -> Stack:
    return Stack(tmp_path)


def envelope(file_ref: str, **overrides: object) -> CaptureEnvelope:
    fields: dict[str, object] = {
        "id": CAPTURE_ID,
        "source": CaptureSource(
            type=CaptureSourceType.UPLOAD,
            provider="curl",
            url="https://example.com/papers/one.docx",
        ),
        "payload": CapturePayload(
            type=CapturePayloadType.DOCUMENT,
            mime_type=DOCX_MIME_TYPE,
            file_ref=file_ref,
        ),
        "context": CaptureContext(captured_at=CAPTURED_AT, device="laptop"),
    }
    return CaptureEnvelope(**(fields | overrides))  # type: ignore[arg-type]


class TestTheDocumentLifecycle:
    def test_intake_leaves_it_stored(self, stack: Stack) -> None:
        record = stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))

        assert record.status is CaptureStatus.STORED

    def test_processing_leaves_it_complete(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))

        stack.orchestrator.process(CAPTURE_ID)

        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.COMPLETE

    def test_no_lifecycle_state_was_added(self, stack: Stack) -> None:
        """A second document format uses the states that already existed and no others."""
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        record = stack.record_store.get(CAPTURE_ID)

        assert record.status in set(CaptureStatus)
        assert record.status is CaptureStatus.COMPLETE
        assert record.error is None

    def test_the_canonical_object_is_durable(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))

        returned = stack.orchestrator.process(CAPTURE_ID)

        assert stack.content_store.get_for_capture(CAPTURE_ID).id == returned.id

    def test_the_content_is_durable_before_the_capture_says_complete(self, stack: Stack) -> None:
        """The Phase 0I ordering, re-checked for the new format.

        The capture-record store is made to fail on the write that would mark it
        complete. The content must already be there, and the capture must stay
        ``processing`` rather than claim a state it did not reach.
        """
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        original_replace = stack.record_store.replace
        calls: list[CaptureStatus] = []

        def replace_then_fail(record: CaptureRecord) -> None:
            calls.append(record.status)
            if record.status is CaptureStatus.COMPLETE:
                raise RuntimeError("the database went away")
            original_replace(record)

        stack.record_store.replace = replace_then_fail  # type: ignore[method-assign]
        with pytest.raises(RuntimeError):
            stack.orchestrator.process(CAPTURE_ID)
        stack.record_store.replace = original_replace  # type: ignore[method-assign]

        assert calls == [CaptureStatus.PROCESSING, CaptureStatus.COMPLETE]
        assert stack.content_store.get_for_capture(CAPTURE_ID) is not None
        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.PROCESSING

    def test_the_durable_object_is_a_document(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        assert stack.content_store.get_for_capture(CAPTURE_ID).type is ContentType.DOCUMENT

    def test_the_body_survives_the_round_trip_through_sqlite(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        returned = stack.orchestrator.process(CAPTURE_ID)

        durable = stack.content_store.get_for_capture(CAPTURE_ID)

        assert durable.model_dump_json() == returned.model_dump_json()

    def test_the_body_order_survives(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        segments = stack.content_store.get_for_capture(CAPTURE_ID).segments

        assert [segment.text for segment in segments] == [
            docxs.PARAGRAPH_A,
            docxs.TABLE_ROW_ONE,
            docxs.TABLE_ROW_TWO,
            docxs.PARAGRAPH_B,
        ]
        assert [segment.position for segment in segments] == [0, 1, 2, 3]

    def test_the_block_metadata_survives(self, stack: Stack) -> None:
        """A free-form mapping through SQLite, with its integers still integers."""
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        segments = stack.content_store.get_for_capture(CAPTURE_ID).segments

        assert segments[1].metadata == {
            "docx_block": "table_row",
            "table_index": 0,
            "row_index": 0,
        }
        assert segments[3].metadata == {"docx_block": "paragraph"}

    def test_no_durable_segment_carries_a_page_number(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        segments = stack.content_store.get_for_capture(CAPTURE_ID).segments

        assert all(segment.spatial is None for segment in segments)

    def test_the_segments_are_provenanced_to_the_original(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        content = stack.content_store.get_for_capture(CAPTURE_ID)

        assert {segment.provenance.source_type for segment in content.segments} == {
            ProvenanceSourceType.ORIGINAL
        }

    def test_the_exact_docx_is_still_in_raw_storage(self, stack: Stack) -> None:
        data = docxs.paragraph_table_paragraph_docx()
        stack.intake.accept(envelope(stack.stage(data)))
        stack.orchestrator.process(CAPTURE_ID)

        record = stack.record_store.get(CAPTURE_ID)

        assert record.raw_object is not None
        assert stack.raw_store.read_bytes(record.raw_object) == data

    def test_intake_wrote_no_second_copy_of_the_document(
        self, stack: Stack, tmp_path: Path
    ) -> None:
        """One immutable object on disk: the one that was staged."""
        stack.intake.accept(envelope(stack.stage(docxs.paragraph_table_paragraph_docx())))
        stack.orchestrator.process(CAPTURE_ID)

        objects = [path for path in (tmp_path / "raw").rglob("*") if path.is_file()]

        assert len(objects) == 1


class TestARefusedDocument:
    """A textless or unreadable DOCX follows the failure rules that already existed."""

    def test_an_image_only_docx_raises_a_processing_error(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.image_only_docx())))

        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process(CAPTURE_ID)

    def test_the_capture_is_durably_failed(self, stack: Stack) -> None:
        """A verdict about the capture, so a terminal state is honest here."""
        stack.intake.accept(envelope(stack.stage(docxs.image_only_docx())))
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process(CAPTURE_ID)

        record = stack.record_store.get(CAPTURE_ID)

        assert record.status is CaptureStatus.FAILED
        assert record.error is not None

    def test_no_content_object_is_stored(self, stack: Stack) -> None:
        stack.intake.accept(envelope(stack.stage(docxs.image_only_docx())))
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process(CAPTURE_ID)

        with pytest.raises(ContentObjectNotFoundError):
            stack.content_store.get_for_capture(CAPTURE_ID)

    def test_the_original_outlives_the_failure(self, stack: Stack) -> None:
        """Nothing is rolled back, so a richer build can read these bytes."""
        data = docxs.image_only_docx()
        stack.intake.accept(envelope(stack.stage(data)))
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process(CAPTURE_ID)

        record = stack.record_store.get(CAPTURE_ID)

        assert record.raw_object is not None
        assert stack.raw_store.read_bytes(record.raw_object) == data

    @pytest.mark.parametrize(
        "data",
        [
            docxs.corrupt_docx(),
            docxs.truncated_body_docx(),
            docxs.encrypted_docx(),
            docxs.textless_docx(),
            docxs.header_only_docx(),
        ],
        ids=["corrupt", "broken-xml", "encrypted", "empty-body", "header-only"],
    )
    def test_other_unreadable_documents_fail_the_same_way(self, stack: Stack, data: bytes) -> None:
        stack.intake.accept(envelope(stack.stage(data)))
        with pytest.raises(ProcessingInputError):
            stack.orchestrator.process(CAPTURE_ID)

        assert stack.record_store.get(CAPTURE_ID).status is CaptureStatus.FAILED


class TestTwoCapturesOfOneDocument:
    def test_each_gets_its_own_content_object(self, stack: Stack) -> None:
        file_ref = stack.stage(docxs.paragraph_table_paragraph_docx())
        stack.intake.accept(envelope(file_ref, id="cap_docx_a"))
        stack.intake.accept(envelope(file_ref, id="cap_docx_b"))

        first = stack.orchestrator.process("cap_docx_a")
        second = stack.orchestrator.process("cap_docx_b")

        assert first.id != second.id
        assert first.assets[0].id != second.assets[0].id
        assert [segment.id for segment in first.segments] != [
            segment.id for segment in second.segments
        ]

    def test_but_one_raw_object_on_disk(self, stack: Stack, tmp_path: Path) -> None:
        file_ref = stack.stage(docxs.paragraph_table_paragraph_docx())
        stack.intake.accept(envelope(file_ref, id="cap_docx_a"))
        stack.intake.accept(envelope(file_ref, id="cap_docx_b"))

        first = stack.orchestrator.process("cap_docx_a")
        second = stack.orchestrator.process("cap_docx_b")

        assert first.original.sha256 == second.original.sha256
        objects = [path for path in (tmp_path / "raw").rglob("*") if path.is_file()]
        assert len(objects) == 1


class TestBothDocumentFormatsInOneStack:
    """The question Phase 3 PR 1 deferred, asked against the real stack."""

    def test_one_stack_serves_a_pdf_and_a_docx(self, stack: Stack) -> None:
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_mix_pdf",
                payload=CapturePayload(
                    type=CapturePayloadType.DOCUMENT,
                    mime_type=PDF_MIME_TYPE,
                    file_ref=stack.stage(pdfs.two_page_pdf(), mime_type=PDF_MIME_TYPE),
                ),
            )
        )
        stack.intake.accept(
            envelope(stack.stage(docxs.paragraph_table_paragraph_docx()), id="cap_mix_docx")
        )

        kinds = {
            capture_id: stack.orchestrator.process(capture_id).type
            for capture_id in ("cap_mix_pdf", "cap_mix_docx")
        }

        assert kinds == {
            "cap_mix_pdf": ContentType.DOCUMENT,
            "cap_mix_docx": ContentType.DOCUMENT,
        }

    def test_the_pdf_keeps_its_page_numbers_and_the_docx_has_none(self, stack: Stack) -> None:
        """One canonical type, two honest answers about physical location."""
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_mix_pdf",
                payload=CapturePayload(
                    type=CapturePayloadType.DOCUMENT,
                    mime_type=PDF_MIME_TYPE,
                    file_ref=stack.stage(pdfs.two_page_pdf(), mime_type=PDF_MIME_TYPE),
                ),
            )
        )
        stack.intake.accept(
            envelope(stack.stage(docxs.paragraph_table_paragraph_docx()), id="cap_mix_docx")
        )

        pdf = stack.orchestrator.process("cap_mix_pdf")
        docx = stack.orchestrator.process("cap_mix_docx")

        assert [segment.spatial.page for segment in pdf.segments] == [1, 2]  # type: ignore[union-attr]
        assert all(segment.spatial is None for segment in docx.segments)

    def test_each_names_the_processor_that_read_it(self, stack: Stack) -> None:
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_mix_pdf",
                payload=CapturePayload(
                    type=CapturePayloadType.DOCUMENT,
                    mime_type=PDF_MIME_TYPE,
                    file_ref=stack.stage(pdfs.two_page_pdf(), mime_type=PDF_MIME_TYPE),
                ),
            )
        )
        stack.intake.accept(
            envelope(stack.stage(docxs.paragraph_table_paragraph_docx()), id="cap_mix_docx")
        )

        pdf = stack.orchestrator.process("cap_mix_pdf")
        docx = stack.orchestrator.process("cap_mix_docx")

        assert pdf.processing[0].processor == "pdf"
        assert docx.processing[0].processor == "docx"

    def test_all_four_modalities_together(self, stack: Stack) -> None:
        """Four payload shapes, four processors, no ambiguity anywhere."""
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_all_pdf",
                payload=CapturePayload(
                    type=CapturePayloadType.DOCUMENT,
                    mime_type=PDF_MIME_TYPE,
                    file_ref=stack.stage(pdfs.two_page_pdf(), mime_type=PDF_MIME_TYPE),
                ),
            )
        )
        stack.intake.accept(
            envelope(stack.stage(docxs.paragraph_table_paragraph_docx()), id="cap_all_docx")
        )
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_all_text",
                payload=CapturePayload(
                    type=CapturePayloadType.TEXT, mime_type="text/plain", text="plain words"
                ),
            )
        )
        stack.intake.accept(
            envelope(
                "unused",
                id="cap_all_web",
                payload=CapturePayload(
                    type=CapturePayloadType.WEBPAGE,
                    mime_type="text/html",
                    html="<html><body><p>a page</p></body></html>",
                ),
            )
        )

        kinds = {
            capture_id: stack.orchestrator.process(capture_id).type
            for capture_id in ("cap_all_pdf", "cap_all_docx", "cap_all_text", "cap_all_web")
        }

        assert kinds == {
            "cap_all_pdf": ContentType.DOCUMENT,
            "cap_all_docx": ContentType.DOCUMENT,
            "cap_all_text": ContentType.TEXT,
            "cap_all_web": ContentType.WEB,
        }
