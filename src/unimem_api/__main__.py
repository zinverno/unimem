"""``python -m unimem_api`` — the local capture server, made runnable.

::

    python -m unimem_api --data-dir ./data

Three options and no more: where the data lives, and what to bind. There is no
config file, environment lookup, settings framework, profile, service manager,
systemd unit, Docker image, or reload mode. Starting the server is one command
and stopping it is Ctrl-C.

**The default bind is 127.0.0.1, and that is a security decision rather than a
convenience.** This phase has no authentication, no authorization, no API keys,
and no TLS: every caller that can reach the port can submit captures and read
back everything stored. Loopback is the only interface on which that is an
acceptable posture, so it is the default, and a ``--host`` that widens it is an
explicit choice made by whoever types it.
"""

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

import uvicorn
from fastapi import FastAPI

from unimem_api.wiring import build_local_app

#: Loopback. See the module docstring — this is not a placeholder.
DEFAULT_HOST: Final = "127.0.0.1"

#: A high port, unlikely to collide with something already on the machine.
DEFAULT_PORT: Final = 8765


class Server(Protocol):
    """What :func:`main` needs from a server: take this app and run it."""

    def __call__(self, app: FastAPI, *, host: str, port: int) -> None: ...


@dataclass(frozen=True)
class Options:
    """Everything the command line decides."""

    data_dir: Path
    host: str
    port: int


def build_parser() -> argparse.ArgumentParser:
    """The command line, in full."""
    parser = argparse.ArgumentParser(
        prog="python -m unimem_api",
        description="Run the local UniMem capture API.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        required=True,
        help="directory holding the raw object store and the SQLite database; created if missing",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=(
            f"interface to bind (default: {DEFAULT_HOST}). This server has no "
            f"authentication — do not expose it to an untrusted network."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"port to bind (default: {DEFAULT_PORT})",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Options:
    """Turn an argument vector into the options, or exit with usage."""
    namespace = build_parser().parse_args(argv)
    return Options(
        data_dir=namespace.data_dir,
        host=namespace.host,
        port=namespace.port,
    )


def serve(app: FastAPI, *, host: str, port: int) -> None:
    """Hand the app to uvicorn and block until it stops.

    Programmatic, with the app object already built — not an import string, which
    would make uvicorn construct the application itself and take the composition
    decision away from :mod:`unimem_api.wiring`. No reload, no worker count, no
    lifespan hooks, no logging configuration: whatever uvicorn does by default is
    what this phase wants.
    """
    uvicorn.run(app, host=host, port=port)


def main(
    argv: Sequence[str] | None = None,
    *,
    server: Server = serve,
) -> int:
    """Parse, wire, and serve.

    ``server`` is a parameter so the argument handling and the composition can be
    exercised without binding a TCP port. It is the only seam, and it exists for
    testability rather than for configuration — nothing reads it from anywhere.
    """
    options = parse_args(argv)
    app = build_local_app(options.data_dir)
    server(app, host=options.host, port=options.port)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by `python -m unimem_api`
    raise SystemExit(main())
