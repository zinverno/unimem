"""Normalization of plain UTF-8 text captures.

The first real processor, and the smallest useful one: it reads the immutable
original through the raw object store, decodes it as strict UTF-8, and emits a
content object carrying that text as a single canonical segment.

What it deliberately does not do: guess the encoding, normalize Unicode, rewrite
line endings, trim whitespace, chunk, split on headings or paragraphs, or derive
anything. The decoded text reaches the segment exactly as it was stored.
"""

import uuid
from datetime import UTC, datetime
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

#: The only encoding this phase reads. A second encoding is a decision, not a
#: fallback: nothing here sniffs, detects, or retries.
TEXT_ENCODING: Final = "utf-8"

#: MIME type recorded on the original asset when the capture declared none.
#: ``Asset.mime_type`` is required by the contract, and a text capture whose
#: bytes decoded as UTF-8 is text. The fallback is not written back onto
#: ``ContentObject.original``, so a capture that declared no MIME type still
#: looks like one that declared no MIME type.
DEFAULT_TEXT_MIME_TYPE: Final = "text/plain"


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule: Phase 0A leaves
    identifier format open, and consumers must keep treating ids as opaque.
    Content, segment, and asset ids are explicitly *not* derived from the raw
    digest — that address identifies bytes, and two captures of identical bytes
    are two different content objects holding two different asset records.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str) -> Asset:
    """Describe the immutable original as an asset of the content object.

    The asset is what keeps the original retrievable from the content object
    alone. ``OriginalReference`` carries identity (``sha256``) but has nowhere
    to put a reference, and rebuilding one from the digest would require every
    consumer to know that raw storage happens to be content-addressed —
    knowledge that lives in :mod:`core.storage`, not in the contracts.
    ``Asset.ref`` is the contract's designated home for a storage-neutral
    handle and ``AssetRole.ORIGINAL`` is its designated role.

    The asset id is minted fresh, like every other id this processor produces.
    An asset is a *record within one content object* that points at an original;
    it is not the original. The raw object's identity travels on ``ref`` and
    ``sha256``, where it belongs, so reprocessing the same bytes yields a new
    asset id and the same reference and digest. Reusing the raw id here would
    quietly make the SHA-256 an identifier for something other than the bytes.

    The id is what segment provenance points at, satisfying the content object's
    asset-reference invariant.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=raw_object.mime_type or DEFAULT_TEXT_MIME_TYPE,
        ref=ref,
        sha256=raw_object.sha256,
    )


class TextProcessor:
    """Normalizes ``text`` captures into canonical text content objects."""

    name = "text"
    #: 0.2 carries the capture's submitted title onto the content object.
    #: ``version`` describes processing semantics, so output that changed for
    #: an unchanged input changes it — and the new value travels automatically
    #: onto every ``Provenance`` and ``ProcessingRecord`` this processor emits.
    version = "0.2"

    def __init__(self, raw_store: RawObjectStore) -> None:
        """Take the store the immutable original will be read through.

        The dependency is the :class:`~core.storage.raw.RawObjectStore` port, so
        the processor works against any backend and reaches into none of them.
        """
        self._raw_store = raw_store

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle text payloads and nothing else. Pure; touches no storage."""
        return capture.payload_type is CapturePayloadType.TEXT

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Read, decode, and normalize the capture's original into content.

        The capture record is only read, never written: its lifecycle status is
        not advanced, and the raw original is not modified. Whether the capture
        is ``stored``, ``queued``, or anything else is orchestration's business.

        ``capture.title`` is copied onto the content object exactly as given,
        and stays ``None`` when the capture carried none.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError` — a missing object, a
        malformed reference — and that propagates unchanged. Those describe the
        state of the store rather than of the capture, and a caller deciding
        whether to retry needs to tell them apart from a capture that will never
        process. No ``OSError`` escapes, because the port does not raise one.
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

        text = self._decode(self._raw_store.read_bytes(raw_object), capture)

        original = _original_asset(raw_object, raw_object.ref)
        segment = Segment(
            id=_new_id(),
            type=SegmentType.TEXT,
            text=text,
            provenance=Provenance(
                capture_id=capture.id,
                source_type=ProvenanceSourceType.ORIGINAL,
                asset_id=original.id,
                processor=self.name,
                processor_version=self.version,
            ),
            position=0,
        )
        completed_at = datetime.now(UTC)

        return ContentObject(
            id=_new_id(),
            type=ContentType.TEXT,
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
            # The submitter's title, verbatim or not at all. Plain text has no
            # competing extracted title — no heading to read, no ``<title>``,
            # no document properties — so if the capture carried one it *is*
            # the title. Nothing is inferred from the first line, the file
            # name, or the URL, and no title is minted when the capture had
            # none. A processor that really does extract titles (webpage,
            # document) will need a precedence rule; this one does not, and
            # inventing the rule here would be inventing the problem too.
            title=capture.title,
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
        """Decode strict UTF-8 and reject material no segment can be built from.

        The emptiness checks mirror the contract's own ``NonBlankStr`` rule, so
        a capture holding nothing meaningful fails as a named processing error
        rather than as a validation traceback from inside a segment.
        """
        try:
            text = data.decode(TEXT_ENCODING)
        except UnicodeDecodeError as exc:
            raise TextDecodingError(
                f"raw object of capture {capture.id!r} is not valid {TEXT_ENCODING}: {exc}"
            ) from exc
        if not text:
            raise ProcessingInputError(f"raw object of capture {capture.id!r} is empty")
        if not text.strip():
            raise ProcessingInputError(
                f"raw object of capture {capture.id!r} holds only whitespace"
            )
        return text
