# UniMem → Obsidian development setup

The official channel is **Browser → UniMem → UniMem Connector → Obsidian**.
Use a separate test vault. Veynrel/Companion are unrelated and unnecessary.
Decision, recovery table and physical-filesystem limits: [ADR-027](ADR/ADR-027-standalone-obsidian-connector.md).

## Server and pairing

```sh
python -m pip install -e '.[youtube]'
python -m unimem_api --data-dir /tmp/unimem-data --init-token
python -m unimem_api --data-dir /tmp/unimem-data --show-token
python -m unimem_api --data-dir /tmp/unimem-data --create-destination 'Main Obsidian'
python -m unimem_api --data-dir /tmp/unimem-data --youtube
```

`--show-token` is the browser credential; paste it only into the browser
extension. `--create-destination` prints a different receiver token once; paste
that only into UniMem Connector. The destination UUID is stable and unrelated
to a vault path. The server retains only a hash of the receiver token. Keep
the output private. The plugin stores its token unencrypted in its own
`data.json`; it is not redisplayed after saving. Protect the test profile and
vault config, and exclude this file from sharing/sync to other installations.

One receiver installation is enrolled on the first claim. Do not copy its
journal/settings to another vault. To replace a lost installation, create a new
destination; inspect uncertain existing notes/deliveries before explicitly
sending to it. No credential rotation/reassignment UI is invented in this MVP.
Browser-token rotation remains the existing `--rotate-token` procedure.

## Build and install the standalone plugin

```sh
cd clients/obsidian-plugin
npm ci
npm test
npm run build
cat dist/SHA256SUMS
```

Plugin version: **0.1.0**, desktop manifest minimum **1.8.7**; native evidence is
Linux Obsidian 1.13.7. The dev artifact directory is
`clients/obsidian-plugin/dist/unimem-connector/` (`main.js`, `manifest.json`).
Hashes are in `dist/SHA256SUMS`. No stylesheet is needed. Copy those two files
to `<TEST_VAULT>/.obsidian/plugins/unimem-connector/`, enable community plugins
in this test vault and enable **UniMem Connector**. Do not copy node_modules,
test fixtures, a personal `data.json` or another installation's journal.

In settings, enter the loopback UniMem address (default `http://127.0.0.1:8765`),
receiver token and normal inbox folder, e.g. `Inbox/UniMem`. Click **Сохранить**,
then **Проверить подключение**. Verify destination name/ID, then enable
**Принимать материалы**. The inbox is created only when a delivery is imported.
Checks alone perform no vault write. Saving changed configuration disables
reception and clears verification. Commands: **UniMem: Получить сейчас** and
**UniMem: Открыть последний импорт**. The latter uses the last recorded path;
it does not scan for renamed notes or recreate missing notes.

## Browser flow

Build/load the existing extension using [BROWSER_DELIVERY.md](BROWSER_DELIVERY.md).
Complete a YouTube operation and refresh its status. Its Obsidian section lists
destination name, exact proposed basename and current delivery state. Click
**Отправить в Obsidian** once. Concurrent/double requests share the same durable
capture + destination receipt. There is no implicit queue submission on preview,
download, page open or status refresh. **Обновить статус доставки** reads server
state, including after background/page restart. Imported appears only after ACK.
The existing **Сохранить .md…** download is independent and unchanged.

Pending means awaiting a receiver. Offline/429/5xx preserves pending or claimed
work and triggers bounded receiver backoff. Receiving disabled makes “receive
now” inert. A conflict or ambiguous result needs human inspection; this version
does not offer a retry that could overwrite/duplicate a note. Existing files,
including identical content, never get an automatic `(1)` suffix.

## HTTP version 1

All data routes require the appropriate bearer; query strings are rejected.

| Credential | Method/path | Behavior |
| --- | --- | --- |
| Browser | GET `/v1/destinations` | UUID/name list; no receiver secrets |
| Browser | POST `/v1/deliveries` | `{destination_id, source_capture_id}`; 202 after commit, 200 on replay |
| Browser | GET `/v1/destinations/{id}/captures/{capture}/delivery` | Filename and receipt, or null before submission |
| Receiver | GET `/v1/receiver/destination` | Own UUID/name/enrolled installation ID |
| Receiver | GET `/v1/receiver/deliveries/next` | Next pending/expired claim, or null; no claim side effect |
| Receiver | GET `/v1/receiver/deliveries/{id}` | Own immutable snapshot/receipt |
| Receiver | POST `/v1/receiver/deliveries/{id}/claim` | `{receiver_id, claim_id}`, 120-second lease |
| Receiver | POST `/v1/receiver/deliveries/{id}/ack` | Claim identity + `markdown_sha256`; idempotent receipt |
| Receiver | POST `/v1/receiver/deliveries/{id}/fail` | Claim identity + enumerated `error_code` |

Delivery metadata: `protocol_version="1"`, IDs, export format/version, suggested
basename, UTF-8 SHA-256, creation time, state, lease expiry and safe error code.
Receiver responses also contain immutable `markdown`. Browser receipts omit
that snapshot; the existing Markdown preview/download remains available.
Source formats do not enter the delivery protocol. The first acceptance uses
the existing YouTube Markdown, with no additional format handlers.

States: pending, claimed, imported, conflict, failed, ambiguous. Error codes for
receiver reports: path_rejected, file_exists, digest_mismatch, write_failed,
write_ambiguous, config_changed. Other API errors include unauthorized (401),
destination_not_found/delivery_not_found (404), receiver_mismatch/lease_active/
lease_expired/claim_mismatch/delivery_terminal/result_not_ready (409),
markdown_too_large (413), delivery_storage_unavailable/markdown_unavailable (503),
delivery_history_full (507). No arbitrary path/stack/provider message is accepted.

## Acceptance and limits

[OBSIDIAN_VERIFICATION.md](OBSIDIAN_VERIFICATION.md) records actual automated,
native synthetic and separate live-source evidence. Synthetic captions do not
establish live YouTube retrieval. Existing B4 signed-install/restart/update,
normal Linux save chooser and other-browser gaps stay open.

The API never opens a vault directory. Only the plugin creates notes via the
official Vault API. Journal + pinned installation + lease + receipts provide
practical idempotency, not a distributed exactly-once transaction. Back up the
journal; losing it requires operator review. Symlink checks are best effort
against existing links, not an atomic defense against concurrent OS path swaps.
Post-ACK user edits/moves/deletions are never repaired by this Connector.

The npm development graph inherits two moderate audit findings from Obsidian's
type-package dependency on Moment; Moment is not imported or bundled here.
No runtime npm dependency is shipped, and the plugin does not call Moment.
Do not downgrade the Obsidian API to the obsolete npm-audit suggested version.
No Community Plugins publication, GitHub release or merge is part of this work.
