"""The HTTP delivery adapter: five routes around the finished core.

This is the first product surface in the system, and it is an *adapter* — the
narrowest possible one. It parses a request, calls the core services that
already exist, and turns what comes back into a response. It owns no lifecycle
rule, no retry, no reconciliation, and no state::

    POST /v1/uploads         a multipart file part
                                 -> RawObjectStore.store_stream
                                 -> 200 + file_ref, sha256

    POST /v1/captures        CaptureEnvelope
                                 -> CaptureIntake.accept
                                 -> ProcessingOrchestrator.process
                                 -> 201 + capture id, content id, complete

                             a duplicate id, resent unchanged
                                 -> the completed capture it already made
                                 -> 200 + the same capture id, content id, complete

    GET  /v1/captures/{id}           the authoritative CaptureRecord
    GET  /v1/captures/{id}/content   the canonical ContentObject
    GET  /health                     {"status": "ok"}

**Uploading is not capturing, and the split is the whole design.** A PDF cannot
travel inside a JSON ``CaptureEnvelope`` — it is binary, and base64 in a text
field would be a lie about what was captured — so bytes are staged first and the
envelope names them by ``file_ref``. ``POST /v1/uploads`` therefore mints no
capture id, creates no ``CaptureRecord``, advances no lifecycle, runs no
processor, and produces no ``ContentObject``. It puts immutable bytes in the raw
store and hands back the content-addressed reference to them, and that is all it
does. An object nobody ever references stays there, unclaimed and harmless;
there is no lease, expiry, upload table, or collector, because none of those can
be designed honestly before something actually needs them.

**The POST body is ``CaptureEnvelope`` itself.** Not a wrapper, not an HTTP
mirror of it, not a subset — the canonical ingress contract, validated by its
own Pydantic rules before a route body runs. That is the whole point of the
surface: the browser extension, the Obsidian connector, and a shell script all
hand over the same document, and the day one of them arrives this API does not
change.

**201 means the pipeline finished.** Not that intake reached ``STORED``, and
not that work was queued — there is no queue. Intake and processing both run
synchronously inside the request, and the status code is written only after the
orchestrator has returned, which by its own contract means the canonical content
and the ``COMPLETE`` snapshot are both durable. A client that gets 201 can ask
for the content in the next call and find it.

**A lost response is answerable; a stranded capture is not.** The one
idempotency this surface has is the narrow completed replay in
:mod:`unimem_api.replay`: a client that resends the same capture id with the
same request, after losing the reply, gets the result the first attempt
produced rather than a conflict it cannot act on. It is a read, it is granted
only against a ``COMPLETE`` capture whose equivalence can be *proven*, and it
claims nothing about concurrent submissions. Every other duplicate — a
different request under the same id, a capture still processing, one that
failed — remains the ``409`` it always was.

**Failures are not repaired here.** Core owns lifecycle, including its
half-states: a ``ProcessingError`` leaves a durable ``FAILED`` capture, an
infrastructure failure mid-run leaves ``PROCESSING``, and a raw-store failure
after intake's receipt leaves ``RECEIVED``. This adapter maps each to a status
code (:mod:`unimem_api.errors`) and writes nothing. ``GET /v1/captures/{id}`` is
what makes those states observable, and that is the entire recovery story this
phase offers — deliberately, because reconciliation needs a real requirement to
be designed against and a connector has not asked for one yet.

There is no authentication, authorization, API key, TLS, or CORS policy here.
See :mod:`unimem_api.wiring` for why the CLI binds to localhost.
"""

from typing import Annotated

from fastapi import FastAPI, File, Response, UploadFile, status

from core.contracts import CaptureEnvelope, CaptureRecord, ContentObject
from core.intake import CaptureIntake
from core.persistence import (
    CaptureRecordAlreadyExistsError,
    CaptureRecordStore,
    ContentObjectStore,
)
from core.processing import ProcessingOrchestrator
from core.storage import RawObjectStore, build_raw_ref, resolve_digest
from unimem_api.errors import install_error_handlers
from unimem_api.models import (
    CaptureAcceptedResponse,
    ErrorResponse,
    HealthResponse,
    UploadedObjectResponse,
)
from unimem_api.replay import resolve_completed_replay

#: Documented on every route, so a client generating from the OpenAPI schema
#: sees one error shape rather than FastAPI's default ``{"detail": ...}``.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorResponse, "description": "The request could not be accepted."},
    "5XX": {"model": ErrorResponse, "description": "The server could not serve the request."},
}

