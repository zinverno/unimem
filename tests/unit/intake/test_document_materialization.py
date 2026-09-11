"""Document intake: resolving staged material instead of writing it.

This is the first payload type whose material never travelled inside the
envelope, and almost everything worth asserting follows from that one fact:

* the bytes are **not** written again — they were immutable before the capture
  existed, and a second write would be a second copy of something that
  deduplicates to itself;
* the reference is settled **before** any side effect, so an envelope naming
  material nobody staged leaves no ``RECEIVED`` record, no clock read, and
  nothing at all behind;
* only the raw store's own reference format is resolved. A filesystem path, a
  ``file://`` URL, and an HTTP URL are refused, and — the part that matters — are
  never opened, resolved, or fetched.
"""

import hashlib
from pathlib import Path

import pytest

from core.contracts import CaptureEnvelope, CapturePayloadType, CaptureStatus
from core.intake import (
    CaptureIntake,
    CaptureIntakeError,
    CaptureMaterialUnavailableError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.storage import build_raw_ref
from tests.pdfs import two_page_pdf
from tests.unit.intake.builders import (
    PDF_MIME,
    RECEIVED_AT,
    UPDATED_AT,
    make_document_envelope,
    make_document_payload,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore

PDF_BYTES = two_page_pdf()
PDF_DIGEST = hashlib.sha256(PDF_BYTES).hexdigest()
PDF_FILE_REF = build_raw_ref(PDF_DIGEST)


@pytest.fixture
def staged(raw_store: FakeRawObjectStore, journal: list[str]) -> FakeRawObjectStore:
    """A raw store that already holds the PDF, as an upload would have left it.

    The staging write is deliberately erased from the journal and the write log
    the assertions read: it happened before the capture, through a different
    caller, and counting it as one of intake's calls would hide the very thing
    under test.
    """
    raw_store.store_bytes(PDF_BYTES, mime_type=PDF_MIME)
    journal.clear()
    raw_store.writes.clear()
    return raw_store


@pytest.fixture
def envelope() -> CaptureEnvelope:
    return make_document_envelope(payload=make_document_payload(file_ref=PDF_FILE_REF))


class TestAStagedPdfIsAccepted:
    def test_the_capture_is_stored(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        assert intake.accept(envelope).status is CaptureStatus.STORED

    def test_the_payload_type_is_carried_onto_the_record(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        assert intake.accept(envelope).payload_type is CapturePayloadType.DOCUMENT

    def test_no_second_raw_write_happens(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        envelope: CaptureEnvelope,
        journal: list[str],
    ) -> None:
        """The whole point: the bytes were already there, so nothing is written."""
        intake.accept(envelope)

        assert journal == ["records.create", "records.replace"]
        assert staged.writes == []

    def test_the_staged_reference_reaches_the_record_unchanged(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        stored = intake.accept(envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.ref == PDF_FILE_REF

    def test_the_digest_is_preserved(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        stored = intake.accept(envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.sha256 == PDF_DIGEST
        assert stored.raw_object.id == PDF_DIGEST

    def test_the_declared_mime_type_is_carried_onto_the_reference(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        """The submitter's word for *this capture*, not something re-read from the store."""
        stored = intake.accept(envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.mime_type == PDF_MIME

    def test_the_stored_reference_still_resolves_to_the_exact_bytes(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        stored = intake.accept(envelope)

        assert stored.raw_object is not None
        assert staged.read_bytes(stored.raw_object) == PDF_BYTES

    def test_capture_metadata_survives(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        """Same durable metadata rule as every other payload type."""
        stored = intake.accept(envelope)

        assert stored.id == envelope.id
        assert stored.source == envelope.source
        assert stored.context == envelope.context
        assert stored.intent == envelope.intent
        assert stored.title == envelope.payload.title

    def test_the_lifecycle_is_the_same_two_snapshots(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        envelope: CaptureEnvelope,
        record_store: FakeCaptureRecordStore,
        clock: FakeClock,
    ) -> None:
        """``RECEIVED`` then ``STORED``: no state was added for documents."""
        intake.accept(envelope)

        assert [record.status for record in record_store.created] == [CaptureStatus.RECEIVED]
        assert [record.status for record in record_store.replaced] == [CaptureStatus.STORED]
        assert clock.reads == [RECEIVED_AT, UPDATED_AT]

    def test_the_received_snapshot_carries_no_raw_object_yet(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        envelope: CaptureEnvelope,
        record_store: FakeCaptureRecordStore,
    ) -> None:
        intake.accept(envelope)

        assert record_store.created[0].raw_object is None

    def test_the_envelope_is_not_mutated(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        before = envelope.model_dump_json()

        intake.accept(envelope)

        assert envelope.model_dump_json() == before

    def test_two_captures_of_one_staged_pdf_share_the_bytes_and_not_the_identity(
        self,
        staged: FakeRawObjectStore,
        record_store: FakeCaptureRecordStore,
        envelope: CaptureEnvelope,
    ) -> None:
        """One immutable original, two captures — and still not a single write."""
        intake = CaptureIntake(
            staged,
            record_store,
            now=FakeClock(RECEIVED_AT, UPDATED_AT, RECEIVED_AT, UPDATED_AT),
        )
        second = make_document_envelope(
            id="cap_intake_doc_02", payload=make_document_payload(file_ref=PDF_FILE_REF)
        )

        first_record = intake.accept(envelope)
        second_record = intake.accept(second)

        assert first_record.id != second_record.id
        assert first_record.raw_object == second_record.raw_object
        assert staged.writes == []


class TestUnsupportedDocumentShapes:
    """Valid envelopes naming a capability this build does not have."""

    def test_a_document_backed_by_text_alone_is_refused(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=None, text="the paper, pasted")
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="backed by text"):
            intake.accept(envelope)

    def test_a_document_with_file_ref_and_text_is_refused(self, intake: CaptureIntake) -> None:
        """A capture stores one raw original; choosing would discard the other."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, text="also the paper")
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="file_ref and text"):
            intake.accept(envelope)

    def test_a_document_with_file_ref_and_html_is_refused(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, html="<p>also the paper</p>")
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="file_ref and html"):
            intake.accept(envelope)

    def test_a_document_with_no_mime_type_is_refused(self, intake: CaptureIntake) -> None:
        """Nothing sniffs the bytes to find out; the submitter has to say."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type=None)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no mime_type"):
            intake.accept(envelope)

    @pytest.mark.parametrize(
        "mime_type",
        [
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/epub+zip",
            "text/plain",
            "application/octet-stream",
            "application/pdf; charset=utf-8",
            "APPLICATION/PDF",
        ],
        ids=["docx", "epub", "text", "octets", "parameterized", "uppercase"],
    )
    def test_a_document_of_another_mime_type_is_refused(
        self, intake: CaptureIntake, mime_type: str
    ) -> None:
        """Exact match only. A format with no processor must not reach the router."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type=mime_type)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="application/pdf"):
            intake.accept(envelope)

    def test_the_refusal_never_echoes_the_declared_mime_type(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type="x-secret/internal")
        )

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert "x-secret" not in str(raised.value)

    def test_an_unsupported_shape_has_no_side_effects(
        self, intake: CaptureIntake, journal: list[str], clock: FakeClock
    ) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type="text/plain")
        )

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []


#: Every ``file_ref`` this build refuses to interpret. Each is a shape somebody
#: might reasonably expect to work — a path, a URL, a bucket key, a bare digest,
#: a nearly-right reference — and every one of them is refused *without being
#: opened, resolved, or fetched*.
UNRESOLVABLE_REFS = [
    "/etc/passwd",
    "/home/someone/Documents/paper.pdf",
    "C:\\Users\\someone\\paper.pdf",
    "file:///etc/passwd",
    "http://169.254.169.254/latest/meta-data/",
    "https://example.com/paper.pdf",
    "s3://a-bucket/paper.pdf",
    "blob://document",
    "./relative/paper.pdf",
    "sha256:not-a-digest",
    "sha256:" + "A" * 64,
    "sha512:" + "a" * 64,
    "a" * 64,
]


class TestReferencesThisBuildDoesNotResolve:
    """The line between an upload boundary and a local-file-read primitive."""

    @pytest.mark.parametrize("file_ref", UNRESOLVABLE_REFS)
    def test_the_reference_is_refused(self, intake: CaptureIntake, file_ref: str) -> None:
        envelope = make_document_envelope(payload=make_document_payload(file_ref=file_ref))

        with pytest.raises(UnsupportedCapturePayloadError, match="raw object reference"):
            intake.accept(envelope)

    @pytest.mark.parametrize("file_ref", UNRESOLVABLE_REFS)
    def test_the_reference_leaves_no_trace(
        self,
        intake: CaptureIntake,
        file_ref: str,
        journal: list[str],
        clock: FakeClock,
        record_store: FakeCaptureRecordStore,
    ) -> None:
        envelope = make_document_envelope(payload=make_document_payload(file_ref=file_ref))

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []
        assert record_store.created == []

    @pytest.mark.parametrize("file_ref", UNRESOLVABLE_REFS)
    def test_the_reference_is_not_echoed_back(self, intake: CaptureIntake, file_ref: str) -> None:
        """It may be somebody's home directory. A refusal is not a reason to publish it."""
        envelope = make_document_envelope(payload=make_document_payload(file_ref=file_ref))

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert file_ref not in str(raised.value)

    def test_nothing_on_the_local_filesystem_is_opened(
        self, intake: CaptureIntake, tmp_path: Path
    ) -> None:
        """A path that really exists is refused exactly like one that does not."""
        real = tmp_path / "paper.pdf"
        real.write_bytes(PDF_BYTES)
        envelope = make_document_envelope(payload=make_document_payload(file_ref=str(real)))

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)


class TestMaterialThatIsNotStaged:
    """A reference this build understands, naming bytes nobody put there."""

    def test_it_is_refused_as_material_unavailable(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=build_raw_ref("b" * 64))
        )

        with pytest.raises(CaptureMaterialUnavailableError, match="not in the raw object store"):
            intake.accept(envelope)

    def test_it_leaves_no_record_no_clock_read_and_no_write(
        self,
        intake: CaptureIntake,
        journal: list[str],
        clock: FakeClock,
        record_store: FakeCaptureRecordStore,
        raw_store: FakeRawObjectStore,
    ) -> None:
        """The ordering rule, stated as a negative: no receipt for a non-capture."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=build_raw_ref("b" * 64))
        )

        with pytest.raises(CaptureMaterialUnavailableError):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []
        assert record_store.created == []
        assert raw_store.writes == []

    def test_it_is_not_an_unsupported_payload(self, intake: CaptureIntake) -> None:
        """This build *does* handle this shape. The bytes are simply not staged."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=build_raw_ref("b" * 64))
        )

        with pytest.raises(CaptureMaterialUnavailableError) as raised:
            intake.accept(envelope)

        assert not isinstance(raised.value, UnsupportedCapturePayloadError)
        assert not isinstance(raised.value, InvalidCaptureEnvelopeError)

    def test_it_shares_the_intake_error_base(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=build_raw_ref("b" * 64))
        )

        with pytest.raises(CaptureIntakeError):
            intake.accept(envelope)

    def test_the_identical_envelope_succeeds_once_the_bytes_are_staged(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        """What makes this a request problem rather than a capability one."""
        with pytest.raises(CaptureMaterialUnavailableError):
            intake.accept(envelope)

        raw_store.store_bytes(PDF_BYTES, mime_type=PDF_MIME)

        assert intake.accept(envelope).status is CaptureStatus.STORED

    def test_the_missing_reference_is_not_echoed_back(self, intake: CaptureIntake) -> None:
        missing = build_raw_ref("b" * 64)
        envelope = make_document_envelope(payload=make_document_payload(file_ref=missing))

        with pytest.raises(CaptureMaterialUnavailableError) as raised:
            intake.accept(envelope)

        assert missing not in str(raised.value)


def emptied_document_envelope() -> CaptureEnvelope:
    """A document payload that carries neither ``file_ref`` nor ``text``.

    Reachable only the way Phase 0A documents: a model-level validator rejects
    the assignment *after* it has been written, so a caller can hold an envelope
    that no longer satisfies its own contract.
    """
    envelope = make_document_envelope()
    with pytest.raises(Exception):  # noqa: B017,PT011 - pydantic's ValidationError
        envelope.payload.file_ref = None
    assert envelope.payload.file_ref is None
    return envelope


def test_a_document_payload_with_no_material_at_all_is_an_invalid_envelope(
    intake: CaptureIntake, journal: list[str], clock: FakeClock
) -> None:
    with pytest.raises(InvalidCaptureEnvelopeError, match="neither file_ref nor text"):
        intake.accept(emptied_document_envelope())

    assert journal == []
    assert clock.reads == []
