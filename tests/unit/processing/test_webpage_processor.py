"""``WebpageProcessor``: from an immutable HTML original to canonical web content.

The extraction algorithm itself is specified in ``test_html_extraction.py``.
What is asserted here is everything *around* it — what the processor reads, what
it refuses, what it builds, and above all what it never touches.

The claim that runs through the file: **the raw original and the canonical text
are two different things, and neither is allowed to become the other.** The
segment is derived and normalized; the original is the exact submitted bytes and
stays retrievable through the content object's original asset. A test that finds
markup in a segment, or extracted text in raw storage, is finding a bug.
"""

import uuid

import pytest

from core.contracts import (
    AssetRole,
    CapturePayloadType,
    CaptureRecord,
    CaptureSource,
    CaptureSourceType,
    ContentType,
    ProcessingStatus,
    ProvenanceSourceType,
    RawObjectRef,
    SegmentType,
)
from core.processing import TextProcessor
from core.processing.errors import ProcessingInputError, TextDecodingError
from core.processing.webpage import DEFAULT_HTML_MIME_TYPE, WebpageProcessor
from core.storage import RawObjectNotFoundError
from tests.unit.processing.builders import make_capture, store_and_capture
from tests.unit.processing.doubles import InMemoryRawObjectStore

PAGE = (
    "<!doctype html>\n"
    "<html>\n"
    "  <head>\n"
    "    <title>Example &amp; Test</title>\n"
    "    <style>.x { display: none; }</style>\n"
    "  </head>\n"
    "  <body>\n"
    "    <main>\n"
    "      <h1>Hello</h1>\n"
    "      <p>First <strong>paragraph</strong>.</p>\n"
    '      <script>window.secret = "not content"</script>\n'
    "      <p>Second&nbsp;paragraph.</p>\n"
    "    </main>\n"
    "  </body>\n"
    "</html>\n"
)

#: What ``PAGE`` extracts to. The no-break space is written as an escape on
#: purpose: ``&nbsp;`` is a character the page asked for, so it survives, and a
#: literal one here would make this assertion unreadable rather than exact.
EXPECTED_TEXT = "Hello\n\nFirst paragraph.\n\nSecond\u00a0paragraph."


@pytest.fixture
def processor(store: InMemoryRawObjectStore) -> WebpageProcessor:
    """A webpage processor reading through the in-memory store."""
    return WebpageProcessor(store)


def webpage_capture(
    store: InMemoryRawObjectStore,
    html: str = PAGE,
    *,
    mime_type: str | None = "text/html",
    **overrides: object,
) -> CaptureRecord:
    """Store HTML and build the webpage capture that points at it."""
    fields: dict[str, object] = {
        "id": "cap_web_01",
        "payload_type": CapturePayloadType.WEBPAGE,
    }
    return store_and_capture(
        store,
        html.encode("utf-8"),
        mime_type=mime_type,
        **(fields | overrides),
    )


