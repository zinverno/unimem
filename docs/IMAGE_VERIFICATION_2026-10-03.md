# Image capture and attachment verification — 2026-10-03

Scope: one local PNG/JPEG, original-only or existing Tesseract OCR, explicit
delivery into desktop Obsidian through the standalone Connector. ASR profiles,
model evaluation and previous native evidence were not changed.

## Automated evidence

Focused image/old-delivery checks preceded the full run. Final commands:

```sh
.venv/bin/pytest tests/unit/api/test_image_delivery.py tests/unit/api/test_obsidian_delivery.py -q
.venv/bin/pytest --cov=core --cov=unimem_api --cov=unimem_images -q
.venv/bin/mypy src tests
.venv/bin/ruff check .
.venv/bin/ruff format --check .
npm test --prefix clients/obsidian-plugin
npm run build --prefix clients/obsidian-plugin
npm test --prefix clients/browser-extension
npm run lint --prefix clients/browser-extension
npm run build --prefix clients/browser-extension
npm run check-package --prefix clients/browser-extension
uv build --wheel --out-dir /tmp/unimem-image-20261003/dist
```

Results: focused **29 passed**; full **4403 passed**, **96.69% coverage** (90%
gate retained); strict mypy **281 files**; ruff check/format passed. Connector
**80 passed** plus TypeScript/build; browser **606 passed / 69 suites**;
package check **24 shipped files**. Lint: zero errors, two existing Gecko
warnings (ignored MV3 service worker and Android minimum-version metadata).
Sandbox-only process/socket restrictions were rerun with authorized host access;
host results above are the evidence. Node 24.14.1 was used locally.

The new deterministic tests cover signatures/MIME, byte/pixel/decode/time limits,
animation/corruption refusal, original-only, fake OCR text/empty/skipped/error,
durable identity/recovery, frozen v1/v2 Python/TypeScript fixtures, immutable
snapshot/manifest, destination/credential/asset isolation, old ACK rejection,
binary size/hash/truncation, both path conflicts, journal failures at each write,
rollback and persistent save queue, unload/settings/lease fences, partial write
recovery, lost ACK, no false imported and no restoration of user-owned files.
These are mocked/deterministic claims; they do not stand in for real OCR/native.
CI adds a separate images-extra job and preserves every previous gate.

## Real OCR, separate from doubles

Tesseract **5.5.3**, installed **eng/rus** data, existing `TesseractImageOcr`,
OEM 1 / PSM 3, no engine/model comparison. Production API ran with `--image-ocr`.
The screenshots were created in Zen from three known lines on white background,
28 px sans-serif, margin 55 px, line-height 1.7. Inputs and reference text are
preserved in [assets](assets/image-delivery-2026-10-03/verified.json):

| Input | Mode | Observed result |
| --- | --- | --- |
| [ru.png](assets/image-delivery-2026-10-03/ru.png), 29,387 bytes | OCR | All three Russian lines equal [reference](assets/image-delivery-2026-10-03/ru.txt) after stripping outer whitespace |
| [en.png](assets/image-delivery-2026-10-03/en.png), 23,366 bytes | OCR | All three English lines equal [reference](assets/image-delivery-2026-10-03/en.txt) after stripping outer whitespace |
| Photograph, 1,328,587 bytes, 4494×3371 | OCR | Engine invoked; empty text, no expected text in this fixture |
| Same photograph | Original-only | No OCR invocation or OCR metadata; explicit not_requested |

Observed text difference: Tesseract added a trailing newline; no word errors in
these two clean screenshots. This is a small smoke, not a general accuracy claim.
OCR describes no objects/scenes and does not infer meaning from a filename.

