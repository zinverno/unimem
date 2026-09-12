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

Phase 3 PR 2 adds a second document format, and the interesting assertions are
the ones that stay identical. A DOCX travels the same staging route, produces the
same two snapshots, triggers the same absence of a second write, and is refused
in the same way and at the same moment when its material is missing — because
none of that was ever about PDFs. What is genuinely new is one line: which
declared MIME types intake accepts, and that ``application/msword`` is still not
one of them.
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
from tests.docxs import paragraph_table_paragraph_docx
from tests.pdfs import two_page_pdf
from tests.unit.intake.builders import (
    DOC_MIME,
    DOCX_MIME,
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

DOCX_BYTES = paragraph_table_paragraph_docx()
DOCX_DIGEST = hashlib.sha256(DOCX_BYTES).hexdigest()
DOCX_FILE_REF = build_raw_ref(DOCX_DIGEST)


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
            DOC_MIME,
            "application/vnd.ms-word.document.macroEnabled.12",
            "application/vnd.oasis.opendocument.text",
            "application/rtf",
            "application/epub+zip",
            "application/zip",
            "text/plain",
            "application/octet-stream",
            "application/pdf; charset=utf-8",
            "APPLICATION/PDF",
            DOCX_MIME.upper(),
            f"{DOCX_MIME}; charset=utf-8",
        ],
        ids=[
            "doc",
            "docm",
            "odt",
            "rtf",
            "epub",
            "zip",
            "text",
            "octets",
            "parameterized-pdf",
            "uppercase-pdf",
            "uppercase-docx",
            "parameterized-docx",
        ],
    )
    def test_a_document_of_another_mime_type_is_refused(
        self, intake: CaptureIntake, mime_type: str
    ) -> None:
        """Exact match only. A format with no processor must not reach the router.

        ``application/zip`` earns its place here: a DOCX *is* a ZIP, and nothing
        in this build looks inside one to find out what kind. A client declaring
        the container rather than the format is declaring something this build
        has no processor for, and is told so.
        """
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type=mime_type)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="application/pdf"):
            intake.accept(envelope)

    def test_the_refusal_of_legacy_word_names_both_supported_formats(
        self, intake: CaptureIntake
    ) -> None:
        """A ``.doc`` client is told what this build does read, not merely "no"."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=PDF_FILE_REF, mime_type=DOC_MIME)
        )

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert PDF_MIME in str(raised.value)
        assert DOCX_MIME in str(raised.value)

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


@pytest.fixture
def staged_docx(raw_store: FakeRawObjectStore, journal: list[str]) -> FakeRawObjectStore:
    """A raw store that already holds the DOCX, as an upload would have left it."""
    raw_store.store_bytes(DOCX_BYTES, mime_type=DOCX_MIME)
    journal.clear()
    raw_store.writes.clear()
    return raw_store


@pytest.fixture
def docx_envelope() -> CaptureEnvelope:
    return make_document_envelope(
        id="cap_intake_docx_01",
        payload=make_document_payload(file_ref=DOCX_FILE_REF, mime_type=DOCX_MIME),
    )


class TestAStagedDocxIsAccepted:
    """The second document format, on the route the first one did not have to widen.

    Every assertion in this class is the PDF assertion with two constants
    changed, and that is the finding rather than a shortcut: nothing about
    staging, ordering, the receipt, the reference, or the absence of a second
    write was ever PDF-shaped.
    """

    def test_the_capture_is_stored(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        assert intake.accept(docx_envelope).status is CaptureStatus.STORED

    def test_the_payload_type_is_carried_onto_the_record(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        assert intake.accept(docx_envelope).payload_type is CapturePayloadType.DOCUMENT

    def test_no_second_raw_write_happens(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
        journal: list[str],
    ) -> None:
        intake.accept(docx_envelope)

        assert journal == ["records.create", "records.replace"]
        assert staged_docx.writes == []

    def test_the_staged_reference_reaches_the_record_unchanged(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        stored = intake.accept(docx_envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.ref == DOCX_FILE_REF

    def test_the_digest_is_preserved(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        stored = intake.accept(docx_envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.sha256 == DOCX_DIGEST
        assert stored.raw_object.id == DOCX_DIGEST

    def test_the_declared_mime_type_is_carried_onto_the_reference(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        """What the router will read to tell this capture from a PDF one."""
        stored = intake.accept(docx_envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.mime_type == DOCX_MIME

    def test_the_stored_reference_still_resolves_to_the_exact_bytes(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        stored = intake.accept(docx_envelope)

        assert stored.raw_object is not None
        assert staged_docx.read_bytes(stored.raw_object) == DOCX_BYTES

    def test_capture_metadata_survives(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
    ) -> None:
        stored = intake.accept(docx_envelope)

        assert stored.source == docx_envelope.source
        assert stored.context == docx_envelope.context
        assert stored.intent == docx_envelope.intent
        assert stored.title == docx_envelope.payload.title

    def test_the_lifecycle_is_the_same_two_snapshots(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
        record_store: FakeCaptureRecordStore,
        clock: FakeClock,
    ) -> None:
        intake.accept(docx_envelope)

        assert [record.status for record in record_store.created] == [CaptureStatus.RECEIVED]
        assert [record.status for record in record_store.replaced] == [CaptureStatus.STORED]
        assert clock.reads == [RECEIVED_AT, UPDATED_AT]

    def test_the_received_snapshot_carries_no_raw_object_yet(
        self,
        intake: CaptureIntake,
        staged_docx: FakeRawObjectStore,
        docx_envelope: CaptureEnvelope,
        record_store: FakeCaptureRecordStore,
    ) -> None:
        intake.accept(docx_envelope)

        assert record_store.created[0].raw_object is None

    def test_missing_docx_material_is_refused_before_any_side_effect(
        self,
        intake: CaptureIntake,
        journal: list[str],
        clock: FakeClock,
        record_store: FakeCaptureRecordStore,
        raw_store: FakeRawObjectStore,
    ) -> None:
        """Nothing staged the bytes, so there is no capture and no receipt."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=DOCX_FILE_REF, mime_type=DOCX_MIME)
        )

        with pytest.raises(CaptureMaterialUnavailableError):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []
        assert record_store.created == []
        assert raw_store.writes == []

    def test_a_docx_with_text_alongside_is_refused(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(
                file_ref=DOCX_FILE_REF, mime_type=DOCX_MIME, text="also the paper"
            )
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="file_ref and text"):
            intake.accept(envelope)

    def test_a_docx_with_html_alongside_is_refused(self, intake: CaptureIntake) -> None:
        envelope = make_document_envelope(
            payload=make_document_payload(
                file_ref=DOCX_FILE_REF, mime_type=DOCX_MIME, html="<p>also the paper</p>"
            )
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="file_ref and html"):
            intake.accept(envelope)

    @pytest.mark.parametrize("file_ref", UNRESOLVABLE_REFS)
    def test_an_unresolvable_ref_is_still_refused_when_the_format_is_docx(
        self, intake: CaptureIntake, file_ref: str
    ) -> None:
        """Declaring a second supported format opens no new path to the filesystem."""
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=file_ref, mime_type=DOCX_MIME)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="raw object reference"):
            intake.accept(envelope)

    def test_a_real_docx_on_disk_is_not_opened(self, intake: CaptureIntake, tmp_path: Path) -> None:
        real = tmp_path / "paper.docx"
        real.write_bytes(DOCX_BYTES)
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=str(real), mime_type=DOCX_MIME)
        )

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

    def test_a_docx_declared_as_pdf_is_accepted_by_intake_and_left_to_the_processor(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """Intake does not verify a declaration; it records it.

        Nothing here sniffs the bytes, so a mislabelled document is a *stored*
        capture whose declared format is wrong — and the processor that claims
        that format is where it fails, truthfully and with the original intact.
        """
        raw_store.store_bytes(DOCX_BYTES, mime_type=PDF_MIME)
        envelope = make_document_envelope(
            payload=make_document_payload(file_ref=DOCX_FILE_REF, mime_type=PDF_MIME)
        )

        stored = intake.accept(envelope)

        assert stored.raw_object is not None
        assert stored.raw_object.mime_type == PDF_MIME
