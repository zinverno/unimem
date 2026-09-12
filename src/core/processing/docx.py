"""Normalization of DOCX-backed document captures.

The fourth processor, and the second whose original never arrived inside a
``CaptureEnvelope``. It exists to prove something the previous phase asserted
but could not demonstrate: that binary acquisition was made *generic*. A DOCX
takes exactly the route a PDF takes — bytes staged through ``POST /v1/uploads``
into the same content-addressed raw store, a ``file_ref`` naming them, a
``DOCUMENT`` capture declaring what they mean — and nothing along that route had
to learn the word "docx". What this module adds is the last step only: reading
those staged bytes as an Office Open XML word-processing package.

**What it produces is body-ordered canonical text.** The main document body is
walked in source order, and each nonblank paragraph and each nonblank table row
becomes one canonical ``Segment``. Order is the fact being preserved: a table
sitting between two paragraphs produces segments between those two paragraphs'
segments, so the canonical reading order is the order the document was written
in.

**There are no page numbers here, and their absence is deliberate.** A PDF page
is a fact recorded in the file; a DOCX page is a *result* — of the fonts
installed, the printer driver, the page size, the renderer's line-breaking. Two
machines opening the same .docx can legitimately disagree about what is on page
four, so a page number computed here would be this server's opinion presented as
the document's property. ``Segment.spatial`` is therefore ``None`` on every
segment this processor emits, and nothing in this module counts, estimates, or
renders a page. See `ADR-017
<../../docs/ADR/ADR-017-docx-document-ingestion.md>`_.

**This is body-text extraction, not document understanding.** It reads the main
body's paragraphs and tables and does nothing else with them. Word's heading
styles are not interpreted — a ``Heading 1`` is a paragraph of text here, and
turning styles into a ``SECTION`` hierarchy is a design question waiting for a
downstream requirement rather than a guess to make now. Headers, footers,
footnotes, endnotes, comments, tracked-change history, text boxes, embedded
files, images, charts, and equations are all outside this build; a document
whose only text lives in them is a *failure* here rather than an empty success,
because the one thing a memory system must never do is report that it remembered
something when it remembered nothing.

**Nothing is executed, fetched, or opened.** A DOCX is a ZIP of XML parts, and
those parts may reference external relationships, remote images, or linked
objects. None of that is followed: the only input is the byte stream the raw
object store hands over, and there is no network, no subprocess, no macro
evaluation, and no filesystem access anywhere in this module.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import BinaryIO, Final
from zipfile import BadZipFile

from docx import Document
from docx.document import Document as DocxDocument
from docx.exceptions import PythonDocxError
from docx.opc.exceptions import OpcError
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml.etree import XMLSyntaxError

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
)
from core.contracts.base import JsonMapping
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError
from core.storage import RawObjectStore

#: The one document format this processor handles, matched exactly against the
#: MIME type the submitter declared and intake recorded on the raw reference.
#:
#: Modern Office Open XML only. ``application/msword`` (legacy ``.doc``), the
#: macro-enabled ``.docm`` type, ODT, and RTF are all different formats that a
#: reader of this one cannot read, and none of them is inferred, sniffed, or
#: accepted here. As with :mod:`core.processing.pdf`, the match is exact rather
#: than a prefix or a sniff of the leading bytes, because ``supports`` must stay
#: pure and storage-free: the only thing it can consult is what the capture
#: record says.
DOCX_MIME_TYPE: Final = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: The metadata key naming which kind of body block a segment came from.
BLOCK_METADATA_KEY: Final = "docx_block"

#: ``docx_block`` value for a segment made from a body paragraph.
PARAGRAPH_BLOCK: Final = "paragraph"

#: ``docx_block`` value for a segment made from one row of a body table.
TABLE_ROW_BLOCK: Final = "table_row"

#: What separates one cell's text from the next inside a table-row segment.
#:
#: This is a canonical *flattening boundary*, not a character claimed to have
#: appeared in the document. A table row is a two-dimensional thing and a
#: ``Segment`` carries a string, so the row has to be flattened somehow; a tab
#: is the least surprising, most machine-splittable choice, and saying so
#: explicitly is the difference between a documented convention and a silent
#: one. The ``docx_block``/``table_index``/``row_index`` metadata is what lets a
#: consumer tell a flattened row from a paragraph that merely contains tabs.
CELL_SEPARATOR: Final = "\t"

#: Failures that describe the *submitted document* rather than this code.
#:
#: The DOCX reader raises a wider and less tidy set than the PDF one, because a
#: DOCX is three layers deep — a ZIP container, an OPC package of parts and
#: relationships, and XML inside those parts — and each layer fails in its own
#: vocabulary:
#:
#: * ``BadZipFile`` — not a ZIP at all, or truncated. An encrypted Word document
#:   lands here too: Office packages a password-protected document inside a
#:   compound-file container rather than a ZIP, so the reader never reaches any
#:   OOXML to decrypt, which is exactly the refusal this build wants.
#: * ``KeyError`` — a ZIP that is missing a part the package requires, or a
#:   relationship that names nothing.
#: * ``ValueError`` — a well-formed OPC package that is not a *word-processing*
#:   one, such as an ``.xlsx`` renamed to ``.docx``.
#: * ``XMLSyntaxError`` — a part whose XML does not parse.
#: * ``PythonDocxError`` / ``OpcError`` — the reader's own base classes, whose
#:   members all describe something wrong with the package.
#:
#: Deliberately *not* a bare ``except``. Everything listed is a verdict about
#: someone's file; an ``AttributeError`` or a ``TypeError`` from a defect in this
#: module is a bug, and dressing it up as "your document is malformed" would
#: hide it and lie to the client at the same time. ``KeyError`` and
#: ``ValueError`` are the two broad names here, which is why the block they
#: guard is kept to parsing and iteration with no arithmetic, no lookups, and no
#: conversions of this module's own.
_PACKAGE_INPUT_ERRORS: Final = (
    BadZipFile,
    KeyError,
    ValueError,
    XMLSyntaxError,
    PythonDocxError,
    OpcError,
)


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content, segment, and
    asset ids are explicitly *not* derived from the document's digest: that
    address identifies bytes, and two captures of the identical DOCX are two
    different content objects holding two different asset records and two
    different sets of segments.
    """
    return str(uuid.uuid4())


