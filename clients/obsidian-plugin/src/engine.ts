import { randomUUID } from "node:crypto";
import { delivery, destination, digest, SafeError, UUID, FAILURES, type Delivery, type Failure, type Attachment, attachment, binaryDigest, type VideoAttachment, videoAttachment, MAX_VIDEO_BYTES } from "./contract";
import { folder, notePath, assetPath } from "./paths";
import { serverAddress } from "./client";
export { delivery, destination, digest, STATES, FAILURES, UUID } from "./contract";

export interface Settings {
  server: string; token: string; inbox: string; enabled: boolean; verified: boolean;
  destination_id: string; destination_name: string; attachments?: boolean; videoFrames?: boolean;
}
type JournalAsset = (Attachment | VideoAttachment) & { path: string; state: "prepared" | "creating" | "written" };
export interface Journal {
  delivery_id: string; destination_id: string; server: string; inbox: string; path: string;
  expected_digest: string; state: "prepared" | "creating" | "written" | "error";
  error: Failure | null; time: string; acked: boolean;
  package_digest?: string; attachments?: JournalAsset[]; protocol_version?: "2" | "3";
}
export interface Data {
  version: 1 | 2 | 3; receiver_id: string; settings: Settings; journal: Journal[];
  lastImport: { path: string; time: string } | null;
}
export function loadData(raw: unknown): Data {
  if (raw === null || raw === undefined) return { version: 3, receiver_id: randomUUID(),
    settings: { server: "http://127.0.0.1:8765", token: "", inbox: "Inbox/UniMem", enabled: false,
      verified: false, attachments: false, videoFrames: false, destination_id: "", destination_name: "" }, journal: [], lastImport: null };
  try {
    const d = raw as Data, s = d.settings;
    if (![1, 2, 3].includes(d.version) || !UUID.test(d.receiver_id) || !s || serverAddress(s.server) !== s.server ||
        typeof s.token !== "string" || (s.token !== "" && !/^[A-Za-z0-9_-]{43}$/.test(s.token)) ||
        typeof s.enabled !== "boolean" || typeof s.verified !== "boolean" ||
        typeof s.destination_name !== "string" || typeof s.destination_id !== "string" ||
        (s.verified && !UUID.test(s.destination_id)) || !Array.isArray(d.journal) || d.journal.length > 1000) throw 0;
    if (s.attachments !== undefined && typeof s.attachments !== "boolean") throw 0;
    if (s.videoFrames !== undefined && typeof s.videoFrames !== "boolean") throw 0;
    folder(s.inbox);
    const ids = new Set();
    for (const j of d.journal) {
      if (!UUID.test(j.delivery_id) || !UUID.test(j.destination_id) || ids.has(j.delivery_id) ||
          serverAddress(j.server) !== j.server || !/^[0-9a-f]{64}$/.test(j.expected_digest) ||
          typeof j.path !== "string" || !j.path.startsWith(j.inbox + "/") ||
          notePath(j.inbox, j.path.slice(j.inbox.length + 1)) !== j.path ||
          !["prepared", "creating", "written", "error"].includes(j.state) ||
          typeof j.acked !== "boolean" || !Number.isFinite(Date.parse(j.time)) ||
          !(j.error === null || FAILURES.includes(j.error)) || (j.state === "error") !== (j.error !== null)) throw 0;
      const legacy = j as Journal & { attachment?: JournalAsset };
      if (legacy.attachment) {
        if (d.version === 3 || j.attachments || j.protocol_version) throw 0;
        j.attachments = [legacy.attachment]; j.protocol_version = "2"; delete legacy.attachment;
      }
      if ((j.package_digest === undefined) !== (j.attachments === undefined) ||
          (j.attachments === undefined) !== (j.protocol_version === undefined)) throw 0;
      if (j.attachments) {
        if (!["2", "3"].includes(j.protocol_version!) || !Array.isArray(j.attachments) ||
            !/^[0-9a-f]{64}$/.test(j.package_digest!) || j.attachments.length < 1 ||
            j.attachments.length > (j.protocol_version === "2" ? 1 : 3) ||
            new Set(j.attachments.map(a => a.asset_id)).size !== j.attachments.length ||
            new Set(j.attachments.map(a => a.path)).size !== j.attachments.length ||
            (j.protocol_version === "3" && j.attachments.reduce((sum, a) => sum + a.size_bytes, 0) > MAX_VIDEO_BYTES)) throw 0;
        for (const a of j.attachments) {
          const { path, state, ...manifest } = a;
          if (!(j.protocol_version === "2" ? attachment(manifest) : videoAttachment(manifest as VideoAttachment)) ||
              assetPath(j.inbox, manifest.relative_name) !== path ||
              (j.protocol_version === "2" && manifest.relative_name.replace(/\.(png|jpg)$/, ".md") !== j.path.slice(j.inbox.length + 1)) ||
              !["prepared", "creating", "written"].includes(state) ||
              (["creating", "written"].includes(j.state) && state !== "written")) throw 0;
        }
      }
      ids.add(j.delivery_id);
    }
    if (d.lastImport !== null && (typeof d.lastImport?.path !== "string" || !Number.isFinite(Date.parse(d.lastImport.time)))) throw 0;
    return { ...d, version: 3, settings: { ...s, attachments: d.version === 1 ? false : s.attachments === true, videoFrames: d.version === 3 && s.videoFrames === true } };
  } catch { throw new SafeError("journal_corrupt"); }
}
export interface VaultPort {
  configDir: string;
  guard(path: string): Promise<void>;
  exists(path: string): Promise<boolean>;
  read(path: string): Promise<string>;
  create(path: string, markdown: string): Promise<void>;
  readBinary(path: string): Promise<Uint8Array>;
  createBinary(path: string, bytes: Uint8Array): Promise<void>;
  mkdir(path: string, checkActive: () => void): Promise<void>;
}
type Http = (path: string, body?: object, signal?: AbortSignal) => Promise<unknown>;

