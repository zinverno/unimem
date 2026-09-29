# YouTube captions: capture once, render offline

This CLI slice retrieves an external YouTube caption track, stores an immutable
caption artifact plus canonical content through UniMem's existing pipeline, and
writes a Markdown note into an explicit staging directory. It does not save the
original video, analyse audio/images, generate summaries/topics or import a vault.

## Installation and commands

From the repository, with Python 3.13 or later and an activated virtual environment:

```bash
python -m pip install ".[youtube]"
python -m unimem_youtube capture 'https://www.youtube.com/watch?v=jNQXAC9IVRw' \
  --data-dir /tmp/unimem-youtube-demo/data \
  --output-dir /tmp/unimem-youtube-demo/notes --languages ru en
```

Languages are exact codes in priority order (default: `en`). Within each language,
manual tracks precede generated ones: an automatic Russian track beats a manual
English track for `--languages ru en`. Translation is never requested. Supported
inputs are HTTP(S) watch URLs with one video ID, `youtu.be/ID`, and YouTube
`shorts/ID`, `embed/ID`, `live/ID`. The acquisition source is canonical HTTPS;
playlist-only URLs, credentials, explicit ports and lookalike hosts are rejected.

Success prints one JSON object with `capture_id`, `content_id`,
`capture_status=complete`, `export_status=complete` and the absolute file path.
The filename is a safe deterministic digest of the capture ID. To export again,
use the returned ID and a different output directory:

```bash
python -m unimem_youtube render CAPTURE_ID \
  --data-dir /tmp/unimem-youtube-demo/data \
  --output-dir /tmp/unimem-youtube-demo/rendered-again
```

`render` needs only the base installation (`python -m pip install .`), not the
`youtube` extra. It reopens the existing stores and renders canonical content;
it does not reacquire or reprocess captions. Existing files are not overwritten.
Only the explicit data/output directories are written. Keep them outside a real
vault; agreed Obsidian delivery is the next milestone after Zen, through the
existing Veynrel/Companion boundary.

| Exit | Meaning |
| --- | --- |
| 0 | A completed capture was read/created and Markdown was published |
| 2 | Invalid command/input or acquisition failed; inspect the safe error code |
| 3 | Capture pipeline or saved-content read failed; no completion is claimed |
| 4 | Capture is complete, export failed; IDs are printed for a later `render` |

An identified pipeline failure reports the capture attempt ID and a code, not an
invented lifecycle state. For example, a content-store failure leaves the existing
orchestrator's `processing` receipt, while malformed caption input becomes `failed`.
No retry/reconciliation worker is provided. A retrieval failure before intake has
no accepted capture. An export failure never changes or removes persisted content.

## What is saved

[ADR-024](ADR/ADR-024-youtube-caption-artifact-and-cli.md) is the format contract.
The artifact is `unimem.youtube-captions/1` JSON, with canonical source/video ID,
acquisition time, library/version, selected track facts and UTF-8 decoded caption
XML. This is **UniMem's serialization of externally supplied captions**, not
exact network-response bytes and not an original video. Future UniMem audio
transcription would instead be derived from its separately preserved audio input.

The processor reads this artifact into ordered transcript segments with source
provenance and optional time boundaries. Missing duration remains absent, explicit
zero remains zero, overlapping/repeated text stays intact. A blank cue keeps its
exact blank text in segment metadata. Normal XML entity decoding and line-ending
rules apply; no trimming or deduplication heuristic is applied to cue text.
Unsupported XML structure, invalid/negative/non-finite timing, entirely blank
tracks and exceeded budgets fail explicitly, without a false complete result.

The separate `youtube-caption-markdown/1` projection keeps `markdown/0.1` unchanged.
It emits safely quoted YAML frontmatter, source and capture time, both IDs,
language/known track origin, cue time links, and text in safe plain-text fences.
Moment URLs use whole seconds rounded down; labels retain the stored start/end
values. Missing times do not gain a timestamp/link. A technical heading identifies
the video ID; no title, author or overall duration is invented. The note states
that UniMem did not analyse audio/images or download the video.

## Retrieval limits and refusals

Optional `youtube-transcript-api==1.2.4` supplies track discovery and its public
fetch operation. UniMem's processor uses the preserved XML body because the
library's parsed snippets otherwise default a missing duration to zero. The
library uses YouTube's public web/player interfaces; availability can change or
be blocked by YouTube. The public page's client key is not a user's credential.

