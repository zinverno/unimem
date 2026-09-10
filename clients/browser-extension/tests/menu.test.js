/**
 * The one context-menu item: its identity, its contexts, and its lifecycle.
 *
 * This is the extension's only new UI, and it is a security-shaped decision as
 * much as a product one. An item registered on the `page` or `selection`
 * context would appear in the right-click menu of every page the user ever
 * opens; an item on the `action` context appears only when they right-click
 * UniMem's own icon, which is the gesture that grants `activeTab` in the first
 * place. So the contexts are pinned exactly, and each broader context is named
 * individually — a failure then says which one came back.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import {
  WHOLE_PAGE_MENU_CONTEXTS,
  WHOLE_PAGE_MENU_ID,
  WHOLE_PAGE_MENU_TITLE,
  createWholePageMenu,
  isWholePageMenu,
} from "../lib/menu.js";

/** A `chrome.contextMenus` that records every item it was asked to create. */
function fakeContextMenus(options = {}) {
  const created = [];
  return {
    created,
    create: (item) => {
      if (options.refuseWith) {
        throw options.refuseWith;
      }
      created.push(item);
      return item.id;
    },
  };
}

describe("the whole-page menu item", () => {
  it("has the stable id the click handler recognizes it by", () => {
    assert.equal(WHOLE_PAGE_MENU_ID, "unimem-save-whole-page");
  });

  it("says exactly what choosing it does", () => {
    assert.equal(WHOLE_PAGE_MENU_TITLE, "Save whole page to UniMem");
  });

  it("appears on the action's own context and nowhere else", () => {
    assert.deepEqual([...WHOLE_PAGE_MENU_CONTEXTS], ["action"]);
  });

  for (const context of ["page", "selection", "link", "image", "video", "audio", "all", "frame"]) {
    it(`does not appear on the ${context} context`, () => {
      assert.ok(!WHOLE_PAGE_MENU_CONTEXTS.includes(context));
    });
  }
});

describe("registering it", () => {
  it("creates exactly one item", () => {
    const contextMenus = fakeContextMenus();

    createWholePageMenu(contextMenus);

    assert.equal(contextMenus.created.length, 1);
  });

  it("creates it with the pinned id, title, and contexts", () => {
    const contextMenus = fakeContextMenus();

    createWholePageMenu(contextMenus);

    assert.deepEqual(contextMenus.created[0], {
      id: "unimem-save-whole-page",
      title: "Save whole page to UniMem",
      contexts: ["action"],
    });
  });

  it("asks for no property beyond those three", () => {
    const contextMenus = fakeContextMenus();

    createWholePageMenu(contextMenus);

    assert.deepEqual(Object.keys(contextMenus.created[0]).sort(), ["contexts", "id", "title"]);
  });

  it("hands Chrome a fresh contexts array rather than the shared constant", () => {
    // `WHOLE_PAGE_MENU_CONTEXTS` is frozen and is read by tests and by the
    // click path; passing it straight to an API that may hold on to it is a
    // needless coupling.
    const contextMenus = fakeContextMenus();

    createWholePageMenu(contextMenus);

    assert.notEqual(contextMenus.created[0].contexts, WHOLE_PAGE_MENU_CONTEXTS);
    assert.deepEqual(contextMenus.created[0].contexts, [...WHOLE_PAGE_MENU_CONTEXTS]);
  });

  it("never throws when Chrome refuses the item", () => {
    // Creating a duplicate id throws. There is no user waiting on the answer and
    // no `onInstalled` frame to catch it, so this must be terminal on its own.
    const contextMenus = fakeContextMenus({ refuseWith: new Error("Cannot create item") });

    assert.doesNotThrow(() => createWholePageMenu(contextMenus));
  });
});

describe("recognizing a click on it", () => {
  it("accepts our own item", () => {
    assert.equal(isWholePageMenu({ menuItemId: "unimem-save-whole-page" }), true);
  });

  for (const [label, info] of [
    ["another extension's item", { menuItemId: "some-other-extension-item" }],
    ["a numeric id", { menuItemId: 12 }],
    ["a near miss", { menuItemId: "unimem-save-whole-page-2" }],
    ["an empty id", { menuItemId: "" }],
    ["no id at all", {}],
    ["no info object", undefined],
    ["a null info object", null],
  ]) {
    it(`rejects ${label}`, () => {
      assert.equal(isWholePageMenu(info), false);
    });
  }
});
