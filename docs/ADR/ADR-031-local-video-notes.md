# ADR-031: Explicit local video notes and bounded multi-PNG delivery

Status: accepted for this development slice; depends on ADR-028/029/030.
No release, publication, model comparison, scene detection or full-video understanding.

## Decision and existing owners

An explicit operation in the existing Zen management page processes one uploaded
MP4 into one `ContentType.VIDEO` object. Independent choices are speech (ru/en/auto),
up to three sampled frames, and optional descriptions of those frames (off by
default). Description requires frames and the prepared ADR-030 profile; speech
requires the prepared ADR-028 capability. No mode/model substitution or downloads
occur at acceptance. Profiles, language, flags, decoder and sampling versions are
part of durable request identity; a changed request under the same ID conflicts.

`unimem_video` is an application/adapter package outside core. `VideoCaptureService`
composes the existing intake, raw store, router, lifecycle and canonical stores.
The original structural-only `VideoProcessor` remains unchanged. Existing
`DurableOperationStore`, replay/reconciliation, `ServerLease` and `heavy_slot` own
acceptance and recovery. The browser reuses the image upload/reference mechanism
through `file-jobs.js`; it retains references, not video/transcript bytes. Closing
the page is safe only after confirmed upload and durable operation acceptance.

There is one original VIDEO asset, zero to three derived KEYFRAME assets, ASR
TRANSCRIPT segments referencing the original, and optional VISUAL/VISION segments
referencing individual PNGs. No independent audio capture or capture per frame.
Core schema stays 0.3. Each PNG records source asset/digest, requested time, actual
PTS/time_base/track, dimensions/orientation/transform and its own digest. Existing
isolated vision receives only that frame. Text in pixels has no tool authority.

## Decoder and common clock

The optional pinned PyAV 16.1.0 adapter consumes a controlled seekable raw stream;
no request supplies a decoder URL/path/command. ISO-BMFF signature/brands and MIME
are checked before acceptance; full structure, codecs and actual decode are
checked in the worker. Forced MOV/MP4 demux, an empty protocol whitelist, disabled
external data references/absolute paths and a refusing `io_open` callback forbid
nested/network opens and playlists. Filename extensions are not evidence.

Supported envelope: one progressive H.264 stream with square pixels, zero or one
AAC stream (1–2 channels, 8–48 kHz), no other streams; no rotation, format changes,
missing/nonmonotone timestamps or unsupported discontinuities. Limits and exact
failure semantics are in [LOCAL_VIDEO.md](../LOCAL_VIDEO.md).

Let `origin = min(stream.start_time * stream.time_base)` and
`D = max(stream end) - origin`. Every frame time is its actual PTS minus origin.
Sequential decoding chooses the first frame at or after `D/4`, `D/2`, `3D/4`;
multiple targets landing on the same frame keep that frame once. There is no
frame-number/fps clock and no sample seek, so inaccurate seeks cannot choose an
earlier keyframe. VFR is supported when PTS/duration are continuous and usable.
Missing timing, metadata/decode disagreement or complex gaps cause explicit refusal.
All packets/frames are checked even when frames or speech are not requested.

Audio PCM is staged as mono s16le/16 kHz without removing internal silence. The
first decoded audio PTS retains its offset from origin; ASR cue times are shifted
by that offset. Tracks are never independently rebased. AAC priming/padding is
recorded as decoded, with bounded codec/rounding tolerances, not replaced with a
requested one-second offset. ASR still uses the existing base/int8 profile and
VAD parameters. `transcribe_pcm` factors out recognition from the old decoder;
there is no copied recognition engine. The PCM child exits before vision starts.

## Stages, bounds and failure ownership

The parent video worker alone holds the shared heavy slot. Nested PCM/vision
adapters do not acquire it. Stages are extracting, transcribing, frame_1/2/3,
saving, with no invented progress percentages. Extraction is a single bounded
pass staging PCM and selecting PNGs; recognition follows, then persisted frame
assets and sequential optional descriptions. No LLM combines the descriptions.

