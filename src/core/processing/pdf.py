"""Normalization of PDF-backed document captures.

The third processor, and the first whose original never arrived inside a
``CaptureEnvelope``. Text and HTML are strings: a client can put either into
JSON and intake can write its UTF-8 encoding. A PDF is binary, so its bytes were
staged as an immutable raw object *before* the capture existed, and the envelope
carried only the reference. By the time this processor runs that distinction has
already been settled — it reads the original through the raw object store
exactly as the other two do — but it is why this modality needed an acquisition
step and the others did not.

**What it produces is page-aware canonical text.** One nonblank PDF page becomes
one canonical ``Segment``, carrying the page's embedded text and a
``SpatialLocation`` naming the physical page it came from. The immutable PDF
stays exactly as it was submitted and remains retrievable through the content
object's original asset, so a better extractor later can re-read precisely what
this one saw.

**This is embedded-text extraction, not document understanding.** It reads the
text a PDF already carries and does nothing else with it. There is no OCR, no
layout reconstruction, no column detection, no table structure, no reading-order
inference beyond the order the parser reports, no hyphen repair, no header or
footer removal, no form interpretation, no annotation or attachment extraction,
and no image handling. A PDF that carries no embedded text — a scan, a
photograph of a page — is a *failure* here rather than an empty success, because
the one thing a memory system must never do is report that it remembered
something when it remembered nothing.

**Nothing is executed, fetched, or opened.** A PDF may reference external files,
carry JavaScript, or embed other documents. None of that is followed: the only
input is the byte stream the raw object store hands over, and there is no
network and no subprocess anywhere in this module.
"""

import uuid
from datetime import UTC, datetime
from typing import BinaryIO, Final

from pypdf import PdfReader
from pypdf.errors import PyPdfError

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
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectStore

