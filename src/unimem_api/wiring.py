"""The composition root: the one place that names concrete backends.

:mod:`unimem_api.app` takes its five services as parameters and never
constructs one. This module is where they actually get built, for the one
deployment that exists today — a single local directory holding a
content-addressed raw store and a SQLite file::

    <data-dir>/
        raw/                 LocalRawObjectStore's content-addressed tree
        unimem.sqlite3       capture_records and content_objects

Both SQLite adapters point at that one file, which ADR-010 allows explicitly:
each owns its own table, neither reaches into the other's, and sharing a file is
a deployment convenience rather than a shared transaction. Nothing here opens a
connection, and no path reaches a domain contract or an HTTP body.

**Preparing the workspace is this layer's job.** ``data_dir`` is created if it is
missing, because choosing and preparing where an application keeps its data is a
deployment decision and this is the deployment. That is not a change to
``SqliteCaptureRecordStore``, which still refuses to fabricate a missing parent
directory — the store's rule is that *it* does not repair a misconfigured path,
and the composition root satisfying the precondition beforehand is how the rule
was always meant to be met.

There is no settings framework, environment lookup, config file, profile, or DI
container. The set of processors this application runs with is a list written
here, exactly as :class:`~core.processing.ProcessorRouter` intends.

**Three optional capabilities now reach this function, and all arrive as
parameters.** Phase 3 PR 3 added local OCR for scanned PDF pages, Phase 4 PR 2
added it for staged images, and Phase 5A-2 adds media structure probing; a
deployment turns any on by handing :func:`build_local_app` the corresponding
port — :class:`~core.processing.ocr.PdfPageOcr`,
:class:`~core.processing.image_recognition.ImageOcr`, or
:class:`~core.processing.media_probe.MediaProbe`. They are independent: every
combination is valid, and none enables, requires, or configures another.

``media_probe`` differs from the other two in one way worth naming. Those choose
*which* processor handles a capture this build already accepts; this one decides
whether a whole modality is accepted at all. So it reaches two places rather than
one — intake's ``media_enabled`` and the router's processor list — and both are
derived from the single argument, because a build that accepted audio at the door
and had no processor behind it would strand every such capture.

What arrives is the *port*, already constructed: the concrete adapters are built
one layer out, in :mod:`unimem_api.__main__`, so this module still imports no
native library, no imaging package, no media framework, no codec binding, and no
``subprocess``, and a default deployment with none of them installed still loads
it. There is no concrete media adapter to build yet — Phase 5A-2 ships the policy
and the seam, and the only way to exercise media here is to pass a probe in. That keeps the optional
dependencies genuinely optional rather than optional-until-something-imports-it,
and it keeps this function what it has always been: a list of constructor calls
with nothing to look anything up in. No container, no registry, no module-level
service, no ``app.state``.

Every processor, intake, *and* the upload route share the one
``LocalRawObjectStore`` instance, which is not a detail. A raw original is
content-addressed and identical bytes deduplicate, so one store is what makes
"the exact submitted bytes are still there" true no matter which processor read
them — and since Phase 3 PR 1 it is also what makes a two-step capture possible
at all. A client uploads bytes through the API and then submits an envelope
naming them by ``file_ref``; if the route wrote into one store and intake
resolved against another, that reference would dangle every time.
"""

from pathlib import Path
from typing import Final

from fastapi import FastAPI

from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import (
    AudioProcessor,
    DocxProcessor,
    ImageOcrProcessor,
    ImageProcessor,
    PdfOcrProcessor,
    PdfProcessor,
    ProcessingOrchestrator,
    Processor,
    ProcessorRouter,
    TextProcessor,
    VideoProcessor,
    WebpageProcessor,
)
from core.processing.image_recognition import ImageOcr
from core.processing.media_probe import MediaProbe
from core.processing.ocr import PdfPageOcr
from core.storage import LocalRawObjectStore
from unimem_api.app import create_app

#: The content-addressed raw object tree, under the data directory.
RAW_DIRNAME: Final = "raw"

#: The SQLite file both persistence adapters open. One file, two tables.
DATABASE_FILENAME: Final = "unimem.sqlite3"


