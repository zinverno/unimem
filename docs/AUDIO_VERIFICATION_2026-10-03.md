# Local audio acceptance — 2026-10-03

This record separates real model execution, deterministic contracts and native
delivery. No user recordings, weights, tokens or diagnostic dumps are committed.
Operator commands and limits: [LOCAL_AUDIO.md](LOCAL_AUDIO.md). Historical
[YouTube BLOCKED](OBSIDIAN_VERIFICATION.md) and
[successful unchanged-code repeat](YOUTUBE_LIVE_VERIFICATION_2026-10-03.md)
remain separate. This slice does not establish the original timeout's cause.

## Engine and environment

Linux x86-64; Python 3.13.13; AMD Ryzen 5 5500U, 6 cores/12 threads,
7,254 MiB physical RAM and 12,638 MiB swap as observed locally.
faster-whisper 1.2.1; CTranslate2 4.8.2; PyAV 16.1.0; onnxruntime 1.30.0;
numpy 2.5.3. No CUDA installation or paid/external inference API.

Multilingual `Systran/faster-whisper-base`, revision
`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`; CPU/int8, 2 inference threads,
1 worker, beam 5, temperature 0, task transcribe, no previous-text conditioning,
Silero VAD/minimum silence 500 ms. Local manifest hashes:

| File | SHA-256 |
|---|---|
| config.json | `56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a` |
| model.bin | `d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9` |
| tokenizer.json | `fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab` |
| vocabulary.txt | `34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913` |

## Authorized samples and assessment

