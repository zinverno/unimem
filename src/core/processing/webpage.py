"""Normalization of HTML-backed webpage captures.

The first non-plain-text processor. It reads the immutable original through the
raw object store, decodes it as strict UTF-8, extracts the page's visible text
with a deterministic standard-library parser, and emits a canonical ``web``
content object carrying that text as a single segment.

**Two different things live in one capture, and this module never confuses
them.** The raw original is the exact bytes the client submitted — byte-for-byte
immutable, never rewritten, and always retrievable through the content object's
original asset. The segment text is a *derived* normalization of those bytes,
produced by the algorithm documented in :class:`HtmlTextExtractor` and
reproducible from the original at any time. Nothing here writes back into the
original, and nothing here treats the extraction as authoritative.

**This is HTML text extraction, not article extraction.** It is emphatically not
reader mode, Readability, boilerplate removal, or semantic content detection. It
does not know what a navigation bar is, does not rank blocks by text density,
and does not try to find "the article". Site chrome, menus, cookie banners, and
footers are text on the page and come out as text — because the alternative is a
heuristic that silently deletes content, and a lossy guess is a much worse thing
for a memory system to store than an honest transcription.

**Nothing on the page is executed or fetched.** Scripts are inert text that is
ignored, CSS is inert text that is ignored, and no image, stylesheet, iframe,
font, or ``source.url`` is ever requested. The submitted HTML string is the
entire input; there is no network here at all.
"""

import re
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Final

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
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError, TextDecodingError
from core.storage import RawObjectStore

#: The only encoding this processor reads, and it is not a guess about the web.
#:
#: A webpage arriving over the wire from an arbitrary server would be a charset
#: problem: meta tags, transport headers, and mislabelled bytes are all real.
#: That is not what happens here. The HTML reached UniMem as a JSON string —
#: already Unicode, already decoded by whoever submitted it — and intake wrote
#: its UTF-8 encoding. So this processor is decoding *the encoding UniMem itself
#: chose*, which is a closed loop with exactly one right answer. Nothing sniffs
#: bytes, reads ``<meta charset>``, retries Latin-1 or Windows-1251, or calls
#: chardet, because there is no charset here to detect.
#:
#: Deliberately a local twin of :data:`core.processing.text.TEXT_ENCODING`
#: rather than an import of it, the same way intake keeps its own: two
#: processors agreeing on a value is not a dependency between them.
HTML_ENCODING: Final = "utf-8"

#: MIME type recorded on the original asset when the capture declared none.
#: ``Asset.mime_type`` is required by the contract, and bytes this processor
#: parsed as HTML are HTML. The fallback is processor-local and is *not* written
#: back onto ``ContentObject.original``, ``CaptureRecord``, or ``RawObjectRef``,
#: so a capture that declared no MIME type still looks like one that declared
#: no MIME type everywhere the submitter's own words are recorded.
DEFAULT_HTML_MIME_TYPE: Final = "text/html"

#: Elements whose text is never page content. Everything between one of these
#: start tags and its matching end tag is discarded, nested markup included.
#:
#: ``script`` and ``style`` are code, not prose. ``noscript`` is the fallback
#: for a renderer that did not run scripts, and including it alongside the
#: scripted content would duplicate the page. ``template`` is inert by
#: definition — it is markup held for later instantiation, and it was not
#: instantiated. ``svg`` carries drawing instructions whose text nodes are
#: labels inside a picture, not text flow.
IGNORED_TAGS: Final = frozenset({"script", "style", "noscript", "template", "svg"})

#: Elements that separate blocks of text. Crossing one — in either direction —
#: emits a paragraph boundary, which the normalizer below collapses to at most
#: one blank line.
#:
#: This is a fixed list, not a CSS ``display`` computation. There is no
#: stylesheet engine here and there will not be one: it would need the CSS to be
#: fetched, cascaded, and resolved, which is a browser.
BLOCK_TAGS: Final = frozenset(
    {
        "article",
        "aside",
        "blockquote",
        "div",
        "footer",
        "header",
        "main",
        "nav",
        "section",
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ul",
        "ol",
        "table",
        "tr",
        "td",
        "th",
        "pre",
    }
)

#: The whitespace HTML uses for source formatting, and the only whitespace this
#: module collapses or trims.
#:
#: Deliberately ASCII, and deliberately not ``\s`` or a bare ``str.strip()``:
#: both of those also match U+00A0 and the other Unicode spaces, and a
#: no-break space is a *character the page asked for* — usually written
#: ``&nbsp;`` — not indentation the author left in the file. Entity meaning is
#: preserved; source formatting is not content.
_ASCII_WHITESPACE: Final = " \t\n\r\f\v"
_WHITESPACE_RUN: Final = re.compile(r"[ \t\n\r\f\v]+")

