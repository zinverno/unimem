# ADR-022: Engine-independent media processing, and an opt-in media capability

Status: **accepted (Macro Phase 5A, PR 2); implemented.** This slice realizes the
engine-independent half of
[ADR-021](ADR-021-original-first-time-based-media-ingestion.md): the policy that
turns a probe result into canonical content, the capability that decides whether
a deployment accepts media at all, and the delivery semantics that keep "no
trusted structural result" distinguishable from "a trusted structural result that
fails policy". **ADR-021 is not rewritten**,
and neither is any earlier ADR.

No new Python dependency of any kind, no new `core` runtime dependency, no new
route, no lifecycle state, no persistence migration, and no schema change — the
contract set stays at `0.3`. **Macro Phase 5A remains open after this PR.**

**What this PR does not contain** is as load-bearing as what it does. There is no
`FfprobeMediaProbe`, no `unimem_media` package, no `--media` flag, and no engine
of any kind. Phase 5A-2 adds **no `subprocess` and no temporary-file machinery to
the media processing path**; the pre-existing `tempfile` use in
`core.storage.local`, which stages immutable raw objects and has done since
Phase 0B, is untouched and is not a media concern. The only way to exercise media here
is to hand `build_local_app` a `MediaProbe` programmatically. Concrete local
execution — and the security, startup, prerequisite and resource mechanics that
go with running an external engine — is Phase 5A-3, recorded with that
implementation and its own ADR. Nothing below should be read as deciding it.

## Context

ADR-021 fixed the vocabulary, the seam, and the shapes: `AUDIO` at schema `0.3`,
the `MediaProbe` port, the normalized result types, the MIME and container
allowlists, the structural requirements, and the durable metadata. What it
deliberately did not do was make any of it run. A schema-valid `AUDIO` envelope
was refused by every deployment, and no processor claimed the modality.

That left one question unanswered, and it is the one this slice is about: **who
decides whether a deployment has media at all, and when?**

Media is the first modality whose processing needs an engine that is not part of
this package, and the comparison with the two optional recognizers is worth
drawing precisely rather than loosely.

The document and image modalities each have a **baseline processor** that needs
no recognizer at all, so a deployment without one still ingests those captures.
What the recognizer adds differs between them. Image OCR is optional
*enrichment*: `ImageProcessor` already produces canonical content for any
supported image, and `--image-ocr` adds a text segment when there are words to
read. PDF OCR does more than enrich: beyond recognizing scanned pages, it lets
the build process a PDF whose pages carry **no embedded text at all** — the kind
`PdfProcessor` refuses — so the flag widens which documents are processable, not
merely how much is extracted from them.

Media is different from both. There is no baseline media processor to fall back
on: without a `MediaProbe` this deployment registers no media processor and
refuses the `AUDIO` or `VIDEO` capture at intake. That is not a smaller result,
it is no result — and accepting a capture this build cannot normalize would
strand it mid-lifecycle for a reason nobody could act on.

## Decision

### Two processors, not one

```
AudioProcessor   audio@0.1   AUDIO + audio/mpeg | audio/wav | audio/ogg  -> ContentType.AUDIO
VideoProcessor   video@0.1   VIDEO + video/mp4  | video/webm             -> ContentType.VIDEO
```

Siblings rather than one parameterized processor, because what they *require*
differs and that difference is the whole of the structural policy:

- **`AUDIO` requires at least one audio stream.** A file declaring itself audio
  whose container holds none is not a recording of anything.
- **`VIDEO` requires at least one video stream, and audio is optional.** A
  silent clip is perfectly ordinary video, so requiring audio would invent a rule
  no format has; a video carrying one or several audio streams is equally valid
  and its audio is described rather than refused. There is no rule against audio
  in a `VIDEO` capture, and `VideoProcessor` inspects only
  `result.video_streams`.
- Extra streams of the other kind are welcome and described — embedded cover art
  is an ordinary MP3.
- Several streams of either kind are valid. **No primary stream is selected.**
- Subtitle, data and attachment streams do not exist in `MediaProbeResult`, so
  they cannot affect these rules. A container carrying them is fine; 5A ignores
  them.

