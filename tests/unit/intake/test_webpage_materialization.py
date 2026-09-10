"""What intake does with an HTML-backed webpage capture.

Two claims run through all of it.

**The raw original is the exact submitted HTML.** Not a re-serialized DOM, not a
tidied document, not a normalized string — the UTF-8 encoding of the very
characters the client sent, and nothing else. Every test that pushes indentation,
CRLFs, entities, or awkward Unicode through intake is a promise that no
well-meaning normalizer got at it on the way in.

**An ambiguous webpage is refused, not resolved.** A ``CaptureRecord`` holds one
raw original, so an envelope offering HTML *and* another material representation
offers more than the record can hold. Picking one would durably discard the other
and report success. So it is refused — before the clock is read, before a record
exists, and before a byte is written.
"""

import pytest

from core.contracts import (
    CapturePayload,
    CapturePayloadType,
    CaptureStatus,
)
from core.intake import (
    CaptureIntake,
    CaptureIntakeError,
    InvalidCaptureEnvelopeError,
    UnsupportedCapturePayloadError,
)
from tests.unit.intake.builders import (
    CAPTURED_AT,
    HTML,
    RECEIVED_AT,
    TEXT,
    UPDATED_AT,
    make_envelope,
    make_payload,
    make_webpage_envelope,
    make_webpage_payload,
)
from tests.unit.intake.doubles import FakeCaptureRecordStore, FakeClock, FakeRawObjectStore


def stored_bytes(raw_store: FakeRawObjectStore) -> bytes:
    (written,) = raw_store.writes
    return written[0]


class TestTextIsUnchanged:
    """The text path is the same code path it was, byte for byte.

    Phase 2 widened *which field* becomes the original. It did not touch what
    happens to a text capture, and these say so from inside the same module that
    changed.
    """

    def test_a_text_capture_still_stores_its_text(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        intake.accept(make_envelope())

        assert stored_bytes(raw_store) == TEXT.encode("utf-8")

    def test_a_text_capture_still_reaches_stored(self, intake: CaptureIntake) -> None:
        accepted = intake.accept(make_envelope())

        assert accepted.status is CaptureStatus.STORED
        assert accepted.payload_type is CapturePayloadType.TEXT

    def test_html_alongside_text_is_not_read_for_a_text_capture(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """A ``TEXT`` payload means ``payload.text``. It always did."""
        intake.accept(make_envelope(payload=make_payload(html="<p>ignored</p>")))

        assert stored_bytes(raw_store) == TEXT.encode("utf-8")


class TestTheSupportedWebpageForm:
    """HTML and nothing else — the one webpage shape this build ingests."""

    def test_it_is_accepted(self, intake: CaptureIntake) -> None:
        accepted = intake.accept(make_webpage_envelope())

        assert accepted.status is CaptureStatus.STORED
        assert accepted.payload_type is CapturePayloadType.WEBPAGE

    def test_the_exact_html_utf8_bytes_are_stored(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        intake.accept(make_webpage_envelope())

        assert stored_bytes(raw_store) == HTML.encode("utf-8")

    def test_the_lifecycle_is_received_then_stored(
        self, intake: CaptureIntake, journal: list[str]
    ) -> None:
        intake.accept(make_webpage_envelope())

        assert journal == [
            "records.create",
            "raw.store_bytes",
            "records.replace",
        ]

    def test_the_html_is_never_written_onto_the_record(self, intake: CaptureIntake) -> None:
        """``CaptureRecord`` describes a capture; it does not carry its content."""
        accepted = intake.accept(make_webpage_envelope())

        assert HTML not in accepted.model_dump_json()

    def test_the_envelope_is_not_mutated(self, intake: CaptureIntake) -> None:
        envelope = make_webpage_envelope()
        before = envelope.model_dump_json()

        intake.accept(envelope)

        assert envelope.model_dump_json() == before


@pytest.mark.parametrize(
    ("label", "html"),
    [
        ("plain", "<p>plain</p>"),
        ("leading whitespace", "\n\n   <html><body><p>hi</p></body></html>"),
        ("trailing whitespace", "<p>hi</p>\n\n   \t"),
        ("crlf", "<p>one</p>\r\n<p>two</p>\r\n"),
        ("indented", "<div>\n\t\t<p>deeply indented</p>\n</div>"),
        ("unicode", "<p>Привет 你好 \U0001f30d</p>"),
        ("combining", "<p>Å vs Å</p>"),
        ("entities", "<p>&amp;amp; &lt; &nbsp; &#233;</p>"),
        ("nbsp literal", "<p>a\u00a0b</p>"),
        ("bom", "﻿<html><body>marked</body></html>"),
        ("no doctype", "<p>bare</p>"),
        ("malformed", "<p>unclosed<div>next"),
        ("script only", "<script>var x = 1;</script>"),
    ],
    ids=lambda value: value if isinstance(value, str) and " " not in value else None,
)
class TestTheHtmlReachesStorageByteForByte:
    """Nothing is trimmed, re-indented, re-encoded, or entity-rewritten.

    Intake does not parse HTML. It does not know that ``&amp;amp;`` is a doubly
    escaped ampersand or that ``<p>unclosed`` is malformed, because it never
    looks — which is exactly why the original stays trustworthy.
    """

    def test_the_bytes_are_the_submitted_string(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore, label: str, html: str
    ) -> None:
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html=html)))

        assert stored_bytes(raw_store) == html.encode("utf-8")

    def test_the_bytes_decode_back_to_the_submitted_string(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore, label: str, html: str
    ) -> None:
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html=html)))

        assert stored_bytes(raw_store).decode("utf-8") == html