@dataclass(frozen=True)
class DocxBlock:
    """One piece of body text, with the structural provenance of where it sat.

    Deliberately not a ``Segment``: extraction answers "what does the body say,
    in what order", and knows nothing about capture ids, assets, provenance, or
    positions. ``metadata`` is carried verbatim onto the segment built from it,
    which is what lets a consumer tell a flattened table row from a paragraph
    without the contract growing a field or an enum for the distinction.
    """

    text: str
    metadata: JsonMapping = field(default_factory=dict)


def _core_title(document: DocxDocument) -> str | None:
    """The document's own core-property ``title``, if it actually says something.

    Two things it deliberately is not. It is not trimmed: like paragraph text,
    the stored value is what the document carries, and ``strip`` is used to
    *decide* whether the field is blank rather than to rewrite it. And it is not
    Unicode normalized.

    A package with no ``dc:title`` at all reports an empty string here, which is
    the same answer as a title made only of spaces, and both mean the document
    did not name itself.
    """
    title = document.core_properties.title
    return title if title.strip() else None


def _table_blocks(table: Table, table_index: int) -> Iterator[DocxBlock]:
    """Flatten one body table into one block per nonblank row.

    Rows are visited in document order and each row's cells in column order.
    ``row_index`` counts every row in the table including the blank ones that
    emit nothing, so it stays a truthful coordinate into the document rather
    than a position in the output.

    **Cell strings are joined exactly as the reader returned them.** ``strip`` is
    used only to decide whether the whole row is blank; a cell whose text is
    padded keeps its padding, because a table cell's whitespace is the document's
    business and this build does not tidy documents.

    Merged cells are taken as the reader presents them: ``row.cells`` reports a
    horizontally merged cell once per grid column it spans, so a merged cell's
    text repeats across those positions. That is a known and documented
    limitation. Reconstructing a merge grid is real work with real ambiguities
    and no downstream requirement yet, and a half-done reconstruction would be
    worse than an honest flattening.
    """
    for row_index, row in enumerate(table.rows):
        cell_texts = [cell.text for cell in row.cells]
        if not any(text.strip() for text in cell_texts):
            continue
        yield DocxBlock(
            text=CELL_SEPARATOR.join(cell_texts),
            metadata={
                BLOCK_METADATA_KEY: TABLE_ROW_BLOCK,
                "table_index": table_index,
                "row_index": row_index,
            },
        )


