"""The composition root: the one place that names concrete backends.

:mod:`unimem_api.app` takes its four services as parameters and never
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
"""

from pathlib import Path
from typing import Final

from fastapi import FastAPI

from core.intake import CaptureIntake
from core.persistence import SqliteCaptureRecordStore, SqliteContentObjectStore
from core.processing import ProcessingOrchestrator, ProcessorRouter, TextProcessor
from core.storage import LocalRawObjectStore
from unimem_api.app import create_app

#: The content-addressed raw object tree, under the data directory.
RAW_DIRNAME: Final = "raw"

#: The SQLite file both persistence adapters open. One file, two tables.
DATABASE_FILENAME: Final = "unimem.sqlite3"


def build_local_app(data_dir: Path) -> FastAPI:
    """Wire the real stack against ``data_dir`` and return the API over it.

    The whole application, in the order it depends on itself::

        LocalRawObjectStore        the immutable originals
        SqliteCaptureRecordStore   capture lifecycle snapshots
        SqliteContentObjectStore   canonical content
        CaptureIntake              envelope -> stored capture
        TextProcessor              stored capture -> canonical content
        ProcessorRouter            exactly one processor per capture
        ProcessingOrchestrator     stored -> complete, or a truthful failure

    ``TextProcessor`` is the only processor registered, because it is the only
    one that exists. The router still requires an exact match rather than a first
    match, so the day a webpage processor joins the list an overlap is an error
    instead of an accident of ordering.

    Two apps built against one directory are interchangeable: neither store keeps
    a connection, a cache, or in-process state between calls, which is what makes
    a restart a non-event and is exactly what the restart test checks.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / DATABASE_FILENAME

    raw_store = LocalRawObjectStore(data_dir / RAW_DIRNAME)
    record_store = SqliteCaptureRecordStore(database)
    content_store = SqliteContentObjectStore(database)

    intake = CaptureIntake(raw_store, record_store)
    router = ProcessorRouter([TextProcessor(raw_store)])
    orchestrator = ProcessingOrchestrator(router, record_store, content_store)

    return create_app(
        intake=intake,
        orchestrator=orchestrator,
        record_store=record_store,
        content_store=content_store,
    )
