# First UniMem RC: verification record

Checked 2026-10-04 on Manjaro/Linux x86_64, Zen 1.21.8b and Obsidian 1.13.7,
in a separate browser profile and synthetic vault containing only the standalone
UniMem Connector. No personal vault/profile, Veynrel or Companion participated.

**Комплект подготовлен; постоянная установка ожидает подписи.**
One external distribution gate remains: a matching Mozilla-signed XPI and its
ordinary installation/full Zen restart. No signing submission, AMO listing,
release/tag or Community Plugins submission was made.

## Frozen installable kit

Directory: `dist/first-rc-final/`. Build source:
`ce25f06d3d3f6524f01289041b246f44dbd5f93d`.
Subsequent evidence-only commits do not rename/rebuild this accepted artifact.
Run `sha256sum -c SHA256SUMS` in the kit directory.

| Component | Version | Artifact / SHA-256 |
|---|---|---|
| Local Python service | 0.3.1rc1 | `capture_core-0.3.1rc1-py3-none-any.whl` · `0765c55aa845985db45c0f9bf1c0460ad0a693a3a00bbd2620b496fd416aee00` |
| Zen extension, development package | 0.6.1 | `unimem-browser-0.6.1-dev.zip` · `6caa13ba137abed5bff246e357e5c56d807ddb5bae4a25f022f54ece3fc91a24` |
| Mozilla review sources | 0.6.1 | `unimem-browser-0.6.1-source.zip` · `b444325d0e2648cb79efc2b6aeb5d6ed16f7e09da5965a7587b1047f80721d7e` |
| Standalone Obsidian Connector | 0.3.0 | `unimem-connector-0.3.0.zip` · `ce563a8217ba333ba0d7252b25c23ba86ffd0036349caaa034703663d7f96d04` |

The kit also contains FIRST_RUN, MOZILLA_SIGNING, user unit, signed-payload
comparator, COMPATIBILITY and SHA256SUMS. All hashes are frozen in
[evidence/first-rc/SHA256SUMS](evidence/first-rc/SHA256SUMS).
SHA256SUMS itself: `796fb2cc1d7b00829c8f888265cb0ba635246afcd5ab52bb0a894b7ed17a2a55`.
Delivery protocols: v1/v2/v3; stable add-on ID: `unimem@zinverno.github.io`.

Installation and everyday commands have one entry point: [FIRST_RUN](../FIRST_RUN.md).
The launcher is a saved argument list for the existing Python CLI. No new server,
configuration framework, updater, model, provider or acquisition feature was added.

## Evidence boundaries: do not combine incompatible builds

The final wheel, Connector ZIP and user unit are **byte-identical** to those
installed for the successful `1d04d4c` native media pass. Final browser ZIP differs
from that pass (`95f2e93114cf0bc0169516db78b1f7d8e06f098fceea438ebbb3cdbcdb42034e`)
only by preserving `download_failed` as a typed ClientError in the shared Save As
helper. Tests and source archive also include its regression.

After that correction, the exact final browser ZIP was installed in the same
isolated profile. It reopened the same saved IDs/results, read confirmed imports,
and passed native Save As save/cancel and explicit credential-storage checks
against the final wheel/Connector hashes above. No heavy inference was repeated
for this UI correction. This is a bounded recheck of the changed browser behavior,
not a claim that every acquisition was submitted again with the final browser ZIP.
Per-operation acquisition SHA and component hashes are explicit in
[native.json](evidence/first-rc/native.json).

Intermediate `63e17d8` evidence is **not** final fresh-inference evidence: its PNG
was retained as an older-data upgrade case, and its first vision-video attempt
failed with `vision_budget_exceeded`. The UI reported failure, raw MP4 remained,
and no completed material/import was invented. The final-wheel video attempt
succeeded. Its approximately 106 seconds and 2.18 GiB peak process-tree RSS are
one observed run, not an ETA or model benchmark. The slowdown in the earlier
attempt was not assigned an unproven root cause; no budget/model was changed.

## Installed/native results

