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
export interface DeliveryFields {
  delivery_id: string; destination_id: string;
  source_capture_id: string; source_content_id: string; export_format: string; export_version: string;
  markdown: string; markdown_sha256: string; suggested_filename: string; created_at: string;
  state: typeof STATES[number]; lease_expires_at: string | null; error_code: Failure | null;
}
export interface Attachment {
  asset_id: string; mime_type: "image/png" | "image/jpeg"; size_bytes: number;
  sha256: string; relative_name: string;
}
export interface TextDelivery extends DeliveryFields { protocol_version: "1" }
export interface ImageDelivery extends DeliveryFields {
  protocol_version: "2"; attachments: [Attachment]; package_sha256: string;
}
export interface VideoAttachment extends Attachment { mime_type: "image/png"; width: number; height: number }
export interface VideoDelivery extends DeliveryFields {
  protocol_version: "3"; attachments: VideoAttachment[]; package_sha256: string;
}
export type Delivery = TextDelivery | ImageDelivery | VideoDelivery;
export const MAX_VIDEO_BYTES = 6 * 1024 * 1024;
export function videoAttachment(a: VideoAttachment): boolean {
  if (!a || a.mime_type !== "image/png" || a.size_bytes > 2 * 1024 * 1024 ||
      !Number.isSafeInteger(a.width) || a.width < 1 || a.width > 1024 ||
      !Number.isSafeInteger(a.height) || a.height < 1 || a.height > 1024) return false;
  const { width, height, ...rest } = a;
  return attachment(rest);
}
export function videoPackageDigest(d: VideoDelivery): string {
  return digest(JSON.stringify(["3", d.delivery_id, d.destination_id, d.source_capture_id, d.source_content_id,
    d.export_format, d.export_version, d.suggested_filename, d.markdown_sha256,
    d.attachments.map(a => [a.asset_id, a.mime_type, a.size_bytes, a.sha256, a.relative_name, a.width, a.height])]));
}
export const MAX_IMAGE_BYTES = 16 * 1024 * 1024;
export const binaryDigest = (bytes: Uint8Array) => createHash("sha256").update(bytes).digest("hex");
export function packageDigest(d: ImageDelivery): string {
  const a = d.attachments[0];
  return digest(JSON.stringify(["2", d.delivery_id, d.destination_id, d.source_capture_id, d.source_content_id,
    d.export_format, d.export_version, d.suggested_filename, d.markdown_sha256,
    a.asset_id, a.mime_type, a.size_bytes, a.sha256, a.relative_name]));
}
export function attachment(a: Attachment): boolean {
  return !!a && typeof a.asset_id === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(a.asset_id) &&
    ["image/png", "image/jpeg"].includes(a.mime_type) && Number.isSafeInteger(a.size_bytes) &&
    a.size_bytes > 0 && a.size_bytes <= MAX_IMAGE_BYTES && /^[0-9a-f]{64}$/.test(a.sha256) &&
    /^unimem-[0-9a-f]{64}\.(png|jpg)$/.test(a.relative_name) &&
    a.relative_name.endsWith(a.mime_type === "image/png" ? ".png" : ".jpg") &&
    Object.keys(a).sort().join() === "asset_id,mime_type,relative_name,sha256,size_bytes";
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
  if (!d || !["1", "2", "3"].includes(d.protocol_version) || !UUID.test(d.delivery_id) || d.destination_id !== destinationId ||
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
  const keys = ["protocol_version", "delivery_id", "destination_id", "source_capture_id", "source_content_id",
    "export_format", "export_version", "markdown", "markdown_sha256", "suggested_filename", "created_at",
    "state", "lease_expires_at", "error_code"];
  if (d.protocol_version === "2") {
    keys.push("attachments", "package_sha256");
    if (!Array.isArray(d.attachments) || d.attachments.length !== 1 || !attachment(d.attachments[0]) ||
        d.attachments[0].relative_name.replace(/\.(png|jpg)$/, ".md") !== d.suggested_filename ||
        Buffer.byteLength(d.markdown, "utf8") + d.attachments[0].size_bytes > MAX_BYTES + MAX_IMAGE_BYTES ||
        packageDigest(d) !== d.package_sha256) throw new SafeError("invalid_response");
  }
  if (d.protocol_version === "3") {
    keys.push("attachments", "package_sha256");
    if (!Array.isArray(d.attachments) || d.attachments.length < 1 || d.attachments.length > 3 ||
        !d.attachments.every(videoAttachment) || new Set(d.attachments.map(a => a.asset_id)).size !== d.attachments.length ||
        new Set(d.attachments.map(a => a.relative_name)).size !== d.attachments.length ||
        d.attachments.reduce((sum, a) => sum + a.size_bytes, 0) > MAX_VIDEO_BYTES ||
        videoPackageDigest(d) !== d.package_sha256) throw new SafeError("invalid_response");
  }
  if (Object.keys(d).sort().join() !== keys.sort().join()) throw new SafeError("invalid_response");
  return d;
}