They share small private policy helpers and nothing more. There is no processor
framework, no base class and no registry: two processors is not a population.

Both depend on exactly two ports — `RawObjectStore` and `MediaProbe` — and on no
engine. `supports()` stays pure and reads only the capture record: the payload
type, that a raw original exists, and that its declared MIME type is in *that
processor's* allowlist. Probing to route would mean reading storage to answer a
routing question, and a capture whose original cannot currently be read is still
a capture this processor handles.

`process()` runs in a fixed order: record-only preconditions, open the original,
**probe exactly once**, `validate_media_probe_result`, container policy, stream
policy, build content.

### The declaration routes; the observation verifies

| declared | required alias | canonical family |
|---|---|---|
| `audio/mpeg` | `mp3` | `mp3` |
| `audio/wav` | `wav` | `wav` |
| `audio/ogg` | `ogg` | `ogg` |
| `video/mp4` | `mp4` | `mp4` |
| `video/webm` | `webm` | `webm` |

Only the required alias is checked. A probe legitimately reports a family — an
ISO base media file is `mov`, `mp4`, `m4a`, `3gp`, `3g2` and `mj2` at once — and
demanding an exact tuple would refuse an ordinary MP4 for being described
completely.

**There is no sniff-and-rewrite.** A capture declaring `video/mp4` whose bytes
probe as something else is refused, never corrected. This only works because the
two facts are observed independently, which is why ADR-021 kept the declared type
out of `probe()`.

A mismatch is a `ProcessingInputError` carrying one **fixed generic sentence**:

> media bytes do not match the declared supported format

Identical for every mismatch, and naming nothing the probe saw — not the observed
aliases, not the declared type, not the `file_ref`, not the digest. A message
that varied with the observation would be a read oracle over staged bytes.

The missing-stream refusals are equally generic: *submitted audio lacks a
verifiable audio stream*, *submitted video lacks a verifiable video stream*.

### Canonical media content

`ProcessingStatus.COMPLETE`, and **`segments = []`**.

That is the honest normalization of material nothing has listened to or watched,
exactly as ADR-019 fixed for an uninterpreted image. No transcript, no `VISUAL`
placeholder, no `PARTIAL` state, and no warning. Transcription is Phase 5B.

The **immutable original is the only asset**, at `AssetRole.ORIGINAL`. No
extracted audio track, no keyframe, no thumbnail, no derived binary of any kind.
**Streams are metadata; they are not assets.**

The title is exactly `CaptureRecord.title` or `None`. No embedded tag, artist,
album or filename is read — this build reads no tags at all.

**A new content object carries schema `0.3`, whatever the capture carries.** A
historical `VIDEO` `CaptureRecord` at `0.1` or `0.2` keeps its own version under
ADR-002's compatibility promise, and the content object this run produces is a
new document written by this build. `capture.schema_version` is deliberately not
copied across; the two describe different documents.

### The durable metadata

Exactly the shape ADR-021 fixed, now produced:

```python
metadata = {
    "media": {
        "container": "<mp3|wav|ogg|mp4|webm>",
        "duration_seconds": ...,  # only if observed
        "audio_stream_count": len(result.audio_streams),
        "video_stream_count": len(result.video_streams),
    },
    "audio_streams": [{"index": ..., "codec": ..., "sample_rate": ..., "channels": ...}],
    "video_streams": [{"index": ..., "codec": ..., "width": ..., "height": ..., "frame_rate": ...}],
}
```

Always present: `media`, its `container` and both counts, and both stream lists.
**An empty list stays present** — it is a real observation that the stream type
was absent, and omitting it would make "no audio" indistinguishable from "nobody
checked".

**Counts are stored but never independently observed.** They are exactly those
two `len()` calls and are computed by no other means, so they are a projection of
the lists beside them. The probe is never asked for a count and cannot report
one.

Optional facts are **omitted entirely** when not observed: `duration_seconds`,
and every per-stream field but `index`. No `null`, no `0`, no `"unknown"`, no
`"N/A"`. Nothing records an engine, a version, a bitrate, a language, an embedded
title, an artist, an album, tags, disposition, rotation, colour data, chapters,
subtitles, or raw probe output. Stream order is the normalized order the port
already guaranteed.