| Check | Result and boundary |
|---|---|
| Clean wheel installation | PASS: fresh venv outside checkout; no editable install. Repeated setup/destination preserved credentials and destination. [Record](evidence/first-rc/clean-wheel-install.json) names the earlier source SHA with the identical final wheel hash. |
| Saved launcher | PASS: same data directory across full API exits, independent cwd, preserved SQLite/raw store/receipts, no spontaneous acquisition/inference. Read-only diagnosis imported no app/model modules; missing model did not download. |
| User unit | PASS: shipped template with only isolated ExecStart/config/WorkingDirectory substitutions; two full starts/stops, `linked-runtime`, no enable/lingering, runtime link removed. [Record](evidence/first-rc/systemd.json). CI also checks original template syntax. |
| General connection check | PASS in native Zen: service/auth, optional readiness and destination/Connector protocols shown together. Audio-only/image-only/offline/401 behavior also covered by deterministic tests. |
| YouTube | PASS: one public captions source `jNQXAC9IVRw`, complete, explicit Send, v1 imported. No repeated network investigation. |
| Local audio | PASS: PCM WAV, existing base ASR, same previously saved ID recovered after fixing Zen's standard `audio/vnd.wave` alias; v1 imported. |
| PNG original + OCR | PASS: one short final-wheel image intent; original digest preserved, v2 imported. The earlier image survived API update/restart separately. |
| MP4 speech + selected frames | PASS: ten-second H.264/AAC fixture, description off, v3 imported. |
| MP4 with vision | PASS: one successful final-wheel ASR + three selected-frame descriptions, explicit opt-in, v3 imported. Same existing local base/Qwen profile, no weights downloaded. |
| Ordinary Save As | PASS on final ZIP: actual GNOME Files portal, file saved and byte-checked; cancellation showed “Операция сохранена”, retained the result and created no cancelled file. No mocked file-picker/download. |
| Connector install | PASS: exact shipped main.js/manifest in an initially empty synthetic vault; separate Markdown/image/frame permissions explicitly enabled through test settings. |
| Connector upgrade | PASS automated: actual built main.js loads saved v1/v2/v3 journals with identity/entries/digests preserved, repeated load identical, new permissions not enabled. This part uses a mocked Obsidian lifecycle, explicitly separate from native checks. [Record](evidence/first-rc/connector-upgrade.json). |
| Incompatible downgrade | PASS native: old Connector 0.2.0 refused the v3 journal and did not start its receiver; data.json and imported files remained byte-identical. Restoring exact 0.3.0 files recovered identity and journal. [Record](evidence/first-rc/connector-native-downgrade.json). |
| Full API + Obsidian restart | PASS: actual processes exited and reopened. Six imports stayed acknowledged, 14 files and a manual user edit retained their hashes; two further manual polls created no duplicate. Old results reopened without inference. |
| Token modes | PASS within native temporary runtime: default session-only had no local credential; explicit opt-in moved credential to local storage and survived management-page reopening. **Full signed browser restart is not covered.** [Record](evidence/first-rc/browser.json). |
| Mozilla source reproduction | PASS: fresh extraction, npm ci/build reproduced the exact final browser ZIP hash. Package scan found no credentials, weights, node_modules, test profiles or build-machine paths. |
| Mozilla signature / permanent Zen installation / full signed restart | **BLOCKED**: matching signed XPI absent. Temporary add-on reload is not accepted as this gate. |

The tested media fixtures were synthetic/public. Test models reused the already
prepared local weights; daily install instructions place data/config/models outside
`/tmp` and outside the checkout. Test applications have been stopped. No owner
startup service was enabled and no machine reboot was performed.

Two concrete native issues were fixed with fail-before regressions: WAV MIME alias
compatibility and the Save As cancellation code. No adjacent refactor was needed.

## Automated gates

Local full Python run: **4481 passed**, **95.81%** core/API coverage. Focused alias
regressions passed after the subsequent narrow fix. Full final browser run:
**617 passed**, lint **0 errors / 2 existing warnings**. Connector: **111 passed**.
Ruff/format and strict mypy passed. The two manifest warnings concern shared Gecko
background declarations and Android minimum metadata; no Android acceptance is claimed.

All mandatory checks passed at the frozen build source `ce25f06`. The final PR
HEAD must also pass the complete CI set, including the added clean-wheel install,
launcher/restart and user-unit check. GitHub is the authoritative final-HEAD status:
[PR #42 checks](https://github.com/zinverno/unimem/pull/42/checks).
No gates or coverage floors were weakened. Synthetic tests of `verify_signed.py`
cover equal payload, changed payload and missing metadata; they do not establish
Mozilla cryptographic trust.

## Remaining blocker and accepted limits

**BLOCKER:** owner obtains the unlisted Mozilla signature, then tests ordinary
installation and full restart on that exact XPI. Follow
[MOZILLA_SIGNING](MOZILLA_SIGNING.md); do not send signing credentials in chat.
No other unresolved code blocker was observed in this bounded pass.

Format/size/duration/quality limits, optional model preparation, selected-frame
semantics, originals retained in UniMem, separately granted attachment permissions,
and manual signed-XPI updates are intentional first-version constraints. Take a
consistent stopped-service backup before component upgrades. Replacing a binary
alone does not roll back a database/journal. Windows/macOS/mobile and complete
Chromium/Firefox acceptance remain outside this Zen/Linux RC; no PASS is claimed.

## Git integration

PRs #38, #39 and #40 were already merged before work began. Work started on current
`origin/main` `f5da0f7` (merge of #40, whose head was the supplied
`56216d497decdbb30b8564c4d9213142b1dbe2d3`). Final branch:
`chore/first-release-candidate`, base `main`, [PR #42](https://github.com/zinverno/unimem/pull/42),
left open/unmerged. No earlier PR was merged by this task.

Dependency order remains **#38 → #39 → #40 → #42**. The first three steps are
complete; integration of this RC into main is not. Any future base change requires
reviewing the resulting diff and rerunning CI. No force push/reset/clean was used.