EN: [Vosk public example](https://github.com/alphacep/vosk-api/blob/78e66149f8ac56a64aa64306c1e8fc132c4eb154/python/example/test.wav),
under that repository's [Apache-2.0 license](https://github.com/alphacep/vosk-api/blob/master/COPYING).
8.308 seconds, PCM16 WAV. Reference is the spoken sequence
`one zero zero zero one / nine oh two one oh / zero one eight zero three`.
Model output preserved the 15-digit sequence, represented as numerals with
punctuation. Assessment normalized the known spoken number words to digits and
ignored punctuation; it did not require identical word spelling.

RU: [Russian LibriSpeech](https://huggingface.co/datasets/istupakov/russian_librispeech),
revision `a519c986bb3342cc8136d3d14e5ad8a4f1e1a2bd`, test row 0,
`audio/2671/2145/poemi_01_pushkin_0000.wav`, 11.35 seconds. The dataset card
identifies public-domain LibriVox readings. The independently supplied reference
is the opening of Pushkin's dedication, starting “Для вас, души моей царицы”.
The recorded reference was obtained before running ASR; model output was never
substituted with it.

RU recognition was **imperfect**: “души моей” became “тушимая”; “небылицы” became
“не полиции” in WAV / “не былицы” in Opus; other errors affected “под шепот”,
“старины”, “рукою верной”. On lowercase alphanumeric word tokens, `ё→е`, ignoring
punctuation, Levenshtein word distance was 9 over 24 reference words: **37.5% WER**
for both encodings. This is one literary sample, not a population quality estimate.
Substantial text was recognized, but some meaning changed: this profile must not
be described as reliable verbatim Russian transcription. The machine-warning and
human preview are material product requirements. No larger model was selected
silently to improve the reported result.

MP3 was locally encoded from the EN WAV with `ffmpeg -c:a libmp3lame -b:a 64k`;
OGG/Opus from RU with `ffmpeg -c:a libopus -b:a 32k`. ffmpeg was a fixture-preparation
tool only; production decoding uses PyAV. Silence was exactly three seconds of
zero PCM16 samples written with Python `wave`. The 113.5-second resource sample
repeats the RU PCM ten times; it assesses resource limits, not recognition quality.

| Local fixture | Bytes | SHA-256 |
|---|---:|---|
| EN WAV | 265914 | `dcfea5712c43a43ba7ae8083afb39d36993e5a69c46e88b68aaa72b65cb615bb` |
| EN MP3 | 67436 | `adc5b4c52eb2b45bcc60d77772dbbf0b4ebf56b76b2e80d27e58d87e1f1c9913` |
| RU WAV | 363244 | `8f3c840638f5ec0ea3f63fe157d49484c301846371f14bdb903427c366a16058` |
| RU OGG/Opus | 45019 | `19361cae75874cb00bea625fba963f0d4ee1da981873abca53406c6de7019e93` |
| Silence WAV | 96044 | `d303811b8c84619667cd0501342f84ec6cbe69f7aa3856dcf52fabda374c92b8` |

## Real-engine measurements

Each row below used a fresh production worker child through
`scripts/audio_real_smoke.py`, local weights, HF offline and an audit hook denying
Python socket connect/DNS/sendto (the hook was self-checked). This is not an OS
packet trace. No provider or network response supplied recognized text.
`elapsed` includes local model verification/loading, decoding and recognition;
wall includes worker startup/persistence/render. RSS is Linux `ru_maxrss` through
ASR completion, in an independent process for each row.

| Input | Seconds | Selected / detected | ASR elapsed s | Worker wall s | Peak RSS MiB | Result |
|---|---:|---|---:|---:|---:|---|
| EN WAV | 8.308 | en / en | 2.942 | 3.356 | 384.95 | transcribed |
| EN MP3 | 8.308 | auto / en | 2.900 | 3.355 | 384.87 | transcribed |
| RU WAV | 11.35 | ru / ru | 3.449 | 3.958 | 384.23 | transcribed |
| RU OGG/Opus | 11.35 | auto / ru | 4.678 | 5.165 | 385.40 | transcribed |
| Silence WAV | 3.0 | auto / null | 0.629 | 1.141 | 384.28 | no_speech; zero segments |
| Repeated RU WAV | 113.5 | ru / ru | 14.551 | 15.042 | 451.19 | transcribed |

These observations support the finite 120-second / 300-second worker / 3-GiB
address-space / 1.5-GiB RSS profile on this host with margin. They do not promise
faster-than-realtime processing on other audio/hardware, nor absence of
hallucinations on noise/music. Earlier exploratory measurements reused a process;
those cumulative RSS values are deliberately not mixed into this table.

Separate real HTTP run on isolated port/data: operation
`61f33a2d-e0f6-42f5-a68d-d088815ab53d` recognized the 113.5-second sample.
61 health requests made while status was `running`: all HTTP 200, median
1.597 ms, maximum 2.534 ms. Then the API was stopped and restarted **without
`--audio-model`**: same operation/replay and byte-identical Markdown, SHA-256
`17f95fedbea088f9564a6215ac1cd61137817a3fdab76128ebaa5cbe37cb21f9`.

An initial smoke launcher attempted to self-check its network guard inside the
execution sandbox, which refused even `socket()` creation (`EPERM`). That
attempt was interrupted, not successful ASR. The separately identified host
runs above completed with their explicit network-denial hook.

## Native Zen → Connector → Obsidian

Installed Zen Flatpak 1.21.8b, isolated temporary profile, dev extension;
Obsidian 1.13.7 / Electron 43.6.0, separate test vault with only the existing
standalone UniMem Connector. The Connector code/build was not changed.

The actual file input selected RU OGG/Opus and auto language, then the explicit
Recognize button uploaded binary data and obtained durable acceptance.
Operation `b47068de-9358-4350-a490-b013f73a8009` completed in 5.618 seconds.
Capture `52f82453-2698-4d68-b9cf-f2993031702b`, content
`56d3522b-c387-43f9-ae9e-da8cf4cb1c4b`.

The management UI was closed after acceptance. Marionette's replacement-tab
operation left this Zen test window without a usable browser context; this was
an automation interruption, not an ASR failure. Only the isolated Zen was
restarted, the same temporary dev addon loaded, and its session-only token
re-entered. The saved operation ID survived. UI refresh and Preview restored the
same result; no second ASR operation was created to bypass this check.
This workaround does **not** close the normal signed-install/restart B4 gate.

Before explicit Send: one operation/capture/content, zero deliveries and zero
vault Markdown files. Explicit Send created delivery
`62693b43-5fc0-485d-b7ca-ab775f8878e8` to test destination
`b6694241-a35d-4159-8609-b2e98470512c`. Existing Connector's Receive now imported
one file, journal state `written`, `acked=true`, server delivery `imported`.

Preview was read from DOM `textContent` to preserve the trailing newline.
Preview → persisted delivery snapshot → actual vault file were exactly equal:
**2,024 bytes**, SHA-256
`9e3939e678ce0d775b719616fa80d1f3025e43e6ae44ad9d5e459460383f2773`.
Repeated Zen status/preview and Connector poll preserved one operation and one
file, with Send disabled for the existing delivery. Hash equality proves
transport fidelity; the independent text assessment above describes ASR quality.

## Automated checks and remaining gates

Deterministic ASR tests cover signatures/MIME/size/codecs/duration/samples,
optional dependencies, explicit model preparation/integrity, native adapter
options, model/decoder errors, invalid times/text, no-speech, original/provenance,
same-ID replay/conflicts, crash reconciliation, actual child kill/reap on wall/RSS
budget and shutdown, one active child/API responsiveness, model-free render,
and >1-MiB delivery refusal with ordinary export intact.

The CI audio job intentionally mocks recognition; the real runs above are
separate. Existing core/API, YouTube, browser, delivery, lint, type and coverage
gates retain their floors; extracted B1 storage remains in its coverage scope.
Final command counts/build hashes are recorded below after final validation.

Remaining B4 signed distribution, normal installation permissions/restart/update,
other browser/platform and old native Save As chooser rows remain open in
[BROWSER_VERIFICATION.md](BROWSER_VERIFICATION.md). This slice does not claim
Windows/macOS codec/process or filesystem acceptance. No release/publication.

## Final validation and dev artifacts

- `ruff check .`, `ruff format --check .`: PASS (331 formatted files).
- `mypy`: PASS (272 source/test files; strict settings unchanged).
- `pytest --cov=core --cov=unimem_api`: **4353 PASS, 31 SKIP**, coverage **98.28%**.
  Existing optional/explicit-integration skips remain; dedicated CI jobs enforce
  their own prerequisites. Initial regression run exposed three wiring/route
  expectations; the complete rerun above passed after the correction.
- `pytest tests/unit/asr --cov=unimem_asr --cov=unimem_api.audio_http
  --cov=unimem_api.audio_worker --cov=unimem_api.audio_operations`:
  **62 PASS**, independent coverage **91.84%** (90% floor).
- `npm test --prefix clients/browser-extension`: **599 PASS**, 69 suites.
- Browser lint: 0 errors; the same two Firefox service-worker / Android metadata
  warnings. Package validation: 22 shipped files, exact source/archive match.
- Two consecutive `npm run build --prefix clients/browser-extension` runs
  produced identical bytes. Version stays **0.3.0**, dev-only ZIP:
  `clients/browser-extension/dist/unimem-browser-0.3.0-dev.zip`, SHA-256
  **`90d29a9d832e4417494e3a8ee524c15f9e73a6059d01ddaf8608fa735be18b50`**.
- Existing Connector **0.1.0**, unchanged: `main.js` SHA-256
  `594a18fa257b59d2d43b7da7b36267ebbe38f8555e038471813ade21cdfc442d`;
  `manifest.json` SHA-256
  `84b0c64da085f99d303e67f996c7c59a16da1f9b86a0faa56375459efa15948d`.

The final ZIP was loaded into the same installed test Zen. Temporary-addon
reload cleared its session-only token; entering that token again restored access.
With the current API restarted without ASR, the UI displayed the same 2,024-byte
preview and `imported`, Send disabled. No model execution or second note occurred.
Temporary-addon reload behavior is not signed-install/update acceptance.

Local private evidence directory: `/tmp/unimem-asr-20261003/`, including independent
smoke JSON/Markdown/stores, `measure-api/EVIDENCE.json`, native `EVIDENCE.json`,
preview and process/UI records. These paths are operator evidence locations,
not links embedded in delivered notes. They are not repository artifacts.
