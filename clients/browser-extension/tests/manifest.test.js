/**
 * The manifest is a security document, so it is asserted rather than reviewed.
 *
 * Most of these tests are about what is *absent*. A permission is easy to add
 * while debugging and easy to forget afterwards, and the difference between
 * this extension and one that can read every page you visit is a single line
 * nobody re-read. So the permission set is pinned exactly, and each dangerous
 * permission is named individually — a failure then says which one came back.
 */

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
import { describe, it } from "node:test";

const manifest = JSON.parse(
  readFileSync(fileURLToPath(new URL("../manifest.json", import.meta.url)), "utf8"),
);

describe("manifest", () => {
  it("is Manifest V3", () => {
    assert.equal(manifest.manifest_version, 3);
  });

  it("declares a background service worker", () => {
    assert.equal(manifest.background.service_worker, "service-worker.js");
  });

  it("declares the service worker as a module, because it imports lib/", () => {
    assert.equal(manifest.background.type, "module");
  });

  it("declares an action with a title that says what clicking does", () => {
    assert.equal(manifest.action.default_title, "Save selected text to UniMem");
  });

  it("has no popup, so onClicked stays the user gesture that grants activeTab", () => {
    assert.equal(manifest.action.default_popup, undefined);
  });
});

describe("permissions", () => {
  it("are exactly the minimal intended set", () => {
    assert.deepEqual(manifest.permissions, ["activeTab", "scripting"]);
  });

  it("include activeTab", () => {
    assert.ok(manifest.permissions.includes("activeTab"));
  });

  it("include scripting", () => {
    assert.ok(manifest.permissions.includes("scripting"));
  });

  for (const forbidden of [
    "tabs",
    "storage",
    "notifications",
    "webRequest",
    "cookies",
    "clipboardRead",
    "clipboardWrite",
    "contextMenus",
    "declarativeNetRequest",
  ]) {
    it(`do not include ${forbidden}`, () => {
      assert.ok(!manifest.permissions.includes(forbidden));
    });
  }

  it("declare no optional permissions either", () => {
    assert.equal(manifest.optional_permissions, undefined);
    assert.equal(manifest.optional_host_permissions, undefined);
  });
});

describe("host permissions", () => {
  it("are loopback-only", () => {
    assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*"]);
  });

  it("do not include <all_urls>", () => {
    const everything = JSON.stringify(manifest);
    assert.ok(!everything.includes("<all_urls>"));
  });

  it("name no host other than 127.0.0.1", () => {
    for (const pattern of manifest.host_permissions) {
      assert.match(pattern, /^http:\/\/127\.0\.0\.1\//);
    }
  });
});

describe("what the manifest must not do", () => {
  it("registers no static content scripts", () => {
    assert.equal(manifest.content_scripts, undefined);
  });

  it("references no remote script or code URL", () => {
    const everything = JSON.stringify(manifest);
    assert.ok(!/https?:\/\/(?!127\.0\.0\.1)/.test(everything));
  });

  it("declares no externally connectable surface", () => {
    assert.equal(manifest.externally_connectable, undefined);
  });

  it("declares no web accessible resources", () => {
    assert.equal(manifest.web_accessible_resources, undefined);
  });
});
