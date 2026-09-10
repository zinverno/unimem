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

import { SCHEMA_VERSION } from "../lib/envelope.js";

const manifest = JSON.parse(
  readFileSync(fileURLToPath(new URL("../manifest.json", import.meta.url)), "utf8"),
);

const packageJson = JSON.parse(
  readFileSync(fileURLToPath(new URL("../package.json", import.meta.url)), "utf8"),
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

describe("what the extension says it is", () => {
  it("no longer describes itself as selection-only", () => {
    // The product changed the day right-clicking the icon started saving pages.
    // A name that still said "selection capture" would be the store listing and
    // the extensions page both lying about what this can do.
    assert.equal(manifest.name, "UniMem browser capture");
    assert.ok(!/selection/i.test(manifest.name));
  });

  it("describes both of the things it can save", () => {
    assert.equal(
      manifest.description,
      "Save selected text or the current page to your local UniMem capture API.",
    );
  });

  it("carries a deliberately bumped extension version", () => {
    assert.equal(manifest.version, "0.2.0");
    assert.notEqual(manifest.version, "0.1.0");
  });

  it("keeps the package version in step with the manifest's", () => {
    assert.equal(packageJson.version, manifest.version);
  });

  it("does not confuse its own version with the canonical schema version", () => {
    // The extension is at 0.2.0; the contract it emits is schema 0.2. They are
    // unrelated numbers that happen to look alike, and this test exists so that
    // nobody "fixes" one to match the other.
    assert.notEqual(manifest.version, SCHEMA_VERSION);
    assert.equal(SCHEMA_VERSION, "0.2");
  });
});

describe("permissions", () => {
  it("are exactly the minimal intended set", () => {
    assert.deepEqual(manifest.permissions, ["activeTab", "scripting", "contextMenus"]);
  });

  it("include activeTab", () => {
    assert.ok(manifest.permissions.includes("activeTab"));
  });

  it("include scripting", () => {
    assert.ok(manifest.permissions.includes("scripting"));
  });

  it("include contextMenus, the one permission whole-page capture added", () => {
    // `contextMenus` grants the right to put an item in a menu. It grants no
    // access to any website, no ability to read a page, and no host of any kind
    // — which is the entire reason whole-page capture cost one permission
    // rather than `<all_urls>`.
    assert.ok(manifest.permissions.includes("contextMenus"));
  });

  it("grew by exactly one permission", () => {
    const beforeThisPhase = ["activeTab", "scripting"];
    const added = manifest.permissions.filter((name) => !beforeThisPhase.includes(name));

    assert.deepEqual(added, ["contextMenus"]);
  });

  for (const forbidden of [
    "tabs",
    "storage",
    "notifications",
    "webRequest",
    "cookies",
    "clipboardRead",
    "clipboardWrite",
    "declarativeNetRequest",
    "pageCapture",
    "tabCapture",
    "downloads",
    "history",
    "bookmarks",
    "debugger",
    "management",
    "background",
    "alarms",
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
  it("are unchanged by whole-page capture", () => {
    // The point of the whole design: the extension can now serialize an entire
    // document, and still holds standing access to exactly one host — the local
    // API it posts to.
    assert.deepEqual(manifest.host_permissions, ["http://127.0.0.1/*"]);
    assert.equal(manifest.host_permissions.length, 1);
  });

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
