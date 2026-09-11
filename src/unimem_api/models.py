"""The HTTP data transfer objects — and nothing that could be mistaken for one.

Everything in this module is *delivery-layer* shape: what the wire looks like
for a request or a response that has no canonical domain contract behind it.
There are exactly four of them, and they are all responses.

There is deliberately **no request model here.** The capture POST body is
:class:`~core.contracts.CaptureEnvelope` itself, unchanged and unwrapped, and
the upload POST body is not JSON at all — it is a multipart file part, declared
on the route where FastAPI can see it. A second HTTP-flavoured copy of the
ingress contract would be a second definition of what a capture *is*, kept in
step by hand, and the first field that drifted would drift silently — which is
the whole reason the canonical contract exists. The envelope is the ingress
contract; HTTP is one way of handing one over.

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


class UploadedObjectResponse(BaseModel):
    """What a client learns from staging bytes, and deliberately nothing more.

    ``file_ref`` is the whole point of the response: it is exactly what a later
    :class:`~core.contracts.CapturePayload` puts in its own ``file_ref`` field,
    so a client copies one string from here to there and never has to know how
    raw storage is arranged. ``sha256`` is the same fact in the form a client
    can *check* — hash the file you just sent and compare — which is what makes
    "the original is byte-exact" verifiable from outside rather than a promise.

    ``mime_type`` is descriptive. It echoes what the upload declared and takes
    no part in the object's identity: raw objects are addressed by their bytes
    and by nothing else, so the same PDF uploaded as ``application/pdf`` and as
    ``application/octet-stream`` is one stored object with one reference.

    What is **not** here is as deliberate. There is no capture id, because no
    capture was made. No status, because nothing entered a lifecycle. No
    filename, because the submitted one is untrusted client text that decides
    nothing — not the storage path, not the identity, not a title — and echoing
    it back would suggest otherwise. And no "created" flag, because the store
    deduplicates by content and genuinely does not know whether this request
    wrote a new file or found an identical one already there.
    """

    model_config = ConfigDict(extra="forbid")

    file_ref: str
    sha256: str
    mime_type: str | None = None


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
