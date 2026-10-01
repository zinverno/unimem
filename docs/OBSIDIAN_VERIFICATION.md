# Standalone Obsidian delivery verification

Checked 2026-10-01 (Europe/Moscow), using temporary data and a synthetic caption
provider. This verifies delivery, not fresh live YouTube acquisition.

## Baseline and artifact

PR #33 was already merged (2026-09-29T21:54:12Z); its actual head matched the
supplied `bea715b8cef9cea7ea598695bf0004b1297a38c6`. The clean checkout contained
no earlier Obsidian implementation. `feat/obsidian-connector` started from
`origin/main` at `334f04b9cc6209f1015bed9312ba15b036dd2bfb`; PR base is `main`.
The new diff is the delivery slice. No previous PR was merged by this work.
The PR's checks are the authoritative hosted CI record for its exact head;
local results below are not a substitute for that record.

Plugin **0.1.0**: `clients/obsidian-plugin/dist/unimem-connector/`.
Two consecutive builds produced identical bytes, and the final bundle was
installed and exercised in the native test vault. The directory contains only:

| File | SHA-256 |
| --- | --- |
| `main.js` | `8029b0b9c9697b6beb319cf6510a2d9ec6afee72c0022fb7a4bb5fdf7d0640b2` |
| `manifest.json` | `84b0c64da085f99d303e67f996c7c59a16da1f9b86a0faa56375459efa15948d` |

No stylesheet is needed. No dependencies, credentials, test data, source maps
or absolute build paths are in that directory. Generated files are ignored by
Git; [setup instructions](OBSIDIAN_DELIVERY.md) reproduce the dev artifact and
explain copying just these files to a test vault.

Browser **0.3.0** dev ZIP:
`clients/browser-extension/dist/unimem-browser-0.3.0-dev.zip`, SHA-256
`5c85b71c206dbd8277f2c937a9ea4e3d5e5dc46e789ca97a7a13ee35f39e4312`.
The final ZIP was reinstalled in the isolated Zen profile: protected connection
passed, the password field cleared, and the original delivery restored as
**Импортировано**. Replacing the temporary add-on invalidated its old tab;
the test profile was fully restarted before that final smoke. This is not
evidence of a normal signed-extension update or persistent signed installation.

## Automated checks

| Check | Result |
| --- | --- |
| `ruff check .`, `ruff format --check .` | PASS; 303 files formatted |
| `mypy` | PASS; 251 source files |
| `pytest --cov=core --cov=unimem_api` | **4,322 passed**, no skips; **98.73%** coverage, existing 90% floor retained |
| Existing protected YouTube delivery gate | **36 passed**, **91.75%** coverage; unchanged test selection and 90% floor |
| `npm test --prefix clients/obsidian-plugin` | **23 passed**; includes real loopback HTTP and real temporary symlink |
| `npm run build --prefix clients/obsidian-plugin` | PASS; TypeScript check, repeated build hashes and two-file allowlist |
| `npm test --prefix clients/browser-extension` | **593 passed** |
| Browser lint/build/package verification | PASS; zero lint errors, same two pre-existing Gecko compatibility warnings; 20-file package |

New server tests cover durable commit before response (including forced commit
failure), immutable replay after a renderer change, concurrent registration,
destination/token isolation, browser denial on ACK, receiver denial on capture,
single-installation claim ownership, lease fencing/expiry, idempotent/lost ACK,
restart, oversized UTF-8 Markdown, unchanged capture lifecycle, and reserving
ingress capacity before asynchronous receiver authentication.

Plugin tests cover read-only checks, disabled polling, folder creation,
create-only imports, existing identical/different files, digest mismatch,
prepared/creating/written crash boundaries, lost ACK request/response, restart,
duplicate polling, expired claims, config switches, unload during HTTP/save/
directory creation, server rollback after local ACK, edited/deleted imported
notes, corrupt journal, traversal and platform-specific paths, safe errors and
absence of destructive APIs. Cross-language tests consume
`fixtures/obsidian-delivery-v1.json` for JSON, IDs, enums, error codes and exact
UTF-8/CRLF digest. Browser tests cover durable server status and idempotent send,
scope isolation and absence of receiver secrets; existing download tests remain.

Review found and fixed three issues before handoff: receiver authentication had
to reserve ingress capacity before its first await; settings-save continuations
had to stop on unload; folder creation had to check cancellation after each
await. Regression tests cover them. The final independent review had no
remaining required findings.

## Native Linux acceptance

Actual installed **Obsidian 1.13.7** (Electron 43) and Flatpak **Zen 1.21.8b /
Gecko 152.0.6**. Separate profiles, server data and vault live under
`/tmp/unimem-connector-native/`. Only **unimem-connector** is installed/enabled in
the test vault. Veynrel and Companion are absent. No personal vault was used.
Obsidian was controlled through its native CDP endpoint; Marionette controlled
the real installed Zen temporary WebExtension, in headless mode.

The YouTube action used the installed extension's menu listener with a synthetic
tab event. The subsequent Send button was clicked in the real management-page
DOM. This is not a physical mouse click on a toolbar menu, and captions came
from `tests.delivery_process`'s explicit synthetic provider. HTTP, processing,
storage, browser delivery action, plugin and Vault API were real.

