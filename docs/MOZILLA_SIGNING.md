# UniMem 0.6.1: unlisted Mozilla signing

Checked 2026-10-04 against Mozilla's
[signing overview](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/),
[self-distributed installation](https://extensionworkshop.com/documentation/publish/install-self-distributed/),
[submission procedure](https://extensionworkshop.com/documentation/publish/submitting-an-add-on/),
[source requirements](https://extensionworkshop.com/documentation/publish/source-code-submission/),
[built-in data consent](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/)
and [Zen extension documentation](https://docs.zen-browser.app/user-manual/extensions).

Self-distribution is **unlisted**, without a public AMO catalog page. It still
requires Mozilla validation/signing and can receive manual review. Signature
approval and timing are Mozilla's decisions. Zen uses Firefox extensions and its
Add-ons Manager; the browser must actually accept the signed artifact.

## Owner action (nothing is submitted by this project)

1. Verify the kit's `SHA256SUMS` and `COMPATIBILITY.json`. Stable add-on ID:
   `unimem@zinverno.github.io`. Version: `0.6.1`; desktop Gecko minimum: `140.0`.
   Keep this ID on updates. No `update_url` or updater service is included.
2. The owner signs in to Mozilla Add-on Developer Hub, reads/accepts the current
   developer agreement personally, and chooses **Submit a New Add-on → On your
   own**. For an already owned ID use its existing entry and upload a new version.
   Do not choose public listing. Do not give signing credentials to an assistant
   or paste them into chat. No API keys are needed for this browser-based flow.
3. Upload `unimem-browser-0.6.1-dev.zip`. Select desktop Linux for this bounded
   candidate. Address validator errors before continuing. Two pre-existing lint
   warnings concern the shared Gecko/Chromium background manifest and Android
   minimum-version metadata; Android acceptance is not claimed.
4. Supply the reviewer notes below. Shipped JS is readable, not minified or
   bundled; no third-party runtime libraries. The accompanying
   `unimem-browser-0.6.1-source.zip` contains the exact source, tests, lockfile and
   build script, and can be provided when requested. Rebuild instructions below.
5. Submit only after the owner's explicit decision. Wait for the signature, open
   My Add-ons / version details and download the **signed XPI**. Preserve the
   original kit and record the XPI SHA-256 as a separate signed artifact.
6. Compare signed payload to the kit:

   ```sh
   python3 verify_signed.py unimem-browser-0.6.1-dev.zip /path/to/signed.xpi
   sha256sum /path/to/signed.xpi
   ```

   This comparison proves payload equality excluding Mozilla signature metadata;
   it does **not** validate Mozilla's cryptographic signature. Zen installation
   must do that. A differing manifest/code requires a new kit and acceptance.
7. In a separate Zen profile: `about:addons` → settings cog → **Install Add-on
   From File…** → XPI → Add. Keep signature verification enabled. Configure using
   FIRST_RUN, capture/import, then completely exit Zen, API and Obsidian and reopen
   the **same** profiles/data. Do not temporarily reload the add-on after restart.
   Confirm installed version, history, prior result, explicit token mode, receiver
   identity and no duplicate imports/acquisition. Record hashes with evidence.

Until step 7 passes: **Комплект подготовлен; постоянная установка ожидает подписи.**
Signed runtime/restart is BLOCKED, even if temporary-addon tests pass. Manual
updates using a newly signed XPI are sufficient; automatic updates are not promised.

## Reviewer notes / network and permissions

UniMem sends only explicit captures to the user's separately installed local
Python service at **http://127.0.0.1:8765**. The destination is fixed in source and
CSP; redirects and cookies are refused. The extension has no arbitrary endpoint,
remote code, telemetry, advertising, remote model service or automatic page scan.
The Python service optionally requests public YouTube captions after an explicit
YouTube capture. Model downloads are separate operator CLI actions; inference is
local. Obsidian delivery uses a separate local receiver credential and explicit
Send. Credentials, personal data, vaults and models are absent from the package.

- `activeTab` + `scripting`: read the user's selected text or top-level HTML only
  on the original toolbar/context-menu gesture; no persistent site access.
- `contextMenus`: existing selection/page/YouTube/open actions.
- `storage`: bounded local operation references/settings; session-only credential
  by default, unencrypted storage.local only with explicit opt-in; no storage.sync.
- `downloads`: ordinary user-requested Markdown Save As, including cancellation.
- Host permission `http://127.0.0.1/*`: WebExtensions host patterns cannot restrict
  ports; code and CSP restrict actual requests to 8765. No other host permissions.

The manifest declares `websiteContent`, `browsingActivity`, `authenticationInfo`:
selected content/current URL and local API authentication leave the browser for
loopback. It does not claim `none` merely because the service is local. Gecko 140+
supports the built-in consent prompt. There is no collection of general browsing
history/cookies. Explicit local file selections (audio/images/MP4) are uploaded to
that same service; original image metadata is preserved. Users should select only
content they intend to store. Local results and credentials can be removed through
the user's local data/profile controls; no cloud account or server deletion needed.

## Rebuild the extension source archive

In the extracted source archive, use Node 22+, npm and Python 3:

```sh
npm ci
npm test
npm run lint
npm run build
npm run check-package
sha256sum dist/unimem-browser-0.6.1-dev.zip
```

No compilation/transpilation/minification changes the JavaScript. Build copies an
allowlist and writes a deterministic ZIP with fixed metadata. Compare the resulting
ZIP hash to the kit. `npm ci` needs npm registry access only for the pinned
web-ext development tool, never a credential/model/personal dataset.
