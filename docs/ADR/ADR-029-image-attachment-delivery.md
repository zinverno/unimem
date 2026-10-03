# ADR-029: Local image operations and required attachment delivery

Status: implementation, 2026-10-03. Extends ADR-020/026/027; existing captures,
markdown/0.1, delivery v1 and ASR retain their semantics.

An explicit image operation chooses original-only or OCR. Its composition uses
exactly one existing ImageProcessor or ImageOcrProcessor in a normal router.
This narrowly supersedes ADR-020's prohibition on request-selected processing
only for this new operation surface. Ordinary /v1/captures keeps deployment-wide
selection. Intake, raw store, canonical persistence and lifecycle remain owners.

Upload uses /v1/uploads; immutable operation receipts use the existing durable
store, a separate image_operations table, eight pending/running jobs and 10,000
retained identities. Replays/read/render/delivery never recognize again. A Linux
child has a 60-second total budget, 1 GiB address-space limit and an inherited
server lease. Recognition uses the existing Tesseract adapter (eng+rus, 30 s,
20 million pixels); known skips remain skips and errors produce no fake content.
Restart reconciles canonical evidence; uncertain execution is not retried.

The image boundary detects PNG/JPEG signatures, checks the existing structural
parser, caps input at 16 MiB and 40 million pixels, then verifies and decodes in
the child using the optional images extra (Pillow, no OCR dependency). Its small
unimem_images adapter owns decode validation; core and API import no native
imaging package. Empty or
octet-stream MIME permits detection; contradictory/other declarations fail.
Animation, corruption and unsupported formats fail; originals are never encoded,
stripped or replaced. EXIF/ancillary metadata travel into the vault unchanged.

image-markdown/1.0 emits one manifest-derived relative image link and fenced OCR
text with a fence longer than any input run. No HTML from recognition is rendered.
Original-only explicitly means no semantic analysis. Time comes from the capture.

Delivery v2 uses separate browser and receiver routes. V1 routes never return,
claim or acknowledge v2. Explicit receiver attachment permission is off on old
settings migration. Capability registration is destination/installation scoped;
browser submission refuses until a receiver opts in. Normal v1 polling continues.
The snapshot and exactly one attachment manifest are committed together; the
manifest names an opaque asset ID, PNG/JPEG MIME, byte length, SHA-256 and a fixed
safe sibling basename. A package digest binds identity, snapshot and manifest.
ACK v2 confirms this digest and both verified files. Binary bytes use a separate
destination/delivery/asset-scoped response; raw refs never authorize reads.

Limits: Markdown 1 MiB, image 16 MiB, combined content 17 MiB. JSON responses
allow 6 MiB + 16 KiB for escaping; binary responses allow 16 MiB, with a 30-second
download deadline. Nothing is truncated. The server never opens a vault.

The Connector writes only through public Vault.createBinary/readBinary and
create/read. Both deterministic sibling paths are checked before any file write.
Each file has prepared/creating/written journal evidence and expected digest;
the package intent is durable before download. Attachment is verified before
Markdown creation; both are verified before ACK. Existing files in prepared
state conflict, even with matching bytes. Creating/written may verify only their
previously journalled path. Missing creating/written files are ambiguous, never
recreated. Save failures roll back unconfirmed memory state and retain the queue.

Two files are not an atomic transaction. Partial writes remain journalled; no
cleanup, replacement or suffix creation occurs. Lease/settings/unload checks
fence new writes after every await. Imported receipts end recovery: later user
edits/moves/deletions belong to the user. Lost ACK responses reconcile from the
server receipt without restoring files. Filesystem races and loss/corruption of
the journal remain explicit limits, as in ADR-027.

Public API checked 2026-10-03:
[Obsidian Vault API](https://raw.githubusercontent.com/obsidianmd/obsidian-api/master/obsidian.d.ts).