def extract_blocks(stream: BinaryIO) -> tuple[list[DocxBlock], str | None]:
    """Extract ``([body blocks in order], core-properties title)`` from a DOCX stream.

    A module-level function because extraction is worth reasoning about — and
    testing — with no capture, no store, and no content object anywhere near it.
    It reads the stream and nothing else; it opens no path and fetches no URL.

    The semantics, in full:

    * **The main document body is walked in source order**, through the reader's
      public body-order iteration, so a table that sits between two paragraphs
      produces blocks between those two paragraphs' blocks. Order is the fact
      this processor exists to preserve.
    * **A paragraph's text is whatever the reader returns for it.** A paragraph
      that is empty or whitespace-only emits nothing; ``strip`` decides that
      question and nothing else, and the *stored* text is the reader's string,
      untouched.
    * **A table becomes one block per nonblank row**, tab-joined, with the table
      and row it came from recorded in ``metadata``. No wrapper block is emitted
      for the table itself: a table is a container, and inventing a segment for
      it would put text in the canonical stream that nobody wrote.
    * **Only the body is read.** Headers, footers, footnotes, endnotes,
      comments, text boxes, and drawing-layer text are all outside it, and none
      of them reaches a block. Deleted revision text is likewise absent — the
      reader reports the document as it currently reads, not its edit history.

    Nothing is normalized on the way out: no Unicode normalization, no
    whitespace collapsing, no line-ending rewriting, no punctuation repair, no
    paragraph joining, no sentence splitting, and no heading inference.

    Raises :class:`~core.processing.errors.ProcessingInputError` for a package
    that is not a readable DOCX — including one that is encrypted, since an
    encrypted Word document is not a ZIP and no password is attempted. Nothing
    outside :data:`_PACKAGE_INPUT_ERRORS` is caught: a ``TypeError`` from a bug
    in this module is a bug, not a verdict about someone's document.
    """
    try:
        document = Document(stream)
        blocks: list[DocxBlock] = []
        table_index = 0
        for item in document.iter_inner_content():
            if isinstance(item, Paragraph):
                if item.text.strip():
                    blocks.append(
                        DocxBlock(text=item.text, metadata={BLOCK_METADATA_KEY: PARAGRAPH_BLOCK})
                    )
            else:
                blocks.extend(_table_blocks(item, table_index))
                # Every body table advances the index, including one that
                # contributed no rows. The number names a table in the document,
                # so it must not depend on what the extraction happened to keep.
                table_index += 1
        return blocks, _core_title(document)
    except _PACKAGE_INPUT_ERRORS as exc:
        # The reader's message is not carried forward. It names archive members,
        # XML line and column numbers, relationship URIs, and — for a package of
        # the wrong content type — the ``repr`` of the stream it was handed,
        # which describes this server rather than the client's file.
        raise ProcessingInputError(
            "the document could not be read as a DOCX package; it appears to be malformed, "
            "truncated, not an Office Open XML word-processing document, or password-protected. "
            "This build reads unencrypted .docx documents and does not attempt passwords"
        ) from exc


