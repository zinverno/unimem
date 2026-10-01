import { build } from "esbuild";
import { builtinModules } from "node:module";
import { mkdir, copyFile, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
const test = process.argv[2] === "test";
await mkdir("dist/unimem-connector", { recursive: true });
await build({ entryPoints: test ? ["src/engine.ts", "src/client.ts", "src/paths.ts"] : ["src/main.ts"],
  bundle: true, platform: "node", format: test ? "esm" : "cjs", target: "es2021",
  external: ["obsidian", ...builtinModules, ...builtinModules.map(x => `node:${x}`)],
  ...(test ? { outdir: "build" } : { outfile: "dist/unimem-connector/main.js", minify: true }),
  sourcemap: false });
if (test) await build({ entryPoints: ["src/main.ts"], bundle: true, platform: "node", format: "esm",
  target: "es2021", alias: { obsidian: "./tests/obsidian-mock.mjs" }, outfile: "build/main.js" });
if (!test) {
  await copyFile("manifest.json", "dist/unimem-connector/manifest.json");
  const lines = [];
  for (const file of ["main.js", "manifest.json"]) {
    const data = await readFile(`dist/unimem-connector/${file}`);
    if (data.includes(Buffer.from(process.cwd()))) throw new Error("Absolute build path in artifact");
    lines.push(`${createHash("sha256").update(data).digest("hex")}  ${file}`);
  }
  await writeFile("dist/SHA256SUMS", lines.join("\n") + "\n");
}