One `ProcessingRecord` per run, `audio@0.1` or `video@0.1`, `COMPLETE`, with
empty `warnings` and `errors`. **Missing optional metadata is not a warning** —
it is a fact about the file.

### The intake capability gate

```python
CaptureIntake(..., media_enabled: bool = False)
```

Default `False`, which is not a cautious default but the accurate one: a build
that was not handed a probe cannot honour a media capture.

One boolean, deliberately **not a capability registry**. There is one optional
acquisition capability gating two payload types, and a lookup table for that
would be a framework with one row.

**Capability refusal has priority over every media-specific check.** Once the
envelope itself has passed contract validation, a media envelope arriving at a
build without the capability is refused *before* the declared MIME type is
inspected, before the `file_ref` is parsed, before the raw store is asked
anything, before the clock is read, and before any record is written. It holds
when the reference names nothing and when the MIME type would be unsupported
anyway.

That ordering is the guarantee, not an optimization. A refusal that varied with
what happens to be staged would let a client probe durable storage through the
shape of an error, and a build with no media capability has no business touching
storage to discover which way to say no.

With the capability on, media is the staged-original path documents and images
already use: `file_ref` only, declared MIME in the allowlist, UniMem raw
reference parsed, read-only `exists()`, and the same immutable original recorded.
**No second write.** `text` or `html` alongside the reference is refused rather
than resolved — a capture stores one raw original and this build will not choose
between submitted representations.

**Intake never sniffs, opens, probes, infers a MIME type, or fetches a path or
URL.** Whether the bytes carry the declared container is a processing question.

### Wiring: both halves from one argument

```python
build_local_app(data_dir, *, pdf_ocr=None, image_ocr=None, media_probe=None)
```

`media_probe is None` decides both `CaptureIntake(media_enabled=...)` and whether
`AudioProcessor` and `VideoProcessor` are registered. **Both or neither, never
one.** The two broken states are unreachable by construction: media accepted with
nothing to process it, or media processors behind an intake that refuses every
such capture.

One `MediaProbe` instance serves both processors; the port is a single
synchronous method over a stream and holds nothing on the caller's behalf.

The probe arrives **already constructed**, exactly as the two recognizers do.
Wiring builds no engine and imports no adapter. PDF OCR and image OCR are
completely unchanged.

### Two failures, answered differently

| failure | meaning | HTTP | capture | content |
|---|---|---|---|---|
| `ProcessingInputError` | deterministic verdict about the bytes | 422 `processing_failed` | `FAILED` | none |
| `MediaProbeExecutionError` | no trusted structural result | 503 `media_probe_unavailable` | stays `PROCESSING` | none |

`MediaProbeExecutionError` stays outside the `ProcessingError` hierarchy, so it
flows through the orchestrator untouched and **must not be wrapped**. Its fixed
public sentence is:

> media structure probing could not be completed; the capture is stored and no
> content was produced

It carries no exception text, no observed container alias, no stream detail, no
stdout or stderr, no local path, no temporary-file detail and no engine
information — all of which the underlying error's own message may carry for a
server log.

**What the 503 row does not claim.** It does not say the bytes went unexamined:
a probe may fail before reading anything, after reading part of the file, or
after reading all of it. It does not say the failure is transient, and it does
not promise that identical bytes would succeed on a retry. The single durable
conclusion is that *no trusted structural result was produced*, so UniMem cannot
reach a verdict about the submitted media.

Keeping the rows apart is the point. Collapsing them would either record a
verdict this build has no evidence for, or hide a real refusal behind an
unavailability answer.

### Completed media replay, and the one thing it cannot prove

Replay is extended narrowly to `AUDIO` and `VIDEO`. `DOCUMENT` and `IMAGE` remain
non-replayable and `TEXT` is unchanged.

**A media replay requires the incoming envelope to declare schema `0.3`,
explicitly.** Intake records every capture at the current version and nothing
durably retains the version the request arrived with, so a legacy `0.1` or `0.2`
`VIDEO` resubmission has no stored fact that could prove it matches the original
request. **No `ingress_schema_version` is added to persistence** — that would be
storage added purely to serve a replay. Such a duplicate keeps the conflict it
would have received anyway, which is not a regression: a duplicate was always a
conflict.

