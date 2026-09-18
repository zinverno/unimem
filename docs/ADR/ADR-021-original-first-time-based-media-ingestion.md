# ADR-021: Original-first ingestion of time-based media, and standalone audio as a first-class modality

Status: **accepted (Macro Phase 5A); being delivered in slices.** This document
records the **domain, schema and contract foundation** of Macro Phase 5A — the
original-first ingestion model, the `AUDIO` modality, schema `0.3`, and the
`MediaProbe` seam — and the canonical shapes those fix for the slices that
follow. **Phase 5A PR 1 is implemented**; the section
[What this PR implements, and what it does not](#what-this-pr-implements-and-what-it-does-not)
says exactly which parts.

**Scope boundary.** Concrete local `ffprobe` execution, and the security,
startup, deployment and resource mechanics that go with running an external
engine, are **not** this document's subject. They belong to the later 5A slices
that implement them and are recorded with that implementation and its own ADR.
This document constrains them — it fixes the port they must satisfy, the
allowlists they must honour, and the metadata shape they must produce — but it
does not specify them, and nothing here should be read as saying those concerns
are still open questions.

Macro Phase 4 is closed; the closure record is the *Macro Phase 4 — Image
Ingestion* section of
[`docs/ARCHITECTURE.md`](../ARCHITECTURE.md#macro-phase-4--image-ingestion),
together with [`docs/MANUAL_IMAGE_ACCEPTANCE.md`](../MANUAL_IMAGE_ACCEPTANCE.md).
[ADR-020](ADR-020-opt-in-local-image-ocr.md) is deliberately **not** cited as
that evidence: it records Macro Phase 4 as still *open* at the time it was
accepted, which was true then and stays as written. Macro Phase 3 remains
[closed](ADR-019-still-image-ingestion.md#macro-phase-3-is-closed). Macro Phase 2
remains [closed](ADR-016-pdf-document-ingestion.md#macro-phase-2-is-closed).
Macro Phase 1 remains
[closed](ADR-014-html-webpage-ingestion.md#macro-phase-1-is-closed). Phase 0
remains [closed](ADR-010-canonical-content-persistence.md#phase-0-is-closed).

This PR **does** change the canonical contract set: the schema advances to `0.3`
for one reason, `AUDIO`. It adds **no new Python dependency of any kind**, no new
`core` runtime dependency, no route, no lifecycle state, no persistence
migration, and no processor.

[ADR-019](ADR-019-still-image-ingestion.md) and
[ADR-020](ADR-020-opt-in-local-image-ocr.md) are **not edited by this decision**,
and neither is any earlier ADR. Where an accepted ADR says the schema was `0.2`
when it was accepted, that statement was true then and stays as written.

## Context

Every modality UniMem ingests today is something you look at all at once. A text
file, a webpage, a PDF, a DOCX, a still image: the whole artifact is present at
one instant, and normalizing it means reading structure out of bytes that are
not going anywhere.

Time-based media is the first kind that is not. An audio file or a video has a
*duration*, and almost everything interesting about it — what is said, what is
shown, what happens when — is only available by decoding it, which costs real
time, real memory, and in practice a system prerequisite the way Tesseract is
one.

That creates a temptation this decision exists to refuse: to make ingestion
*wait* for interpretation. A build that only accepts a podcast once it can
transcribe it is a build that cannot keep the podcast. ADR-019 already answered
the equivalent question for images — a valid image nothing has interpreted is
canonical content with `segments = []` and `ProcessingStatus.COMPLETE` — and
Phase 5A applies the same answer to media, where the gap between "stored
faithfully" and "understood" is far wider.

There is a second question images did not raise. `ContentType.VIDEO` and
`CapturePayloadType.VIDEO` have been in the closed sets since Phase 0A, at
schema `0.1`. There has never been an `AUDIO`. A podcast, a voice memo, a
recorded meeting and a music file are not videos, and the vocabulary had no
honest word for any of them.

## Decision

### Standalone audio is first-class, and distinct from video

`CapturePayloadType.AUDIO` and `ContentType.AUDIO` are added. Audio is **not**
modelled as a video that happens to have no picture, and not as a `FILE` with a
suggestive MIME type. The two modalities have different structural requirements
(below), different future processors, and different meanings to a person looking
for something they saved.

`AssetRole.AUDIO`, which has existed since `0.1`, is a **different concept** and
is not touched. It names an audio track extracted *from* some other content
object. Phase 5A extracts nothing, so nothing produces one.

### Schema `0.3` introduces `AUDIO` only

The canonical contract set advances to `0.3`:

```python
SCHEMA_VERSION = "0.3"
SchemaVersion = Literal["0.1", "0.2", "0.3"]
SUPPORTED_SCHEMA_VERSIONS == {"0.1", "0.2", "0.3"}
```

`0.1` and `0.2` remain readable, and a document written at either keeps its own
version when read, rewritten, or advanced through the lifecycle. Nothing stored
is rewritten because the current version moved.

**`audio` is valid only from `0.3`.** A `0.1` or `0.2` `CaptureEnvelope`,
`CaptureRecord`, or `ContentObject` naming it is refused at validation, for the
same reason a `0.1` `CaptureRecord` carrying a `title` is refused: the document
is wrong about its own shape, and a reader built against that version — with
`extra="forbid"`, as every contract here has — would reject the value.

**`video` is not gated, and must never be.** It has been valid since `0.1`. A
stored `0.1` `VIDEO` document is historically valid and stays valid at every
supported version. Gating it now to tidy up the modality would break the
compatibility promise [ADR-002](ADR-002-versioned-domain-contracts.md) makes, in
service of a rule it is not even about.

**The version rules are written down, not computed.** Which versions carry the
ADR-008 capture metadata, and which carry `audio`, are explicit named sets. They
are not derived by ordering version strings and not expressed as "the previous
version", because either would have silently re-classified `0.2` the moment
`0.3` arrived. A version is classified by being written down once, deliberately,
in a diff someone reviews.

### Phase 5A is original-first structural ingestion

A media capture becomes a canonical `ContentObject` that holds:

- the **immutable original** as its one asset, byte for byte;
- **`segments = []`**;
- optional structural metadata describing what the container declares.

`segments = []` is the honest normalization of media nothing has listened to or
watched, exactly as it is for an uninterpreted image under ADR-019 and invariant
19. Nothing is invented to stand in for the interpretation that did not happen.

**Transcription is Phase 5B**, and is not designed here. **The immutable
`ORIGINAL` is the only asset in 5A**: no extracted audio track, no keyframes, no
thumbnails, no derived renditions. Nothing is re-encoded, remuxed, trimmed, or
written a second time.

### `MediaProbe` is a narrow port in `core`

```python
class MediaProbe(Protocol):
    def probe(self, stream: BinaryIO) -> MediaProbeResult: ...
```

One method, one parameter. The implementation is **not** told the declared MIME
type, the capture modality, the capture id, a filename, a `RawObjectRef`, a
filesystem path, or any execution detail. It is asked one question — *what
structural facts were observed in these bytes?* — and given no way to answer a
different one, or to be told what answer is expected.

This is deliberately narrower than
[`ImageOcr`](ADR-020-opt-in-local-image-ocr.md), which does take the declared
MIME type and the validated dimensions so a recognizer can refuse an oversized
image *before* decoding. A probe has nothing to pre-authorize, and telling it
what the submitter declared would invite it to agree.

**The future concrete implementation is `ffprobe`, outside `core`.** `core`
imports no media framework, no codec binding, and no `subprocess`, and the media
side of `core.processing` neither uses nor reaches for `tempfile`. Which engine
reads the container is an adapter's business.

`core` is **not** free of `tempfile` overall, and this document does not claim it
is: `core.storage.local` has used `tempfile.mkstemp` since Phase 0B to stage an
in-flight raw write beside the finalized objects, on the same filesystem, under
a name no caller chose. That is the raw object store's own business and is
untouched here. The narrower guarantee Phase 5A-1 makes is the one that matters
for this port: **no `subprocess` or `tempfile` use is added to the media or
`core.processing` boundary, and no media adapter or temporary-file machinery
exists in this PR at all.** It is worth stating rather than assuming, because a
probe adapter is the one thing in this system that will plausibly want to spill
a stream to a path so an engine can be pointed at it — which is exactly why
`probe` takes a stream and cannot express a path.

### The normalized result

```python
MediaProbeResult
    container_names: tuple[str, ...]
    duration_seconds: float | None
    audio_streams: tuple[AudioStreamInfo, ...]
    video_streams: tuple[VideoStreamInfo, ...]

AudioStreamInfo   index, codec, sample_rate, channels
VideoStreamInfo   index, codec, width, height, frame_rate
```

- **The result carries no stream-count fields.** A count that travels beside the
  list it describes is a second thing that can be wrong about one fact, and the
  wrong one is the one people read. `len(audio_streams)` and
  `len(video_streams)` are the counts at this boundary. This is a rule about the
  *port*, not about storage: the canonical metadata below does record counts,
  and records them only as those two `len()` calls — see
  [The durable media metadata shape](#the-durable-media-metadata-shape).
- **No subtitle, data, attachment, or other stream type is modelled.** A
  container carrying them is perfectly acceptable; they are simply not
  described, and 5A ignores them.
- **No raw `ffprobe` JSON crosses the boundary**, and neither does an
  engine-specific object, an arbitrary mapping, or a temporary path.
- **No engine name or version is recorded.** Which build of which tool read the
  container does not change what the container says — unlike an OCR engine,
  whose identity is part of what its output means. The probing engine is **not**
  canonical metadata.
- **`frame_rate` stays a rational string.** `30000/1001` is the exact fact the
  container carries; `29.97` is a lossy rendering no later stage could undo.
- **Optional structural metadata is omission-first.** A fact the container does
  not declare is absent, never `0`, never `"N/A"`, and never guessed.

### One failure, and what it is not

`MediaProbeExecutionError` means: *the probe did not produce a result `core` can
trust.* It is **not** a `ProcessingError`, so it flows through the orchestrator
untouched — the capture stays `PROCESSING`, no content object is built or
persisted, and the delivery layer will answer a fixed `503` once the media
processors exist.

An adapter that contradicts its own contract raises this and **never**
`ProcessingInputError`. A malformed answer has told the system nothing about the
submitted bytes, so the bytes must not be blamed for it — and specifically must
not be recorded as having been examined and found to contain no streams.

`MediaProbePrerequisiteError` is deliberately **not** introduced in `core`.
Whether a particular engine is installed is a fact about a concrete adapter's
deployment; that error belongs to the future adapter package.

### The result is validated before it is believed

`validate_media_probe_result` re-checks, at run time, everything the type hints
only assert statically. An adapter is third-party code, and the rules it could
break would otherwise become permanent lies on a stored content object: a
container name still reading `"N/A"`, a duration of `nan`, two streams claiming
one index, a `frame_rate` of `"0/0"`.

Nothing is trimmed, lowercased, reduced, sorted, or repaired. Normalizing in the
validator would make it a second normalizer competing with the adapter's, and
would hide exactly the inconsistency it exists to catch.

The validator checks the **port contract only**. It reaches no policy verdict.

### Future policy, recorded here and implemented later

The following are accepted decisions about Phase 5A that later slices implement.

**Supported MIME allowlist:**

```
audio/mpeg   audio/wav   audio/ogg
video/mp4    video/webm
```

**Canonical container families:**

```
mp3   wav   ogg   mp4   webm
```

**Structural requirements:**

- `AUDIO` requires **at least one audio stream**.
- `VIDEO` requires **at least one video stream**.
- `VIDEO` does **not** require audio — a silent clip is valid media.
- Extra stream types (subtitle, data, attachment) are **allowed and ignored**.

**The declared MIME type routes; the probed container verifies.** There is no
sniff-and-rewrite: a capture declaring `video/mp4` whose container probes as
something else is a disagreement to refuse, not a declaration to correct. This
is the same rule ADR-019 fixed for images, and it only works because the two
facts are observed independently — which is why the port is not handed the
declared type.

#### The durable media metadata shape

**The canonical `ContentObject.metadata` a media capture carries is exactly
this, and a later slice implements it:**

```python
metadata = {
    "media": {
        "container": "<mp3|wav|ogg|mp4|webm>",
        "duration_seconds": <finite non-negative number>,   # only if observed
        "audio_stream_count": len(result.audio_streams),
        "video_stream_count": len(result.video_streams),
    },
    "audio_streams": [
        {
            "index": ...,
            "codec": ...,          # only if observed
            "sample_rate": ...,    # only if observed
            "channels": ...,       # only if observed
        },
        ...
    ],
    "video_streams": [
        {
            "index": ...,
            "codec": ...,          # only if observed
            "width": ...,          # only if observed
            "height": ...,         # only if observed
            "frame_rate": ...,     # only if observed
        },
        ...
    ],
}
```

**Counts are stored here, and that is not a contradiction of the port rule
above.** The two statements are about different things, and conflating them was
the error this section exists to correct:

- `MediaProbeResult` has **no** count fields. An adapter is never asked for a
  count and can never report one that disagrees with the streams it reported.
- The canonical metadata **does** carry `audio_stream_count` and
  `video_stream_count`, because a stored object is read by things that want the
  shape of a capture without walking its stream lists.
- The counts are therefore **never an independent observation**. A processor
  computes them as exactly `len(result.audio_streams)` and
  `len(result.video_streams)` and by no other means. They are a projection of
  the lists stored beside them, and the lists remain the truth.

What is **always present**:

- `media.container` — always, because it is only written after the declared MIME
  type and the probed container have been verified against each other, and a
  capture that fails that check never produces a content object at all;
- `media.audio_stream_count` and `media.video_stream_count` — always, including
  as `0`;
- `audio_streams` and `video_streams` — always, including as `[]`.

**An empty list is a real observation**, not a gap: it says this stream type was
looked for and was absent. That is why it is written rather than omitted, and it
is the one place in this shape where a present-but-empty value is the honest
answer. Correspondingly a count of `0` is a fact, not a placeholder.

What is **optional**:

- `media.duration_seconds` — omitted entirely when the container declares none;
- every per-stream technical field — `codec`, `sample_rate`, `channels`,
  `width`, `height`, `frame_rate` — omitted entirely when not observed.

**Optional means omitted, not filled in.** No `null`, no `0`, no `"unknown"`,
no `"N/A"`, and no guess is ever written for a fact the container did not
declare. `index` is the only per-stream field that is always present, because a
stream without one is not a stream the probe may report.

**Explicitly out of scope for the whole of Phase 5A:** transcription; segments
for media; extracted audio assets; keyframes; thumbnails; embedded tag or title
extraction; primary-stream selection; raw `ffprobe` JSON; full frame, packet, or
decode validation; and recording the probing engine's identity.

### Schema support and deployment capability are separate

A schema-valid `AUDIO` object is **contract vocabulary**. It is not a claim that
any deployment can ingest one. Runtime media capability will be **opt-in and
separate from schema knowledge**, in the same way `--pdf-ocr` and `--image-ocr`
are separate from the contracts' knowledge of OCR segments.

## What this PR implements, and what it does not

**Implemented in Phase 5A PR 1:**

- schema `0.3`, with `0.1`, `0.2` and `0.3` all readable and version rules
  written as explicit named sets;
- `CapturePayloadType.AUDIO` and `ContentType.AUDIO`, gated to `0.3` on all
  three versioned contracts, with `VIDEO` left historically valid;
- the `AUDIO` payload shape — `file_ref`, like every other staged modality;
- `core.processing.media_probe`: the `MediaProbe` port, the three frozen value
  types, `MediaProbeExecutionError`, and `validate_media_probe_result`;
- tests for all of the above, none of which need `ffprobe`, `ffmpeg`, a
  subprocess, a temporary file, or any system media tool;
- the browser connector advanced to emit schema `0.3` — see
  [Consequences](#consequences).

**Not implemented, and not to be inferred from anything above:**
`AudioProcessor`; `VideoProcessor`; `FfprobeMediaProbe`; a `unimem_media`
package; any `subprocess` use, or any `tempfile` use added to `core.processing`
(`core.storage.local`'s pre-existing Phase 0B staging is untouched); a `--media`
flag; prerequisite
checks or a startup self-test; a `media_enabled` intake flag; `AUDIO` or `VIDEO`
intake materialization; media MIME allowlists in intake; media replay; the
`503` HTTP mapping; real media fixtures; FFmpeg in CI; transcription; segments
for media; extracted assets; keyframes; embedded tags; routing changes; a
capability registry; or any new Python dependency.

**A local deployment still has no media capability at all.** A schema-valid
`AUDIO` envelope is refused by intake with the same
`UnsupportedCapturePayloadError` a `VIDEO` envelope has always been refused
with, and no processor claims the modality.

## Consequences

Positive:

- The vocabulary can finally name a podcast, a voice memo, or a recorded meeting
  as what it is, and a stored audio object will not have to be reinterpreted
  when transcription arrives.
- The policy Phase 5A needs is testable today, on a machine with no media
  tooling installed, because the seam is values in and values out.
- An inconsistent engine cannot quietly produce a `COMPLETE` object describing
  streams nobody observed.

Negative / costs:

- **A version bump touches every producer.** ADR-002 named this cost when `0.2`
  arrived, and it is being paid again. Intake stamps every record with the
  *current* schema version, and completed-capture replay
  ([ADR-013](ADR-013-completed-capture-replay.md)) requires the resubmitted
  envelope's version to match the stored record's. A connector left at `0.2`
  would therefore keep capturing correctly and **lose its lost-response
  recovery**, turning every replay into a `409`. The browser connector is
  advanced to `0.3` in this PR for that reason; any other producer must be
  advanced too. The extension's own version (`0.2.0`) is a different number and
  is not touched.
- A third supported version is a third row in every compatibility test, and the
  explicit version sets are one more thing to remember to extend. That is the
  point — extending them is a deliberate reviewed act — but it is friction.
- `AUDIO` is contract-valid and unusable at the same time, which is a state a
  reader has to be told about rather than one they would guess. Hence the
  separation stated above, and the intake test that keeps the two facts apart.

## Alternatives considered

- **Model audio as `VIDEO` with no video stream.** Rejected: it makes every
  future consumer ask "is this really a video?", and it gives a voice memo a
  name nobody would search for.
- **Model audio as `FILE`.** Rejected: `FILE` means "bytes this build has no
  modality for", which is the opposite of a first-class decision.
- **Gate `VIDEO` to `0.3` too, for symmetry.** Rejected, firmly: `VIDEO` has
  been valid since `0.1`, and retroactively invalidating stored documents to
  make a table look tidy is precisely what ADR-002's compatibility promise
  forbids. Symmetry is not a reason to break a guarantee.
- **Pass the declared MIME type into `probe`.** Rejected: the declared type
  routes and the probed container verifies, which only works if the adapter
  observes independently. Handing it the expected answer would make the
  verification circular.
- **Return raw `ffprobe` JSON and let `core` interpret it.** Rejected: it would
  put an engine's output format into the kernel, make the policy untestable
  without that engine, and let `"N/A"` reach a stored content object.
- **Record stream counts alongside the stream lists.** Rejected: two sources of
  truth for one fact, and the one that can be wrong is the one people read.
- **Normalize in the validator** — lowercase, sort, reduce. Rejected: it would
  hide the adapter inconsistency the validator exists to catch, and make `core`
  a second normalizer competing with the adapter's.
- **Record the probing engine and version, as the OCR ports do.** Rejected: an
  OCR engine's identity is part of what its output *means*, because a different
  engine would read different words. A container declares what it declares, and
  any correct probe reads the same facts.
- **Wait for transcription and ship media in one piece.** Rejected: it is the
  build that cannot keep the podcast, and ADR-019 already settled that storing
  faithfully without understanding is a real result rather than a placeholder.
