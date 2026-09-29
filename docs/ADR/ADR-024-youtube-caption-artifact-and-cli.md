# ADR-024: External caption artifacts, durable normalization and CLI export

Status: accepted for the YouTube CLI slice. No browser or HTTP surface change.

## Context

The first source-specific slice needs real YouTube caption retrieval, persistence
and repeatable Markdown export. Captions supplied by YouTube are external input;
future speech recognition by UniMem produces a derivative of a different input.
Neither is an original video. Existing canonical schema 0.3 already has FILE,
TEXT content, transcript segments, optional temporal locations and provenance.

## Decision

Use optional `youtube-transcript-api==1.2.4` through its public list/fetch API.
Check requested languages in order, then manual before generated within that
language. Do not translate, retry, download video or use credentials. A bounded
Requests adapter allows only HTTPS `www.youtube.com` watch/player/timedtext
endpoints. It rejects redirects, cookies, auth and environment proxies/netrc.
Limits are three requests, 5/10-second connect/read timeouts, a 45-second elapsed
budget checked between requests/chunks, 4 MiB per metadata response, 2 MiB for
captions and 10 MiB total. OS DNS resolution is not a hard wall-clock deadline.
TLS verification stays enabled. Blocking is a reported result, never bypassed.

The stored artifact is UTF-8 JSON `unimem.youtube-captions/1`, MIME
`application/vnd.unimem.youtube-captions+json`. It contains canonical source URL,
video ID, acquisition completion time, retrieval library/version, the selected
track's language name/code, generated/manual and translation availability, plus
the UTF-8 decoded caption XML entity body. It contains no expiring track URL,
credentials, invented title, author or duration. It is a **UniMem serialization**,
not exact bytes of a wire response. This JSON is the immutable original of the
caption capture, never the original video. Keep XML rather than the library's
parsed snippet list: 1.2.4 substitutes zero for absent duration and filters text.

The application stages this artifact in existing raw storage and submits a FILE
envelope. An explicit `caption_artifacts_enabled=False` intake gate accepts only
this staged MIME when enabled. It resolves the existing storage-neutral ref and
checks existence before registration. No schema or database migration is needed;
the default HTTP composition keeps the capability off. No arbitrary file importer
or registry is introduced.

An external `YoutubeCaptionProcessor` implements the existing Processor port.
Only it creates canonical content; intake/orchestration retain all lifecycle
writes and content-before-complete ordering. Mapping:

| Artifact | Canonical representation |
| --- | --- |
| URL / provider | CaptureSource and ContentSource (`provider=youtube`; entry channel remains `api`) |
| captured_at | CaptureContext plus content metadata, for offline pure rendering |
| original JSON | One ORIGINAL asset, raw reference and digest |
| track and retrieval facts | ContentObject.metadata.youtube_captions |
| XML text elements, in source order | TRANSCRIPT segments, unchanged decoded text, position; no deduplication |
| start / dur, when supplied | TemporalLocation.start; end only if both start and duration exist; observed duration also in segment metadata |
| blank cue | SECTION with text absent and exact blank string in metadata.caption_text; an observed cue, not invented content |
| origin / method | Provenance ORIGINAL referencing the caption artifact; processor/version on every segment and processing record |

Missing times remain missing, zero remains zero, overlaps remain overlaps.
The XML subset is `transcript/text`, without DTD/entities, nested elements or
unknown attributes. Malformed, non-finite, negative or over-budget inputs fail
explicitly; no partial capture is reported as complete. Artifact JSON is bounded
to 16 MiB and tracks to 20,000 cues.

`python -m unimem_youtube capture` calls a callable application service and then
exports; `render` reads the completed capture and content stores by capture ID.
Retrieval imports occur only for capture. No separate lifecycle/database/queue.
Retrieval errors happen before acceptance; later errors leave the existing
pipeline's truthful durable state. Export never changes capture state.

Keep `markdown/0.1` unchanged. A separate pure `youtube-caption-markdown/1`
projection emits JSON-quoted YAML frontmatter, source, capture/content IDs, time,
track facts, cue text/time links and a statement that UniMem did not analyse
audio or images. Plain-text fences protect source text from Markdown/HTML
interpretation; fence length accounts for embedded backticks. The technical
heading is explicitly “YouTube captions”, not an inferred video title.
Output publication is atomic and exclusive in the explicit output directory;
an existing filename is a conflict, never silently overwritten. Failed export
can be repeated by capture ID without network or the retrieval extra.

## Consequences and verification

Core learns only the opt-in staged artifact capability; networking and
source-specific normalization/export remain outside it. Schema 0.1/0.2/0.3 and
existing processors/renderers retain their meanings. This deliberately extends
intake's supported capabilities without reopening previous manual acceptances.
The HTTP API, browser connector, OCR/image/document/media capabilities stay intact.

Tests exercise the real library over synthetic HTTP responses, pipeline/storage
restart, offline CLI render, malformed input, limits, output conflicts and
optional dependency isolation. A separate CI job measures the entire new package
and enforces its own coverage floor. Live YouTube smoke is opt-in and reported
separately; a blocked network does not make fixtures into live evidence.

Primary sources checked 2026-09-29:
[library documentation](https://github.com/jdepoix/youtube-transcript-api/tree/v1.2.4),
[Requests transport/streaming](https://requests.readthedocs.io/en/latest/user/advanced/).