So: a legacy `VIDEO` capture is accepted and processed the *first* time; only its
exact resubmission is a `409`. (`AUDIO` has no legacy case at all — the 5A-1
contract gate refuses it below `0.3`.)

Equivalence is proven from durable facts alone: same capture id, record
`COMPLETE`, both sides at `0.3`, same payload type and it is `AUDIO` or `VIDEO`,
same `source`, `context`, `intent`, submitted title and declared MIME type,
`file_ref`-backed with nothing alongside, a parseable UniMem reference whose
digest matches the durable `RawObjectRef`, and canonical content present.

**The path stays read-only.** No re-probe, no reprocessing, no rewrite, no
`MediaProbe` call, and no raw-store read added for replay — the submitted
material is a content-addressed digest, so "the same bytes" is a comparison.
Anything different or unprovable keeps the existing `409`.

### The upload route is untouched

`POST /v1/uploads` stays completely unaware of media capability. Staging
arbitrary MP3 or MP4 bytes remains valid whether or not the deployment has a
probe; the later `AUDIO`/`VIDEO` capture is what gets refused. No format
sniffing, no media MIME validation, no `media_enabled` behaviour, and no size
policy was added.

## Consequences

Positive:

- A deployment with a probe can ingest audio and video end to end, and a stored
  media object will not need reinterpreting when transcription arrives.
- The media processor policy is testable with a fake `MediaProbe` and requires no
  `ffprobe`, no `ffmpeg`, no subprocess, no media fixture and no media-specific
  temporary-file machinery — the seam was designed for exactly this. That is a
  claim about the media seam, not about every test in the suite: the API and
  intake tests exercise `LocalRawObjectStore`, whose Phase 0B staging has its own
  pre-existing `tempfile` use, and this slice leaves it untouched.
- An engine that is missing, crashed, or inconsistent cannot produce a `COMPLETE`
  object describing streams nobody observed, and cannot get a file blamed for it.

Negative / costs:

- **The allowlists are stated twice**, in `core.intake.service` and
  `core.processing.media`. That is the same deliberate duplication the document
  and image tuples already carry — intake decides what *this deployment* accepts
  at its boundary, which is a fact about the build rather than about any one
  processor — and a test asserts the two agree so they cannot drift.
- **Legacy `VIDEO` replay is not available**, as described above. The alternative
  was durable storage bought for a narrow case.
- **Media capability is programmatic only** until Phase 5A-3. There is no way to
  turn it on from the command line, which is correct for a slice with no engine
  but does mean the capability cannot be exercised by an operator yet.

## Alternatives considered

- **One `MediaProcessor` with a mode flag.** Rejected: the asymmetry between
  audio and video requirements would live in a conditional instead of in a type,
  and the router's disjoint-claim property would be lost.
- **Let intake import the processing allowlists.** Rejected: intake would then
  depend on the processing layer for a decision that is about the build's
  boundary, and the existing document and image tuples set the precedent.
- **A capability registry instead of one boolean.** Rejected: one optional
  capability gating two payload types is not a population, and the registry would
  be the abstraction arriving before the requirement.
- **Separate `audio_enabled` and `video_enabled` flags.** Rejected: they are
  enabled by the same engine and would only permit a half-configured build.
- **Wrap `MediaProbeExecutionError` in `ProcessingInputError`** to get one
  failure path. Rejected outright: it would durably mark a capture `failed` on
  the strength of a result this build has already decided it cannot trust.
- **Answer a container mismatch with 503 too.** Rejected: it is deterministic and
  will fail identically forever, so a terminal state and a 422 are the honest
  answers.
- **Add `ingress_schema_version` so legacy `VIDEO` can replay.** Rejected:
  persistence added to serve a delivery convenience, for a case whose duplicate
  was always a conflict.
- **Sniff the container and rewrite the declaration.** Rejected by ADR-021, and
  nothing here reopens it: the verification would become circular.
- **Emit `0` or `"unknown"` for unobserved optional facts.** Rejected: a stored
  object claiming `channels: 0` is an observation nobody made.
