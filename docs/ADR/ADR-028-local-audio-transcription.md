# ADR-028: Explicit local audio transcription

Status: implemented; acceptance and resource evidence recorded separately.

## Decision

Add one opt-in `--audio-model DIRECTORY` scenario for new captures. The existing
`--media`, structural-only AudioProcessor, canonical schema 0.3, older readable
schemas and completed captures retain their meanings. No automatic reprocessing.
This extends ADR-021/022's deferred interpretation with a separately composed
processor, rather than replacing structural media processing.

The optional `unimem_asr` adapter uses faster-whisper 1.2.1 (MIT), multilingual
`Systran/faster-whisper-base` at revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`, CPU/int8, two inference threads,
one worker, beam size 5, temperature 0, no previous-text conditioning and the
bundled Silero VAD with 500 ms minimum silence. No GPU installation or provider.
PyAV 16.1.0 and CTranslate2 4.8.2 are pinned in the optional `[asr]` extra.
Core does not import an engine, decoder, subprocess, API or this adapter.

`python -m unimem_asr prepare --model-dir DIRECTORY` prints requirements only.
Only the additional `--download` flag fetches the four pinned model files and
writes a SHA-256 manifest. Startup verifies local files; recognition loads an
explicit directory with `local_files_only=True` and local tokenizer. Imports,
opening the extension, baseline API startup and rendering never fetch weights.

## Original and derived content

The browser uploads binary multipart data through the existing `/v1/uploads`.
The original bytes remain in the content-addressed raw store, unmodified; their
`sha256:` reference is the only accepted source locator. No base64 envelope,
client filesystem path, URL, filename execution or new text-file original.

`POST /v1/audio/operations` first confirms the staged object, size, signature and
MIME policy, then commits its operation receipt. Signature checks only select
one of three demuxers; successful acceptance does not claim codec validity or a
transcription. The child verifies and fully decodes the file before recognition.
Empty or `application/octet-stream` MIME permits signature detection. Explicit
known aliases normalize for intake, while the original declaration remains in
the immutable operation request. Unsupported declarations and contradictory
signatures fail. This is a new audio-operation boundary; ADR-021's existing
structural capture endpoint does not begin inferring or rewriting MIME.

The worker composes existing CaptureIntake and ProcessingOrchestrator with a
new AudioTranscriptionProcessor. Exactly one canonical AUDIO ContentObject is
persisted, then the capture becomes COMPLETE. Its original Asset references the
binary audio and digest. Ordered TRANSCRIPT segments carry timing, capture/asset
IDs and `source_type=transcript` with the ASR processor identity. They are model
output, never represented as externally supplied original captions.

The canonical metadata records confirmed container/codec/sample rate/channels,
fully decoded duration, selected and detected languages separately, detection
probability, engine/dependency versions, model revision and file hashes,
parameters, operation ID, capture time, elapsed time and peak process RSS.
Detected language is omitted as null on a VAD no-speech result; it is never
fabricated from the selected language. No filename-derived description, speaker,
summary, topic or translated text is produced.

## Durable execution and budgets

The existing B1 receipt implementation is extracted into
`unimem_delivery.operations`, parameterized only for the two concrete request
models and two fixed tables. YouTube keeps its prior table and request shape.
Audio uses `audio_operations` with its own routes, reserved capture ID, request
parameters and profile identity. Same-ID equivalent POST replays; changed
parameters conflict. Status/Markdown are read-only. Eight accepted pending/running
audio jobs and 10,000 retained receipts bound storage without deleting keys.

One Linux ASR coordinator runs one child at a time. It inherits the existing
server lease, so a new API cannot overlap an orphan child. Restart recovery
requires both COMPLETE capture and canonical content to declare success;
uncertain work becomes interrupted and is never automatically retried.
The source reference and request survive model/decoder/export/delivery failure.
Known ASR run failures may terminate the operation without inventing a canonical
capture failure: only the existing orchestrator writes capture lifecycle.

The independent limits are 32 MiB input, 120 seconds, 1,920,000 resampled mono
samples, 4,000 segments, 2 MiB UTF-8 transcript text, 300 seconds wall/CPU time,
3 GiB address space and 1.5 GiB monitored RSS. WAV supports unsigned 8-bit and
signed little-endian 16/24/32-bit PCM; MP3 supports MPEG layer III; OGG requires
Opus. All require exactly one stream, 1–2 channels and 8–48 kHz. Embedded cover
art, chained/multiple streams, float WAV and OGG/Vorbis are outside this slice.

PyAV receives a raw-store file object with a forced demuxer, empty protocol
allowlist, strict decoder error policy and a nested-open callback that always
refuses. Playlists cannot select other demuxers or pull network/local resources.
Decoded samples are counted incrementally, including resampler flush. The parent
kills and joins an over-budget child; kernel SIGALRM also terminates native work
in an orphan. Resource limits are Linux process bounds, not a claim of a full
codec security sandbox or guaranteed performance for every valid recording.

## Results, UI and delivery

Only exhausted recognition produces a validated canonical result. Text types,
sizes, finite nonnegative monotonic non-overlapping times and duration bounds
are checked; 100 ms tolerance covers the 20 ms timestamp grid and codec padding.
No missing words or timestamps are invented. Empty VAD/ASR output is no-speech,
an observation about this mechanism, not proof of speech absence or immunity
to hallucinations on music/noise.

The existing extension page owns a transient File object during upload. Its
bounded `audioJobs` storage contains only references, parameters and observations;
Web Locks serialize writes across pages. Before confirmed upload, closing the
page can lose untransferred bytes. After acceptance, closing either UI/background
does not cancel work or create a new operation. Explicit retry probes the same
ID first and only resubmits equivalent parameters when acceptance is unconfirmed.

AudioMarkdownRenderer reads persisted content without importing the engine or
decoding again. It renders safe fixed headings, source/time/IDs, language/model
facts, fenced text, timestamps and a machine-output warning. It says audio stays
in UniMem; it creates no attachment link. The existing Obsidian delivery route
selects this renderer, preserving immutable snapshot/digest, the 1 MiB refusal,
capture+destination replay, receiver auth, create-only import, journal and ACK.
The Connector itself requires no change.
