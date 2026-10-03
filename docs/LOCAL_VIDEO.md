# Local video → speech and selected frames → one Obsidian note

Linux desktop development slice. This samples static images; it does not analyze
all events, infer motion between samples or establish unseen actions/causes.
[Decision](ADR/ADR-031-local-video-notes.md) · [measured evidence](VIDEO_VERIFICATION_2026-10-03.md).

## Prepare and run

Use the existing [ASR preparation](LOCAL_AUDIO.md) and
[vision preparation](local-image-description.md); their models and quality profiles
are unchanged. Preparation/download is an explicit operator action, never a browser
side effect. The default installation and structural `--media` path stay optional.

```sh
python -m pip install -e '.[video,asr]'
python -m unimem_api --data-dir ./data --init-token
python -m unimem_api --data-dir ./data --create-destination 'My Obsidian'
python -m unimem_api --data-dir ./data --video-notes \
  --audio-model /absolute/path/to/prepared-base \
  --image-description-profile /absolute/path/to/prepared-vision
```

Omit unused model flags for frame-only processing. Speech/description selections
will then be refused, not silently disabled. Existing `--youtube` and image/OCR
options can be composed as before; video does not download clips or change captions.

Build/install the development browser 0.6.0 and Connector 0.3.0 using
[the browser guide](BROWSER_DELIVERY.md) and [Connector guide](OBSIDIAN_DELIVERY.md).
No signed/release artifact is produced. Both old v1/v2 delivery and their journals
remain readable by the new Connector. Back up plugin data before a manual downgrade;
old Connector versions do not understand a version-3 journal.

1. Save the browser API credential in the existing Zen connection page.
2. Configure the standalone Connector with its **separate receiver credential**.
   Explicitly allow **selected PNG video frames**, save, check, and enable receiving.
   This permission does not authorize MP4 or arbitrary binary files.
3. Select one local MP4 in the Video section. Independently select speech and
   ru/en/auto, up to three frames, and optional model descriptions (default off).
   Check capability status. The sampling rule is shown before upload.
4. Keep the page open until upload and durable acceptance are confirmed. Afterwards
   it can close; reopen the same operation ID to see genuine stage/status.
   Refresh does not resubmit or rerun inference. A conflicting parameter/profile
   under that ID is refused; a genuinely new processing attempt needs a new ID.
5. Open the ordinary preview and review speech, actual frame times and optional
   separate descriptions. Use explicit **Send to Obsidian**. `imported` means the
   Connector checked every PNG and the Markdown; a queued request is not an import.

Original MP4 remains in the UniMem raw store. There is no dangling video link in
the note. Selected PNGs and text remain readable after the API/models stop.

## Input and processing limits

| Item | Bound / policy |
| --- | --- |
| Input | One nonempty MP4, at most 32 MiB; MIME empty/octet-stream/video/mp4 plus actual ISO-BMFF signature and full decode validation |
| Tracks | Exactly one progressive H.264; zero or one AAC, mono/stereo, 8–48 kHz; no other tracks |
| Geometry | Square pixels, no rotation/interlace/format change, each side ≤1920, ≤2,073,600 pixels/frame |
| Duration | Container/common track span ≤120 s; actual decoded span checked independently |
| Work | ≤12,000 demux packets, 3600 video frames, 6000 audio frames, 7.5 billion decoded video pixels |
| PNGs | At most three; fit longest edge to 1024, RGB, bilinear, no crop; ≤2 MiB each / 6 MiB total |
| PCM | Staged mono s16le/16 kHz, ≤120 seconds, no internal-gap compression |
| Time | Extraction 60 s; ASR 300 s; each of up to three descriptions 210 s; save 30 s; total 1020 s after acquiring heavy slot |
| Memory | 4 GiB address-space bound per child; sampled aggregate process-tree RSS ≤3 GiB (shared pages conservatively counted repeatedly) |
| Delivery | Markdown ≤1 MiB; all selected PNGs required; no base64, MP4 or truncation |

Codec frame padding is not speech: decoded end may exceed container track end by
at most 50 ms and the global span by at most 25 ms; PCM itself remains capped at
120 s. Video continuity tolerance is 2 ms; audio continuity tolerance is two source
samples. Container/common span disagreement >100 ms or final decoded/track end
disagreement >50 ms is refused. These are validation tolerances, not alignment
estimates; saved PTS is never replaced with a nominal target.

The common origin is the earliest declared stream start, checked against the first
decoded PTS. Targets are ¼, ½ and ¾ of the common duration. Sequential decode takes
the first actual PTS at/after each target. Repeated target hits on the same PTS
produce one frame; identical pixels at different times remain separate observations.
If no usable sample/timing exists, the operation refuses rather than inventing an
exact time. Missing/nonmonotone timing, complex discontinuities, unsupported tracks,
corrupt input or exceeded budgets fail explicitly. Other containers/codecs, HDR
colour fidelity, rotated/phone-camera variants and other platforms are not claimed.

ASR timestamps are cue intervals, not verified word alignment. Audio's first actual
PTS offset is added to those cue intervals; neither track is independently zeroed.
No-audio, model no_speech and not-requested are different results. A required ASR or
vision failure ends failed/interrupted, preserves the source and does not create a
complete partial note. There is no automatic replay of uncertain inference.

## Saved evidence and protocol

`/v1/video/capabilities`, `/operations`, `/operations/{id}`, `/result`, and
`/frames/{asset_id}` reuse the protected browser credential boundary. Result reads
render saved canonical evidence only. Raw ASR text, normalized transcript, raw vision
JSON/parsed description, fixed model revisions/hashes and frame provenance remain
in the canonical object. Export quotes model text literally; speech and vision do
not correct each other. The Markdown documents performed/skipped stages and the
limited visual sampling, with separate frame descriptions and actual timestamps.

Framed video uses `/v3/deliveries` and `/v3/receiver/...`, with the existing claim,
lease, failure and ACK lifecycle. The capability permission is distinct from v2.
Manifest order is sampling order; digest covers the whole ordered package. No-frame
video uses v1. Format never changes during replay; strict v2 stays one IMAGE.
The Connector writes PNGs first and verifies all files before ACK. A partially
written package is not an atomic transaction. Foreign files conflict; ambiguous
own writes require review and are never removed. After import, changes/moves/deletes
belong to the user and are not repaired by polling or restart.

## Reproduce the small acceptance inputs

The unit tests generate their own tiny MP4s. For the manual real-model fixture,
use the Apache-2.0 [Vosk example audio at its pinned revision](https://github.com/alphacep/vosk-api/blob/78e66149f8ac56a64aa64306c1e8fc132c4eb154/python/example/test.wav)
and its [license](https://github.com/alphacep/vosk-api/blob/78e66149f8ac56a64aa64306c1e8fc132c4eb154/COPYING).
The generator requires its frozen SHA-256 and never downloads or reads personal media.

```sh
python scripts/create-video-fixtures.py /path/to/approved-vosk-test.wav /tmp/new-video-fixtures
```

With PyAV 16.1.0, Pillow 12.3.0 and DejaVuSans at
`/usr/share/fonts/TTF/DejaVuSans.ttf` (SHA-256
`6038a160b491e121c1f12c7bccb4a9c8730296e3adc1086a059404ed84b7451c`),
two generations produced the exact MP4 hashes in the dated evidence. The output
contains three known shapes/labels, a 10-second AAC clip with delayed public speech,
and a 3-second no-audio clip. It performs no inference and contains no credentials.