Budgets: 60 s extraction, 300 s ASR, 210 s per frame (including profile/preprocessing
and existing 180 s inference), 30 s saving, 1020 s total execution after slot
acquisition; 4 GiB address space and 3 GiB sampled process-tree RSS. The parent
kills the process group and waits/reaps on timeout/shutdown. Linux parent-death
fences cover API/worker/PCM SIGKILL races; the existing vision PID namespace and
`--die-with-parent` cover its descendants. Kernel-orphan zombies await host init
but have no running inference, memory or open slot/lease descriptors.

No audio, ASR no_speech, and ASR not requested are distinct. Requested stage errors
end failed/interrupted; no placeholder completion. Originals survive. Recovery
reconciles committed canonical state, never automatically reruns uncertain inference.
Result/status/render/delivery need only saved canonical text and derived PNGs.
Model raw output and normalized text remain linked in saved metadata; generated
visual descriptions are never labelled OCR or used to silently correct speech.

## v3 and Connector migration

v1 remains text-only; strict v2 remains exactly one IMAGE attachment. A video with
frames uses a distinct v3 package: one immutable Markdown snapshot (max 1 MiB),
1–3 required PNGs (2 MiB each, 6 MiB total), unique asset IDs and relative names,
dimensions/byte sizes/digests, and an ordered manifest. No MP4, base64 or truncation.
A video without frames uses v1. Format is frozen on delivery creation/replay.

v3 package SHA-256 hashes compact JSON of protocol, delivery/destination/source
IDs, export format/version, suggested filename, Markdown digest, followed by the
ordered arrays `[asset_id,mime_type,size_bytes,sha256,relative_name,width,height]`.
Python and TypeScript use the shared frozen fixture. The v3 receiver route requires
a package ACK after all files verify. Old queues/ACKs cannot consume v3; failure to
negotiate v3 does not block ordinary v1/v2 deliveries. A separate explicit PNG-frame
permission is off by default and independent of existing PNG/JPEG permission.
Receiver credentials remain disjoint from browser credentials.

Existing delivery SQLite tables/readers persist v1/v2; additive tables own v3
permission and scoped asset references. Connector data version 3 migrates old v1/v2
journals, preserving receiver ID/results and converting a single attachment into
a bounded list. Journal identity/paths/digests precede writes; all targets are
preflighted, all attachments created/verified before Markdown, and the entire
package rechecked before ACK. Existing persist rollback, lease, settings and
unload fences remain authoritative. Foreign files conflict, including identical
bytes; interrupted ownership requires journal intent plus digest. No modify,
delete, rename, suffix, cleanup or post-import restoration. Multi-file creation is
not atomic; uncertain partial writes remain visible.

## Evidence and consequences

See [dated evidence](../VIDEO_VERIFICATION_2026-10-03.md) for deterministic native
codec tests with fake inference, one real-model native pipeline, a separate silent
clip, resources, quality errors and unverified platforms. The simpler alternative
for visual selection is a fixed sequential quarter sampling; this slice uses it.
Semantic key moments, scene search, video download, recording and summarization
remain outside scope. Existing B4 signed-install/update and other-browser gates
are not closed by this temporary Zen/Linux acceptance.

API references checked against the installed pin: [container documentation](https://pyav.org/docs/stable/api/container.html),
[frame documentation](https://pyav.org/docs/stable/api/frame.html),
[PyAV v16.1.0 container source](https://github.com/PyAV-Org/PyAV/blob/v16.1.0/av/container/input.pyx)
and [v16.1.0 video frame source](https://github.com/PyAV-Org/PyAV/blob/v16.1.0/av/video/frame.py).
The stable documentation URL served an older manual during this work; installed
16.1.0 signatures/source and synthetic decode tests were the decisive API check.
