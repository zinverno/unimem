export class Plugin {}
export class PluginSettingTab {}
export class Setting {
  constructor(container) { this.container = container; }
  setName(name) { this.name = name; return this; }
  setDesc() { return this; }
  addText(callback) { return this.addControl(callback); }
  addButton(callback) { return this.addControl(callback); }
  addToggle(callback) { return this.addControl(callback); }
  addControl(callback) {
    const control = { name: this.name, inputEl: {},
      setValue(value) { this.value = value; return this; },
      setDisabled(value) { this.disabled = value; return this; },
      setPlaceholder() { return this; }, setButtonText() { return this; },
      onChange(handler) { this.change = handler; return this; },
      onClick(handler) { this.click = handler; return this; },
    };
    this.container.controls.push(control); callback(control); return this;
  }
}
export class TFile {}
export class TFolder {}
export class FileSystemAdapter { getBasePath() { return "/unused-test-vault"; } }
export class Notice {}
