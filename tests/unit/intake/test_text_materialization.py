"""What exactly gets written to raw storage.

Encoding is the only transformation intake performs. Every test here is a
promise that something a well-meaning normalizer might "fix" is left alone: the
bytes a future processor reads back are the bytes the caller submitted.
"""

import pytest

from core.contracts import CaptureEnvelope
from core.intake import CaptureIntake
from tests.unit.intake.builders import AWKWARD_TEXT, TEXT, make_envelope, make_payload
from tests.unit.intake.doubles import FakeRawObjectStore


def stored_bytes(raw_store: FakeRawObjectStore) -> bytes:
    (written,) = raw_store.writes
    return written[0]


def test_ascii_text_is_stored_as_its_utf8_bytes(
    intake: CaptureIntake, envelope: CaptureEnvelope, raw_store: FakeRawObjectStore
) -> None:
    intake.accept(envelope)

    assert stored_bytes(raw_store) == TEXT.encode("utf-8")


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("plain ascii", id="ascii"),
        pytest.param(
            "\u041f\u0440\u0438\u0432\u0435\u0442 \u2014 \u4f60\u597d \U0001f30d", id="unicode"
        ),
        pytest.param("line 1\r\nline 2\r\n", id="crlf"),
        pytest.param("\tleading tab and trailing spaces   ", id="whitespace"),
        pytest.param("\ufeffbyte order mark", id="bom"),
        pytest.param("A\u030a vs \u00c5", id="unnormalized"),
        pytest.param("vertical\x0btab", id="control-character"),
        pytest.param("  padded  ", id="padded"),
    ],
)
def test_text_reaches_storage_byte_for_byte(
    intake: CaptureIntake, raw_store: FakeRawObjectStore, text: str
) -> None:
    """No trimming, normalizing, line-ending rewriting, or BOM meddling.

    Text that is *only* whitespace never reaches intake: ``NonBlankStr``
    refuses it at the contract, which is where that rule belongs.
    """
    intake.accept(make_envelope(payload=make_payload(text=text)))

    assert stored_bytes(raw_store) == text.encode("utf-8")


def test_awkward_text_survives_intact(intake: CaptureIntake, raw_store: FakeRawObjectStore) -> None:
    intake.accept(make_envelope(payload=make_payload(text=AWKWARD_TEXT)))

    written = stored_bytes(raw_store)
    assert written == AWKWARD_TEXT.encode("utf-8")
    assert written.decode("utf-8") == AWKWARD_TEXT


def test_unicode_composition_is_left_alone(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    """Decomposed and precomposed forms stay distinct: no NFC/NFD normalization."""
    decomposed = "A\u030a"
    intake.accept(make_envelope(payload=make_payload(text=decomposed)))

    assert stored_bytes(raw_store) == decomposed.encode("utf-8")
    assert stored_bytes(raw_store) != "\u00c5".encode()


def test_no_bom_is_added(intake: CaptureIntake, raw_store: FakeRawObjectStore) -> None:
    intake.accept(make_envelope(payload=make_payload(text="no mark here")))

    assert not stored_bytes(raw_store).startswith(b"\xef\xbb\xbf")


def test_the_declared_mime_type_is_passed_through(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    intake.accept(make_envelope(payload=make_payload(mime_type="text/markdown")))

    (_, mime_type) = raw_store.writes[0]
    assert mime_type == "text/markdown"


def test_an_absent_mime_type_stays_absent(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    """Intake invents no ``text/plain``; an undeclared type stays undeclared."""
    accepted = intake.accept(make_envelope(payload=make_payload(mime_type=None)))

    (_, mime_type) = raw_store.writes[0]
    assert mime_type is None
    assert accepted.raw_object is not None
    assert accepted.raw_object.mime_type is None


def test_the_mime_type_reaches_the_stored_reference(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    accepted = intake.accept(make_envelope(payload=make_payload(mime_type="text/plain")))

    assert accepted.raw_object is not None
    assert accepted.raw_object.mime_type == "text/plain"


def test_the_bytes_are_stored_once_and_never_streamed(
    intake: CaptureIntake, envelope: CaptureEnvelope, raw_store: FakeRawObjectStore
) -> None:
    intake.accept(envelope)

    assert raw_store.journal.count("raw.store_bytes") == 1
    assert "raw.store_stream" not in raw_store.journal


def test_the_title_is_not_smuggled_into_the_bytes(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    """``payload.title`` has nowhere durable to go, and is not hidden in content."""
    intake.accept(make_envelope(payload=make_payload(text="body only", title="A Title")))

    assert stored_bytes(raw_store) == b"body only"


def test_envelope_metadata_never_reaches_the_raw_bytes(
    intake: CaptureIntake, raw_store: FakeRawObjectStore
) -> None:
    """Metadata is durable in the record; the bytes stay the content alone."""
    intake.accept(make_envelope(payload=make_payload(text="body only", title="A Title")))

    written = stored_bytes(raw_store)
    assert written == b"body only"
    for absent in (b"A Title", b"laptop", b"terminal", b"reading", b"architecture", b"save"):
        assert absent not in written


def test_metadata_lands_in_its_own_fields_not_in_error(
    intake: CaptureIntake, envelope: CaptureEnvelope
) -> None:
    """Context, intent, and title have real homes now, so nothing is improvised."""
    accepted = intake.accept(envelope)

    assert accepted.error is None
    assert accepted.context is not None
    assert accepted.intent is not None
    assert accepted.title == "A note"