def _pdf_processor(raw_store: LocalRawObjectStore, pdf_ocr: PdfPageOcr | None) -> Processor:
    """The one PDF processor this deployment runs. Never both, never neither.

    A single ``if`` is the whole mutual exclusion, and it lives here rather than
    in the router because "which implementation of a claim runs" is a composition
    decision. The router's job is to notice that two processors claim one capture
    and refuse to guess; giving it a precedence rule instead would make an
    accidental double registration look like a working deployment.

    ``PdfOcrProcessor`` takes the recognizer as a constructor argument, so a
    deployment that did not supply one cannot end up with a processor holding a
    ``None`` engine that fails on the first scan.
    """
    if pdf_ocr is None:
        return PdfProcessor(raw_store)
    return PdfOcrProcessor(raw_store, pdf_ocr)


def _image_processor(raw_store: LocalRawObjectStore, image_ocr: ImageOcr | None) -> Processor:
    """The one image processor this deployment runs. Never both, never neither.

    The same single ``if`` :func:`_pdf_processor` is, for the same reason and with
    the same consequence: ``ImageProcessor`` and ``ImageOcrProcessor`` make
    identical capability claims, so registering both is an
    ``AmbiguousProcessorError`` on every image capture rather than a precedence
    rule nobody wrote down.

    ``ImageOcrProcessor`` takes the recognizer as a constructor argument, so a
    deployment that did not supply one cannot end up with a processor holding a
    ``None`` engine that fails on the first photograph.
    """
    if image_ocr is None:
        return ImageProcessor(raw_store)
    return ImageOcrProcessor(raw_store, image_ocr)


def _media_processors(
    raw_store: LocalRawObjectStore, media_probe: MediaProbe | None
) -> tuple[Processor, ...]:
    """Both media processors, or neither. Never one.

    Unlike :func:`_pdf_processor` and :func:`_image_processor`, this is not a
    choice between two implementations of one claim — ``AudioProcessor`` and
    ``VideoProcessor`` claim disjoint payload types and never compete. It is a
    choice about whether the capability exists at all, so the return is a tuple
    that is either empty or holds both.

    **The pairing is deliberate and load-bearing.** Registering one without the
    other would leave the matching intake acceptance with no processor behind it,
    and every capture of that modality would strand mid-lifecycle at a router that
    has nothing for it. The caller derives intake's ``media_enabled`` from the
    same ``media_probe`` argument, so acceptance and processing cannot disagree.

    One :class:`~core.processing.media_probe.MediaProbe` instance serves both.
    The port is a single synchronous method over a stream and holds nothing on
    the caller's behalf, so two processors sharing one is the ordinary case
    rather than a shortcut.

    The probe arrives already constructed, exactly as the two recognizers do.
    Nothing here builds an engine, and this module still imports no media
    framework, no codec binding, no ``subprocess`` and no concrete adapter — which
    is what lets a default deployment with no media tooling installed import it.
    """
    if media_probe is None:
        return ()
    return (AudioProcessor(raw_store, media_probe), VideoProcessor(raw_store, media_probe))


