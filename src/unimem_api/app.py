"""The HTTP delivery adapter: four routes around the finished core.

This is the first product surface in the system, and it is an *adapter* — the
narrowest possible one. It parses a request, calls the core services that
already exist, and turns what comes back into a response. It owns no lifecycle
rule, no retry, no reconciliation, no idempotency, and no state::

    POST /v1/captures        CaptureEnvelope
                                 -> CaptureIntake.accept
                                 -> ProcessingOrchestrator.process
                                 -> 201 + capture id, content id, complete

    GET  /v1/captures/{id}           the authoritative CaptureRecord
    GET  /v1/captures/{id}/content   the canonical ContentObject
    GET  /health                     {"status": "ok"}

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

from fastapi import FastAPI, Response, status

from core.contracts import CaptureEnvelope, CaptureRecord, ContentObject
from core.intake import CaptureIntake
from core.persistence import CaptureRecordStore, ContentObjectStore
from core.processing import ProcessingOrchestrator
from unimem_api.errors import install_error_handlers
from unimem_api.models import CaptureAcceptedResponse, ErrorResponse, HealthResponse

#: Documented on every route, so a client generating from the OpenAPI schema
#: sees one error shape rather than FastAPI's default ``{"detail": ...}``.
_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    "4XX": {"model": ErrorResponse, "description": "The request could not be accepted."},
    "5XX": {"model": ErrorResponse, "description": "The server could not serve the request."},
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
) -> FastAPI:
    """Build the API over four already-constructed core services.

    Every dependency is a parameter, and each is held in the closure of the
    routes that use it. There is no module-level app, no registry, no settings
    object, no container, and no lazy singleton — so a test supplies doubles by
    calling this function, and two apps in one process share nothing.

    The services arrive as their existing types: the two stores as their ports,
    intake and the orchestrator as the concrete classes that *are* the domain
    orchestrations. Nothing about FastAPI travels the other way; ``core`` does
    not import this package and does not know it exists.
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
        "/v1/captures",
        status_code=status.HTTP_201_CREATED,
        response_model=CaptureAcceptedResponse,
        responses=_ERROR_RESPONSES,
        summary="Submit a capture and run it through the pipeline",
    )
    def create_capture(envelope: CaptureEnvelope) -> CaptureAcceptedResponse:
        """Accept a capture envelope, store it, and normalize it — synchronously.

        The two calls are the two orchestrations core already owns, in the only
        order that makes sense, and this route adds nothing between them. It does
        not pre-check whether the id is free, catch a failure to retry it, or
        clean up after one: a duplicate id is a conflict, and a capture stranded
        by a failure keeps whatever truthful state core left it in.

        ``process`` is called with ``envelope.id`` rather than with the record
        ``accept`` returned, because the orchestrator's contract is that it loads
        the authoritative snapshot itself. Handing it an object would be handing
        it a decision about the past.
        """
        intake.accept(envelope)
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