| Step | Observed result |
| --- | --- |
| Configure plugin, click connection check | Correct destination; zero Markdown files and no inbox creation |
| Save settings / disabled receive | Reception disabled and verification cleared; disabled poll does nothing; explicit check/enable restores reception |
| Close Obsidian; double-click Send in Zen | Exactly one delivery, **pending**, zero Markdown files |
| Start Obsidian with Connector enabled | Exactly one new note under `Inbox/UniMem`; read-back equals all 1,205 snapshot bytes; server **imported** |
| Browser delivery refresh | **Импортировано**, same destination and basename |
| Repeat poll; restart Obsidian; poll again | Still one note; no second creation |
| Open-last command | Opens the imported note through Obsidian |
| Edit note manually through Obsidian editor, then poll | User text remains; no restoration or duplication |
| Stop browser background, then refresh | Same server receipt restored; no new delivery |
| Pre-existing target filename | **conflict**; original content preserved, no suffix |
| Actual process crash after Vault.create | Recovery after natural lease expiry verified exact existing file and ACKed it without another create |
| Linked inbox to an external temporary directory | Obsidian adapter recognizes the link; Connector reports **path_rejected**, outside directory remains empty |
| Install final plugin bundle and import another synthetic capture | Exact 1,205 bytes, ACK persisted, server imported; earlier user edit/conflict preserved |

Main browser-send evidence:

- Operation: `0a53a98e-4e60-426c-b334-7ad0fa26b921`.
- Capture: `8d3fe799-8c82-4d2d-b8bc-0264c9fcf3fe`.
- Delivery: `fa7be045-875b-414a-bdec-5db7f4679c4f`.
- Destination: `9c64848f-d15c-435b-b2a8-cfb5eaa8f4ec`, `Acceptance Obsidian`.
- Original snapshot: 1,205 bytes, SHA-256
  `c699bef6265ce1a072cb878807aee88e27eb0ba0447c27bc88ed1df669f17973`.

After the deliberate edit, that note no longer matches the immutable snapshot;
this is the expected successful no-repair check, not a failed initial import.
Conflict delivery `b030d1c2-2c08-452a-adab-9aec6a72d64f` preserved the pre-created
`Existing user note.` file unchanged.

Crash delivery `a2120e74-64d9-4c6b-80f8-152a71b3dae7` used a **test-only** CDP
wrapper around `Vault.create`: it awaited the real create, then withheld its
return so the on-disk journal remained `creating`, ACK false. SIGKILL terminated
only the isolated Obsidian process. On restart, the 120-second lease expired
naturally; recovery read/hashed the existing file and reached `written`, ACK
true, server imported. SHA-256 remained
`567de57b419f81a93aa59a24612ae5a646c05cb9b3b1cc249abac2942ef534d3`.
Neither the wrapper nor a production fault-injection switch is shipped.

Final-bundle smoke delivery `97e8ef8b-ee2c-4598-8824-1a9516cb6c4f` had SHA-256
`e147f841cad20857ae80b2e30a96a574125000fced6c29cc7da96c4ff26999f8`, equal to the
server snapshot. The final vault has four Markdown files for three deliberate
successful deliveries plus one pre-existing conflict file. The rejected symlink
delivery creates none. This count includes separate acceptance scenarios;
the initial browser delivery alone produced exactly one note.

Final UI screenshots, containing only synthetic data:
[Zen imported state](assets/obsidian-connector/zen-imported.png),
[Obsidian final-bundle note](assets/obsidian-connector/obsidian-imported.png).

To repeat using the existing offline fixture, install development dependencies,
start `python -m tests.delivery_process server success <TEMP_DATA> 8765`, pair a
destination with the CLI, and follow the browser/plugin setup guide. That
**test-only** server uses the browser token `a` repeated 43 times and the fixture
URL `https://www.youtube.com/watch?v=abcdefghijk`. Do not use it with personal
data or present its acquisition as live. Close Obsidian before Send to observe
pending, then follow the rows above. Fault injection belongs only in the
disposable profile. Automated crash-boundary coverage runs with `npm test`.

## Separate unverified gates and limits

- **Fresh live YouTube → Zen → Obsidian: NOT RUN.** Historical A4 live-caption
  evidence remains historical; this delivery run used synthetic captions.
- **Existing B4 gates remain OPEN:** normal Linux save chooser, complete
  standalone Firefox and Chromium user flows, fresh live-caption browser run,
  signed installation/normal-install permissions/full restart/updates. See
  [the unchanged B2/B3 evidence](BROWSER_VERIFICATION.md). Ordinary Markdown
  download logic was not changed; its existing chooser limitation is retained.
- Native Windows/macOS filesystem behavior remains **UNVERIFIED**. The Linux
  symlink test does not establish Windows-junction or macOS acceptance.
- Journal + lease + pinned installation + server receipt provide practical
  idempotency, not distributed exactly-once or atomic power-loss durability.
  Lost/corrupt journals and ambiguous missing files require operator review.
  Existing path-link checks cannot atomically prevent a hostile concurrent OS
  path swap. See the [recovery table and boundary](ADR/ADR-027-standalone-obsidian-connector.md).
- The development dependency graph has two moderate Moment audit findings
  inherited from Obsidian's types package. Moment is not imported or bundled
  into the plugin. No runtime npm dependency is shipped.
- Existing Phase 5A owner gates remain open. No Community Plugins submission,
  GitHub release, signed extension publication or PR merge was performed.
