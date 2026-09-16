"""Normalization of still-image captures in an image-OCR-enabled deployment.

The seventh processor, and the second that is *optional*. It is not registered
unless the deployment explicitly asked for it, and when it is registered it takes
:class:`~core.processing.image.ImageProcessor`'s place rather than standing
beside it — both claim ``IMAGE`` plus ``image/png`` or ``image/jpeg``, and the
router's exactly-one-match rule means a deployment that registered both would be
told, loudly, that it is wired wrong. Which of the two runs is a *composition*
decision made where the application is assembled; it is not a request field, a
MIME type, a capture intent, a header, or a router precedence rule, and no client
can reach it. See `ADR-020 <../../docs/ADR/ADR-020-opt-in-local-image-ocr.md>`_.

**The image is canonical either way, and that changes every failure answer.**
`ADR-019 <../../docs/ADR/ADR-019-still-image-ingestion.md>`_ established that for
a still image the pixels *are* the artifact, so an image that is staged, stored
immutably, content-addressed, described structurally and tied to its capture has
already been remembered completely before any recognizer is consulted.
Recognition is enrichment over content that is already canonical.

That is why a ``COMPLETE`` object with no segments is the right answer
here and the *wrong* answer for a document, and why
:class:`~core.processing.pdf_ocr.PdfOcrProcessor` refuses a document nothing
could be read from while this processor refuses nothing of the kind.

Three outcomes, all of them successful captures:

* **Nonblank recognition** — one ``OCR`` segment for the whole image, with
  ``ProvenanceSourceType.OCR`` pointing at the original asset.
* **Successful recognition that read nothing** — no segment. The engine ran and
  found no nonblank text, which is evidence about the recognizer and is never
  recorded as the image being blank.
* **A resource budget that declined to run the engine** — no segment, and the
  skip recorded durably under ``metadata["image_ocr"]``. A structurally valid
  image that exceeds an enrichment budget is still a valid image; the budget
  refuses the decode, not the capture.

And one that is not: a recognition that produced no trusted result leaves the
capture non-terminal. See :class:`~core.processing.image_recognition.ImageOcrExecutionError`.

**Nothing here decodes, recognizes, or executes anything.** This module imports
no imaging library, no decoder, no OCR engine, and no ``subprocess``; it holds an
:class:`~core.processing.image_recognition.ImageOcr` port and reads the original
through a :class:`~core.storage.raw.RawObjectStore`, exactly as the other
processors do.
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
from core.contracts.base import JsonMapping
from core.contracts.enums import CapturePayloadType
from core.processing.errors import ProcessingInputError
from core.processing.image import (
    ENCODED_FORMAT_KEY,
    ENCODED_HEIGHT_KEY,
    ENCODED_WIDTH_KEY,
    IMAGE_METADATA_KEY,
    IMAGE_MIME_TYPES,
    read_image_header,
)
from core.processing.image_recognition import (
    ENCODED_BYTE_LIMIT,
    ENCODED_PIXEL_LIMIT,
    ImageOcr,
    ImageOcrLimitExceeded,
    ImageOcrResult,
    LimitReason,
    validate_image_ocr_limit,
    validate_image_ocr_result,
)
from core.storage import RawObjectStore

#: The metadata key, on the content object and on the recognized segment, under
#: which this processor records what actually ran.
#:
#: One namespaced key rather than a scattering of top-level ones, following the
#: ``pdf_ocr`` precedent. It sits *beside* :data:`IMAGE_METADATA_KEY`, which this
#: processor reproduces byte for byte from the Phase 4A parser output: the
#: structural observations are the same facts in either deployment, and only the
#: recognition story differs.
IMAGE_OCR_METADATA_KEY: Final = "image_ocr"

#: Whether the OCR engine was actually run for this capture.
#:
#: Deliberately *not* named "attempted". An encoded-byte-limit refusal has
#: already read part of the original — that is how the byte count was measured —
#: so "attempted" would be ambiguous about the very case this key exists to
#: describe. ``engine_invoked`` states precisely one thing, and it is the thing
#: that decides what the rest of the mapping may contain.
ENGINE_INVOKED_KEY: Final = "engine_invoked"

#: Which documented resource bound stopped recognition before it began.
#:
#: Present only when :data:`ENGINE_INVOKED_KEY` is false, and carrying the
#: adapter's own structured reason verbatim. Named for the bound that was
#: crossed rather than for any property of the picture, which is the same
#: discipline that named ``ocr_pages_without_text`` in the PDF path.
SKIPPED_REASON_KEY: Final = "skipped_reason"

#: The engine's own name, as it reported it.
ENGINE_KEY: Final = "engine"

#: The version the installed engine reported for itself at startup.
ENGINE_VERSION_KEY: Final = "engine_version"

#: The effective recognition configuration this run used.
SETTINGS_KEY: Final = "settings"

#: The most encoded pixels this deployment will hand to a recognizer.
MAX_ENCODED_PIXELS_KEY: Final = "max_encoded_pixels"

#: The most encoded bytes the adapter will hold for one recognition.
MAX_ENCODED_BYTES_KEY: Final = "max_encoded_bytes"

#: Which metadata key carries the bound that stopped a skipped recognition.
#:
#: One entry per refusal reason, written out rather than derived from the reason
#: string, so that adding a third bound is a decision made here rather than a
#: string that happens to transform correctly.
_SKIPPED_LIMIT_KEYS: Final[dict[str, str]] = {
    ENCODED_PIXEL_LIMIT: MAX_ENCODED_PIXELS_KEY,
    ENCODED_BYTE_LIMIT: MAX_ENCODED_BYTES_KEY,
}


def _new_id() -> str:
    """Mint an opaque identifier.

    UUID4 is this producer's choice, not a contract rule. Content, segment, and
    asset ids are explicitly *not* derived from the image's digest: that address
    identifies bytes, and two captures of the identical photograph — including
    the same one submitted once before OCR was enabled and once after — are two
    different content objects holding two different asset records.
    """
    return str(uuid.uuid4())


def _original_asset(raw_object: RawObjectRef, ref: str, mime_type: str) -> Asset:
    """Describe the immutable original image as an asset of the content object.

    Exactly one asset is ever created, and it is the submitted image. Nothing is
    derived from it, stored beside it, or referenced in its place: there is no
    thumbnail, no normalized copy, no re-encode, and no raster. Recognized text
    is the most *derived* content this system produces, and the original is the
    only place the picture — everything the recognizer read and everything it
    missed — still exists, for the better recognizer that comes later.
    """
    return Asset(
        id=_new_id(),
        role=AssetRole.ORIGINAL,
        mime_type=mime_type,
        ref=ref,
        sha256=raw_object.sha256,
    )


class ImageOcrProcessor:
    """Normalizes staged PNG and JPEG captures, recognizing any text in them."""

    name = "image-ocr"
    #: The first version of *these* semantics, which are not
    #: :class:`~core.processing.image.ImageProcessor`'s. A separate name and its
    #: own version is what keeps the two tellable apart on a ``Provenance`` and a
    #: ``ProcessingRecord`` years later: ``image@0.1`` says nothing was
    #: interpreted, and ``image-ocr@0.1`` says a recognizer was configured and
    #: this is what came of it. Any change to the rules in this module's
    #: docstring — the blankness rule, the metadata vocabulary, the skip
    #: semantics — changes this value.
    version = "0.1"

    def __init__(self, raw_store: RawObjectStore, image_ocr: ImageOcr) -> None:
        """Take the store the original is read through and the recognizer to use.

        Both are ports. The store is
        :class:`~core.storage.raw.RawObjectStore` and the recognizer is
        :class:`~core.processing.image_recognition.ImageOcr`, so this class works
        against any backend and any engine and reaches into neither. There is no
        default recognizer, no lazily-imported one, and no lookup: a deployment
        that wants recognition constructs an adapter and hands it over, which is
        the same thing as saying this processor cannot exist by accident.
        """
        self._raw_store = raw_store
        self._image_ocr = image_ocr

    def supports(self, capture: CaptureRecord) -> bool:
        """Handle staged PNG and JPEG images and nothing else. Pure; touches no storage.

        Byte-for-byte the same three questions
        :meth:`~core.processing.image.ImageProcessor.supports` asks, and that is
        deliberate: this processor is the *other implementation of the same
        claim*, not a broader or narrower one. It does not sniff the leading
        bytes to guess whether an image might contain words, does not read
        storage to find out, and does not claim a format ``ImageProcessor`` does
        not.

        Two consequences follow, both intended. Registering both is an
        ``AmbiguousProcessorError`` rather than a silent preference, because
        registration order is not precedence and this overlap is a wiring mistake
        with two plausible resolutions. And an image whose bytes cannot be read
        is still an image capture this processor handles — whether the header
        parses is a ``process`` question, not a routing one.
        """
        raw_object = capture.raw_object
        return (
            capture.payload_type is CapturePayloadType.IMAGE
            and raw_object is not None
            and raw_object.mime_type in IMAGE_MIME_TYPES
        )

    def process(self, capture: CaptureRecord) -> ContentObject:
        """Validate the header, then recognize, then build canonical content.

        The sequence, in full::

            validate the capture, its raw reference, and its declared type
            open the original    -> read_image_header()   format, width, height
            open the original    -> recognize_image()
            validate the recognizer's answer
            build the object: one OCR segment, or none

        **Header validation runs first and its failures are final.** A malformed,
        truncated, structurally illegal, or type-contradicting image raises
        :class:`~core.processing.errors.ProcessingInputError` out of
        :func:`~core.processing.image.read_image_header` before the recognizer is
        ever constructed a stream, so recognition is never attempted as a repair
        for an image the parser refused. A recognizer pointed at bytes the parser
        rejected would either fail the same way or — far worse — read *something*
        out of a file nobody could open.

        **The recognizer gets its own stream.** The original is opened a second
        time rather than rewound, because "rewind whatever the parser left
        behind" is a promise about stream position this module cannot make. Two
        opens of an immutable content-addressed object are two reads of identical
        bytes, and the practical consequence is the point: nothing on this path
        seeks, so a non-seekable backend is a non-event rather than a supported
        special case.

        **A resource refusal is caught here and nowhere else.**
        :class:`~core.processing.image_recognition.ImageOcrLimitExceeded` never
        escapes to orchestration: it is a control signal whose canonical meaning
        is this processor's to decide, and that meaning is a successful capture
        with the enrichment skipped.

        **A failed recognition is not.**
        :class:`~core.processing.image_recognition.ImageOcrExecutionError`
        propagates untouched — it is not a ``ProcessingError``, so the
        orchestrator leaves the capture ``processing``, nothing is persisted, and
        delivery answers a fixed 503. It is emphatically not converted into a
        skip: "no trusted result came back" and "the engine ran and found no
        text" are different facts, and only one of them may be written down.

        The capture record is only read, never written: its lifecycle status is
        not advanced, its ``title`` is not altered, its declared MIME type is not
        corrected, and the raw original is not modified.

        Storage failures are not translated. If the store cannot produce the
        bytes it raises its own typed
        :class:`~core.storage.errors.RawObjectStoreError`, and that propagates
        unchanged, exactly as it does from the other processors — and,
        importantly, still distinguishably from an ``ImageOcrExecutionError``,
        which also propagates unchanged and also leaves the capture non-terminal.
        """
        started_at = datetime.now(UTC)

        # All three preconditions are properties of the capture record itself, so
        # they are settled before any I/O. The MIME check repeats what
        # ``supports`` already asked, because ``process`` is callable directly and
        # must not hand a PDF to an image recognizer just because nobody routed
        # the capture first.
        raw_object = capture.raw_object
        if raw_object is None:
            raise ProcessingInputError(f"capture {capture.id!r} has no raw object to process")
        if raw_object.ref is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references raw object {raw_object.id!r} "
                f"without a storage reference"
            )
        mime_type = raw_object.mime_type
        if mime_type is None:
            raise ProcessingInputError(
                f"capture {capture.id!r} references a raw object declaring no mime_type; "
                f"this processor reads still images and does not infer a format from the bytes"
            )

        # The Phase 4A parsers, reached through the function that publishes their
        # dispatch. Not a copy of them, not a variant with OCR-shaped tweaks: an
        # image's structural facts must reach a content object through exactly the
        # code path they reach one through in the default build, or "enabling OCR
        # changes nothing about an image that already worked" would be an
        # aspiration rather than a fact.
        with self._raw_store.open(raw_object) as handle:
            header = read_image_header(handle, mime_type)

        recognized: ImageOcrResult | None = None
        refusal: ImageOcrLimitExceeded | None = None
        try:
            with self._raw_store.open(raw_object) as handle:
                recognized = self._image_ocr.recognize_image(
                    handle,
                    mime_type=mime_type,
                    encoded_width=header.width,
                    encoded_height=header.height,
                )
        except ImageOcrLimitExceeded as exc:
            # Checked before it is believed, for the same reason the result is:
            # an adapter is third-party code, the signal's attributes are enforced
            # by nothing at run time, and this one is about to become durable
            # metadata on a *successful* capture. A signal this build cannot
            # interpret is adapter inconsistency rather than a resource skip, and
            # leaves here as an execution failure instead.
            validate_image_ocr_limit(exc)
            # Kept as the value it is, so the metadata below reads its structured
            # attributes rather than its message. The image is valid, it is still
            # remembered completely, and the only thing that did not happen is
            # optional enrichment.
            refusal = exc
        else:
            validate_image_ocr_result(recognized)

        original = _original_asset(raw_object, raw_object.ref, mime_type)
        segments = self._segments(capture, original, recognized)
        completed_at = datetime.now(UTC)

        return ContentObject(
            id=_new_id(),
            type=ContentType.IMAGE,
            source=ContentSource(
                capture_id=capture.id,
                provider=capture.source.provider,
                url=capture.source.url,
            ),
            original=OriginalReference(
                asset_id=original.id,
                mime_type=mime_type,
                sha256=raw_object.sha256,
            ),
            # The submitted capture title, exactly as given, or none. Unchanged
            # from the default build and specifically *not* extended with a
            # recognized source: a title taken from OCR text is a guess about
            # what a picture is called, dressed as a fact, and it would be
            # indistinguishable downstream from a title a submitter chose. Not
            # from the recognized text, not from the filename — which this build
            # never records — not from the digest, and not from the URL.
            title=capture.title,
            metadata={
                # Byte for byte what ``ImageProcessor`` records, because these are
                # the same observations about the same bytes.
                IMAGE_METADATA_KEY: {
                    ENCODED_FORMAT_KEY: header.encoded_format,
                    ENCODED_WIDTH_KEY: header.width,
                    ENCODED_HEIGHT_KEY: header.height,
                },
                IMAGE_OCR_METADATA_KEY: _recognition_metadata(recognized, refusal),
            },
            segments=segments,
            assets=[original],
            processing=[
                # ``ProcessingRecord`` has no metadata field and is not given one
                # here: it records *that* ``image-ocr@0.1`` ran and how it went,
                # and the description of what ran belongs to the content.
                #
                # ``COMPLETE`` describes this processor's run, and
                # ``image-ocr@0.1`` publishes what that run is: the original is
                # held, its structure described, and recognition applied within
                # this deployment's budget. A run that skipped the engine on that
                # budget completed exactly what this version says it does, and
                # ``PARTIAL`` would be a different falsehood — nothing was
                # attempted and missed.
                ProcessingRecord(
                    processor=self.name,
                    processor_version=self.version,
                    started_at=started_at,
                    completed_at=completed_at,
                    status=ProcessingStatus.COMPLETE,
                )
            ],
        )

    def _segments(
        self,
        capture: CaptureRecord,
        original: Asset,
        recognized: ImageOcrResult | None,
    ) -> list[Segment]:
        """One segment for the whole image, or none at all.

        ``strip()`` decides blankness and **never rewrites the stored string**:
        leading indentation, trailing newlines, and internal whitespace are what
        the engine produced. There is no Unicode normalization, no line-ending
        rewriting, no hyphen repair, no paragraph joining, no spell correction,
        and no model anywhere near the text. An engine that returns ``"Th1s"``
        gets ``"Th1s"`` stored; correcting it would be inventing a reading nobody
        can audit.

        When there is nothing nonblank to store the answer is an empty list. Not
        an empty ``OCR`` segment — which the contract could not build anyway,
        since ``SegmentType.OCR`` requires nonblank text — not a text-less
        ``VISUAL`` placeholder, and not a caption. Absence of interpretation is
        represented by absence.
        """
        if recognized is None or not recognized.text.strip():
            return []
        return [
            Segment(
                id=_new_id(),
                type=SegmentType.OCR,
                text=recognized.text,
                # No temporal location, because an image has no timeline. No
                # spatial location either, and that is a contract fact rather
                # than a style choice: ``SpatialLocation`` refuses to be
                # constructed locating nothing, so "no fake page, no invented
                # bounding box" *is* omitting the field. This build performs no
                # region detection and has nothing true to put there.
                temporal=None,
                spatial=None,
                provenance=Provenance(
                    capture_id=capture.id,
                    source_type=ProvenanceSourceType.OCR,
                    # The original image, and not a dangling reference: the
                    # pixels the engine read *are* that asset, in full, and it is
                    # the only artifact that still exists.
                    asset_id=original.id,
                    processor=self.name,
                    processor_version=self.version,
                ),
                # The engine that produced it, and nothing else. The full
                # settings block lives once on the content object; repeating it
                # here would be the same paragraph of configuration stored twice,
                # and the identity is the part a reader of one segment needs.
                metadata={
                    IMAGE_OCR_METADATA_KEY: {
                        ENGINE_KEY: recognized.engine,
                        ENGINE_VERSION_KEY: recognized.engine_version,
                    }
                },
                # One image, one segment, and no ordering question to answer.
                position=0,
            )
        ]


def _recognition_metadata(
    recognized: ImageOcrResult | None,
    refusal: ImageOcrLimitExceeded | None,
) -> JsonMapping:
    """What actually happened to recognition, and nothing more.

    Two shapes, and which one is used is decided by whether the engine ran.

    When it ran, the engine's own identity and the effective settings are
    recorded — taken from the recognizer rather than assumed here, because the
    point of recording them is to describe the run that happened rather than the
    run this module expected.

    When it did not, **the run facts are absent**. Nothing ran, so there is no
    engine to name, no version to report, and no effective configuration to
    describe; recording the engine a deployment *would* have used would be
    describing a run that did not occur. What is recorded instead is the bound
    that was crossed and its value.

    There is deliberately no ``returned_text`` flag. Nonblank recognition is
    represented by the OCR segment and a successful empty recognition by
    ``engine_invoked`` together with no segment, so a boolean would be a second
    place for the same fact to be wrong. There is deliberately no confidence
    score: the engine this build ships against is not asked for one, and an
    invented number would be read as a measurement. And there is deliberately no
    ``rasterizer``: nothing rasterizes here.
    """
    if refusal is not None:
        reason: LimitReason = refusal.reason
        return {
            ENGINE_INVOKED_KEY: False,
            SKIPPED_REASON_KEY: reason,
            _SKIPPED_LIMIT_KEYS[reason]: refusal.limit,
        }
    if recognized is None:  # pragma: no cover - the two outcomes are exhaustive
        raise ProcessingInputError("recognition neither produced a result nor was refused")
    return {
        ENGINE_INVOKED_KEY: True,
        ENGINE_KEY: recognized.engine,
        ENGINE_VERSION_KEY: recognized.engine_version,
        SETTINGS_KEY: dict(recognized.settings),
    }
