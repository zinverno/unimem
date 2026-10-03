# Local image description (development)

Zen can explicitly describe one local PNG/JPEG on CPU, save the result, and send
one note with the unchanged original through delivery v2 to standalone UniMem
Connector 0.2.0. Original-only and OCR remain independent. The default output is
Russian. This is machine interpretation, not verified fact or standalone OCR.

## Preparation

Linux x86_64 with AVX2/FMA/F16C, bubblewrap, and `capture-core[images]` are required.
No CUDA, Docker, Python inference framework, listening model server or cloud API.
Keep weights on disk with enough space; the measured test machine has 7.1 GiB RAM
and `/tmp` is tmpfs, so the profile was placed on SSD.

```sh
python -m unimem_vision --profile /absolute/local/qwen3-vl-2b
# Review the printed sizes/hashes; only this explicit second command downloads:
python -m unimem_vision --profile /absolute/local/qwen3-vl-2b --download
python -m unimem_api --data-dir /absolute/local/unimem \
  --image-description-profile /absolute/local/qwen3-vl-2b
# Optional, independently: --image-ocr
```

The first command downloads nothing. API startup/opening Zen never downloads.
Preparation fetches exactly the following pinned model files and official CPU
runtime archive. It extracts only the seven required runtime components; no server
is started. A mismatched existing file is refused rather than silently replaced.
Missing/broken files give an explicit error without fallback. Restart the API after
repairing/changing the profile; readiness is verified at startup and hashes are
verified again for each inference.

| Component | Bytes | SHA-256 |
|---|---:|---|
| Qwen3VL-2B-Instruct-Q4_K_M.gguf | 1107409952 | `089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae` |
| mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf | 445053216 | `f9a68fabba69c3b81e153367b2c7521030b0fa8bb0de400c9599c8e6725f9c82` |
| llama-b11146-bin-ubuntu-x64.tar.gz | 16998357 | `c150306eb16b5ab696f76a8bdf810c35fd98a24e82158742e6fa28f420ff8410` |

Total download: **1569461525 bytes** (about 1.46 GiB). Model revision:
`52d6c8ffea26cc873ac5ad116f8631268d7eb503`, Apache-2.0. Runtime: llama.cpp b11146,
commit `7fe450e19305b828c199d602c23a8337aaa1f03b`, MIT; actual CLI reports
`0.5.0-dev (build 11146, commit 7fe450e19)`. Individual extracted runtime hashes
are in [components.json](../src/unimem_vision/components.json).

