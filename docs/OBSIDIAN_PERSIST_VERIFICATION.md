# Connector persistence correction and live-source attempt

Checked 2026-10-01, after the original [synthetic delivery acceptance](OBSIDIAN_VERIFICATION.md).
That historical evidence and its artifact hashes remain unchanged.

## Baseline and correction

PR [#34](https://github.com/zinverno/unimem/pull/34) was OPEN at
`74c1d9d508fc81084e925c9ff7effbd4d8feb587`; the working tree was clean.
Its existing `feat/obsidian-connector` branch was retained, with base `main`
at `334f04b9cc6209f1015bed9312ba15b036dd2bfb`. No delivery contract, server,
browser implementation, dependency or plugin version changed.

The new regression imports the real bundled plugin class and calls its actual
`persist()`. Before the fix, three explicit calls made only **one** `saveData`
call and **zero** successful saves. The first and queued-save regressions both
failed. They pass after separating the caller's promise from the recovered
internal queue: `persist()` returns the original rejecting operation, while
only the queue tail catches its rejection. Snapshots remain serial and ordered;
the failed caller still receives its original error. No automatic retry loop
was added.

Receiver journal/check mutations now restore the preceding confirmed in-memory
state when saving rejects. Required write intent must still be saved before
`Vault.create`. Settings changes wait for the old receiver to stop, serialize
against one another, and restore the previous settings on failure. The settings
UI restores the saved toggle and explicitly reports that receiving has stopped;
an explicit successful retry reconnects it. The receiver ID, journal and notes
are preserved. Unload still prevents restarting work after a pending save.

Review also found a related catch boundary: a failed conflict-journal save could
be caught as a create failure and continue with a detached record. Its new test
failed before the correction (**five saves instead of three**). The catch now
covers only `Vault.create`; failed conflict persistence stops the attempt.
Independent follow-up review approved the corrected diff.

## Automated evidence

| Case | Observed result |
| --- | --- |
| A: first real persist rejects | Next two explicit calls reach saveData and succeed without reloading |
| B: three queued snapshots, middle save rejects | Maximum one active save; original order; each caller gets its own outcome |
| C: prepared/creating journal save rejects | No create or imported ACK; matching external file without saved creating intent becomes conflict |
| D: file created, written-journal save rejects | Saved creating intent recovers unchanged bytes without another create; edited file fails digest; missing file is ambiguous |
| E: server accepts ACK, local result save rejects | Same delivery's server receipt confirms import; edited file is neither read nor rewritten |
| F: enable/disable/configuration save rejects | Previous settings restored; UI handles rejection and shows error; explicit retry works; concurrent change is rejected |
| Connection-check save rejects | Unsaved verified state is rolled back; next check can succeed |
| Conflict-journal save rejects | No false create-failure report, ACK or extra persistence attempt |

`npm test --prefix clients/obsidian-plugin`: **39 passed**, no skips or
cancellations (16 additional regression cases). Existing unload, configuration
switch, restart, lease, path and create-only checks still pass. No modify,
delete or rename API was added. Save failures at disk boundaries are injected
by tests; this is not a physical power-loss durability claim.

`npm run build --prefix clients/obsidian-plugin`: TypeScript and bundle PASS.
Two consecutive builds produced identical hashes. Package inspection confirmed
exactly `main.js` and `manifest.json`, no absolute build paths, secrets,
node_modules or fixtures. Server/browser source was untouched; their regressions
and all existing required gates run through the unchanged PR CI workflow.
The PR checks for the final pushed HEAD are the authoritative hosted CI result.

Plugin **0.1.0**, artifact `clients/obsidian-plugin/dist/unimem-connector/`:

| File | SHA-256 |
| --- | --- |
| `main.js` | `594a18fa257b59d2d43b7da7b36267ebbe38f8555e038471813ade21cdfc442d` |
| `manifest.json` | `84b0c64da085f99d303e67f996c7c59a16da1f9b86a0faa56375459efa15948d` |

These exact files were installed in the fresh native test vault. No stylesheet
is required. Reproduce with `npm ci`, `npm test`, `npm run build` from
`clients/obsidian-plugin`; copy only the two artifact files as described in
[the setup guide](OBSIDIAN_DELIVERY.md#build-and-install-the-standalone-plugin).

## Real YouTube E2E: BLOCKED by acquisition timeout

Actual applications: **Obsidian 1.13.7 / Electron 43**, **Zen 1.21.8b /
Gecko 152.0.6**, browser extension **0.3.0**, Connector **0.1.0**, and real
`youtube-transcript-api` **1.2.4**. Separate vault, browser profile and server data
were created under `/tmp/unimem-persist-live/`. Only `unimem-connector` was
installed in the vault; no Veynrel, Companion or personal notes were present.

The production command was:

```sh
.venv/bin/python -m unimem_api --data-dir /tmp/unimem-persist-live/data --youtube
```

The CLI created separate browser and receiver credentials. The plugin settings
UI saved and checked destination **Live Obsidian**
(`879cba1e-ff8f-4151-b0aa-2dc363917c82`), inbox `Inbox/UniMem`, then enabled
receiving. Its native state was verified as connected, enabled and verified.
The installed bundle matched the hashes above.

One real acquisition was accepted for
[Me at the zoo](https://www.youtube.com/watch?v=jNQXAC9IVRw). The source is
English; requested caption priority was `ru, en`. **No caption language, text
or timestamps could be verified**, because acquisition failed before a result.

Automation details: Marionette installed the dev extension in the isolated
headed Zen profile and entered its fresh browser credential through the settings
form. The browser customization API pinned the existing action button. Direct
WebDriver navigation and early menu attempts did not produce a valid operation;
one diagnostic invocation of the displayed XUL item's `doCommand()` also only
returned `invalid_url`. No internal extension capture handler was invoked.
The successful submission used address-bar typing plus Enter, a WebDriver
pointer right-click on the installed UniMem button, and WebDriver element-click
on its displayed **YouTube → Markdown** menu item. These were automated browser
UI actions, not a claimed physical user gesture or a synthetic tab passed to a
background listener. Status was refreshed with the extension's actual button.

- Operation: `83b5c27a-5f7a-4290-b013-67cae37a63b0`.
- Server started processing: `2026-10-01T19:08:30.015187Z`.
- Server result: **failed / timeout**, `2026-10-01T19:08:41.493908Z`.
- Capture ID, content ID, delivery ID, snapshot digest: **none**.
- Read-only database verification: **1 operation, 0 captures, 0 content objects,
  0 deliveries**. Test vault: **0 Markdown files**.
- Send-to-Obsidian was unavailable because processing never completed.
  Import, digest equality and duplicate checks for this live source are **NOT RUN**.

[Screenshot of the actual timeout UI](assets/obsidian-connector/zen-live-timeout.png).
The observed cause is the adapter's safe `timeout` result, not proof of an IP
block or a specific upstream failure. No timeout was extended and no second
source was attempted. No personal cookies, paid service, video download,
synthetic provider, prebuilt capture or access-restriction workaround was used.

To finish locally: follow the [server/pairing and isolated installation guide](OBSIDIAN_DELIVERY.md),
start with `--youtube`, open that public video in a fresh Zen profile, and choose
**YouTube → Markdown** from the installed action's menu. Once complete, check
source/language/text/timestamps with **Получить Markdown**, send to the configured
destination, and compare the single note's UTF-8 bytes/SHA-256 with the immutable
delivery snapshot. Refresh delivery status and run **UniMem: Получить сейчас**
again; verify imported, one delivery and one file. Stop if acquisition is blocked.

## Gates retained

The original synthetic native PASS remains synthetic and historical. This run
does not establish live E2E success. Existing B4 normal system save chooser,
other-browser full flows, signed installation/permissions/restart/update gates
remain OPEN; see [BROWSER_VERIFICATION.md](BROWSER_VERIFICATION.md).
No PR merge, release, Community Plugins submission or publication was performed.