export class Receiver {
  private stopped = false;
  private running: Promise<void> | null = null;
  private controller = new AbortController();
  private timer: ReturnType<typeof setTimeout> | null = null;
  private delay = 30000;
  private lastNotice = "";
  status = "Не проверено";
  error = "";
  constructor(public data: Data, private http: Http, private vault: VaultPort,
    private save: () => Promise<void>, private notify: (code: string) => void) {}

  private live() { if (this.stopped) throw new SafeError("stopped"); }
  private async update(change: () => void) {
    const before: Data = JSON.parse(JSON.stringify(this.data));
    change();
    try { await this.save(); }
    catch (error) { Object.assign(this.data, before); throw error; }
  }
  private async request(path: string, body?: object, version = "1") {
    this.live(); const result = await this.http(`/v${version}/receiver/${path}`, body, this.controller.signal);
    this.live(); return result;
  }
  private exclusive(work: () => Promise<void>): Promise<void> {
    if (this.running) return this.running;
    if (this.stopped) return Promise.resolve();
    this.running = work().then(() => { this.delay = 30000; }, e => {
      const code = e instanceof SafeError ? e.code : "write_failed";
      if (code !== "stopped") {
        this.error = code; this.status = "Ошибка"; this.delay = Math.min(this.delay * 2, 300000);
        if (this.lastNotice !== code) { this.lastNotice = code; this.notify(code); }
      }
    }).finally(() => { this.running = null; });
    return this.running;
  }
  private async verify() {
    const d = destination(await this.request("destination"));
    const s = this.data.settings;
    if ((s.destination_id && d.destination_id !== s.destination_id) ||
        (d.receiver_id !== null && d.receiver_id !== this.data.receiver_id)) throw new SafeError("receiver_mismatch");
    this.status = "Подключено"; this.error = "";
    return d;
  }
  check(): Promise<void> {
    return this.exclusive(async () => {
      const d = await this.verify(); this.live();
      await this.update(() => { Object.assign(this.data.settings, {
        destination_id: d.destination_id, destination_name: d.display_name, verified: true,
      }); });
    });
  }
  start() {
    if (this.stopped || !this.data.settings.enabled || !this.data.settings.verified || this.timer) return;
    this.timer = setTimeout(async () => {
      this.timer = null; await this.poll();
      if (!this.stopped) this.schedule();
    }, 0);
  }
  private schedule() {
    if (this.stopped || !this.data.settings.enabled || this.timer) return;
    this.timer = setTimeout(async () => { this.timer = null; await this.poll(); this.schedule(); }, this.delay);
  }
  stop(): Promise<void> {
    this.stopped = true; this.controller.abort();
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    return this.running ?? Promise.resolve();
  }
  poll(): Promise<void> {
    const s = this.data.settings;
    if (this.stopped || !s.enabled || !s.verified) return Promise.resolve();
    const configuration = JSON.stringify(s);
    const active = (d?: Delivery, writing = false) => {
      this.live();
      if (JSON.stringify(this.data.settings) !== configuration) throw new SafeError("config_changed");
      if (d && Date.parse(d.lease_expires_at!) - Date.now() <= (writing ? 15000 : 0)) throw new SafeError("lease_expired");
    };
    return this.exclusive(async () => {
      await this.verify(); active();
      const recovering = this.data.journal.find(j => !j.acked && j.server === s.server && j.destination_id === s.destination_id &&
        (!j.attachments || (j.protocol_version === "3" ? s.videoFrames : s.attachments)));
      let version = recovering?.protocol_version ?? "1";
      let raw = await this.request(recovering ? `deliveries/${recovering.delivery_id}` : "deliveries/next", undefined, version); active();
      if (raw === null && !recovering && s.attachments) {
        // V1 is drained before negotiating attachments: an older server cannot block text imports.
        await this.request("capabilities", { receiver_id: this.data.receiver_id, enabled: true }, "2"); active();
        version = "2";
        raw = await this.request("deliveries/next", undefined, version); active();
      }
      if (raw === null && !recovering && s.videoFrames) {
        await this.request("capabilities", { receiver_id: this.data.receiver_id, enabled: true }, "3"); active();
        version = "3";
        raw = await this.request("deliveries/next", undefined, version); active();
      }
      if (raw === null) return;
      let d = delivery(raw, s.destination_id);
      if (d.protocol_version !== version || (recovering && d.delivery_id !== recovering.delivery_id)) throw new SafeError("invalid_response");
      if (recovering && (recovering.expected_digest !== d.markdown_sha256 ||
          recovering.package_digest !== (d.protocol_version !== "1" ? d.package_sha256 : undefined))) throw new SafeError("receipt_mismatch");
      if (!["pending", "claimed"].includes(d.state)) {
        if (recovering) {
          await this.update(() => {
            recovering.acked = true;
            if (d.state === "imported") this.data.lastImport = { path: recovering.path, time: new Date().toISOString() };
          }); active();
        }
        return;
      }
      if (this.data.journal.some(j => j.delivery_id === d.delivery_id && j.acked)) throw new SafeError("receipt_mismatch");
      const claim = { receiver_id: this.data.receiver_id, claim_id: randomUUID() };
      const claimed = delivery(await this.request(`deliveries/${d.delivery_id}/claim`, claim, version), s.destination_id); active();
      if (claimed.protocol_version !== version || claimed.delivery_id !== d.delivery_id || claimed.markdown_sha256 !== d.markdown_sha256 ||
          claimed.suggested_filename !== d.suggested_filename || claimed.state !== "claimed" ||
          (d.protocol_version !== "1" && (claimed.protocol_version !== d.protocol_version || claimed.package_sha256 !== d.package_sha256))) throw new SafeError("invalid_response");
      d = claimed;
      const check = (writing = false) => active(d, writing);
      check();
      const path = notePath(s.inbox, d.suggested_filename, this.vault.configDir);
      let j = recovering;
      if (!j) {
        if (this.data.journal.length >= 1000) throw new SafeError("journal_full");
        j = { delivery_id: d.delivery_id, destination_id: s.destination_id, server: s.server, inbox: s.inbox,
          path, expected_digest: d.markdown_sha256, state: "prepared", error: null, time: new Date().toISOString(), acked: false };
        if (d.protocol_version !== "1") {
          j.protocol_version = d.protocol_version;
          j.package_digest = d.package_sha256;
          j.attachments = d.attachments.map(a => ({ ...a, path: assetPath(s.inbox, a.relative_name, this.vault.configDir), state: "prepared" }));
        }
        const prepared = j;
        await this.update(() => { this.data.journal.push(prepared); }); check();
      }
      const record = j;
      const fail = async (error: Failure) => {
        check();
        await this.update(() => { record.state = "error"; record.error = error; }); check();
        const result = delivery(await this.request(`deliveries/${d.delivery_id}/fail`, { ...claim, error_code: error }, version), s.destination_id); check();
        if (result.delivery_id !== d.delivery_id || result.error_code !== error) throw new SafeError("invalid_response");
        await this.update(() => { record.acked = true; }); active();
        this.notify(error); this.error = error;
      };
      if (record.path !== path || record.inbox !== s.inbox || record.expected_digest !== d.markdown_sha256 ||
          record.package_digest !== (d.protocol_version !== "1" ? d.package_sha256 : undefined)) {
        await fail("config_changed"); return;
      }
      if (record.attachments && d.protocol_version !== "1") {
        const manifest = record.attachments.map(({ path, state, ...a }) => a);
        if (JSON.stringify(manifest) !== JSON.stringify(d.attachments)) throw new SafeError("journal_corrupt");
      }
      if (record.error) { await fail(record.error); return; }
      // Check every target before creating any file, including cache-invisible files.
      const files = [...(record.attachments ?? []), record];
      for (const f of files) {
        try { await this.vault.guard(f.path); } catch { await fail("path_rejected"); return; }
        check();
        const exists = await this.vault.exists(f.path); check();
        if (f.state === "prepared" && exists) { await fail("file_exists"); return; }
        if (f.state !== "prepared" && !exists) { await fail("write_ambiguous"); return; }
      }
      const binaries = new Map<string, Uint8Array>();
      for (const a of record.attachments ?? []) {
        if (a.state !== "prepared") continue;
        const value = await this.request(`deliveries/${d.delivery_id}/assets/${a.asset_id}`, undefined, version); check();
        if (!(value instanceof Uint8Array) || value.byteLength !== a.size_bytes || binaryDigest(value) !== a.sha256) {
          await fail("digest_mismatch"); return;
        }
        binaries.set(a.path, value);
      }
      // mkdir is also a write; it gets the same settings/lease/unload fence.
      check();
      try { await this.vault.mkdir(s.inbox, () => check()); } catch (e) {
        check(); await fail("write_failed"); return;
      }
      check();
      for (const f of files) {
        if (f.state === "prepared") {
          await this.update(() => { f.state = "creating"; }); check(true);
          await this.vault.guard(f.path); check(true);
          const exists = await this.vault.exists(f.path); check(true);
          if (exists) { await fail("file_exists"); return; }
          try {
            if (f === record) await this.vault.create(f.path, d.markdown);
            else await this.vault.createBinary(f.path, binaries.get(f.path)!);
          } catch (e) {
            check(); await fail("write_ambiguous"); return;
          }
          check();
        }
        let matches = false;
        try {
          if (f === record) {
            const text = await this.vault.read(f.path); check();
            matches = digest(text) === record.expected_digest;
          } else {
            const bytes = await this.vault.readBinary(f.path); check();
            const a = f as JournalAsset;
            matches = bytes.byteLength === a.size_bytes && binaryDigest(bytes) === a.sha256;
          }
        } catch (e) { check(); await fail("write_ambiguous"); return; }
        if (!matches) { await fail("digest_mismatch"); return; }
        await this.update(() => { f.state = "written"; record.time = new Date().toISOString(); }); check();
      }
      // Recheck every attachment after the note write: never ACK a missing/changed asset.
      for (const a of record.attachments ?? []) {
        let bytes: Uint8Array;
        try { bytes = await this.vault.readBinary(a.path); } catch { check(); await fail("write_ambiguous"); return; }
        check();
        if (bytes.byteLength !== a.size_bytes || binaryDigest(bytes) !== a.sha256) {
          await fail("digest_mismatch"); return;
        }
      }
      const body = record.package_digest ? { ...claim, package_sha256: record.package_digest } : { ...claim, markdown_sha256: record.expected_digest };
      const confirmed = delivery(await this.request(`deliveries/${d.delivery_id}/ack`, body, version), s.destination_id); active();
      if (confirmed.delivery_id !== d.delivery_id || confirmed.protocol_version !== version || confirmed.state !== "imported" ||
          confirmed.markdown_sha256 !== record.expected_digest ||
          (confirmed.protocol_version !== "1" && confirmed.package_sha256 !== record.package_digest)) throw new SafeError("invalid_response");
      await this.update(() => {
        record.acked = true; this.data.lastImport = { path, time: new Date().toISOString() };
      }); active();
      this.lastNotice = ""; this.notify("imported");
    });
  }
}