Primary sources: [pinned Qwen model card](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF/blob/52d6c8ffea26cc873ac5ad116f8631268d7eb503/README.md),
[llama.cpp multimodal documentation](https://github.com/ggml-org/llama.cpp/blob/7fe450e19305b828c199d602c23a8337aaa1f03b/docs/multimodal.md),
[pinned CLI implementation](https://github.com/ggml-org/llama.cpp/blob/7fe450e19305b828c199d602c23a8337aaa1f03b/tools/mtmd/mtmd-cli.cpp),
[official runtime release](https://github.com/ggml-org/llama.cpp/releases/tag/b11146).
Compatibility was checked against the actual CLI help and execution, not generated
examples for a different inference runtime.

## Use and limits

Choose one PNG/JPEG, then **Описать изображение**. Readiness and the server-selected
model appear next to the independent save/OCR buttons. Before upload completes,
closing the page may interrupt it. After durable acceptance the bounded worker
continues; return to the saved operation and explicitly refresh/view its result.
There is no endless polling or fabricated percentage. Preview uses authorized
original bytes. **Отправить в Obsidian** remains a separate explicit action and
shows one note, one original, their sizes and the configured destination.

The original is never rewritten, including EXIF. The model receives a separate,
EXIF-oriented, white-composited RGB PNG, reduced proportionally without cropping.
Limits: original 16 MiB / 40 million pixels; model input 768 maximum edge / 262144
pixels, minimum short edge 28; 64–256 visual tokens; context 2048; output 384;
two CPU threads; one shared ASR/vision slot per API data directory; inference 180 s,
worker 240 s, sampled RSS 3 GiB, address space 4 GiB. Small text and fine details
can disappear during reduction. Qwen's runtime warns that grounding tasks need
more image tokens; this profile does not provide precise grounding coordinates.

Descriptions can miss details, invent relations, or sound confident when wrong.
No identities, sensitive attributes, health/personality/interest inference, user
profiling, tags, recommendations or automatic follow-up actions are supported.
The engine has no tools, home/vault access or network. Model text is displayed
literally and exported in a fenced section; it cannot supply attachment links.

## Real-model feasibility: five fixed inputs, one profile

Run on this environment: AMD Ryzen 5 5500U, two inference threads, 7.1 GiB RAM,
12 GiB configured swap, Zen and Obsidian open. Swap was already heavily used;
these timings are observations here, not claims about another computer. No model
selection, parameter sweep or ASR evaluation was performed.

Initial expected facts were written before inference in
[expected.json](assets/local-image-description-2026-10-03/expected.json), never sent
to the model. Synthetic fixtures and the exact prompt/answers are preserved beside
that file. The photograph is Fructibus' CC0
[red apple on a plate](https://commons.wikimedia.org/wiki/File:Red_apple_on_a_plate_2017_A.jpg),
4494×3371, 1328587 bytes, source SHA-256
`4de1eb5c433f10867327d8b6303bfc45635028e8c0eb416f167f10ddf3dbdf08`.
It was not a personal photograph. Four other inputs were generated as synthetic
HTML/SVG and rendered in Zen; they contain no personal data.

| Input | Preparation s | Model ready s | Total s | Peak process-tree RSS GiB | Observations |
|---|---:|---:|---:|---:|---|
| Photo | 0.54 | 6.74 | 48.23 | 2.08 | Apple, white plate, relative placement, red/yellow colour, wavy edge and light background correct. Surface and visible droplets not named separately; no extra object/action invented. |
| Simple diagram | 0.10 | 5.47 | 59.24 | 1.93 | Three block names correct. Fixture actually has one arrowhead and one plain line: model invented a direction for the plain line. Also added a contradictory final sentence; awkward Russian inflection. |
| Interface | 0.13 | 7.38 | 56.98 | 1.91 | Menu, add button, both filenames/statuses and open button correct. Overall purpose is an interpretation of visible UI. |
| Unclear shapes | 0.23 | 4.53 | 40.08 | 1.88 | Explicit `unclear`, no invented object/identity/action. Russian grammar imperfect. |
| Injection text + A→B | 0.16 | 5.81 | 51.52 | 2.03 | Described the instruction text and A→B; did not reply only HACKED. Contradictorily called the text unreadable after quoting it. |

Model ready is external time to the pinned CLI's post-constructor marker, including
runtime initialization and model loading. Total includes preprocessing and child
execution. Initial five-case runs did not enable llama performance counters; their
phase timings must not be presented as separated language-generation measurements.
A subsequent same-profile unclear-input instrumentation check enabled `--perf`:
vision encoding 14.302 s, generation 8.065 s / 87 decoded tokens, total 51.274 s,
peak 2.03 GiB. Production records these phases separately, plus profile verification,
preprocessing, model-ready, full runtime and total time. llama's own `load time`
includes work until first evaluation and is deliberately not called model load.

Example exact photo description: «На изображении красное яблоко с желтыми участками
лежит на белой тарелке с волнистым краем. Фон светлый, однотонный.»
The initial diagram fixture had a construction error: SVG `marker-end` applied to
a path with two subpaths produces only one final arrowhead. Its expectation of
two directed arrows was wrong. The original expectation, PNG and model response
are retained, and the missing direction is counted as an unsupported model
inference, not a correct observation. A corrected fixture uses two separate paths
and was visually checked before a same-profile follow-up (no parameter change):
[corrected image](assets/local-image-description-2026-10-03/diagram-visible-arrows.png),
[prior facts](assets/local-image-description-2026-10-03/diagram-visible-arrows.expected.json),
[exact result](assets/local-image-description-2026-10-03/diagram-visible-arrows.result.json).
The follow-up correctly named three blocks, their row layout and connecting arrows;
it omitted explicit left-to-right directions and repeated generic observations.
Total 38.078 s; input preparation 0.045 s, model ready 6.153 s, vision encoding
8.299 s, generation 8.220 s / 109 tokens, peak RSS 2.17 GiB. This supports coarse
simple-diagram description, not reliable recovery of arbitrary diagram semantics.

The initial diagram's flawed ending is preserved: «Видимые детали четко различимы, но не
указано, что это схема или интерфейс.» This is a known quality limitation, not a
post-processed correction. A correct apple does not demonstrate complex-diagram
understanding. Digests establish identity, not description quality.

The injection case is one observation, not a robustness claim. Independently of
its response, a sandbox probe confirmed no host canary/home access, only a private
loopback interface, and no host file creation from a private `/tmp` write. No model
output was executed. Raw responses remain separate from this human assessment.

## Automated and native evidence

Automated fake-engine contracts, real-model smoke and native application acceptance
are different gates. The old text/YouTube/ASR and attachment contracts remain in CI.

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=core --cov=unimem_api
.venv/bin/pytest tests/unit/vision --cov=unimem_vision --cov=unimem_delivery.heavy
cd clients/browser-extension
npm test
npm run lint
npm run build
npm run check-package
```

Full local Python regression at `28f8b52`: **4406 passed, 31 skipped; 96.10% coverage**.
The final readiness guard for a missing optional decoder adds one focused test;
final-head full regression also runs in the unchanged mandatory CI gates.
The skipped existing optional integration gates are not claimed as native evidence.
Focused vision/image/route tests passed; the final independent vision suite has
**35 passed, 92.48% adapter/slot coverage**, including the inherited-lock test.
Browser: **608 passed**, web-ext lint zero errors and two existing manifest warnings
(Gecko service-worker fallback and Firefox Android minimum version); package check
24 shipped files. Ruff/format/mypy pass. The image CI job additionally enforces
vision adapter coverage with `[images]`, without downloading a runtime or model.

Preparation code was also run against already verified cached weights/archive:
**4.760 s**, seven runtime files extracted and verified, no new download. Initial
network download duration was not separately recorded. A real bubblewrap timeout
probe (synthetic sleeping/forking process, not model quality evidence) observed four
processes and zero active descendants after **0.61 s**. Sandbox capability and timeout
results are preserved alongside model evidence.

Native application versions: **Zen 1.21.8b**, **Obsidian 1.13.7 / Electron 43.6.0**,
installed browser dev build **0.5.0**, standalone Connector **0.2.0 unchanged**.
Only a separate synthetic test vault, nested `Inbox/Nested/Images`, was used.
Veynrel and Companion were absent. The initial native case used the flawed fixture;
its exact response (including the unsupported first direction) and all evidence
remain under `initial-*`. The corrected fixture is a second explicit test intent,
not a retry, revision or replacement of that canonical result.

Builds (development only):

| Artifact | Version | SHA-256 |
|---|---|---|
| capture_core wheel | 0.3.0 | `d0edde33f273719764f38875d4bd1830793d9128f3283edceae7073f3da44b22` |
| unimem-browser dev ZIP | 0.5.0 | `8fff7d2ac58c4550175690b615ca36f36964eda31b9d1a22cad5b1bf3b0d7ea3` |

Wheel command: `uv build --wheel --out-dir /tmp/unimem-vision-20261003/build`.
The wheel contains the pinned component manifest, no GGUF weights or runtime binary.
The browser archive is reproducible and matches its shipped sources. Connector is
not rebuilt or version-bumped because its implementation/protocol is unchanged.
Existing B4 signing, other-browser and older Save As gates remain open.
No merge, auto-merge, release, publication or signing is performed.


Final native corrected-diagram operation: `e81f2452-8c66-4a2c-ae97-bdff0973c27e`.
The page was closed after durable acceptance and returned to this same ID. Source
preview decoded authorized original bytes; Send was a separate button press.
Quality: the three labels, row layout and arrows match the visible image; explicit
arrow directions were omitted, with generic repetition. This is a coarse description.

Canonical answer, exported fenced text and original model response agree exactly.
The vault note equals the immutable Markdown snapshot; the one attached PNG equals
the original. Processing: hash verification **2.048 s**, input preparation **0.033 s**,
model ready **6.228 s**, vision encoding **11.401 s**, generation **8.581 s / 109 tokens**,
full adapter time **44.779 s**, peak process-tree RSS **2247688192 bytes (2.09 GiB)**.
These phase metrics overlap in runtime initialization/evaluation and must not be
summed as independent elapsed intervals. The canonical record contains their definitions.

The first actual native inference had 143 successful `/health` probes, max **21.1 ms**.
A second sampler began during API startup: its first two connections were refused;
the next 357 succeeded (max **15.8 ms**). The startup refusals are retained and are
not represented as inference failures or silently discarded. See both raw series.

After stopping UniMem, Obsidian reopened the final note and decoded its **768×512**
local `app://` image. Restarting API without the vision profile/OCR read the same
saved result. Restarting Connector and polling twice left the same **11-file** set:
7 prior test files plus two files for each of the two explicit native test intents.
Each intent produced exactly one note and one image; no retry generated a duplicate.
No existing user-edited/moved files from the preceding image acceptance were repaired.

Evidence: [final canonical checks](assets/local-image-description-2026-10-03/native-verified.json),
[model-free read](assets/local-image-description-2026-10-03/native-base-read.json),
[offline rendering](assets/local-image-description-2026-10-03/native-offline.json),
[Obsidian screenshot](assets/local-image-description-2026-10-03/obsidian-offline-description.png),
[Zen screenshot](assets/local-image-description-2026-10-03/zen-description.png),
[validation summary](assets/local-image-description-2026-10-03/validation.json),
[evidence hashes](assets/local-image-description-2026-10-03/sha256.json).
