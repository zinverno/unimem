import { lstat } from "node:fs/promises";
import { join } from "node:path";
import { SafeError } from "./contract";

export function folder(value: string, configDir = ".obsidian"): string {
  if (!value || value.length > 200 || value !== value.normalize("NFC")) throw new SafeError("path_rejected");
  for (const part of value.split("/")) {
    if (!part || part.length > 80 || part.startsWith(".") || /[. ]$/.test(part) ||
        /[\x00-\x1f\x7f-\x9f\\:%<>"|?*]/.test(part) ||
        /^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)/i.test(part) ||
        part.toLowerCase() === configDir.toLowerCase()) throw new SafeError("path_rejected");
  }
  return value;
}
export function notePath(inbox: string, basename: string, configDir = ".obsidian"): string {
  folder(inbox, configDir);
  if (!/^unimem-[0-9a-f]{64}\.md$/.test(basename)) throw new SafeError("path_rejected");
  return `${inbox}/${basename}`;
}
export function assetPath(inbox: string, basename: string, configDir = ".obsidian"): string {
  folder(inbox, configDir);
  if (!/^unimem-[0-9a-f]{64}\.(png|jpg)$/.test(basename)) throw new SafeError("path_rejected");
  return `${inbox}/${basename}`;
}
// Read-only desktop defense. Vault API remains the sole note/folder writer.
// This detects existing symlinks/junctions; it cannot fence an external OS rename race.
export async function rejectSymlinks(base: string, relative: string): Promise<void> {
  let path = base;
  for (const part of relative.split("/")) {
    path = join(path, part);
    try {
      if ((await lstat(path)).isSymbolicLink()) throw new SafeError("path_rejected");
    } catch (e) {
      if ((e as NodeJS.ErrnoException).code === "ENOENT") return;
      if (e instanceof SafeError) throw e;
      throw new SafeError("path_rejected");
    }
  }
}