class TestCapability:
    def test_it_supports_webpage_captures(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert processor.supports(webpage_capture(store)) is True

    @pytest.mark.parametrize(
        "payload_type",
        [t for t in CapturePayloadType if t is not CapturePayloadType.WEBPAGE],
        ids=lambda t: t.value,
    )
    def test_it_supports_nothing_else(
        self, processor: WebpageProcessor, payload_type: CapturePayloadType
    ) -> None:
        assert processor.supports(make_capture(payload_type=payload_type)) is False

    def test_text_captures_are_explicitly_not_supported(self, processor: WebpageProcessor) -> None:
        assert processor.supports(make_capture(payload_type=CapturePayloadType.TEXT)) is False

    def test_the_text_processor_does_not_claim_webpages(
        self, store: InMemoryRawObjectStore
    ) -> None:
        """The other half of exactly-one-match routing, asserted from here too."""
        assert TextProcessor(store).supports(webpage_capture(store)) is False

    def test_supports_touches_no_storage(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        store.accesses.clear()

        processor.supports(capture)

        assert store.accesses == []

    def test_supports_answers_even_with_no_raw_object(self, processor: WebpageProcessor) -> None:
        """A capture it handles but cannot read is still a capture it handles."""
        capture = make_capture(payload_type=CapturePayloadType.WEBPAGE, raw_object=None)

        assert processor.supports(capture) is True

    def test_supports_does_not_mutate_the_capture(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        before = capture.model_dump_json()

        processor.supports(capture)

        assert capture.model_dump_json() == before


class TestIdentity:
    def test_the_processor_is_named_webpage(self) -> None:
        assert WebpageProcessor.name == "webpage"

    def test_the_version_is_0_1(self) -> None:
        assert WebpageProcessor.version == "0.1"

    def test_the_text_processor_is_untouched(self) -> None:
        """Phase 2 added a processor; it did not revise the existing one."""
        assert TextProcessor.name == "text"
        assert TextProcessor.version == "0.2"


class TestUnusableInput:
    def test_no_raw_object_is_a_processing_input_error(self, processor: WebpageProcessor) -> None:
        capture = make_capture(payload_type=CapturePayloadType.WEBPAGE, raw_object=None)

        with pytest.raises(ProcessingInputError, match="no raw object"):
            processor.process(capture)

    def test_a_reference_without_a_ref_is_a_processing_input_error(
        self, processor: WebpageProcessor
    ) -> None:
        capture = make_capture(
            payload_type=CapturePayloadType.WEBPAGE,
            raw_object=RawObjectRef(id="raw_01", mime_type="text/html", sha256=None, ref=None),
        )

        with pytest.raises(ProcessingInputError, match="without a storage reference"):
            processor.process(capture)

    def test_both_preconditions_are_settled_before_any_io(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = make_capture(payload_type=CapturePayloadType.WEBPAGE, raw_object=None)

        with pytest.raises(ProcessingInputError):
            processor.process(capture)

        assert store.accesses == []

    def test_a_missing_stored_object_propagates_the_storage_error(
        self, processor: WebpageProcessor
    ) -> None:
        """Not translated, not flattened: the store's own type reaches the caller."""
        capture = make_capture(
            payload_type=CapturePayloadType.WEBPAGE,
            raw_object=RawObjectRef(
                id="raw_missing",
                mime_type="text/html",
                sha256=None,
                ref="sha256:" + "0" * 64,
            ),
        )

        with pytest.raises(RawObjectNotFoundError):
            processor.process(capture)

    def test_invalid_utf8_is_the_shared_decoding_error(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """One decoding category for the whole system, not a second hierarchy."""
        capture = store_and_capture(
            store,
            b"<p>\xff\xfe not utf-8</p>",
            mime_type="text/html",
            payload_type=CapturePayloadType.WEBPAGE,
        )

        with pytest.raises(TextDecodingError, match="not valid utf-8"):
            processor.process(capture)

    def test_no_alternative_encoding_is_attempted(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Valid Windows-1251 that is not valid UTF-8 fails rather than being guessed."""
        capture = store_and_capture(
            store,
            "<p>Привет</p>".encode("windows-1251"),
            mime_type="text/html",
            payload_type=CapturePayloadType.WEBPAGE,
        )

        with pytest.raises(TextDecodingError):
            processor.process(capture)

    def test_a_meta_charset_does_not_change_the_decoding(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``<meta charset>`` is page markup, not a transport fact this may act on."""
        capture = store_and_capture(
            store,
            b'<meta charset="windows-1251"><p>' + "Привет".encode("windows-1251"),
            mime_type="text/html",
            payload_type=CapturePayloadType.WEBPAGE,
        )

        with pytest.raises(TextDecodingError):
            processor.process(capture)


class TestPagesWithNoExtractableText:
    """A page that yields nothing is a failure, never a successful empty capture."""

    @pytest.mark.parametrize(
        ("label", "html"),
        [
            ("script only", '<script>window.x = "not content"</script>'),
            ("style only", "<style>body { color: red; }</style>"),
            ("empty body", "<html><body></body></html>"),
            ("whitespace body", "<html><body>   \n\t   </body></html>"),
            ("comments only", "<!-- a comment --><!-- another -->"),
            ("head only", "<html><head><title>Only A Title</title></head></html>"),
            ("noscript only", "<noscript>enable javascript</noscript>"),
            ("template only", "<template><p>held back</p></template>"),
            ("svg only", "<svg><text>label</text></svg>"),
            ("empty blocks", "<div></div><p></p>"),
        ],
        ids=lambda value: value if isinstance(value, str) and "<" not in value else None,
    )
    def test_it_raises_processing_input_error(
        self,
        processor: WebpageProcessor,
        store: InMemoryRawObjectStore,
        label: str,
        html: str,
    ) -> None:
        capture = webpage_capture(store, html)

        with pytest.raises(ProcessingInputError, match="no extractable page text"):
            processor.process(capture)

    def test_no_content_object_is_fabricated(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """An empty segment reported COMPLETE would be the system lying about memory."""
        capture = webpage_capture(store, "<script>x</script>")

        with pytest.raises(ProcessingInputError):
            processor.process(capture)

    def test_a_page_with_a_title_but_no_body_text_still_fails(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A title is not content. A content object needs a segment."""
        capture = webpage_capture(store, "<html><head><title>Just A Title</title></head></html>")

        with pytest.raises(ProcessingInputError):
            processor.process(capture)

    def test_the_error_does_not_echo_the_page(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, '<script>var secret = "s3cr3t"</script>')

        with pytest.raises(ProcessingInputError) as raised:
            processor.process(capture)

        assert "s3cr3t" not in str(raised.value)
        assert "<script>" not in str(raised.value)


class TestTheContentObject:
    def test_the_type_is_web(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert processor.process(webpage_capture(store)).type is ContentType.WEB

    def test_the_source_names_the_capture(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store))

        assert content.source.capture_id == "cap_web_01"
        assert content.source.provider == "cli"
        assert content.source.url == "https://example.com/notes"

    def test_an_absent_provider_and_url_stay_absent(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(
            store, source=CaptureSource(type=CaptureSourceType.BROWSER, provider=None, url=None)
        )
        content = processor.process(capture)

        assert content.source.provider is None
        assert content.source.url is None

    def test_there_is_exactly_one_segment(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        assert len(processor.process(webpage_capture(store)).segments) == 1

    def test_the_segment_is_the_extracted_text(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (segment,) = processor.process(webpage_capture(store)).segments

        assert segment.type is SegmentType.TEXT
        assert segment.text == EXPECTED_TEXT
        assert segment.position == 0

    def test_the_segment_holds_no_markup(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (segment,) = processor.process(webpage_capture(store)).segments
        assert segment.text is not None

        for fragment in ("<p>", "<h1>", "<script", "display: none", "window.secret", "&amp;"):
            assert fragment not in segment.text

    def test_there_is_exactly_one_asset_and_it_is_the_original(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (asset,) = processor.process(webpage_capture(store)).assets

        assert asset.role is AssetRole.ORIGINAL

    def test_the_asset_points_at_the_raw_reference(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        (asset,) = processor.process(capture).assets

        assert capture.raw_object is not None
        assert asset.ref == capture.raw_object.ref
        assert asset.sha256 == capture.raw_object.sha256

    def test_the_original_reference_carries_the_digest(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        content = processor.process(capture)

        assert capture.raw_object is not None
        assert content.original.sha256 == capture.raw_object.sha256
        assert content.original.asset_id == content.assets[0].id

    def test_the_exact_original_is_still_reachable_from_the_content(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The point of the asset: the submitted markup is one hop away, unchanged."""
        capture = webpage_capture(store)
        content = processor.process(capture)
        (asset,) = content.assets

        recovered = store.read_bytes(
            RawObjectRef(id=asset.id, mime_type=asset.mime_type, sha256=asset.sha256, ref=asset.ref)
        )

        assert recovered == PAGE.encode("utf-8")

    def test_one_processing_record_reports_success(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (record,) = processor.process(webpage_capture(store)).processing

        assert record.processor == "webpage"
        assert record.processor_version == "0.1"
        assert record.status is ProcessingStatus.COMPLETE
        assert record.completed_at is not None
        assert record.started_at <= record.completed_at

    def test_nothing_is_derived(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """No summary, topics, or entities: this processor runs no analysis."""
        derived = processor.process(webpage_capture(store)).derived

        assert derived.summary is None
        assert derived.topics == []
        assert derived.entities == []


class TestProvenance:
    def test_the_source_type_is_html(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Not ``ORIGINAL``: the text was derived *from* the HTML, and that is said."""
        (segment,) = processor.process(webpage_capture(store)).segments

        assert segment.provenance.source_type is ProvenanceSourceType.HTML

    def test_it_points_at_the_original_asset(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store))
        (segment,) = content.segments
        (asset,) = content.assets

        assert segment.provenance.asset_id == asset.id

    def test_it_names_the_capture(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (segment,) = processor.process(webpage_capture(store)).segments

        assert segment.provenance.capture_id == "cap_web_01"

    def test_it_names_the_processor_and_version(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        (segment,) = processor.process(webpage_capture(store)).segments

        assert segment.provenance.processor == "webpage"
        assert segment.provenance.processor_version == "0.1"


class TestMime:
    def test_the_declared_type_reaches_the_asset_and_the_original(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store, mime_type="text/html"))

        assert content.assets[0].mime_type == "text/html"
        assert content.original.mime_type == "text/html"

    def test_an_unusual_declared_type_is_not_corrected(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store, mime_type="application/xhtml+xml"))

        assert content.assets[0].mime_type == "application/xhtml+xml"
        assert content.original.mime_type == "application/xhtml+xml"

    def test_the_fallback_applies_only_when_none_was_declared(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store, mime_type=None))

        assert content.assets[0].mime_type == DEFAULT_HTML_MIME_TYPE
        assert DEFAULT_HTML_MIME_TYPE == "text/html"

    def test_the_fallback_is_never_written_back_onto_the_original(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """A capture that declared no MIME type still looks like one that did not."""
        content = processor.process(webpage_capture(store, mime_type=None))

        assert content.original.mime_type is None

    def test_the_fallback_is_never_written_back_onto_the_capture(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, mime_type=None)

        processor.process(capture)

        assert capture.raw_object is not None
        assert capture.raw_object.mime_type is None


class TestTitlePrecedence:
    """Submitted capture title, then HTML ``<title>``, then nothing."""

    def test_the_submitted_title_wins(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store, title="What I Called It"))

        assert content.title == "What I Called It"

    def test_the_submitted_title_is_preserved_exactly(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Not trimmed, case-folded, or normalized — the submitter's own words."""
        submitted = "  A Título — with  spacing  "
        content = processor.process(webpage_capture(store, title=submitted))

        assert content.title == submitted

    def test_the_html_title_is_used_when_none_was_submitted(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store, title=None))

        assert content.title == "Example & Test"

    def test_the_html_title_entity_is_decoded(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<title>A &amp; B</title><p>body</p>", title=None)

        assert processor.process(capture).title == "A & B"

    def test_a_blank_html_title_leaves_no_title(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<title>   </title><p>body</p>", title=None)

        assert processor.process(capture).title is None

    def test_no_title_anywhere_leaves_no_title(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<p>body only</p>", title=None)

        assert processor.process(capture).title is None

    def test_the_first_nonblank_of_several_titles_is_used(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(
            store,
            "<title>  </title><title>First Real</title><title>Second</title><p>b</p>",
            title=None,
        )

        assert processor.process(capture).title == "First Real"

    def test_an_h1_is_never_the_fallback(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<h1>A Heading</h1><p>body</p>", title=None)

        assert processor.process(capture).title is None

    def test_the_url_is_never_the_fallback(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<p>body</p>", title=None)
        content = processor.process(capture)

        assert content.source.url == "https://example.com/notes"
        assert content.title is None

    def test_the_first_line_of_content_is_never_the_fallback(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, "<p>The opening sentence.</p>", title=None)

        assert processor.process(capture).title is None

    def test_the_extracted_title_never_reaches_the_capture_record(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """``CaptureRecord.title`` means what the submitter provided. It stays that."""
        capture = webpage_capture(store, title=None)

        content = processor.process(capture)

        assert content.title == "Example & Test"
        assert capture.title is None

    def test_a_submitted_title_on_the_record_is_not_overwritten(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store, title="Submitted")

        processor.process(capture)

        assert capture.title == "Submitted"


class TestIdentityOfProducedObjects:
    def test_every_id_is_a_fresh_uuid(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store))
        (segment,) = content.segments
        (asset,) = content.assets

        for identifier in (content.id, segment.id, asset.id):
            assert uuid.UUID(identifier).version == 4

    def test_no_id_is_derived_from_the_raw_digest(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """The SHA-256 addresses bytes. It never becomes content identity."""
        capture = webpage_capture(store)
        content = processor.process(capture)

        assert capture.raw_object is not None
        digest = capture.raw_object.sha256
        assert digest is not None
        for identifier in (content.id, content.segments[0].id, content.assets[0].id):
            assert identifier != digest
            assert digest not in identifier

    def test_no_id_is_the_capture_id(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        content = processor.process(webpage_capture(store))

        assert content.id != "cap_web_01"
        assert content.segments[0].id != "cap_web_01"
        assert content.assets[0].id != "cap_web_01"

    def test_the_same_html_under_two_captures_yields_distinct_objects(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        first = processor.process(webpage_capture(store, id="cap_web_a"))
        second = processor.process(webpage_capture(store, id="cap_web_b"))

        assert first.id != second.id
        assert first.segments[0].id != second.segments[0].id
        assert first.assets[0].id != second.assets[0].id

    def test_but_they_share_one_deduplicated_original(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        first = processor.process(webpage_capture(store, id="cap_web_a"))
        second = processor.process(webpage_capture(store, id="cap_web_b"))

        assert first.assets[0].ref == second.assets[0].ref
        assert first.original.sha256 == second.original.sha256

    def test_and_carry_the_same_extracted_text(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        """Extraction is deterministic; only identity differs."""
        first = processor.process(webpage_capture(store, id="cap_web_a"))
        second = processor.process(webpage_capture(store, id="cap_web_b"))

        assert first.segments[0].text == second.segments[0].text


class TestTheProcessorWritesNothing:
    def test_the_capture_record_is_not_mutated(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        before = capture.model_dump_json()

        processor.process(capture)

        assert capture.model_dump_json() == before

    def test_the_lifecycle_status_is_not_advanced(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        status = capture.status

        processor.process(capture)

        assert capture.status is status

    def test_the_raw_original_is_only_read(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)
        store.accesses.clear()

        processor.process(capture)

        assert store.accesses == ["read_bytes"]

    def test_the_original_bytes_are_unchanged_afterwards(
        self, processor: WebpageProcessor, store: InMemoryRawObjectStore
    ) -> None:
        capture = webpage_capture(store)

        processor.process(capture)

        assert capture.raw_object is not None
        assert store.read_bytes(capture.raw_object) == PAGE.encode("utf-8")
