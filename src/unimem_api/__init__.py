"""The local HTTP capture surface: the first product boundary around the core.

Phase 1 asks one question — *how does a real client, over a real network
protocol, hand UniMem a capture and get the result back?* — and this package is
the whole answer::

    browser extension / Obsidian connector / script / curl
            |
          HTTP
            |
      CaptureEnvelope          the canonical ingress contract, unchanged
            |
          core                 CaptureIntake -> ProcessingOrchestrator
            |
      durable raw bytes + capture records + canonical content

It is an adapter and nothing else. ``core`` has no idea it exists: FastAPI,
uvicorn, status codes, routing, and the command line all live here, on this side
of the boundary, and the dependency points one way only.

Four routes, and deliberately no fifth. There is no list, search, batch,
re-process, or delete endpoint, no authentication, and no idempotency — a
duplicate capture id is a conflict, not a retry. Those become real work when a
connector produces a real requirement for them; the connector is the next PR.

See :class:`~unimem_api.app.create_app` for the API over injected services, and
:func:`~unimem_api.wiring.build_local_app` for the one local composition that
exists today.
"""

from unimem_api.app import create_app
from unimem_api.wiring import DATABASE_FILENAME, RAW_DIRNAME, build_local_app

__all__ = [
    "DATABASE_FILENAME",
    "RAW_DIRNAME",
    "build_local_app",
    "create_app",
]
