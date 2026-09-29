// Allowlist only shipped code, deterministic ZIP (no source paths/timestamps).
import { readdir, readFile, mkdir, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
const root = fileURLToPath(new URL('../', import.meta.url));
const manifest = JSON.parse(await readFile(root + 'manifest.json', 'utf8'));
const files = ['manifest.json','service-worker.js','manage.html','manage.js','manage.css',
  ...(await readdir(root + 'lib')).filter(n => n.endsWith('.js')).map(n => 'lib/' + n)].sort();
const stage = root + 'dist/unpacked';
await rm(stage, { recursive: true, force: true }); // Only this generated output.
await mkdir(stage + '/lib', {recursive:true});
for (const file of files) await writeFile(stage + '/' + file, await readFile(root + file));
// stdlib ZIP avoids a runtime/bundler dependency and fixes all archive metadata.
const archive = root + `dist/unimem-browser-${manifest.version}-dev.zip`;
execFileSync('python3', ['-c', `import json,sys,zipfile\nfrom pathlib import Path\nroot,out,files=sys.argv[1:]\nwith zipfile.ZipFile(out,'w',compression=zipfile.ZIP_STORED) as z:\n for name in json.loads(files):\n  info=zipfile.ZipInfo(name,(2026,1,1,0,0,0)); info.external_attr=0o100644<<16\n  z.writestr(info,(Path(root)/name).read_bytes())`, root, archive, JSON.stringify(files)]);
const hash = createHash('sha256').update(await readFile(archive)).digest('hex');
await writeFile(archive + '.sha256', `${hash}  ${archive.split('/').at(-1)}\n`);
console.log(`${archive}\nSHA256 ${hash}`);