- Only HTTPS `www.youtube.com/watch`, `/youtubei/v1/player`, `/api/timedtext`;
  watch/track requests must retain the selected video ID. No redirects, translation,
  proxies, browser profiles, cookies, login, paid API or CAPTCHA handling.
- Maximum three requests and zero retries; 5-second connection and 10-second
  socket-read timeout. A 45-second elapsed budget is checked at request/chunk
  boundaries, not a hard OS DNS deadline.
- 4 MiB per metadata response, 2 MiB per caption body, 10 MiB total response
  bodies, 16 MiB serialized artifact, 20,000 cues. Responses exceeding a budget
  are refused, not truncated into successful captures.
- Distinct safe codes include `captions_unavailable`, `language_unavailable`,
  `video_unavailable`, `request_blocked`, `access_required`, `timeout`,
  `network_error`, `http_error`, `invalid_response`, `destination_denied`,
  `redirect_denied`, `request_limit`, `response_limit`, `dependency_missing`.
  Upstream response text, signed track URLs and credentials are not printed.

## Opt-in live acceptance

Run against one public video whose captions you expect to be available:

```bash
python scripts/youtube_live_smoke.py \
  'https://www.youtube.com/watch?v=jNQXAC9IVRw' --languages en
```

The script makes an actual capture in a temporary directory, launches a second
CLI process to render the saved capture, compares Markdown bytes, prints only
sanitized status/IDs and removes its temporary data. It does not run during CI.
`BLOCKED` (exit 2) means network/access/prerequisites prevented live acceptance;
`FAIL` (exit 1) means the selected input or flow did not pass. Neither is `PASS`.
Use the ordinary commands above if you want to retain the output.

Observed on Linux/Python 3.13.13, 2026-09-29:

| Evidence | Result |
| --- | --- |
| Real public `jNQXAC9IVRw`, requested English, retrieval 1.2.4 | PASS |
| Capture ID | `4ce3f5a5-acee-4a40-ae62-3cadecb0c7fb` |
| Content ID | `25b1a282-1544-409e-807f-28cbac0b0f0a` |
| Re-render in a new CLI process against the same temporary data | PASS, identical Markdown bytes |
| Initial restricted-sandbox attempt | Network unavailable; permitted-network run above passed |
| Zen / Firefox / Chromium runtime acceptance for this feature | NOT RUN |

This is one successful live observation, not a guarantee for every video/network.
No browser was launched. Fixtures and automated results below are separate evidence.

## Automated verification

The focused suite uses the real retrieval library above synthetic HTTP responses,
then intake, normalization, real raw/SQLite persistence and export. Separate
process tests reject network/retrieval imports during `render`. Tests cover errors,
source/track selection, Unicode, missing times, repeated text, frontmatter and
output conflicts without writing to a vault.

```bash
python -m pip install -e ".[dev,youtube]"
pytest tests/unit/youtube tests/integration/youtube --cov=unimem_youtube
ruff check .
ruff format --check .
mypy
pytest --cov=core --cov=unimem_api --cov=unimem_youtube
```

Observed against implementation commit `266497b6c69b56d0b0f0da6199c49b0e31b536a2`
(2026-09-29):

| Check | Result |
| --- | --- |
| Focused YouTube suite | 93 passed; independent branch coverage 95.85% |
| Full local suite, core/API/YouTube coverage | 4,274 passed; branch coverage 97.35% |
| Ruff lint / format / strict mypy | PASS; mypy checked 234 source files |
| Base installation without retrieval dependencies | 60 passed; two retrieval-only modules skipped; API/application imports and mypy PASS |
| Wheel and sdist | Built; wheel installed without retrieval extra and rendered persisted fixture content in a fresh CLI process |
| Existing browser Node suite | 11 test files passed; no browser runtime acceptance implied |

The full local suite was run outside the restricted sandbox after an existing
API test stalled inside it; all data remained in temporary directories. The
dedicated CI job measures the new package independently with a 90% floor; the
existing quality job continues to test the base installation without the
retrieval extra. Final CI status is reported against the actual head of
[PR #31](https://github.com/zinverno/unimem/pull/31) at handoff.

The future browser adapter calls the same application service. Zen/Linux remains
the mandatory next browser target; manifest checks, Python tests and other
browsers do not establish Zen runtime support. GitHub and other multimedia
processors remain later work; this PR adds no browser/HTTP/pairing/queue/vault path.
