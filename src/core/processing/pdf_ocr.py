"""Normalization of PDF document captures in an OCR-enabled deployment.

The fifth processor, and the first that is *optional*. It is not registered
unless the deployment explicitly asked for it, and when it is registered it
takes :class:`~core.processing.pdf.PdfProcessor`'s place rather than standing
beside it — both claim ``application/pdf``, and the router's exactly-one-match
rule means a deployment that registered both would be told, loudly, that it is
wired wrong. Which of the two runs is a *composition* decision made where the
application is assembled; it is not a request field, a MIME type, a capture
intent, a header, or a router precedence rule, and no client can reach it. See
`ADR-018 <../../docs/ADR/ADR-018-opt-in-local-pdf-ocr.md>`_.

**Embedded text first, always.** A PDF page that carries its own text is read
with the ordinary parser and emitted exactly as
:class:`~core.processing.pdf.PdfProcessor` would emit it — the same string, the
same ``TEXT`` segment type, the same ``ORIGINAL`` provenance. Recognition is
only ever applied to the pages that have nothing to read, and a page that was
covered by embedded text is never rasterized at all. Extraction is not
improved, second-guessed, or scored against a recognizer; text a document
carries outranks text a model inferred from pixels, and that ordering is fixed.

**Extraction and recognition stay distinguishable forever.** A page read from
the document's own text stream becomes a ``TEXT`` segment with
``ProvenanceSourceType.ORIGINAL``. A page read from its pixels becomes an
``OCR`` segment with ``ProvenanceSourceType.OCR``. Those two facts are carried
on every segment, so a consumer can always tell which is which, filter on it,
or weight it — and nothing downstream has to trust that this build got the
recognition right in order to trust the pages it did not need to recognize.

**The deliberate limitation, stated plainly.** A page with *any* nonblank
embedded text is treated as embedded-text-covered, whole. If such a page also
contains a photograph of a sign, a scanned figure with a caption, or a stamped
signature block, the words in those images are not read and no segment reports
them. There is no region-level OCR here, no attempt to assess whether an
existing text layer is good or complete, and no blending of extracted and
recognized text on one page. That is a real gap, it is chosen rather than
overlooked — mixing two sources on one page needs a layout model and a conflict
policy that this build does not have — and it is the reason a ``COMPLETE``
document from this processor must never be read as "every visible word was
captured".

**Nothing here rasterizes, recognizes, or executes anything.** This module
imports no native library, no imaging package, and no ``subprocess``; it holds
a :class:`~core.processing.ocr.PdfPageOcr` port and reads the original through
a :class:`~core.storage.raw.RawObjectStore`, exactly as the other processors
do. Page images are the adapter's temporary computation and are never persisted,
never referenced by an asset, and never returned across the port.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Final

from pydantic import JsonValue

from core.contracts import (
    Asset,
    AssetRole,
    CaptureRecord,
    ContentObject,
    ContentSource,
    ContentType,
    OriginalReference,
    ProcessingRecord,
    ProcessingStatus,
    Provenance,
    ProvenanceSourceType,
    RawObjectRef,
    Segment,
    SegmentType,
    SpatialLocation,
)
from core.contracts.base import JsonMapping
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError
from core.processing.ocr import PdfOcrResult, PdfPageOcr, validate_ocr_result
from core.processing.pdf import PDF_MIME_TYPE, extract_pages
from core.storage import RawObjectStore

#: The metadata key, on the content object and on every recognized segment,
#: under which this processor records what actually ran.
#:
#: One namespaced key rather than a scattering of top-level ones:
#: ``ContentObject.metadata`` is shared free-form space, and a processor that
#: spends six top-level names on itself is a processor that will collide with
#: the next one.
OCR_METADATA_KEY: Final = "pdf_ocr"

#: Physical pages whose text came from the document's own embedded text stream.
EMBEDDED_TEXT_PAGES_KEY: Final = "embedded_text_pages"

#: Physical pages that were rasterized and handed to the recognizer.
OCR_ATTEMPTED_PAGES_KEY: Final = "ocr_attempted_pages"

#: Physical pages the recognizer looked at and returned no nonblank text for.
#:
#: Deliberately *not* named "blank pages". An empty recognition result is
#: evidence about the recognizer, not a finding about the page: a faint scan, an
#: unsupported script, a rotated photograph, and a genuinely empty sheet all
#: land here and this build cannot tell them apart. Naming the key after what
#: was observed rather than after a conclusion is the difference between a
#: record and a claim.
OCR_PAGES_WITHOUT_TEXT_KEY: Final = "ocr_pages_without_text"

#: The document's actual physical page count, as the rasterizer counted it.
PAGE_COUNT_KEY: Final = "page_count"


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content, segment, and
    asset ids are explicitly *not* derived from the PDF's digest: that address
    identifies bytes, and two captures of the identical PDF — including the same
    scan submitted once before OCR was enabled and once after — are two
    different content objects holding two different asset records and two
    different sets of segments.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str) -> Asset:
    """Describe the immutable original PDF as an asset of the content object.

    Exactly one asset is ever created, and it is the submitted document. The
    page images this processor's recognition runs on are temporary computation:
    they are never stored, never given an asset record, and never referenced
    from a segment, so there is no such thing here as a raster asset whose
    reference outlives the bytes it named.

    This matters more in an OCR build than anywhere yet. Recognized text is the
    most *derived* content this system produces, and the original is the only
    place the document — its pixels, its layout, everything a recognizer read
    and everything it missed — still exists, for the better recognizer that
    comes later.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=PDF_MIME_TYPE,
        ref=ref,
        sha256=raw_object.sha256,
    )