class TestPreservationInDetail:
    """The individual promises, stated one at a time."""

    def test_entities_stay_literal(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """No entity is decoded on the way in. Decoding is the processor's job."""
        html = "<p>&amp; &lt; &gt; &nbsp; &#8212;</p>"
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html=html)))

        written = stored_bytes(raw_store)
        assert b"&amp;" in written
        assert b"&nbsp;" in written
        assert "\u00a0".encode() not in written

    def test_crlf_is_not_rewritten(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html="<p>a</p>\r\n")))

        assert stored_bytes(raw_store) == b"<p>a</p>\r\n"

    def test_unicode_composition_is_left_alone(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        decomposed = "<p>Å</p>"
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html=decomposed)))

        assert stored_bytes(raw_store) == decomposed.encode("utf-8")
        assert stored_bytes(raw_store) != "<p>Å</p>".encode()

    def test_no_charset_is_detected_or_injected(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """A ``<meta charset>`` claiming something else changes nothing."""
        html = '<html><head><meta charset="windows-1251"></head><body>x</body></html>'
        intake.accept(make_webpage_envelope(payload=make_webpage_payload(html=html)))

        assert stored_bytes(raw_store) == html.encode("utf-8")

    def test_no_title_or_url_is_injected(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        intake.accept(
            make_webpage_envelope(
                payload=make_webpage_payload(html="<p>body</p>", title="A Page Title")
            )
        )

        written = stored_bytes(raw_store)
        assert written == b"<p>body</p>"
        for absent in (b"A Page Title", b"example.com", b"laptop", b"reading"):
            assert absent not in written

    def test_the_bytes_are_stored_once_and_never_streamed(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        intake.accept(make_webpage_envelope())

        assert raw_store.journal.count("raw.store_bytes") == 1
        assert "raw.store_stream" not in raw_store.journal


class TestRawIdentityAcrossCaptures:
    """Content addressing is unchanged: bytes are identity, captures are not.

    These accept twice, so they bring their own intake with a clock prepared for
    two captures. The shared fixture deliberately runs out after one, which is
    how the other tests keep exact claims about how often it is read.
    """

    @pytest.fixture
    def intake(
        self, raw_store: FakeRawObjectStore, record_store: FakeCaptureRecordStore
    ) -> CaptureIntake:
        clock = FakeClock(RECEIVED_AT, UPDATED_AT, RECEIVED_AT, UPDATED_AT)
        return CaptureIntake(raw_store, record_store, now=clock)

    def test_identical_html_under_two_ids_shares_one_raw_object(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        first = intake.accept(make_webpage_envelope(id="cap_web_a"))
        second = intake.accept(make_webpage_envelope(id="cap_web_b"))

        assert first.raw_object is not None
        assert second.raw_object is not None
        assert first.raw_object.sha256 == second.raw_object.sha256
        assert first.raw_object.ref == second.raw_object.ref

    def test_identical_html_under_two_ids_yields_two_records(
        self, intake: CaptureIntake, record_store: FakeCaptureRecordStore
    ) -> None:
        first = intake.accept(make_webpage_envelope(id="cap_web_a"))
        second = intake.accept(make_webpage_envelope(id="cap_web_b"))

        assert first.id != second.id
        assert record_store.get("cap_web_a").id == "cap_web_a"
        assert record_store.get("cap_web_b").id == "cap_web_b"

    def test_the_same_html_as_text_shares_the_raw_object_too(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """Identity is the bytes and nothing else — not the payload type.

        A capture is still a different capture, which is the point of the
        ``payload_type`` on the record: two captures, one deduplicated original,
        and two entirely different processors will read it.
        """
        as_text = intake.accept(make_envelope(id="cap_as_text", payload=make_payload(text=HTML)))
        as_page = intake.accept(make_webpage_envelope(id="cap_as_page"))

        assert as_text.raw_object is not None
        assert as_page.raw_object is not None
        assert as_text.raw_object.sha256 == as_page.raw_object.sha256
        assert as_text.payload_type is CapturePayloadType.TEXT
        assert as_page.payload_type is CapturePayloadType.WEBPAGE


class TestMime:
    """The submitted MIME type is recorded; none is invented."""

    def test_the_declared_type_reaches_the_write_and_the_reference(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        accepted = intake.accept(
            make_webpage_envelope(payload=make_webpage_payload(mime_type="text/html"))
        )

        assert raw_store.writes[0][1] == "text/html"
        assert accepted.raw_object is not None
        assert accepted.raw_object.mime_type == "text/html"

    def test_an_unusual_declared_type_is_passed_through_unchanged(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """Intake does not correct the submitter, and does not sniff."""
        accepted = intake.accept(
            make_webpage_envelope(payload=make_webpage_payload(mime_type="application/xhtml+xml"))
        )

        assert raw_store.writes[0][1] == "application/xhtml+xml"
        assert accepted.raw_object is not None
        assert accepted.raw_object.mime_type == "application/xhtml+xml"

    def test_an_absent_type_stays_absent(
        self, intake: CaptureIntake, raw_store: FakeRawObjectStore
    ) -> None:
        """No ``text/html`` is invented here. That fallback is processor-local."""
        accepted = intake.accept(
            make_webpage_envelope(payload=make_webpage_payload(mime_type=None))
        )

        assert raw_store.writes[0][1] is None
        assert accepted.raw_object is not None
        assert accepted.raw_object.mime_type is None


class TestMetadataSurvives:
    """Phase 0G's metadata rules apply to a webpage exactly as they do to text."""

    def test_every_capture_time_fact_reaches_the_stored_record(self, intake: CaptureIntake) -> None:
        accepted = intake.accept(make_webpage_envelope())

        assert accepted.source.provider == "cli"
        assert accepted.source.url == "https://example.com/notes"
        assert accepted.context is not None
        assert accepted.context.captured_at == CAPTURED_AT
        assert accepted.context.device == "laptop"
        assert accepted.context.application == "terminal"
        assert accepted.intent is not None
        assert accepted.intent.collection == "reading"
        assert accepted.intent.tags == ["architecture"]
        assert accepted.title == "A page"

    def test_the_submitted_title_is_the_record_title(self, intake: CaptureIntake) -> None:
        accepted = intake.accept(
            make_webpage_envelope(payload=make_webpage_payload(title="What I called it"))
        )

        assert accepted.title == "What I called it"

    def test_no_title_is_extracted_from_the_html(self, intake: CaptureIntake) -> None:
        """Intake does not parse. The ``<title>`` in the markup stays in the markup."""
        accepted = intake.accept(
            make_webpage_envelope(
                payload=make_webpage_payload(html="<title>In The Page</title><p>x</p>", title=None)
            )
        )

        assert accepted.title is None

    def test_the_metadata_is_durable_from_the_receipt(
        self, intake: CaptureIntake, record_store: FakeCaptureRecordStore
    ) -> None:
        intake.accept(make_webpage_envelope())

        (received, _) = record_store.created + record_store.replaced
        assert received.status is CaptureStatus.RECEIVED
        assert received.title == "A page"
        assert received.context is not None
        assert received.context.captured_at == CAPTURED_AT


def webpage_payload(**fields: object) -> CapturePayload:
    """A webpage payload built directly, so unsupported shapes are constructible."""
    return CapturePayload(type=CapturePayloadType.WEBPAGE, **fields)  # type: ignore[arg-type]


UNSUPPORTED_SHAPES = [
    pytest.param(
        webpage_payload(html="<p>hi</p>", text="hi"),
        "html and text",
        id="html+text",
    ),
    pytest.param(
        webpage_payload(html="<p>hi</p>", file_ref="blob://page.mhtml"),
        "html and file_ref",
        id="html+file_ref",
    ),
    pytest.param(
        webpage_payload(html="<p>hi</p>", text="hi", file_ref="blob://page.mhtml"),
        "html and text and file_ref",
        id="html+text+file_ref",
    ),
    pytest.param(
        webpage_payload(text="already extracted"),
        "backed by text",
        id="text-only",
    ),
]


@pytest.mark.parametrize(("payload", "expected"), UNSUPPORTED_SHAPES)
class TestUnsupportedWebpageShapes:
    """Valid canonical envelopes this build declines to ingest.

    Each is a *capability* refusal, not a validation one: the contract permits
    these documents and keeps permitting them, and a later phase that can
    represent more than one materialization will accept them unchanged.
    """

    def test_it_is_refused(
        self, intake: CaptureIntake, payload: CapturePayload, expected: str
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError, match=expected):
            intake.accept(make_webpage_envelope(payload=payload))

    def test_the_refusal_shares_the_intake_base(
        self, intake: CaptureIntake, payload: CapturePayload, expected: str
    ) -> None:
        with pytest.raises(CaptureIntakeError):
            intake.accept(make_webpage_envelope(payload=payload))

    def test_the_clock_is_never_read(
        self,
        intake: CaptureIntake,
        clock: FakeClock,
        payload: CapturePayload,
        expected: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(make_webpage_envelope(payload=payload))

        assert clock.reads == []

    def test_no_record_is_created(
        self,
        intake: CaptureIntake,
        record_store: FakeCaptureRecordStore,
        payload: CapturePayload,
        expected: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(make_webpage_envelope(payload=payload))

        assert record_store.created == []
        assert record_store.replaced == []

    def test_no_bytes_are_written(
        self,
        intake: CaptureIntake,
        raw_store: FakeRawObjectStore,
        payload: CapturePayload,
        expected: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(make_webpage_envelope(payload=payload))

        assert raw_store.writes == []

    def test_nothing_happened_at_all(
        self,
        intake: CaptureIntake,
        journal: list[str],
        payload: CapturePayload,
        expected: str,
    ) -> None:
        with pytest.raises(UnsupportedCapturePayloadError):
            intake.accept(make_webpage_envelope(payload=payload))

        assert journal == []

    def test_no_submitted_markup_reaches_the_message(
        self, intake: CaptureIntake, payload: CapturePayload, expected: str
    ) -> None:
        """Refusals name field *names*, never field values."""
        with pytest.raises(UnsupportedCapturePayloadError) as raised:
            intake.accept(make_webpage_envelope(payload=payload))

        message = str(raised.value)
        assert "<p>hi</p>" not in message
        assert "already extracted" not in message
        assert "blob://page.mhtml" not in message


class TestAWebpageContradictingItsOwnContract:
    """Neither ``html`` nor ``text``: not a missing capability, a broken document.

    Unreachable through the contract's own validator, and reachable exactly the
    way Phase 0A says it is — an assignment a model-level validator rejected has
    already been written, so a caller can hold an envelope that no longer
    satisfies its own invariants.
    """

    @staticmethod
    def emptied() -> object:
        from pydantic import ValidationError

        envelope = make_webpage_envelope()
        with pytest.raises(ValidationError):
            envelope.payload.html = None
        assert envelope.payload.html is None
        assert envelope.payload.text is None
        return envelope

    def test_it_is_an_invalid_envelope_not_an_unsupported_one(self, intake: CaptureIntake) -> None:
        with pytest.raises(InvalidCaptureEnvelopeError, match="neither html nor text"):
            intake.accept(self.emptied())  # type: ignore[arg-type]

    def test_it_leaves_nothing_behind(
        self, intake: CaptureIntake, journal: list[str], clock: FakeClock
    ) -> None:
        with pytest.raises(InvalidCaptureEnvelopeError):
            intake.accept(self.emptied())  # type: ignore[arg-type]

        assert journal == []
        assert clock.reads == []
