import { randomUUID } from "node:crypto";
import { delivery, destination, digest, SafeError, UUID, FAILURES, type Delivery, type Failure } from "./contract";
import { folder, notePath } from "./paths";
import { serverAddress } from "./client";
export { delivery, destination, digest, STATES, FAILURES, UUID } from "./contract";

export interface Settings {
  server: string; token: string; inbox: string; enabled: boolean; verified: boolean;
  destination_id: string; destination_name: string;
}
export interface Journal {
  delivery_id: string; destination_id: string; server: string; inbox: string; path: string;
  expected_digest: string; state: "prepared" | "creating" | "written" | "error";
  error: Failure | null; time: string; acked: boolean;
}
export interface Data {
  version: 1; receiver_id: string; settings: Settings; journal: Journal[];
  lastImport: { path: string; time: string } | null;
}
export function loadData(raw: unknown): Data {
  if (raw === null || raw === undefined) return { version: 1, receiver_id: randomUUID(),
    settings: { server: "http://127.0.0.1:8765", token: "", inbox: "Inbox/UniMem", enabled: false,
      verified: false, destination_id: "", destination_name: "" }, journal: [], lastImport: null };
  try {
    const d = raw as Data, s = d.settings;
    if (d.version !== 1 || !UUID.test(d.receiver_id) || !s || serverAddress(s.server) !== s.server ||
        typeof s.token !== "string" || (s.token !== "" && !/^[A-Za-z0-9_-]{43}$/.test(s.token)) ||
        typeof s.enabled !== "boolean" || typeof s.verified !== "boolean" ||
        typeof s.destination_name !== "string" || typeof s.destination_id !== "string" ||
        (s.verified && !UUID.test(s.destination_id)) || !Array.isArray(d.journal) || d.journal.length > 1000) throw 0;
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
      ids.add(j.delivery_id);
    }
    if (d.lastImport !== null && (typeof d.lastImport?.path !== "string" || !Number.isFinite(Date.parse(d.lastImport.time)))) throw 0;
    return d;
  } catch { throw new SafeError("journal_corrupt"); }
}
export interface VaultPort {
  configDir: string;
  guard(path: string): Promise<void>;
  exists(path: string): Promise<boolean>;
  read(path: string): Promise<string>;
  create(path: string, markdown: string): Promise<void>;
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
  private async request(path: string, body?: object) {
    this.live(); const result = await this.http(`/v1/receiver/${path}`, body, this.controller.signal);
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
    s.destination_id = d.destination_id; s.destination_name = d.display_name;
    this.status = "Подключено"; this.error = "";
  }
  check(): Promise<void> {
    return this.exclusive(async () => {
      await this.verify(); this.live(); this.data.settings.verified = true; await this.save();
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
    return this.exclusive(async () => {
      await this.verify();
      const recovering = this.data.journal.find(j => !j.acked && j.server === s.server && j.destination_id === s.destination_id);
      const raw = await this.request(recovering ? `deliveries/${recovering.delivery_id}` : "deliveries/next");
      if (raw === null) return;
      let d = delivery(raw, s.destination_id);
      if (recovering && d.delivery_id !== recovering.delivery_id) throw new SafeError("invalid_response");
      if (!["pending", "claimed"].includes(d.state)) {
        if (recovering) {
          recovering.acked = true;
          if (d.state === "imported") this.data.lastImport = { path: recovering.path, time: new Date().toISOString() };
          await this.save();
        }
        return;
      }
      if (this.data.journal.some(j => j.delivery_id === d.delivery_id && j.acked)) throw new SafeError("receipt_mismatch");
      const claim = { receiver_id: this.data.receiver_id, claim_id: randomUUID() };
      const claimed = delivery(await this.request(`deliveries/${d.delivery_id}/claim`, claim), s.destination_id);
      if (claimed.delivery_id !== d.delivery_id || claimed.markdown_sha256 !== d.markdown_sha256 ||
          claimed.suggested_filename !== d.suggested_filename || claimed.state !== "claimed") throw new SafeError("invalid_response");
      d = claimed;
      const path = notePath(s.inbox, d.suggested_filename, this.vault.configDir);
      let j = recovering;
      if (!j) {
        if (this.data.journal.length >= 1000) throw new SafeError("journal_full");
        j = { delivery_id: d.delivery_id, destination_id: s.destination_id, server: s.server, inbox: s.inbox,
          path, expected_digest: d.markdown_sha256, state: "prepared", error: null, time: new Date().toISOString(), acked: false };
        this.data.journal.push(j); await this.save(); this.live();
      }
      const record = j;
      const fail = async (error: Failure) => {
        this.live();
        record.state = "error"; record.error = error; await this.save(); this.live();
        const result = delivery(await this.request(`deliveries/${d.delivery_id}/fail`, { ...claim, error_code: error }), s.destination_id);
        if (result.delivery_id !== d.delivery_id || result.error_code !== error) throw new SafeError("invalid_response");
        record.acked = true; await this.save();
        this.notify(error); this.error = error;
      };
      if (record.path !== path || record.inbox !== s.inbox || record.expected_digest !== d.markdown_sha256) {
        await fail("config_changed"); return;
      }
      if (record.error) { await fail(record.error); return; }
      try { await this.vault.guard(path); } catch { await fail("path_rejected"); return; }
      this.live();
      const exists = await this.vault.exists(path); this.live();
      if (record.state === "prepared") {
        if (exists) { await fail("file_exists"); return; }
        try { await this.vault.mkdir(s.inbox, () => this.live()); } catch { await fail("write_failed"); return; }
        this.live();
        // Persist intent BEFORE create: a crash cannot cause a blind retry.
        record.state = "creating"; await this.save(); this.live();
        // Refuse to begin a new write near/after expiry. Recovery gets a new lease.
        if (Date.parse(d.lease_expires_at!) - Date.now() < 15000) throw new SafeError("lease_expired");
        try {
          await this.vault.guard(path); this.live();
          if (await this.vault.exists(path)) { await fail("file_exists"); return; }
          this.live(); await this.vault.create(path, d.markdown);
        } catch (e) {
          if (this.stopped) throw e;
          await fail("write_ambiguous"); return;
        }
      } else if (!exists) { await fail("write_ambiguous"); return; }
      this.live();
      let actual: string;
      try { actual = await this.vault.read(path); } catch { await fail("write_ambiguous"); return; }
      this.live();
      if (digest(actual) !== record.expected_digest) { await fail("digest_mismatch"); return; }
      record.state = "written"; record.time = new Date().toISOString(); await this.save(); this.live();
      const confirmed = delivery(await this.request(`deliveries/${d.delivery_id}/ack`,
        { ...claim, markdown_sha256: record.expected_digest }), s.destination_id);
      if (confirmed.delivery_id !== d.delivery_id || confirmed.state !== "imported") throw new SafeError("invalid_response");
      record.acked = true; this.data.lastImport = { path, time: new Date().toISOString() };
      await this.save(); this.lastNotice = ""; this.notify("imported");
    });
  }
}