def _page_list(pages: Iterable[int]) -> list[JsonValue]:
    """Physical page numbers, ascending, as a JSON-compatible list.

    ``ContentObject.metadata`` is declared over ``JsonValue`` so that every
    canonical object round-trips through JSON unchanged. Page numbers are
    ordinary integers; this exists only so the declared element type says so.
    """
    return [*sorted(pages)]


def _engine_metadata(result: PdfOcrResult) -> JsonMapping:
    """What actually ran, taken from the recognizer rather than assumed here.

    Every value is reported by the installed software at run time: the engine
    and rasterizer names, the versions they announce, and the effective
    recognition settings in force for this run. Nothing is a constant compiled
    into ``core``, because the point of recording it is to describe the run that
    happened rather than the run this module expected.

    There is deliberately no confidence score. The engine this build ships
    against is not asked for one, an invented number would be read as a
    measurement, and "how sure are you" is not a question a caller can get an
    honest answer to here.
    """
    return {
        "engine": result.engine,
        "engine_version": result.engine_version,
        "rasterizer": result.rasterizer,
        "rasterizer_version": result.rasterizer_version,
        "settings": dict(result.settings),
    }


class PdfOcrProcessor:
    """Normalizes staged ``application/pdf`` captures, recognizing unreadable pages."""

    name = "pdf-ocr"
    #: The first version of *these* semantics, which are not
    #: :class:`~core.processing.pdf.PdfProcessor`'s. A separate name and its own
    #: version is what keeps the two tellable apart on a ``Provenance`` and a
    #: ``ProcessingRecord`` years later: ``pdf@0.1`` says every segment came out
    #: of the document's text stream, and ``pdf-ocr@0.1`` says some of them may
    #: not have. Any change to the rules in this module's docstring — page
    #: policy, blankness, provenance, title precedence — changes this value.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore, ocr: PdfPageOcr) -> None:
        """Take the store the original is read through and the recognizer to use.

        Both are ports. The store is
        :class:`~core.storage.raw.RawObjectStore` and the recognizer is
        :class:`~core.processing.ocr.PdfPageOcr`, so this class works against
        any backend and any engine and reaches into neither. There is no
        default recognizer, no lazily-imported one, and no lookup: a deployment
        that wants recognition constructs an adapter and hands it over, which is
        the same thing as saying this processor cannot exist by accident.
        """
        self._raw_store = raw_store
        self._ocr = ocr

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged PDF documents and nothing else. Pure; touches no storage.

        Byte-for-byte the same three questions
        :class:`~core.processing.pdf.PdfProcessor` asks, and that is deliberate:
        this processor is the *other implementation of the same claim*, not a
        broader or narrower one. It does not sniff the leading bytes to guess
        whether a PDF is scanned, does not read storage to find out, and does
        not claim ``image/*`` or any other type. A deployment picks one of the
        two implementations; a capture never picks for itself.

        Two consequences follow, both intended. Registering both is an
        ``AmbiguousProcessorError`` rather than a silent preference, because
        registration order is not precedence and this overlap is a wiring
        mistake with two plausible resolutions. And a PDF whose bytes cannot be
        read is still a PDF capture this processor handles — whether the
        document parses is a ``process`` question, not a routing one.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.DOCUMENT
            and raw_object is not None
            and raw_object.mime_type == PDF_MIME_TYPE
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Extract, then recognize what extraction could not read, then combine.

        The sequence, in full::

            validate the capture, its raw reference, and its declared type
            open the original    -> extract_pages()   embedded text + /Title
            open the original    -> recognize_missing_pages()
            validate the recognizer's answer
            combine in physical page order

        **Extraction runs first and its failures are final.** An encrypted or
        malformed document raises
        :class:`~core.processing.errors.ProcessingInputError` out of
        :func:`~core.processing.pdf.extract_pages` before the recognizer is ever
        constructed a stream, so recognition is never attempted as a repair for
        a document the parser refused. A recognizer pointed at a file pypdf
        could not read would either fail the same way or — far worse — render
        *something* and report text for a document nobody could open, which is
        how an unreadable file turns into confident nonsense.

        **The recognizer gets its own stream.** The original is opened a second
        time rather than rewound, because "rewind whatever the parser left
        behind" is a promise about a third-party parser's use of a shared handle
        that this module cannot make. Two opens of an immutable
        content-addressed object are two reads of identical bytes.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not updated from the document's metadata,
        and the raw original is not modified.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError`, and that propagates
        unchanged, exactly as it does from the other four processors — and,
        importantly, still distinguishably from a
        :class:`~core.processing.ocr.PdfOcrExecutionError`, which also
        propagates unchanged and also leaves the capture non-terminal.
        """
        started_at = datetime.now(UTC)

        # All three preconditions are properties of the capture record itself,
        # so they are settled before any I/O. The MIME check repeats what
        # ``supports`` already asked, because ``process`` is callable directly
        # and must not rasterize a DOCX just because nobody routed the capture
        # first.
        raw_object = capture.raw_object
        if raw_object is None:
            raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
        if raw_object.ref is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references raw object {raw_object.id!r} "
                f"without a storage reference"
            )
        if raw_object.mime_type != PDF_MIME_TYPE:
            raise ProcessingInputError(
                f"capture {capture.id!r} references a raw object that is not declared "
                f"{PDF_MIME_TYPE}; this processor reads PDF documents only"
            )

        # The existing extractor, called as the public function it already is.
        # Not a copy of it, not a private helper of it, and not a variant with
        # OCR-shaped tweaks: an embedded-text page must reach a segment through
        # exactly the code path it reaches one through in the default build, or
        # "enabling OCR changes nothing about pages that already worked" would
        # be an aspiration rather than a fact.
        with self._raw_store.open(raw_object) as handle:
            extracted, metadata_title = extract_pages(handle)

        embedded = dict(extracted)
        embedded_pages = frozenset(embedded)

        with self._raw_store.open(raw_object) as handle:
            recognized = self._ocr.recognize_missing_pages(handle, embedded_pages=embedded_pages)
        validate_ocr_result(recognized, embedded_pages=embedded_pages)

        original = _original_asset(raw_object, raw_object.ref)
        segments, attempted, without_text = self._build_segments(
            capture, original, embedded, recognized
        )

        if not segments:
            # Extraction found no embedded text and recognition found no words
            # in any of the pixels it was shown. The document is still exactly
            # as submitted in raw storage, and the honest answer is that this
            # build read nothing from it — not a ``COMPLETE`` content object
            # with no segments, which would claim the document was remembered
            # and say nothing about the fact that it was not.
            raise ProcessingInputError(
                f"the document of capture {capture.id!r} yielded no text: it carries no "
                f"extractable embedded text and recognition returned nothing for any of its "
                f"pages"
            )

        completed_at = datetime.now(UTC)

        return ContentObject(
            id=_new_id(),
            type=ContentType.DOCUMENT,
            source=ContentSource(
                capture_id=capture.id,
                provider=capture.source.provider,
                url=capture.source.url,
            ),
            original=OriginalReference(
                asset_id=original.id,
                mime_type=raw_object.mime_type,
                sha256=raw_object.sha256,
            ),
            # Unchanged from the default build, and specifically *not* extended
            # with a recognized source:
            #
            #   1. the submitted capture title, exactly as given;
            #   2. otherwise a nonblank ``/Title`` in the PDF's metadata;
            #   3. otherwise none.
            #
            # A title read off the first page of a scan is a guess about what a
            # document is called, dressed as a fact, and it would be
            # indistinguishable downstream from a title the document actually
            # carried. Not from OCR text, not from the filename, not from the
            # digest, not from the URL.
            title=capture.title if capture.title is not None else metadata_title,
            metadata={
                OCR_METADATA_KEY: {
                    PAGE_COUNT_KEY: recognized.page_count,
                    EMBEDDED_TEXT_PAGES_KEY: _page_list(embedded_pages),
                    OCR_ATTEMPTED_PAGES_KEY: _page_list(attempted),
                    OCR_PAGES_WITHOUT_TEXT_KEY: _page_list(without_text),
                    **_engine_metadata(recognized),
                }
            },
            segments=segments,
            assets=[original],
            processing=[
                # ``ProcessingRecord`` has no metadata field and is not given
                # one here: it records *that* ``pdf-ocr@0.1`` ran and how it
                # went, and the description of what ran belongs to the content.
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=ProcessingStatus.COMPLETE,
                )
            ],
        )

    def _build_segments(
        self,
        capture: CaptureRecord,
        original: Asset,
        embedded: dict[int, str],
        recognized: PdfOcrResult,
    ) -> tuple[list[Segment], list[int], list[int]]:
        """Walk the document in physical page order and emit at most one segment a page.

        Returns the segments together with the pages recognition was attempted
        on and the pages it returned nothing usable for, which are the two facts
        the content object's metadata records.

        The per-page rule, which is the whole policy of this processor:

        * **Nonblank embedded text** — emit it as ``TEXT`` with ``ORIGINAL``
          provenance, the parser's string untouched. The recognizer was never
          shown this page.
        * **No embedded text, nonblank recognition** — emit the engine's string,
          untouched, as ``OCR`` with ``OCR`` provenance.
        * **No embedded text, blank or empty recognition** — emit nothing. The
          page is recorded in the metadata as looked-at-and-empty rather than
          asserted to be blank.

        ``strip`` appears twice and decides blankness both times. It never
        rewrites a stored string: leading indentation, trailing newlines, and
        internal whitespace are what the parser or the engine produced. There is
        no Unicode normalization, no line-ending rewriting, no hyphen repair, no
        paragraph joining, no spell correction, no de-columnizing, and no model
        anywhere near the text. An engine that returns ``"Th1s"`` gets ``"Th1s"``
        stored; correcting it would be inventing a reading nobody can audit.

        ``position`` counts only the pages that produced a segment, so it is
        contiguous from zero; ``spatial.page`` is the physical page, so it has
        gaps wherever a page produced nothing. Those are two different facts and
        this is where they part company.
        """
        recognized_text = {page.page: page.text for page in recognized.pages}
        engine = {
            "engine": recognized.engine,
            "engine_version": recognized.engine_version,
        }

        segments: list[Segment] = []
        attempted: list[int] = []
        without_text: list[int] = []

        for page_number in range(1, recognized.page_count + 1):
            embedded_text = embedded.get(page_number)
            if embedded_text is not None:
                segments.append(
                    self._segment(
                        capture,
                        original,
                        page_number=page_number,
                        position=len(segments),
                        text=embedded_text,
                        segment_type=SegmentType.TEXT,
                        source_type=ProvenanceSourceType.ORIGINAL,
                        metadata={},
                    )
                )
                continue

            attempted.append(page_number)
            # ``validate_ocr_result`` has already established that every
            # non-embedded page has exactly one result, so this lookup cannot
            # miss. It is written as a lookup rather than as a ``pop`` from a
            # shrinking dict so that the loop reads as "what does page N say"
            # rather than as bookkeeping.
            page_text = recognized_text[page_number]
            if not page_text.strip():
                without_text.append(page_number)
                continue

            segments.append(
                self._segment(
                    capture,
                    original,
                    page_number=page_number,
                    position=len(segments),
                    text=page_text,
                    segment_type=SegmentType.OCR,
                    source_type=ProvenanceSourceType.OCR,
                    # Each recognized segment names the engine that produced it.
                    # The full settings block lives once on the content object;
                    # repeating it on every page would be the same paragraph of
                    # configuration copied a hundred times, and the identity is
                    # the part a reader of one segment actually needs.
                    metadata={OCR_METADATA_KEY: dict(engine)},
                )
            )

        return segments, attempted, without_text

    def _segment(
        self,
        capture: CaptureRecord,
        original: Asset,
        *,
        page_number: int,
        position: int,
        text: str,
        segment_type: SegmentType,
        source_type: ProvenanceSourceType,
        metadata: JsonMapping,
    ) -> Segment:
        """One canonical segment for one physical page.

        Every segment built here — extracted or recognized — points at *this*
        capture and at the one original PDF asset, carries the physical page it
        came from, and is stamped ``pdf-ocr@0.1``. The only things that differ
        between the two kinds are the segment type, the provenance source type,
        and the metadata, which is exactly the distinction worth being able to
        see.

        ``provenance.asset_id`` is the original PDF for a recognized page too.
        That is deliberate and it is not a dangling reference: the page image
        the engine read was derived from that PDF at that page, the PDF is the
        only artifact that still exists, and ``spatial.page`` says which part of
        it. Minting an asset id for a bitmap that was freed before this function
        ran would be a pointer to nothing.
        """
        return Segment(
            id=_new_id(),
            type=segment_type,
            text=text,
            spatial=SpatialLocation(page=page_number),
            provenance=Provenance(
                capture_id=capture.id,
                source_type=source_type,
                asset_id=original.id,
                processor=self.name,
                processor_version=self.version,
            ),
            metadata=metadata,
            position=position,
        )
