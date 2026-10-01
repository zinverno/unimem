# UniMem Connector

Standalone desktop Obsidian plugin, version **0.1.0**. Creates new Markdown
notes from explicit UniMem delivery requests. No Veynrel, Companion, MCP, AI,
server process or full-vault synchronization.

```sh
npm ci
npm test
npm run build
```

Copy `dist/unimem-connector/main.js` and `manifest.json` into a **test vault** at
`.obsidian/plugins/unimem-connector/`. Enable the plugin. Set UniMem address,
the separate receiver token, and inbox folder. Save, check connection, then
enable receiving. `dist/SHA256SUMS` identifies the built files.

[Complete installation, pairing, protocol and recovery guide](../../docs/OBSIDIAN_DELIVERY.md).
[Architecture decision and filesystem limits](../../docs/ADR/ADR-027-standalone-obsidian-connector.md).
[Verification evidence](../../docs/OBSIDIAN_VERIFICATION.md).

Connection check creates no notes. `Получить сейчас` works only when reception
is enabled. `Открыть последний импорт` opens the last recorded path. Files are
create-only: any pre-existing path conflicts; no overwrite, suffix or merge.
After ACK, user edits and deletions are final. The persistent `data.json`
journal is required for recovery; do not delete it or copy it to another vault.
It includes an unencrypted receiver credential, so exclude it from sharing.

Desktop-only HTTP uses Node's abortable client with no listening socket.
Note/folder writes and verification use official Obsidian Vault APIs. Read-only
desktop `lstat` rejects existing symlinks/junctions, with the external path-race
limitations explained in the ADR. Linux is natively tested; Windows/macOS and
mobile are not claimed by this development artifact.

Build layout follows the [official sample plugin](https://github.com/obsidianmd/obsidian-sample-plugin).
No publication is authorized; install this development artifact manually.
