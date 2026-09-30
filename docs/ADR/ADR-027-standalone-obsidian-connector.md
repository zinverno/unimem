# ADR-027: Standalone UniMem Connector is the Obsidian delivery channel

Status: accepted, 2026-10-01. Supersedes the Veynrel/Companion delivery proposal
in the current roadmap. Historical ADR-025/026 and B2/B3 evidence retain their
original scope. Extends invariant 18 with the import journal described here.

## Decision and ownership

Browser → UniMem local API → immutable delivery queue → **UniMem Connector** →
Obsidian Vault API. `clients/obsidian-plugin` is an independent desktop plugin.
Veynrel and Companion have no dependency, authentication, queue, proposal or
write role. They need not be installed. Veynrel could later read an imported
note as ordinary vault content. No MCP, listener/second server, Python process,
vault scan, AI, telemetry, watcher or synchronization is part of the plugin.

The existing capture/orchestration/content owners remain unchanged. The new
adapter renders the persisted COMPLETE result once, at explicit delivery
submission, using the existing caption renderer (or the existing generic
Markdown renderer for other already-supported content). No new processor is
introduced. It commits the exact UTF-8 text and SHA-256 before returning 202.
Replay returns the saved delivery before consulting the renderer. This is a
delivery snapshot, not a new canonical content model.

`obsidian-delivery.sqlite3` owns destination metadata, SHA-256 hashes of random
256-bit receiver tokens and delivery receipts. It is separate from `api.token`
and the capture database, owner-only (0600), and rejects symlinks/unsafe owner
permissions on opening. There is no vault path in server state or requests.
Destination and delivery IDs are UUID v4; source IDs follow the bounded existing
ASCII identifier convention. Suggested names are `unimem-<capture-id-sha256>.md`.
One capture + destination is a durable unique key, including concurrent requests.
Receipts are retained; 1,000 deliveries total and 1 MiB UTF-8 per snapshot are
hard limits. Refusal never truncates or replaces material with a summary.

## Credential scopes and protocol

Operator-only `--create-destination NAME` prints destination ID and receiver
token once. No HTTP endpoint returns the token or its hash. Browser credentials
can list names/IDs, submit and inspect delivery metadata. Receiver credentials
authorize only `/v1/receiver/*`, scoped to their own destination; browser bearer
credentials cannot ACK, and receiver tokens cannot capture, upload, inspect
arbitrary source content, or change server settings. Shared ingress limits,
loopback Host checks and safe errors remain. Receiver requests have no Origin;
the desktop HTTP client supplies an Authorization header, never a query secret,
and follows no redirects. Neither client nor server logs credentials/content.

Version 1 is in `obsidian_contract.py`; the cross-language fixture is
`fixtures/obsidian-delivery-v1.json`. HTTP routes and operator setup are listed
in [OBSIDIAN_DELIVERY.md](../OBSIDIAN_DELIVERY.md).

States: `pending → claimed → imported | conflict | failed | ambiguous`.
Pending means awaiting a receiver; the server cannot infer whether Obsidian is
closed. Capture lifecycle never changes because of delivery. Imported requires
receiver ACK with the expected digest and current claim ID. A replayed matching
ACK is accepted after termination/lease expiry, covering a lost ACK response.
Only bounded enumerated error codes are accepted, not arbitrary diagnostic text.

## Lease and installation ownership

The first successful claim binds the destination permanently to the plugin's
persisted random receiver installation ID. Different installations holding the
same credential are refused, including after lease expiry. Connection checks
do not bind or write to a vault. This conservative single-receiver enrollment
is intentional: loss of the installation journal requires a new destination
and an explicit operator decision, not automatic reassignment of uncertain work.
Do not clone `data.json` into another vault or run independent copies of that
installation. A bearer holder/OS user can impersonate an installation; this is
not a boundary against a malicious authorized receiver or same-user OS attacks.