#: The one document format this processor handles, matched exactly against the
#: MIME type the submitter declared and intake recorded on the raw reference.
#:
#: Deliberately an exact match rather than a prefix, a suffix, or a sniff of the
#: leading bytes. ``supports`` must stay pure and storage-free, so the only
#: thing it can consult is what the capture record says — and a capture that
#: says ``application/pdf`` is the capture this processor claims. A DOCX or
#: EPUB processor will claim its own MIME type, and the router's
#: exactly-one-match rule then keeps them apart without either knowing the
#: other exists.
PDF_MIME_TYPE: Final = "application/pdf"


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content, segment, and
    asset ids are explicitly *not* derived from the PDF's digest: that address
    identifies bytes, and two captures of the identical PDF are two different
    content objects holding two different asset records and two different sets
    of segments.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str) -> Asset:
    """Describe the immutable original PDF as an asset of the content object.

    This is what keeps the exact submitted PDF retrievable from the content
    object alone, and it matters more here than anywhere yet: every segment is a
    *derived* extraction, and the original is the only place the document — its
    layout, its images, its fonts, everything this processor does not read —
    still exists.

    ``Asset.mime_type`` needs no fallback on this path. ``supports`` already
    required the declared MIME type to be exactly ``application/pdf``, so unlike
    the text and HTML processors there is no "the submitter declared nothing"
    case to invent a value for.

    The asset id is minted fresh, like every other id here. An asset is a record
    *within one content object* that points at an original; it is not the
    original. The raw object's identity travels on ``ref`` and ``sha256``.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=PDF_MIME_TYPE,
        ref=ref,
        sha256=raw_object.sha256,
    )


def _metadata_title(reader: PdfReader) -> str | None:
    """The document's own ``/Title``, if it actually says something.

    Three things it deliberately is not. It is not trimmed: like page text, the
    stored value is what the document carries, and ``strip`` is used to *decide*
    whether the field is blank rather than to rewrite it. It is not Unicode
    normalized. And it is not consulted at all unless it is genuinely a string —
    a ``/Title`` holding a number, an array, or a reference to something else is
    a malformed document's idea of a title, not one worth adopting.
    """
    metadata = reader.metadata
    if metadata is None:
        return None
    title = metadata.title
    if not isinstance(title, str) or not title.strip():
        return None
    return title


def extract_pages(stream: BinaryIO) -> tuple[list[tuple[int, str]], str | None]:
    """Extract ``([(page number, text)], metadata title)`` from a PDF stream.

    A module-level function because extraction is worth reasoning about — and
    testing — with no capture, no store, and no content object anywhere near it.
    It reads the stream and nothing else; it opens no path and fetches no URL.

    The semantics, in full:

    * **Encrypted documents are refused, and no password is tried.** The
      supported contract is "unencrypted PDF". ``is_encrypted`` is consulted
      *before* any page is touched, which matters because ``pypdf`` will
      transparently decrypt a document whose user password is empty — so
      without this check an empty-password PDF would quietly succeed while a
      real-password one failed, and "unencrypted" would mean two different
      things depending on how the document was locked.
    * **Pages are read in physical PDF order**, and each page's text is whatever
      the parser's ordinary extraction returns.
    * **A blank page emits nothing.** ``None``, empty, and whitespace-only text
      are all "this page carries no embedded text", and no segment is made for
      it. ``strip`` decides that question and nothing else — the *stored* text
      is the parser's string, untouched.

    Nothing is normalized on the way out: no Unicode normalization, no
    whitespace collapsing, no line-ending rewriting, no hyphen repair, no
    de-columnization, no paragraph joining, no header or footer removal. If the
    parser produced a trailing newline, the segment carries a trailing newline.

    Raises :class:`~core.processing.errors.ProcessingInputError` for a document
    that is encrypted, malformed, or otherwise unreadable as a PDF. Nothing
    else is caught: a ``TypeError`` from a bug in this module is a bug, not a
    verdict about someone's document.
    """
    try:
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise ProcessingInputError(
                "the document is encrypted; this build reads unencrypted PDFs only "
                "and does not attempt passwords"
            )
        pages = [
            (number, text)
            for number, page in enumerate(reader.pages, start=1)
            if (text := page.extract_text()) is not None and text.strip()
        ]
        return pages, _metadata_title(reader)
    except PyPdfError as exc:
        # ``PyPdfError`` is the parser's own base class, and every member of it
        # describes a document it could not read — a truncated stream, a missing
        # header, an object it could not resolve, a limit the file blew past.
        # Those are facts about the input. Deliberately no bare ``except``: a
        # programmer error in this module must not be dressed up as a bad PDF.
        #
        # The parser's message is not carried forward. It names byte offsets,
        # object numbers, and internal parser state, none of which a client can
        # act on and some of which describes this server rather than their file.
        raise ProcessingInputError(
            "the document could not be read as a PDF; it appears to be malformed or truncated"
        ) from exc


class PdfProcessor:
    """Normalizes staged ``application/pdf`` document captures into canonical content."""

    name = "pdf"
    #: The first version of these extraction semantics. ``version`` describes
    #: *what this processor produces*, so any change to the rules above — page
    #: selection, blankness, segment shape, title precedence — changes it, and
    #: the new value travels automatically onto every ``Provenance`` and
    #: ``ProcessingRecord`` emitted afterwards.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore) -> None:
        """Take the store the immutable original will be read through.

        The dependency is the :class:`~core.storage.raw.RawObjectStore` port, so
        the processor works against any backend and reaches into none of them.
        It is also the *only* dependency: no HTTP client, no fetcher, no OCR
        engine, no subprocess, and no filesystem access of its own.
        """
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged PDF documents and nothing else. Pure; touches no storage.

        Three facts, all read from the capture record: it is a ``document``, it
        has a raw original, and that original is declared ``application/pdf``.

        Deliberately *not* "all documents". A document processor that claimed
        the payload type alone would be claiming DOCX, EPUB, and every other
        format the day one of them arrives, and the router's exactly-one-match
        rule would then report an ambiguity that is really a design mistake
        here. Claiming one MIME type means the next document processor can be
        added without touching this one.

        Whether the bytes really are a readable PDF is a ``process`` question,
        not a routing one: answering it here would mean reading storage to
        route, and a PDF capture whose original cannot currently be read is
        still a PDF capture this processor handles.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.DOCUMENT
            and raw_object is not None
            and raw_object.mime_type == PDF_MIME_TYPE
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Read the original PDF, extract its pages, and build canonical content.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not updated with the one found in the
        document's metadata, and the raw original is not modified. Lifecycle is
        orchestration's business, and ``CaptureRecord.title`` means *the title
        the submitter provided* — an extracted one is canonical content, so it
        goes on the ``ContentObject`` and stops there.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError` — a missing object, a
        malformed reference — and that propagates unchanged, exactly as it does
        from the other two processors. Those describe the state of the store
        rather than of the capture, and a caller deciding whether to retry needs
        to tell them apart from a document that will never process.
        """
        started_at = datetime.now(UTC)

        # All three preconditions are properties of the capture record itself,
        # so they are settled before any I/O. The MIME check repeats what
        # ``supports`` already asked, because ``process`` is callable directly
        # and must not read a DOCX as though it were a PDF just because nobody
        # routed the capture first.
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

        # Streamed rather than read whole. A PDF is the first modality that is
        # routinely large, the port already offers a file handle, and the parser
        # wants to seek around the document rather than receive it all at once.
        with self._raw_store.open(raw_object) as handle:
            pages, metadata_title = extract_pages(handle)

        if not pages:
            # The document parsed perfectly well and carries no embedded text.
            # In practice this is a scan or an image-only export, and the honest
            # answer is that this build cannot read it — not a ``COMPLETE``
            # content object with no segments, which would claim the document
            # was remembered and say nothing about the fact that it was not. The
            # exact PDF stays in raw storage either way, so nothing submitted is
            # lost by refusing, and an OCR-capable build can process the very
            # same bytes later.
            raise ProcessingInputError(
                f"the document of capture {capture.id!r} contains no extractable embedded "
                f"text; it is most likely a scanned or image-only PDF, and this build does "
                f"not perform OCR"
            )

        original = _original_asset(raw_object, raw_object.ref)
        segments = [
            Segment(
                id=_new_id(),
                type=SegmentType.TEXT,
                # Exactly what the parser returned. Not stripped, not collapsed,
                # not normalized — ``strip`` was used to decide the page was not
                # blank and for nothing else.
                text=text,
                # Physical location in the document, which is a different fact
                # from canonical reading order below. A blank page leaves a gap
                # here and no gap there.
                spatial=SpatialLocation(page=page_number),
                provenance=Provenance(
                    capture_id=capture.id,
                    # ``ORIGINAL``, and specifically not ``OCR``: this text was
                    # embedded in the PDF and read straight out of it. The
                    # distinction is the whole reason a later OCR-capable build
                    # can be trusted — a consumer can tell text the document
                    # carried from text a model guessed at from pixels. There is
                    # no PDF-specific provenance member and there should not be
                    # one: the asset's MIME type already says what the original
                    # was.
                    source_type=ProvenanceSourceType.ORIGINAL,
                    asset_id=original.id,
                    processor=self.name,
                    processor_version=self.version,
                ),
                # Canonical reading order: contiguous from zero, counting only
                # the pages that produced a segment.
                position=position,
            )
            for position, (page_number, text) in enumerate(pages)
        ]
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
            # The same precedence rule the webpage processor established, with
            # the document's own metadata standing where the ``<title>`` stands:
            #
            #   1. the submitted capture title, exactly as given;
            #   2. otherwise a nonblank ``/Title`` in the PDF's metadata;
            #   3. otherwise none.
            #
            # A person who titled their capture said what they wanted it called,
            # and no extraction outranks that. The document's own metadata is
            # its claim about itself and is the obvious second. There is no
            # third source: not the uploaded filename, which is untrusted client
            # text this build never even records; not the ``file_ref`` or the
            # digest, which name bytes rather than a document; not the first
            # page's text or its largest heading, which invent a name the
            # document never carried.
            title=capture.title if capture.title is not None else metadata_title,
            segments=segments,
            assets=[original],
            processing=[
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=ProcessingStatus.COMPLETE,
                )
            ],
        )