#: The marker a block boundary appends, and the one ``<br>`` appends. They are
#: ordinary newlines in the buffer; every newline in the buffer is one of these,
#: because text nodes have theirs collapsed to spaces on the way in.
_BLOCK_BREAK: Final = "\n\n"
_LINE_BREAK: Final = "\n"


def _collapse(text: str) -> str:
    """Collapse runs of HTML formatting whitespace to single spaces."""
    return _WHITESPACE_RUN.sub(" ", text)


class HtmlTextExtractor(HTMLParser):
    """Deterministic visible-text extraction from an HTML string.

    Small on purpose, and specified rather than tuned. The whole algorithm:

    1. **Parse with the standard library.** ``html.parser.HTMLParser`` with
       ``convert_charrefs=True``, so ``&amp;`` becomes ``&`` and ``&nbsp;``
       becomes U+00A0 through the parser's own normal behaviour. No third-party
       parser is involved, nothing is executed, and malformed markup is handled
       by whatever the standard parser already does with it rather than by
       repair logic here.
    2. **Discard :data:`IGNORED_TAGS` entirely** — everything from the start tag
       to its matching end tag, nested markup included.
    3. **Discard ordinary ``<head>`` text.** ``<title>`` is the one exception,
       and it is collected separately (see :attr:`title`) rather than joined
       into the body. Meta descriptions, keywords, link tags, OpenGraph,
       JSON-LD, and any other head text are not read at all.
    4. **Discard comments, doctypes, and processing instructions.**
    5. **Collect every other text node**, collapsing each node's internal
       whitespace runs to single spaces as it arrives — so a whitespace-only
       node between two inline elements still contributes the one space it
       means, and forty columns of source indentation do not become forty
       spaces of content.
    6. **Emit a break when a boundary is crossed**: a blank-line boundary at
       both the start and the end of any :data:`BLOCK_TAGS` element, and a
       single line break for ``<br>``.
    7. **Normalize the result by line**: collapse whitespace once more across
       node boundaries, trim each line, collapse runs of blank lines to at most
       one, and drop leading and trailing blank lines.

    What it deliberately does **not** do, each of which would be a claim it
    cannot honour:

    * No CSS. ``display: none``, ``visibility: hidden``, ``content:``
      pseudo-elements, media queries, and the cascade are not evaluated —
      hidden-by-CSS text is extracted like any other text.
    * No JavaScript, so no script-generated content and no shadow DOM.
    * No ``<pre>`` whitespace preservation. ``pre`` is treated as a block
      boundary and its text is collapsed like all other text. Preserving it
      would mean two whitespace policies and a second thing to get right, and
      the raw original — where the exact bytes *are* preserved — is one asset
      reference away.
    * No article extraction, boilerplate removal, or text-density heuristics.
    * No DOM fidelity: the output is text, not a tree, and no structure survives
      beyond block separation.

    The parser instance is single-use, matching ``HTMLParser``'s own contract.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        #: Open ignored elements, innermost last. A list rather than a counter
        #: so a stray ``</script>`` inside a ``<template>`` closes the right one.
        self._ignoring: list[str] = []
        self._head_depth = 0
        self._title_depth = 0
        self._title_parts: list[str] = []
        #: Every ``<title>`` element's text, in document order.
        self._titles: list[str] = []
        self._parts: list[str] = []

    # --- Parser callbacks ---------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in IGNORED_TAGS:
            self._ignoring.append(tag)
            return
        if self._ignoring:
            return
        if tag == "head":
            self._head_depth += 1
        elif tag == "title":
            # A new title element starts a new buffer. An unclosed previous one
            # is flushed first, so the count of titles matches the count of
            # start tags and the first-nonblank rule stays predictable.
            self._flush_title()
            self._title_depth += 1
        elif tag == "br":
            self._parts.append(_LINE_BREAK)
        elif tag in BLOCK_TAGS:
            self._parts.append(_BLOCK_BREAK)

    def handle_endtag(self, tag: str) -> None:
        if self._ignoring:
            if tag in self._ignoring:
                # Unwind to the matching open tag, closing anything left open
                # inside it. Malformed nesting resolves the same way every time.
                del self._ignoring[len(self._ignoring) - 1 - self._ignoring[::-1].index(tag) :]
            return
        if tag == "head":
            self._head_depth = max(0, self._head_depth - 1)
        elif tag == "title":
            if self._title_depth:
                self._title_depth -= 1
                self._flush_title()
        elif tag in BLOCK_TAGS:
            self._parts.append(_BLOCK_BREAK)

    def handle_data(self, data: str) -> None:
        if self._ignoring:
            return
        if self._title_depth:
            self._title_parts.append(data)
            return
        if self._head_depth:
            return
        self._parts.append(_collapse(data))

    def handle_comment(self, data: str) -> None:
        """Comments are not content. Stated rather than inherited."""

    def close(self) -> None:
        """Finish parsing and flush a ``<title>`` the document never closed."""
        super().close()
        self._flush_title()

    # --- Results ------------------------------------------------------------

    @property
    def text(self) -> str:
        """The extracted page text, normalized. Empty when there is none.

        Emptiness is a real answer, not an error: deciding what an empty page
        means is the *processor's* call, not the extractor's.
        """
        lines: list[str] = []
        for raw_line in "".join(self._parts).split("\n"):
            line = _collapse(raw_line).strip(_ASCII_WHITESPACE)
            # Keep a blank line only where it separates two non-blank ones, so
            # nested blocks — each contributing its own boundary — collapse to a
            # single paragraph break instead of a run of them.
            if line or (lines and lines[-1]):
                lines.append(line)
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    @property
    def title(self) -> str | None:
        """The first nonblank ``<title>``, whitespace-normalized, or ``None``.

        *First nonblank*, chosen because it is the rule that needs no
        tie-breaking: documents are read top to bottom, a duplicated ``<title>``
        is malformed markup where the first one is what a browser keeps, and
        skipping a blank one means an empty ``<title></title>`` does not shadow
        a real title further down. Surrounding and internal formatting
        whitespace is collapsed and trimmed exactly as body text is; character
        references are already decoded by the parser, so ``&amp;`` arrives here
        as ``&``.
        """
        for candidate in self._titles:
            title = _collapse(candidate).strip(_ASCII_WHITESPACE)
            if title:
                return title
        return None

    # --- Internals ----------------------------------------------------------

    def _flush_title(self) -> None:
        if self._title_parts:
            self._titles.append("".join(self._title_parts))
            self._title_parts = []


def extract(html: str) -> tuple[str, str | None]:
    """Extract ``(text, title)`` from an HTML string. Pure; no I/O.

    A module-level function because extraction is worth testing — and reasoning
    about — entirely on its own, with no capture, no store, and no content
    object anywhere near it.
    """
    extractor = HtmlTextExtractor()
    extractor.feed(html)
    extractor.close()
    return extractor.text, extractor.title


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content, segment, and
    asset ids are explicitly *not* derived from the raw digest: that address
    identifies bytes, and two captures of identical HTML are two different
    content objects holding two different asset records.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str) -> Asset:
    """Describe the immutable original HTML as an asset of the content object.

    The asset is what keeps the exact submitted HTML retrievable from the
    content object alone — which matters more here than for plain text, because
    the segment is a *derived* extraction and the original is the only place the
    markup still exists. ``Asset.ref`` is the contract's storage-neutral handle
    and ``AssetRole.ORIGINAL`` its designated role.

    The asset id is minted fresh, like every other id this processor produces:
    an asset is a record *within one content object* that points at an original,
    not the original itself. The raw object's identity travels on ``ref`` and
    ``sha256``, where it belongs.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=raw_object.mime_type or DEFAULT_HTML_MIME_TYPE,
        ref=ref,
        sha256=raw_object.sha256,
    )