def build_local_app(
    data_dir: Path,
    *,
    pdf_ocr: PdfPageOcr | None = None,
    image_ocr: ImageOcr | None = None,
    media_probe: MediaProbe | None = None,
) -> FastAPI:
    """Wire the real stack against ``data_dir`` and return the API over it.

    The whole application, in the order it depends on itself::

        LocalRawObjectStore        the immutable originals
        SqliteCaptureRecordStore   capture lifecycle snapshots
        SqliteContentObjectStore   canonical content
        CaptureIntake              envelope -> stored capture
        TextProcessor              stored text capture -> canonical content
        WebpageProcessor           stored webpage capture -> canonical content
        PdfProcessor               stored pdf document  -> canonical content
          or PdfOcrProcessor         ...recognizing pages that carry no text
        DocxProcessor              stored docx document -> canonical content
        ImageProcessor             stored png/jpeg image -> canonical content
          or ImageOcrProcessor       ...recognizing any text in the pixels
        AudioProcessor             stored audio -> canonical content   (media only)
        VideoProcessor             stored video -> canonical content   (media only)
        ProcessorRouter            exactly one processor per capture
        ProcessingOrchestrator     stored -> complete, or a truthful failure

    Every processor that exists is registered — with exactly two exceptions,
    which are the point of ``pdf_ocr`` and ``image_ocr``. ``PdfProcessor`` and
    ``PdfOcrProcessor`` both claim ``DOCUMENT`` plus ``application/pdf``, and
    ``ImageProcessor`` and ``ImageOcrProcessor`` both claim ``IMAGE`` plus
    ``image/png`` or ``image/jpeg``, so each pair is a pair of *alternatives*:
    :func:`_pdf_processor` and :func:`_image_processor` each return one or the
    other and never both, because both in one router is an
    ``AmbiguousProcessorError`` on every capture of that kind. Which one a
    deployment gets is decided here, by whether a recognizer was handed in, and it
    cannot be decided anywhere else — not by a request field, not by a MIME type,
    not by a capture intent, and not by registration order.

    Everything else is unchanged and none of it knows OCR exists. The router's
    exactly-one-match rule is doing the same real work it was: ``TEXT`` reaches
    ``TextProcessor``, ``WEBPAGE`` reaches ``WebpageProcessor``, a ``DOCUMENT``
    declared ``application/pdf`` reaches whichever PDF processor was chosen, one
    declared ``.docx`` reaches ``DocxProcessor``, and an ``IMAGE`` declared
    ``image/png`` or ``image/jpeg`` reaches whichever image processor was chosen —
    each because of what it claims, never because of where it sits in this list.
    Order here is not precedence, there is no fallback processor, and an overlap
    would be an error rather than an accident of ordering.

    **The capabilities are independent, and the code says so by having three
    parameters and three helpers rather than one flag consulted repeatedly.**
    Every combination is a valid deployment. ``pdf_ocr`` never reaches an image
    processor, ``image_ocr`` never reaches a PDF one, and ``media_probe`` reaches
    neither, so enabling any cannot change what the others do, and their
    prerequisites differ.

    **``media_probe`` is the one that changes what intake accepts**, and that is
    the whole reason it is a single argument feeding two places. With it absent,
    ``CaptureIntake`` is built with ``media_enabled=False`` and no media processor
    is registered — which is today's deployment, byte for byte. With it present,
    intake accepts ``AUDIO`` and ``VIDEO`` and *both* processors are registered
    together. There is deliberately no way to express the two broken states: media
    accepted with nothing to process it, or media processors registered behind an
    intake that refuses every such capture.

    All three are keyword-only ports defaulting to ``None``, which together are
    the default deployment: identical registrations, identical behaviour, no OCR
    or media dependency imported, probed, or executed anywhere, and audio and
    video captures refused at the door exactly as they always have been.

    Two apps built against one directory are interchangeable: neither store keeps
    a connection, a cache, or in-process state between calls, which is what makes
    a restart a non-event and is exactly what the restart test checks. That stays
    true across the OCR boundary in one direction only, and deliberately: content
    an OCR-enabled app wrote is read back identically by a default one, because a
    stored ``ContentObject`` is just canonical content. What a default app cannot
    do is *process* the scan that produced it.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / DATABASE_FILENAME

    raw_store = LocalRawObjectStore(data_dir / RAW_DIRNAME)
    record_store = SqliteCaptureRecordStore(database)
    content_store = SqliteContentObjectStore(database)

    # One boolean, one source. Intake accepting media and the router holding
    # media processors are two halves of one capability, and deriving both from
    # the same ``media_probe is None`` test is what stops them drifting apart.
    intake = CaptureIntake(raw_store, record_store, media_enabled=media_probe is not None)
    router = ProcessorRouter(
        [
            TextProcessor(raw_store),
            WebpageProcessor(raw_store),
            _pdf_processor(raw_store, pdf_ocr),
            DocxProcessor(raw_store),
            _image_processor(raw_store, image_ocr),
            *_media_processors(raw_store, media_probe),
        ]
    )
    orchestrator = ProcessingOrchestrator(router, record_store, content_store)

    return create_app(
        intake=intake,
        orchestrator=orchestrator,
        record_store=record_store,
        content_store=content_store,
        raw_store=raw_store,
    )