The photograph is Fructibus,
[Red apple on a plate 2017 A](https://commons.wikimedia.org/wiki/File:Red_apple_on_a_plate_2017_A.jpg),
CC0. Original download:
[JPEG](https://upload.wikimedia.org/wikipedia/commons/0/04/Red_apple_on_a_plate_2017_A.jpg).
SHA-256 `4de1eb5c433f10867327d8b6303bfc45635028e8c0eb416f167f10ddf3dbdf08`.
It was downloaded once as an authorized local test input; the product has no
URL-image download feature. No personal images or vault were used.

To repeat the small OCR smoke, run the production server with `[images]` and
`--image-ocr`, select these two PNGs and the hash-checked photograph in Zen,
click Extract text, then compare the persisted text to the two reference files.
Repeat the photograph with Save image. Full per-case IDs, original/snapshot/package
hashes, returned OCR and reference texts are in `verified.json`.

## Native acceptance

**PASS:** installed temporary extension 0.4.0 in **Zen 1.21.8b/Linux** → production
UniMem CLI → independent **Connector 0.2.0** → isolated **Obsidian 1.13.7**
(system package 1.13.7-2, Electron 43.6.0). Only `unimem-connector` was loaded;
Veynrel and Companion were absent. The test inbox was **Inbox/Nested/Images**.
Settings, attachment opt-in, file picker and explicit mode/Send actions were
driven through the installed applications, using Marionette and CDP for inspection.

- Four separate operations/packages yielded exactly **4 Markdown + 4 images**.
  Each Markdown equalled its stored snapshot byte-for-byte; each PNG/JPEG equalled
  the selected original and manifest SHA-256. Every journal recorded both files
  written and ACKed. Browser evidence: [imported result](assets/image-delivery-2026-10-03/zen-image.png).
- The first operation survived navigation away after durable acceptance and
  reopening with its existing ID. Repeated poll and Connector restart did not
  create a ninth file. All four results remained readable after restarting the
  production server **without OCR**, with unchanged content rows and digests:
  [base-read.json](assets/image-delivery-2026-10-03/base-read.json).
- After imported, a test note was edited, its image moved, and another attachment
  deleted through the native Vault API. Poll/restart preserved the edit/move and
  did not restore either old path. Seven files correctly remained:
  [user-edits.json](assets/image-delivery-2026-10-03/user-edits.json).
- The final built Connector was reloaded and polled successfully:
  [final-native.json](assets/image-delivery-2026-10-03/final-native.json).
  UniMem was then stopped. Opening the remaining photographic note displayed
  the local `app:` image, `complete=true`, natural size 4494×3371:
  [offline-final.json](assets/image-delivery-2026-10-03/offline-final.json),
  [offline Obsidian screenshot](assets/image-delivery-2026-10-03/obsidian-offline.png).

The UI driver initially read a stale selected operation ID during its fourth
upload; verification bound that row to the actual canonical capture/snapshot.
WebDriver visible text also strips the final newline. Exact snapshot equality
was checked against vault bytes and SQLite, not inferred from visible text.
Native acceptance used successful real transfers; fault injection and crash
boundary coverage come from the deterministic Connector tests above.

Two-file writes are not atomic. Missing creating/written files, changed digests,
lost/corrupt journals or external filesystem races can require manual review.
No automatic cleanup, overwrite, restore or suffix naming is claimed.

## Development artifacts

| Artifact | Version | SHA-256 |
| --- | --- | --- |
| `clients/browser-extension/dist/unimem-browser-0.4.0-dev.zip` | 0.4.0 | `f599f6644033bf13cdf276c27c2d1c64f395379ef8447f46df482532eab5fb6d` |
| `clients/obsidian-plugin/dist/unimem-connector/main.js` | 0.2.0 | `3cdc26cd3754617364107fd9d04908b9873a9601cdb9d357c8626b0db3be043c` |
| Connector `manifest.json` | 0.2.0 | `79144f446456c0879b52434f482c19218f3d449abc71fbdc3df5d747c93d5464` |
| `/tmp/unimem-image-20261003/dist/capture_core-0.2.0-py3-none-any.whl` | 0.2.0 | `ef3f47750f4c57c9b5cdb58189a325db87264d6e88e3cd9faff9fdfa12f76989` |

The generated artifacts are local, unpublished. Browser ZIP is deterministic;
the wheel hash identifies this build. New source is based on main `4ea4c89`
after PRs #35 and #36 merged; no ASR evaluation files are repeated in this diff.
GitHub CI status and final HEAD are reported with the PR, separately from these
local results.

**NOT RUN / unchanged:** B4 signing/install/update, other browsers, Windows/macOS,
mobile and historical Save As gate. No release, extension publication or merge.
The previous YouTube/audio native acceptances were not rerun; their unchanged
exports and v1 delivery are covered by regression tests.
