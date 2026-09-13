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

**One optional capability now reaches this function, and it arrives as a
parameter.** Phase 3 PR 3 adds local OCR for scanned PDF pages, and a deployment
turns it on by handing :func:`build_local_app` a
:class:`~core.processing.ocr.PdfPageOcr`. What arrives is the *port*, already
constructed: the concrete PDFium/Tesseract adapter is built one layer out, in
:mod:`unimem_api.__main__`, so this module still imports no native library, no
imaging package, and no ``subprocess``, and a default deployment with neither
installed still loads it. That keeps the optional dependency genuinely optional
rather than optional-until-something-imports-it, and it keeps this function what
it has always been: a list of constructor calls with nothing to look anything up
in. No container, no registry, no module-level service, no ``app.state``.

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
    DocxProcessor,
    PdfOcrProcessor,
    PdfProcessor,
    ProcessingOrchestrator,
    Processor,
    ProcessorRouter,
    TextProcessor,
    WebpageProcessor,
)
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


def build_local_app(data_dir: Path, *, pdf_ocr: PdfPageOcr | None = None) -> FastAPI:
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
        ProcessorRouter            exactly one processor per capture
        ProcessingOrchestrator     stored -> complete, or a truthful failure

    Every processor that exists is registered — with exactly one exception, which
    is the point of ``pdf_ocr``. ``PdfProcessor`` and ``PdfOcrProcessor`` both
    claim ``DOCUMENT`` plus ``application/pdf``, so they are *alternatives*:
    :func:`_pdf_processor` returns one or the other and never both, because both
    in one router is an ``AmbiguousProcessorError`` on every PDF capture. Which
    one a deployment gets is decided here, by whether a recognizer was handed in,
    and it cannot be decided anywhere else — not by a request field, not by a
    MIME type, not by a capture intent, and not by registration order.

    Everything else is unchanged and none of it knows OCR exists. The router's
    exactly-one-match rule is doing the same real work it was: ``TEXT`` reaches
    ``TextProcessor``, ``WEBPAGE`` reaches ``WebpageProcessor``, a ``DOCUMENT``
    declared ``application/pdf`` reaches whichever PDF processor was chosen, and
    one declared ``.docx`` reaches ``DocxProcessor`` — each because of what it
    claims, never because of where it sits in this list. Order here is not
    precedence, there is no fallback processor, and an overlap would be an error
    rather than an accident of ordering.

    ``pdf_ocr`` is a keyword-only port and defaults to ``None``, which is the
    default deployment: identical registrations, identical behaviour, and no
    OCR dependency imported, probed, or executed anywhere.

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

    intake = CaptureIntake(raw_store, record_store)
    router = ProcessorRouter(
        [
            TextProcessor(raw_store),
            WebpageProcessor(raw_store),
            _pdf_processor(raw_store, pdf_ocr),
            DocxProcessor(raw_store),
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
