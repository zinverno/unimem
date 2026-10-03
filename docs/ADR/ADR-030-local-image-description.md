# ADR-030: Explicit local image description over existing image operations

Status: accepted for this development slice. Depends on ADR-029; no release/publication.

A user explicitly chooses `describe` for one new PNG/JPEG capture. Original-only
and OCR remain separate. There is no combined mode, hidden OCR, analysis on reads,
revision system, chat, tool execution or arbitrary prompt. The existing operation
store, upload/raw store, intake, router, lifecycle and canonical persistence own
acceptance and recovery. Exactly one image processor is composed for each request.

`ImageDescriptionProcessor` reuses `ImageProcessor` to construct the original asset
in memory, then adds one `VISUAL` segment with `VISION` provenance. These domain
types already exist; the canonical schema does not change. The segment references
the same source capture and original asset. Only the final validated object reaches
persistence. An empty/refused/malformed/limited/crashed inference never creates a
placeholder complete object. The upload survives failure. Interrupted operations
are reconciled against canonical persistence, never blindly retried.

One fixed profile: Qwen3-VL-2B-Instruct GGUF Q4_K_M, Q8_0 mmproj, llama.cpp b11146.
Exact revisions, sizes and component hashes are versioned in `unimem_vision`.
Preparation is an explicit CLI command whose default only prints the download
plan. API startup and browser actions never download. An owner configures a local
profile directory on the API command line; no request controls executables, URLs,
paths, model arguments or prompts. Invalid files disable description, while saved
results and other image modes remain available. Components are verified again
before inference. Readiness is checked at API startup, including a sandboxed CLI
version probe; after changing local components the operator restarts the API.

The child runs CPU-only, two threads, 2048 context, 384 output tokens and 64–256
visual tokens. A separate EXIF-oriented, proportionally reduced RGB PNG is at most
768 pixels on its longest edge and 262144 pixels total. Transparency is flattened
on white; there is no crop. A reduced short edge below 28 pixels is refused. The
original 16 MiB / 40 million pixel intake bounds still apply independently. Only
pixels from the derived input reach the model, not filename, source URL, other EXIF
or expected fixture facts. Canonical metadata records transformations, dimensions,
input digest, exact parsed answer and raw response, runtime/model/component hashes,
fixed parameters and prompt version/hash, timings and limitations.

Bubblewrap provides separate user/network/PID namespaces, read-only runtime/model/
input mounts, private `/tmp`, and no home, vault, credentials or application tools.
The runtime receives a fixed task; text in pixels is untrusted content. The model
has no application authority regardless of what its response says. This is not a
claim that prompt injection or visual hallucination is solved.

One inherited file-lock slot per API data directory serializes vision and the
existing ASR worker. ASR model and parameters are unchanged. Original-only/OCR do
not acquire the heavy slot. An operation may wait in `running` for that slot; its
execution deadline starts when execution starts. Shutdown while waiting reconciles
it as interrupted. This is a bounded local policy, not a scheduler.

The image worker has a 240-second wall/CPU budget and 4 GiB address-space bound.
Vision itself has a 180-second wall budget and a sampled 3 GiB process-tree RSS
ceiling (shared pages are conservatively counted more than once). Timeout/unload
kills the worker process group; the sandbox's parent-death and PID namespace rules
also terminate inference descendants. A child retaining the inherited heavy-slot
and server-lease descriptors prevents an overlapping restart. No automatic retry.
Stdout and diagnostics are separate and bounded; only strict answer JSON is
accepted. The pinned CLI's generated-token performance counter must be present
and below the conservative boundary (383). Reaching the boundary is refused even
if JSON is syntactically complete. Nonzero exit, including interrupted CLI exit,
is failure. Diagnostics and prompt echo never become the description.

`image-markdown/1.1` is selected only for persisted descriptions. It labels machine
interpretation separately, says that standalone OCR was not performed, and fences
the exact description as untrusted literal text. The exporter alone supplies the
one verified image embed. Existing `image-markdown/1.0` and `markdown/0.1` keep their
meaning and historical snapshots are unchanged. Delivery **v2 is unchanged**:
one frozen Markdown snapshot, one original attachment, manifest/package digest and
package ACK. Connector 0.2.0 has no model knowledge and remains the only vault
writer. Connector 0.1.0 remains incompatible with required attachments as before.

The two-file create-only journal and its partial/ambiguous recovery boundaries
remain ADR-029's. No transport or journal redesign is introduced. Delivery errors
do not remove canonical description/original data. Reads, renders and sends never
run inference; delivery replay returns its existing immutable snapshot. After
import, user edits/moves/deletions remain user-owned and are not repaired.

The five-case feasibility assessment and native/automated gates are recorded in
[local image description](../local-image-description.md). This profile is for
coarse objects, interface elements and simple diagrams. Fine text, small objects,
complex connections and confident wording are not reliable; no inferred identity,
sensitive traits, user profile, tags, goals or recommendations are produced by the
application.