SQLite `BEGIN IMMEDIATE` serializes claims. A claim lasts 120 seconds and is
identified by an unpredictable per-attempt UUID. A second attempt cannot claim
an active lease; expired claims are eligible only for the enrolled installation.
Late ACK/failure from a replaced claim is fenced. The same claim request is
idempotent while its lease is active. Expiry never proves absence of a file.

The plugin processes one delivery at a time, with no overlapping check/poll.
HTTP requests have a 15-second absolute timeout and bounded response bytes;
polling is every 30 seconds, backing off to five minutes after errors. Unload
aborts HTTP, clears the timer and blocks continuation into new writes. Receipt
checks precede recovery writes. New settings are saved with receive disabled
and verification cleared; explicit check and enabling are required again.

## Journal and create-only recovery

`Plugin.saveData()` stores configuration, installation ID and at most 1,000
small journal entries: delivery/destination IDs, server identity, configured
inbox, expected relative path/digest, local state, timestamp and server ACK.
No Markdown, vault inventory or note content is stored in the journal.

| Last durable evidence | Recovery |
| --- | --- |
| Claim only; no journal | Wait for lease expiry; reclaim in enrolled installation; prepare intent |
| `prepared`, file absent | Create after fresh claim |
| `prepared`, file exists, even identical | Conflict; no adoption or overwrite |
| `creating`, exact file at recorded path | Read/hash, persist `written`, ACK |
| `creating`, absent file | Ambiguous: could have been created and removed; never recreate |
| `written`, exact file | Reclaim if needed, verify and ACK |
| `written`/`creating`, different content | Failed verification; never modify |
| Server says `imported` after lost ACK | Persist local ACK without inspecting/recreating file |
| Local ACK but server rolls back to pending | Refuse with receipt mismatch; never recreate |

Before creation the plugin records `creating`. It uses only `Vault.create()`
for notes, `Vault.createFolder()` for the explicitly configured inbox, and
`Vault.read()` for verification. There is no modify/append/delete/rename/merge
path and no auto-suffix. After ACK the file belongs to the user, including
changes, moves and deletion. Matching content alone never establishes ownership
without the pre-existing delivery journal intent. A process/power failure that
loses or corrupts `saveData()` is outside an atomic transaction: corrupt journal
fails closed; no distributed exactly-once or filesystem fsync guarantee is made.

## Filesystem boundary

The plugin independently validates each inbox component and basename, rejecting
absolute/traversal/hidden/config paths, NUL/control characters, Windows reserved
names, separators, alternate-stream syntax, percent escapes, non-NFC names and
trailing dots/spaces. Existing symlinks/junctions in the path are rejected with
read-only `lstat`, using the official desktop adapter's `getBasePath()`. These
filesystem reads neither import content nor write notes. The local root never
leaves Obsidian.

Obsidian explicitly supports symlinks/junctions to external directories; lexical
`normalizePath()` therefore cannot establish physical containment. The native
Linux fixture verifies that the Connector refuses a linked inbox. Windows
junction and macOS runtime checks remain unverified. There is still a TOCTOU
window against another OS process replacing a checked directory/filename while
Vault API writes it; that API exposes no atomic beneath/no-follow transaction.
Use a normal, non-linked inbox, do not externally change its structure during
import, and do not claim protection against a hostile same-user filesystem actor.

Primary sources checked 2026-09-30:
[official sample plugin](https://github.com/obsidianmd/obsidian-sample-plugin),
[official TypeScript API](https://github.com/obsidianmd/obsidian-api/blob/master/obsidian.d.ts),
[Vault API](https://docs.obsidian.md/Plugins/Vault),
[symlinks and junctions](https://help.obsidian.md/Files+and+folders/Symbolic+links+and+junctions).

Build mirrors the sample's TypeScript + esbuild CommonJS layout with external
`obsidian` and native modules. The production folder includes only `main.js` and
`manifest.json`; no CSS is needed. Tests, dependency trees and secrets are not
shipped. Installation/publishing remain separate; this slice authorizes only
manual development installation and an unmerged PR.