def _original_asset(raw_object: RawObjectRef, ref: str) -> Asset:
    """Describe the immutable original DOCX as an asset of the content object.

    This is what keeps the exact submitted document retrievable from the content
    object alone, and it carries more weight here than it does for a PDF. Every
    segment is a *derived* flattening, and the original is the only place the
    document as written — its styles, its tables as tables, its headers, its
    images, everything this processor does not read — still exists.

    ``Asset.mime_type`` needs no fallback on this path. ``supports`` already
    required the declared MIME type to be exactly :data:`DOCX_MIME_TYPE`, so
    unlike the text and HTML processors there is no "the submitter declared
    nothing" case to invent a value for.

    The asset id is minted fresh, like every other id here. An asset is a record
    *within one content object* that points at an original; it is not the
    original. The raw object's identity travels on ``ref`` and ``sha256``.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=DOCX_MIME_TYPE,
        ref=ref,
        sha256=raw_object.sha256,
    )


class DocxProcessor:
    """Normalizes staged OOXML ``.docx`` document captures into canonical content."""

    name = "docx"
    #: The first version of these extraction semantics. ``version`` describes
    #: *what this processor produces*, so any change to the rules above — block
    #: selection, blankness, the cell separator, segment shape, title precedence
    #: — changes it, and the new value travels automatically onto every
    #: ``Provenance`` and ``ProcessingRecord`` emitted afterwards.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore) -> None:
        """Take the store the immutable original will be read through.

        The dependency is the :class:`~core.storage.raw.RawObjectStore` port, so
        the processor works against any backend and reaches into none of them.
        It is also the *only* dependency: no HTTP client, no fetcher, no OCR
        engine, no converter, and no subprocess. In particular there is no
        LibreOffice, no Word automation, no headless browser, and no pandoc —
        each of which would be a renderer, and a renderer is how fictional page
        numbers get invented.
        """
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged DOCX documents and nothing else. Pure; touches no storage.

        Three facts, all read from the capture record: it is a ``document``, it
        has a raw original, and that original is declared exactly
        :data:`DOCX_MIME_TYPE`.

        This is the same shape :class:`~core.processing.pdf.PdfProcessor` uses,
        and the two claims are disjoint by construction — which is the point.
        Neither processor knows the other exists, neither is a fallback for the
        other, and the router's exactly-one-match rule needs no precedence,
        no ordering, and no generic ``DOCUMENT`` handler to keep them apart.

        Whether the bytes really are a readable DOCX is a ``process`` question,
        not a routing one: answering it here would mean reading storage to
        route, and a DOCX capture whose original cannot currently be read is
        still a DOCX capture this processor handles.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.DOCUMENT
            and raw_object is not None
            and raw_object.mime_type == DOCX_MIME_TYPE
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Read the original DOCX, extract its body, and build canonical content.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not updated with the one found in the
        document's core properties, and the raw original is not modified.
        Lifecycle is orchestration's business, and ``CaptureRecord.title`` means
        *the title the submitter provided* — an extracted one is canonical
        content, so it goes on the ``ContentObject`` and stops there.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError` — a missing object, a
        malformed reference — and that propagates unchanged, exactly as it does
        from the other three processors. Those describe the state of the store
        rather than of the capture, and a caller deciding whether to retry needs
        to tell them apart from a document that will never process.
        """
        started_at = datetime.now(UTC)

        # All three preconditions are properties of the capture record itself,
        # so they are settled before any I/O. The MIME check repeats what
        # ``supports`` already asked, because ``process`` is callable directly
        # and must not read a PDF as though it were a DOCX just because nobody
        # routed the capture first.
        raw_object = capture.raw_object
        if raw_object is None:
            raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
        if raw_object.ref is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references raw object {raw_object.id!r} "
                f"without a storage reference"
            )
        if raw_object.mime_type != DOCX_MIME_TYPE:
            raise ProcessingInputError(
                f"capture {capture.id!r} references a raw object that is not declared "
                f"{DOCX_MIME_TYPE}; this processor reads OOXML .docx documents only"
            )

        # Streamed rather than read whole, and closed deterministically by the
        # ``with``: the reader wants to seek around a ZIP central directory
        # rather than receive the package as one bytes object, and the port
        # already offers a file handle.
        with self._raw_store.open(raw_object) as handle:
            blocks, metadata_title = extract_blocks(handle)

        if not blocks:
            # The package parsed perfectly well and its body says nothing. In
            # practice this is a document whose content is an image, or one
            # whose only text lives in headers, footers, or footnotes this build
            # does not read. The honest answer is that this build cannot read it
            # — not a ``COMPLETE`` content object with no segments, which would
            # claim the document was remembered and say nothing about the fact
            # that it was not. The exact DOCX stays in raw storage either way,
            # so nothing submitted is lost by refusing, and a richer build can
            # process the very same bytes later.
            raise ProcessingInputError(
                f"the document of capture {capture.id!r} contains no body text; its main "
                f"document body has no nonblank paragraph or table row, and this build reads "
                f"only that body — it does not read headers, footers, footnotes, comments, or "
                f"text boxes, and it does not perform OCR on embedded images"
            )

        original = _original_asset(raw_object, raw_object.ref)
        segments = [
            Segment(
                id=_new_id(),
                type=SegmentType.TEXT,
                # Exactly what extraction produced. A paragraph's text is the
                # reader's string, unstripped and unnormalized; a table row's is
                # that same untouched cell text with the declared separator
                # between cells, and nothing else done to it.
                text=block.text,
                # No spatial location, on purpose and on every segment. A DOCX
                # has flow content; which physical page a paragraph lands on is
                # a property of whoever renders it, not of the document. There
                # is no page to record, and ``SpatialLocation.heading`` is not a
                # stand-in for one — Word's heading *styles* are a semantic
                # question this build has not answered.
                spatial=None,
                provenance=Provenance(
                    capture_id=capture.id,
                    # ``ORIGINAL``, and specifically not ``PROCESSOR``, ``OCR``,
                    # or ``HTML``: this text was written in the document and read
                    # straight out of it. The processor rearranged structure into
                    # canonical segments, but it did not author, translate, or
                    # infer the words. There is no DOCX-specific provenance
                    # member and there should not be one: the asset's MIME type
                    # already says what the original was.
                    source_type=ProvenanceSourceType.ORIGINAL,
                    asset_id=original.id,
                    processor=self.name,
                    processor_version=self.version,
                ),
                # Structural provenance, kept in free-form metadata rather than
                # in a new contract field: which kind of body block this was,
                # and for a table row, exactly which row of which table. It is
                # descriptive, it costs the schema nothing, and it is what a
                # consumer needs to tell a flattened row from a paragraph.
                metadata=dict(block.metadata),
                # Canonical reading order: one contiguous sequence from zero
                # across paragraphs and table rows alike, because they are one
                # body and the order is the body's.
                position=position,
            )
            for position, block in enumerate(blocks)
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
            # The same precedence rule the PDF processor established, with the
            # document's core properties standing where the PDF's ``/Title``
            # stands:
            #
            #   1. the submitted capture title, exactly as given;
            #   2. otherwise a nonblank core-properties ``title``;
            #   3. otherwise none.
            #
            # A person who titled their capture said what they wanted it called,
            # and no extraction outranks that. The document's own metadata is its
            # claim about itself and is the obvious second. There is no third
            # source: not the uploaded filename, which is untrusted client text
            # this build never even records; not the ``file_ref`` or the digest,
            # which name bytes rather than a document; not the first paragraph or
            # the first ``Heading 1``, which invent a name the document never
            # carried. Nor the core properties' ``subject``, ``author``, or
            # ``keywords`` — those are a metadata design of their own and this
            # PR does not open it.
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
