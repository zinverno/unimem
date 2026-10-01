import { createHash } from "node:crypto";

export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
export const STATES = ["pending", "claimed", "imported", "conflict", "failed", "ambiguous"] as const;
export const FAILURES = ["path_rejected", "file_exists", "digest_mismatch", "write_failed", "write_ambiguous", "config_changed"] as const;
export type Failure = typeof FAILURES[number];
export const MAX_BYTES = 1024 * 1024;
export const digest = (text: string) => createHash("sha256").update(text, "utf8").digest("hex");
export class SafeError extends Error {
  constructor(public code: string) { super(code); }
}
export interface Destination { destination_id: string; display_name: string; receiver_id: string | null }
export interface Delivery {
  protocol_version: "1"; delivery_id: string; destination_id: string;
  source_capture_id: string; source_content_id: string; export_format: string; export_version: string;
  markdown: string; markdown_sha256: string; suggested_filename: string; created_at: string;
  state: typeof STATES[number]; lease_expires_at: string | null; error_code: Failure | null;
}
const date = (x: unknown): x is string => typeof x === "string" && x.length <= 40 &&
  /(?:Z|[+-]\d\d:\d\d)$/.test(x) && Number.isFinite(Date.parse(x));
export function destination(value: unknown): Destination {
  const d = value as Destination;
  if (!d || !UUID.test(d.destination_id) || typeof d.display_name !== "string" || !d.display_name.trim() ||
      d.display_name.length > 80 || /[\x00-\x1f\x7f]/.test(d.display_name) ||
      !(d.receiver_id === null || UUID.test(d.receiver_id))) throw new SafeError("invalid_response");
  return { destination_id: d.destination_id, display_name: d.display_name, receiver_id: d.receiver_id };
}
export function delivery(value: unknown, destinationId: string): Delivery {
  const d = value as Delivery;
  if (!d || d.protocol_version !== "1" || !UUID.test(d.delivery_id) || d.destination_id !== destinationId ||
      !UUID.test(d.destination_id) || typeof d.source_capture_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(d.source_capture_id) ||
      typeof d.source_content_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(d.source_content_id) ||
      typeof d.export_format !== "string" || !/^[a-z0-9-]{1,64}$/.test(d.export_format) ||
      typeof d.export_version !== "string" || !/^[0-9][0-9.]{0,15}$/.test(d.export_version) || typeof d.markdown !== "string" ||
      Buffer.byteLength(d.markdown, "utf8") > MAX_BYTES || !/^[0-9a-f]{64}$/.test(d.markdown_sha256) ||
      digest(d.markdown) !== d.markdown_sha256 || !/^unimem-[0-9a-f]{64}\.md$/.test(d.suggested_filename) ||
      !date(d.created_at) || !STATES.includes(d.state) ||
      !(d.lease_expires_at === null || date(d.lease_expires_at)) ||
      (d.state === "claimed") !== (d.lease_expires_at !== null) ||
      !(d.error_code === null || FAILURES.includes(d.error_code)) ||
      (["failed", "conflict", "ambiguous"].includes(d.state)) !== (d.error_code !== null)) {
    throw new SafeError("invalid_response");
  }
  return d;
}
