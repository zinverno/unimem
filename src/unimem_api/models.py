"""The HTTP data transfer objects — and nothing that could be mistaken for one.

Everything in this module is *delivery-layer* shape: what the wire looks like
for a request or a response that has no canonical domain contract behind it.
There are exactly three of them, and they are all responses.

There is deliberately **no request model here.** The POST body is
:class:`~core.contracts.CaptureEnvelope` itself, unchanged and unwrapped. A
second HTTP-flavoured copy of the ingress contract would be a second definition
of what a capture *is*, kept in step by hand, and the first field that drifted
would drift silently — which is the whole reason the canonical contract exists.
The envelope is the ingress contract; HTTP is one way of handing one over.

Nor is there an HTTP shape for :class:`~core.contracts.CaptureRecord` or
:class:`~core.contracts.ContentObject`. Those are canonical, they already
define their own JSON, and the routes emit exactly that (see
:mod:`unimem_api.app`).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from core.contracts import CaptureStatus


class HealthResponse(BaseModel):
    """Process liveness, and strictly nothing more.

    ``ok`` means this process is running and can answer. It says nothing about
    the data directory, the raw object store, the database file, or whether a
    capture submitted right now would succeed — none of which is checked, on
    purpose. A readiness probe that lies is worse than no readiness probe, and
    the honest cheap answer is the one worth shipping first.
    """

    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"


class CaptureAcceptedResponse(BaseModel):
    """What a client learns from a capture that completed the whole pipeline.

    Returned with ``201`` only after intake *and* processing have both
    returned, so ``status`` is ``complete`` by construction rather than by
    inspection: the orchestrator's contract is that a successful return means
    the canonical content and the ``COMPLETE`` snapshot are both durable. It is
    read from that guarantee rather than re-fetched, because a record re-read
    here would describe whatever the store says a moment *later* — a different
    fact, and a worse one to report as the outcome of this call.

    ``content_id`` is the canonical object's own id. It is not derived from
    ``capture_id``, and a client must keep treating both as opaque.
    """

    model_config = ConfigDict(extra="forbid")

    capture_id: str
    content_id: str
    status: Literal[CaptureStatus.COMPLETE] = CaptureStatus.COMPLETE


class ErrorBody(BaseModel):
    """The inside of the error envelope: a code to branch on, prose to read."""

    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Every non-2xx body this API produces deliberately.

    Nested under a single ``error`` key so that a success body and a failure
    body can never be confused by shape alone, and so the envelope has room to
    grow without colliding with a field name a future success response wants.
    """

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