#: The POST's second success. ``status_code=201`` documents the created case, so
#: the replay case has to be declared explicitly or a generated client would
#: treat a perfectly good ``200`` as an undocumented status. Same model, same
#: fields, same values — only the code differs, which is the whole distinction.
_REPLAY_RESPONSE: dict[int | str, dict[str, object]] = {
    status.HTTP_200_OK: {
        "model": CaptureAcceptedResponse,
        "description": (
            "An equivalent resubmission of a capture that is already complete. "
            "Nothing was created or reprocessed; the existing result is returned."
        ),
    },
}


def _canonical(model: CaptureRecord | ContentObject) -> Response:
    """Send a canonical contract as its own JSON, byte for byte.

    ``model_dump_json`` is the contract's definition of itself — the same call
    the SQLite stores persist through, custom serializers and all. Routing the
    object through ``response_model`` instead would re-derive that JSON from a
    second description of the same contract, which is exactly the duplicate
    definition ADR-002 and ADR-006 refused. FastAPI leaves a ``Response`` alone,
    so what the store holds is what the client reads.
    """
    return Response(content=model.model_dump_json(), media_type="application/json")


def create_app(
    intake: CaptureIntake,
    orchestrator: ProcessingOrchestrator,
    record_store: CaptureRecordStore,
    content_store: ContentObjectStore,
    raw_store: RawObjectStore,
) -> FastAPI:
    """Build the API over five already-constructed core services.

    Every dependency is a parameter, and each is held in the closure of the
    routes that use it. There is no module-level app, no registry, no settings
    object, no container, and no lazy singleton — so a test supplies doubles by
    calling this function, and two apps in one process share nothing.

    The services arrive as their existing types: the three stores as their
    ports, intake and the orchestrator as the concrete classes that *are* the
    domain orchestrations. Nothing about FastAPI travels the other way; ``core``
    does not import this package and does not know it exists.

    ``raw_store`` joined the list in Phase 3 PR 1, for the upload route, and it
    is a parameter for the same reason the other four are. Reaching for a module
    global, a lazy singleton, or ``app.state`` would have hidden a real
    dependency to avoid writing it down — and it must be *the same instance*
    intake and the processors hold, or a client would stage bytes into one store
    and hand a ``file_ref`` to another that has never heard of them.
    """
    app = FastAPI(
        title="UniMem capture API",
        version="0.1.0",
        summary="Local HTTP capture surface over the UniMem capture core.",
    )
    install_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, summary="Process liveness")
    def health() -> HealthResponse:
        """Report that this process is running. Nothing else is checked."""
        return HealthResponse()

    @app.post(
        "/v1/uploads",
        status_code=status.HTTP_200_OK,
        response_model=UploadedObjectResponse,
        responses=_ERROR_RESPONSES,
        summary="Stage immutable bytes and get the reference a capture can name",
    )
    def create_upload(file: Annotated[UploadFile, File()]) -> UploadedObjectResponse:
        """Put the uploaded bytes in the raw object store, and say where they went.

        **This is not a capture.** No id is minted, no ``CaptureRecord`` is
        created, no lifecycle is advanced, no processor runs, and no
        ``ContentObject`` appears. The route calls exactly one core method, and
        it is the one that writes immutable bytes.

        The part is streamed into the store rather than read into memory.
        Starlette has already spooled it to a temporary file and left it
        positioned at the start, and
        :meth:`~core.storage.raw.RawObjectStore.store_stream` consumes any
        readable binary source in bounded chunks — so a large document is hashed
        and written a chunk at a time and never has to fit in RAM. The route is
        a plain ``def`` for that reason: FastAPI runs it in a worker thread, so
        the synchronous store call cannot block the event loop.

        **200, not 201.** The raw store deduplicates by content, and
        deliberately does not report whether a write created a new file or found
        the identical bytes already there. That is the right design — an
        immutable content-addressed object *is* the same object either way — but
        it means the server genuinely cannot tell a client "created". Saying so
        anyway would be a guess dressed as a fact, so the honest answer is that
        the object is available, which is 200.

        **The filename decides nothing.** It picks no path — the store's layout
        is derived from a digest it computed itself, and nothing in
        :mod:`core.storage` ever writes through a caller-supplied name — it is
        not part of the object's identity, it is not persisted onto any capture,
        and it is not echoed back. Traversal sequences, absolute paths, and NUL
        bytes in it are therefore not dangerous input to be sanitized; they are
        input that is never read.

        The declared content type is likewise descriptive: it travels onto the
        returned reference so the client can see what it said, and it takes no
        part in the digest. The same bytes always produce the same ``file_ref``,
        whatever they were labelled.

        A raw-store failure raises the store's own typed error and is mapped to
        the same storage-unavailable response every other raw-store failure
        gets. No stack trace, no temporary path, and no byte of the file reaches
        the wire.
        """
        raw_object = raw_store.store_stream(file.file, mime_type=file.content_type)
        # Re-derived from the digest through the store's own vocabulary rather
        # than read off the reference with a ``None`` check bolted on: the
        # logical reference *is* a function of the digest, and a reference the
        # store cannot resolve is the store's failure to report, not this
        # route's to paper over.
        digest = resolve_digest(raw_object)
        return UploadedObjectResponse(
            file_ref=build_raw_ref(digest),
            sha256=digest,
            mime_type=raw_object.mime_type,
        )

    @app.post(
        "/v1/captures",
        status_code=status.HTTP_201_CREATED,
        response_model=CaptureAcceptedResponse,
        responses=_REPLAY_RESPONSE | _ERROR_RESPONSES,
        summary="Submit a capture and run it through the pipeline",
    )
    def create_capture(envelope: CaptureEnvelope, response: Response) -> CaptureAcceptedResponse:
        """Accept a capture envelope, store it, and normalize it — synchronously.

        The two calls are the two orchestrations core already owns, in the only
        order that makes sense, and this route adds nothing between them. It does
        not pre-check whether the id is free, catch a failure to retry it, or
        clean up after one: a capture stranded by a failure keeps whatever
        truthful state core left it in.

        There is exactly one thing between them now, and only on the failure
        path. When intake reports the id is taken, the route asks
        :func:`~unimem_api.replay.resolve_completed_replay` whether this is the
        *same request* resent against an already-completed capture — the case a
        client lands in when its response was lost in transit. If it is, the
        existing result comes back with ``200``; if it is not, the duplicate
        conflict is re-raised untouched.

        The status code carries the whole distinction, and no response field is
        added to restate it: **201 means this attempt created and completed the
        capture, 200 means it was an equivalent replay of one already complete.**
        The body is identical either way, because a replay's honest answer is
        the first submission's answer.

        The question is asked *after* the duplicate, never before it. There is no
        preflight read on the ordinary path, so the record store's primary key
        stays the only thing that decides who created the capture, and no
        check-then-create window is opened for two submissions to slip through.

        ``process`` is called with ``envelope.id`` rather than with the record
        ``accept`` returned, because the orchestrator's contract is that it loads
        the authoritative snapshot itself. Handing it an object would be handing
        it a decision about the past.
        """
        try:
            intake.accept(envelope)
        except CaptureRecordAlreadyExistsError:
            replayed = resolve_completed_replay(envelope, record_store, content_store)
            if replayed is None:
                raise
            response.status_code = status.HTTP_200_OK
            return replayed

        content = orchestrator.process(envelope.id)
        return CaptureAcceptedResponse(capture_id=envelope.id, content_id=content.id)

    @app.get(
        "/v1/captures/{capture_id}",
        response_model=CaptureRecord,
        responses=_ERROR_RESPONSES,
        summary="Read a capture's authoritative lifecycle record",
    )
    def get_capture(capture_id: str) -> Response:
        """Return the capture record exactly as the store holds it.

        This is the endpoint that makes a failure after ``POST`` observable: a
        capture may read ``received``, ``stored``, ``processing``, ``failed``, or
        ``complete``, and each of those is a true statement about how far it got.
        Nothing here advances, repairs, or reinterprets one.
        """
        return _canonical(record_store.get(capture_id))

    @app.get(
        "/v1/captures/{capture_id}/content",
        response_model=ContentObject,
        responses=_ERROR_RESPONSES,
        summary="Read a capture's canonical content object",
    )
    def get_capture_content(capture_id: str) -> Response:
        """Return the canonical ``ContentObject`` a capture normalized into.

        The canonical object, not a projection of it. No renderer is called here
        — not ``JsonRenderer``, whose output is a *derived representation* with
        its own audience and its own version, and not ``MarkdownRenderer``, which
        is lossy by design. A client that wants a rendering derives it from this.

        404 is the ordinary answer for a capture that has not produced content:
        one that failed, one still processing, and one that does not exist all
        say the same thing here, and ``GET /v1/captures/{id}`` is where they are
        told apart.
        """
        return _canonical(content_store.get_for_capture(capture_id))

    return app
