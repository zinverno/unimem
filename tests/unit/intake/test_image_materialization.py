"""Image intake: the staged path, taken by a second kind of material.

Almost everything worth asserting here is already asserted for documents, and
that is the point rather than a redundancy. An image travels the same staging
route, produces the same two snapshots, triggers the same absence of a second
write, and is refused in the same way and at the same moment when its material
is missing — because none of that was ever about documents. The acquisition
boundary was built format-independent, and this is the payload type that shows
it was also *modality*-independent.

What is genuinely new is narrow: which declared MIME types an ``IMAGE`` capture
may carry, that they are a separate set from the document ones rather than a
merged list, and that intake still does not open the bytes to find out whether
they are really a PNG. That last question belongs to the processor, one layer
later, and asking it here would mean parsing in order to decide acceptance.
"""

import hashlib

import pytest
from pydantic import ValidationError

from core.contracts import CaptureEnvelope, CapturePayloadType, CaptureStatus
from core.intake import (
    IMAGE_MIME_TYPES,
    CaptureIntake,
    CaptureMaterialUnavailableError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from core.storage import build_raw_ref
from tests import images
from tests.unit.intake.builders import (
    DOCX_MIME,
    JPEG_MIME,
    PDF_MIME,
    PNG_MIME,
    RECEIVED_AT,
    UPDATED_AT,
    WEBP_MIME,
    make_image_envelope,
    make_image_payload,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore

PNG_BYTES = images.png()
PNG_DIGEST = hashlib.sha256(PNG_BYTES).hexdigest()
PNG_FILE_REF = build_raw_ref(PNG_DIGEST)

JPEG_BYTES = images.jpeg()
JPEG_DIGEST = hashlib.sha256(JPEG_BYTES).hexdigest()
JPEG_FILE_REF = build_raw_ref(JPEG_DIGEST)


@pytest.fixture
def staged(raw_store: FakeRawObjectStore, journal: list[str]) -> FakeRawObjectStore:
    """A raw store already holding both images, as uploads would have left them.

    The staging writes are erased from the journal the assertions read: they
    happened before the capture, through a different caller, and counting them
    as intake's calls would hide the very thing under test.
    """
    raw_store.store_bytes(PNG_BYTES, mime_type=PNG_MIME)
    raw_store.store_bytes(JPEG_BYTES, mime_type=JPEG_MIME)
    journal.clear()
    raw_store.writes.clear()
    return raw_store


@pytest.fixture
def envelope() -> CaptureEnvelope:
    return make_image_envelope(payload=make_image_payload(file_ref=PNG_FILE_REF))


# --- the supported shape ----------------------------------------------------


class TestAStagedImageIsAccepted:
    def test_the_capture_is_stored(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        assert intake.accept(envelope).status is CaptureStatus.STORED

    def test_the_payload_type_is_carried_onto_the_record(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        assert intake.accept(envelope).payload_type is CapturePayloadType.IMAGE

    def test_the_record_points_at_the_staged_bytes(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        raw_object = intake.accept(envelope).raw_object

        assert raw_object is not None
        assert raw_object.sha256 == PNG_DIGEST
        assert raw_object.ref == PNG_FILE_REF

    def test_the_declared_mime_type_is_preserved(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        """What the submitter declared for *this capture*, not what a store inferred."""
        raw_object = intake.accept(envelope).raw_object

        assert raw_object is not None
        assert raw_object.mime_type == PNG_MIME

    def test_the_bytes_are_not_written_a_second_time(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        intake.accept(envelope)

        assert staged.writes == []

    def test_the_capture_metadata_survives(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        stored = intake.accept(envelope)

        assert stored.title == "A photograph"
        assert stored.context is not None
        assert (stored.received_at, stored.updated_at) == (RECEIVED_AT, UPDATED_AT)

    def test_nothing_looks_inside_the_image(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, envelope: CaptureEnvelope
    ) -> None:
        """Existence is a read; the bytes themselves are never opened here."""
        intake.accept(envelope)

        assert "open" not in staged.journal
        assert "read_bytes" not in staged.journal


def test_a_staged_jpeg_is_accepted_the_same_way(
    intake: CaptureIntake, staged: FakeRawObjectStore
) -> None:
    envelope = make_image_envelope(
        payload=make_image_payload(mime_type=JPEG_MIME, file_ref=JPEG_FILE_REF)
    )

    stored = intake.accept(envelope)

    assert stored.raw_object is not None
    assert (stored.raw_object.mime_type, stored.raw_object.sha256) == (JPEG_MIME, JPEG_DIGEST)


def test_intake_declares_exactly_two_image_formats() -> None:
    assert IMAGE_MIME_TYPES == ("image/png", "image/jpeg")


def test_intake_does_not_verify_that_the_bytes_are_an_image(
    intake: CaptureIntake, raw_store: FakeRawObjectStore, journal: list[str]
) -> None:
    """A capture whose declaration is wrong is accepted here and fails downstream.

    Deliberate, and the division of labour the phase rests on: intake decides
    what a deployment accepts at its boundary, and only the processor is allowed
    to open the bytes and find the declaration contradicted.
    """
    not_an_image = b"this is not a PNG"
    raw_store.store_bytes(not_an_image, mime_type=PNG_MIME)
    journal.clear()
    raw_store.writes.clear()
    ref = build_raw_ref(hashlib.sha256(not_an_image).hexdigest())

    stored = intake.accept(make_image_envelope(payload=make_image_payload(file_ref=ref)))

    assert stored.status is CaptureStatus.STORED


# --- refusals ---------------------------------------------------------------


class TestAnUnsupportedImageFormat:
    @pytest.mark.parametrize(
        "mime_type", [WEBP_MIME, "image/gif", "image/tiff", "image/svg+xml", "image/heic"]
    )
    def test_a_deferred_format_is_refused(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, mime_type: str
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(mime_type=mime_type, file_ref=PNG_FILE_REF)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no processor for"):
            intake.accept(envelope)

    @pytest.mark.parametrize("mime_type", [PDF_MIME, DOCX_MIME])
    def test_a_document_format_is_not_an_image_format(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, mime_type: str
    ) -> None:
        """The two sets are separate, so a PDF cannot be declared on an image capture."""
        envelope = make_image_envelope(
            payload=make_image_payload(mime_type=mime_type, file_ref=PNG_FILE_REF)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no processor for"):
            intake.accept(envelope)

    def test_a_missing_mime_type_is_refused(
        self, intake: CaptureIntake, staged: FakeRawObjectStore
    ) -> None:
        """Intake does not infer an image's format from anything."""
        envelope = make_image_envelope(
            payload=make_image_payload(mime_type=None, file_ref=PNG_FILE_REF)
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="no mime_type"):
            intake.accept(envelope)

    def test_nothing_becomes_durable(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        clock: FakeClock,
        record_store: FakeCaptureRecordStore,
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(mime_type=WEBP_MIME, file_ref=PNG_FILE_REF)
        )

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

        assert journal == []
        assert clock.reads == []
        assert record_store.created == []

    def test_no_submitted_string_is_echoed(
        self, intake: CaptureIntake, staged: FakeRawObjectStore
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(mime_type=WEBP_MIME, file_ref=PNG_FILE_REF)
        )

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert WEBP_MIME not in str(raised.value)
        assert PNG_FILE_REF not in str(raised.value)


class TestAReferenceThisBuildDoesNotResolve:
    @pytest.mark.parametrize(
        "file_ref",
        [
            "/etc/passwd",
            "../../secrets/id_rsa",
            "file:///home/someone/photo.png",
            "http://169.254.169.254/latest/meta-data/",
            "s3://bucket/key.png",
            "blob://screenshot",
            "sha256:not-a-digest",
            "sha256:" + "A" * 64,
        ],
    )
    def test_it_is_refused(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, file_ref: str
    ) -> None:
        envelope = make_image_envelope(payload=make_image_payload(file_ref=file_ref))

        with pytest.raises(UnsupportedCapturePayloadError, match="raw object reference"):
            intake.accept(envelope)

    @pytest.mark.parametrize(
        "file_ref", ["/etc/passwd", "file:///home/someone/photo.png", "http://example.com/a.png"]
    )
    def test_it_is_never_opened_resolved_or_fetched(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, journal: list[str], file_ref: str
    ) -> None:
        """The line between a client string and a local-file-read primitive."""
        envelope = make_image_envelope(payload=make_image_payload(file_ref=file_ref))

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

        assert journal == []

    def test_the_rejected_reference_is_not_echoed(
        self, intake: CaptureIntake, staged: FakeRawObjectStore
    ) -> None:
        secret = "/home/someone/private/holiday.png"
        envelope = make_image_envelope(payload=make_image_payload(file_ref=secret))

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert secret not in str(raised.value)


class TestMaterialThatWasNeverStaged:
    @pytest.fixture
    def unstaged(self) -> CaptureEnvelope:
        return make_image_envelope(payload=make_image_payload(file_ref=build_raw_ref("b" * 64)))

    def test_it_is_refused_as_unavailable(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore, unstaged: CaptureEnvelope
    ) -> None:
        with pytest.raises(CaptureMaterialUnavailableError, match="stage the bytes first"):
            intake.accept(unstaged)

    def test_the_refusal_happens_before_any_record_exists(
        self,
        intake: CaptureIntake,
        raw_store: FakeRawObjectStore,
        unstaged: CaptureEnvelope,
        clock: FakeClock,
        record_store: FakeCaptureRecordStore,
    ) -> None:
        """A receipt is evidence a capture was accepted; this one never was."""
        with pytest.raises(CaptureMaterialUnavailableError):
            intake.accept(unstaged)

        assert clock.reads == []
        assert record_store.created == []

    def test_the_same_envelope_succeeds_once_the_bytes_are_staged(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        envelope = make_image_envelope(payload=make_image_payload(file_ref=PNG_FILE_REF))
        raw_store.store_bytes(PNG_BYTES, mime_type=PNG_MIME)

        assert intake.accept(envelope).status is CaptureStatus.STORED


class TestCompetingSubmittedMaterial:
    """A capture stores one raw original, so two representations are refused."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [("text", "a caption I typed"), ("html", "<p>a caption I typed</p>")],
    )
    def test_it_is_refused(
        self, intake: CaptureIntake, staged: FakeRawObjectStore, field: str, value: str
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(file_ref=PNG_FILE_REF, **{field: value})
        )

        with pytest.raises(UnsupportedCapturePayloadError, match="one raw original"):
            intake.accept(envelope)

    def test_nothing_is_silently_discarded(
        self,
        intake: CaptureIntake,
        staged: FakeRawObjectStore,
        journal: list[str],
        record_store: FakeCaptureRecordStore,
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(file_ref=PNG_FILE_REF, text="a caption I typed")
        )

        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(envelope)

        assert journal == []
        assert record_store.created == []

    def test_the_submitted_text_is_not_echoed(
        self, intake: CaptureIntake, staged: FakeRawObjectStore
    ) -> None:
        envelope = make_image_envelope(
            payload=make_image_payload(file_ref=PNG_FILE_REF, text="something private")
        )

        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(envelope)

        assert "something private" not in str(raised.value)


def emptied_image_envelope() -> CaptureEnvelope:
    """An image envelope whose payload no longer names its file_ref.

    Phase 0A documents that an assignment rejected by a model-level validator has
    already been written, so a caller really can end up holding one of these. It
    is the only way to reach the state, and it is why the guard exists.
    """
    envelope = make_image_envelope()
    with pytest.raises(ValidationError):
        envelope.payload.file_ref = None
    assert envelope.payload.file_ref is None
    return envelope


def test_an_image_payload_emptied_after_construction_is_refused(
    intake: CaptureIntake, journal: list[str], clock: FakeClock
) -> None:
    """The envelope contradicts its own contract, which is not a missing capability."""
    with pytest.raises(InvalidCaptureEnvelopeError, match="carries no file_ref"):
        intake.accept(emptied_image_envelope())

    assert journal == []
    assert clock.reads == []


def test_that_refusal_is_not_an_unsupported_payload(intake: CaptureIntake) -> None:
    with pytest.raises(InvalidCaptureEnvelopeError) as raised:
        intake.accept(emptied_image_envelope())

    assert not isinstance(raised.value, UnsupportedCapturePayloadError)