class WebpageProcessor:
    """Normalizes HTML-backed ``webpage`` captures into canonical web content."""

    name = "webpage"
    #: The first version of these extraction semantics. ``version`` describes
    #: *what this processor produces*, so any change to the extraction rules —
    #: a new ignored tag, a different block boundary, a whitespace policy —
    #: changes it, and the new value travels automatically onto every
    #: ``Provenance`` and ``ProcessingRecord`` emitted afterwards. That is what
    #: makes a segment traceable to the algorithm that produced it, and it is
    #: why the derived text being reproducible from the immutable original is
    #: worth anything.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore) -> None:
        """Take the store the immutable original will be read through.

        The dependency is the :class:`~core.storage.raw.RawObjectStore` port, so
        the processor works against any backend and reaches into none of them.
        It is also the *only* dependency: no HTTP client, no fetcher, no browser.
        """
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle webpage payloads and nothing else. Pure; touches no storage.

        The question is answered from the capture record alone, so a webpage
        capture whose original cannot currently be read is still a webpage
        capture this processor handles. Whether the raw bytes are actually HTML
        this can extract anything from is a ``process`` question, not a routing
        one — answering it here would mean reading storage to route.
        """
        return capture.payload_type is CapturePayloadType.WEBPAGE

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Read the original HTML, extract its text, and build canonical content.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not updated with the one found in the
        markup, and the raw original is not modified. Lifecycle is
        orchestration's business, and ``CaptureRecord.title`` means *the title
        the submitter provided* — an extracted one is canonical content, so it
        goes on the ``ContentObject`` and stops there.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError` — a missing object, a
        malformed reference — and that propagates unchanged, exactly as it does
        from ``TextProcessor``. Those describe the state of the store rather
        than of the capture, and a caller deciding whether to retry needs to
        tell them apart from a capture that will never process.
        """
        started_at = datetime.now(UTC)

        # Both preconditions are properties of the capture record itself, so
        # they are settled before any I/O. A reference without ``ref`` names
        # nothing a store could be asked for, and no asset could carry it.
        raw_object = capture.raw_object
        if raw_object is None:
            raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
        if raw_object.ref is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references raw object {raw_object.id!r} "
                f"without a storage reference"
            )

        html = self._decode(self._raw_store.read_bytes(raw_object), capture)
        text, extracted_title = extract(html)
        if not text:
            # A page that decoded perfectly well and yielded no text is a real
            # failure of *this* input, not a successful empty capture. The
            # alternative — a COMPLETE content object with an empty or absent
            # segment — would be the system reporting that it remembered
            # something when it remembered nothing, which is the one answer a
            # memory system must never give. The exact bytes remain in raw
            # storage either way, so nothing submitted is lost by refusing.
            raise ProcessingInputError(
                f"raw object of capture {capture.id!r} holds no extractable page text"
            )

        original = _original_asset(raw_object, raw_object.ref)
        segment = Segment(
            id=_new_id(),
            type=SegmentType.TEXT,
            text=text,
            provenance=Provenance(
                capture_id=capture.id,
                # Not ``ORIGINAL``: this text was *derived from the HTML*, and
                # the enum has the word for that. A consumer reading this knows
                # the segment is an extraction whose source markup is still
                # available at ``asset_id``, rather than the stored bytes
                # themselves — which is exactly the distinction that keeps the
                # raw original and the canonical text from being confused.
                source_type=ProvenanceSourceType.HTML,
                asset_id=original.id,
                processor=self.name,
                processor_version=self.version,
            ),
            position=0,
        )
        completed_at = datetime.now(UTC)

        return ContentObject(
            id=_new_id(),
            type=ContentType.WEB,
            source=ContentSource(
                capture_id=capture.id,
                provider=capture.source.provider,
                url=capture.source.url,
            ),
            original=OriginalReference(
                asset_id=original.id,
                # The MIME type the submitter declared, verbatim, including
                # ``None``. The ``text/html`` fallback above is the asset
                # contract's requirement being met, not a fact about the
                # capture, so it does not travel here.
                mime_type=raw_object.mime_type,
                sha256=raw_object.sha256,
            ),
            # The first title precedence rule in the system, and it is short:
            #
            #   1. the submitted capture title, exactly as given;
            #   2. otherwise the first nonblank ``<title>`` in the HTML;
            #   3. otherwise none.
            #
            # A person who titled their capture said what they wanted it
            # called, and no extraction outranks that. The ``<title>`` is the
            # page's own claim about itself and is the obvious second. There is
            # no third source: not the first ``<h1>``, not the first line of
            # text, not the hostname or URL slug, and certainly not a model —
            # each of those invents a name the document never carried.
            title=capture.title if capture.title is not None else extracted_title,
            segments=[segment],
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

    def _decode(self, data: bytes, capture: CaptureRecord) -> str:
        """Decode the stored original as strict UTF-8.

        :class:`~core.processing.errors.TextDecodingError` is reused rather than
        subclassed or twinned: the category is "stored bytes are not valid text
        in the expected encoding", the encoding is the same UTF-8, and the
        caller's decision — this capture will never process, stop — is the same
        one. A parallel ``HtmlDecodingError`` would be a second name for the
        same fact and a second thing every error table has to learn.

        Blankness is *not* checked here. Whitespace-only markup is not a
        decoding problem, and whether a page yields any text at all is a
        question only extraction can answer — so it is asked once, afterwards,
        in ``process``.
        """
        try:
            return data.decode(HTML_ENCODING)
        except UnicodeDecodeError as exc:
            raise TextDecodingError(
                f"raw object of capture {capture.id!r} is not valid {HTML_ENCODING}: {exc}"
            ) from exc
