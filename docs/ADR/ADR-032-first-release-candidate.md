# ADR-032: a saved launcher and an immutable compatible candidate kit

Status: accepted for first Zen/Linux candidate, 2026-10-04.

Daily operation requires a reproducible installed environment and explicit paths,
not a new server or repeated assembly of optional CLI flags. `unimem_local` is a
stdlib TOML reader and console entrypoint delegating to `unimem_api.__main__.main`.
It adds no service, runtime configuration framework, model or content ownership.
The existing CLI stays compatible. Configuration holds only absolute paths and
optional booleans; token material stays in the existing credential/receiver owners.

Setup is non-destructive: create missing config/browser token, validate existing
ones; never rotate them. Explicit first destination enrollment refuses to create
another when any destination exists. The receiver secret is shown once, just as
by the existing operator CLI. A crash after destination creation cannot recreate
or reveal that credential: preserve existing enrollment and resolve explicitly.
Diagnostics import only stdlib, never run native probes or models, and do not
open SQLite or create lock files. They distinguish filesystem presence from
runtime readiness. Process ownership still belongs to ServerLease; no lock unlink.

The browser's connection action checks health and authenticated destinations,
then the existing read-only image/video capabilities and YouTube status probe.
Image capability now also exposes availability of the optional decoder. No
capture, recovery state, token persistence choice or delivery permissions change.

A thin builder archives committed HEAD, delegates to the existing builders and
emits a wheel, deterministic browser ZIP/source ZIP, Connector ZIP, checksums,
compatible component versions/source SHA, unit template and FIRST_RUN. Native
evidence identifies the actual kit hashes; later evidence-only commits do not
magically rebuild it. Signing is an external owner action. Temporary addon tests
never establish signed installation/restart. No tag, release, updater or merge.
