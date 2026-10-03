# UniMem browser 0.6.1

Use the kit's **FIRST_RUN.md** for installation and **MOZILLA_SIGNING.md** for
owner-controlled unlisted signing. Temporary ZIP loading is only a development
check; it cannot prove permanent installation or signed restart.

Rebuild this source archive with Node 22+, npm and Python 3:

```sh
npm ci
npm test
npm run lint
npm run build
npm run check-package
```

Output: `dist/unimem-browser-0.6.1-dev.zip`, deterministic fixed ZIP metadata.
Runtime is native readable ES modules; web-ext is a pinned development tool only.
Network requests go only to fixed `http://127.0.0.1:8765`; no credentials, datasets,
model weights, node_modules, tests or build tools are shipped in the dev ZIP.
