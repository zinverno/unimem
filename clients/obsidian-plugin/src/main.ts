import { FileSystemAdapter, Notice, Plugin, PluginSettingTab, Setting, TFile, TFolder } from "obsidian";
import { lstat } from "node:fs/promises";
import { join } from "node:path";
import { Receiver, loadData, type Data, type Settings } from "./engine";
import { receiverClient, serverAddress } from "./client";
import { folder, rejectSymlinks } from "./paths";
import { SafeError } from "./contract";

const notices: Record<string, string> = {
  imported: "Новая заметка импортирована", file_exists: "Конфликт: файл уже существует",
  unavailable: "Сервер недоступен", timeout: "Сервер не ответил вовремя", unauthorized: "Credential отклонён",
  receiver_mismatch: "Назначение связано с другой установкой плагина",
  write_ambiguous: "Результат записи неоднозначен — нужна ручная проверка",
  digest_mismatch: "Содержимое файла не совпало — нужна ручная проверка",
  journal_corrupt: "Журнал повреждён. Приём остановлен; сохраните data.json для проверки",
  path_rejected: "Небезопасный путь или symlink — приём остановлен",
};

export default class UniMemConnector extends Plugin {
  data!: Data;
  receiver: Receiver | null = null;
  private saving = Promise.resolve();
  private closing = false;
  private changingSettings = false;
  settingsError = "";
  async onload() {
    try {
      this.data = loadData(await this.loadData());
      await this.persist();
      if (this.closing) return;
      this.connect();
      this.addSettingTab(new ConnectorSettings(this));
      this.addCommand({ id: "receive-now", name: "UniMem: Получить сейчас", callback: () => { void this.receiver?.poll(); } });
      this.addCommand({ id: "open-last-import", name: "UniMem: Открыть последний импорт", callback: () => {
        const path = this.data.lastImport?.path;
        const file = path ? this.app.vault.getAbstractFileByPath(path) : null;
        if (file instanceof TFile) void this.app.workspace.getLeaf(false).openFile(file);
        else new Notice("UniMem: Последний импорт не найден по прежнему пути");
      } });
      this.app.workspace.onLayoutReady(() => { if (!this.closing) this.receiver?.start(); });
    } catch { new Notice(`UniMem: ${notices.journal_corrupt}`); }
  }
  onunload() { this.closing = true; void this.receiver?.stop(); }
  persist() {
    const snapshot = JSON.parse(JSON.stringify(this.data));
    const saved = this.saving.then(() => this.saveData(snapshot));
    this.saving = saved.catch(() => {}); // Recover the queue, not this caller's result.
    return saved;
  }
  connect() {
    if (this.closing) return;
    const adapter = this.app.vault.adapter;
    if (!(adapter instanceof FileSystemAdapter)) throw new SafeError("path_rejected");
    const vault = this.app.vault, base = adapter.getBasePath();
    const guard = async (path: string) => {
      if (this.closing) throw new SafeError("stopped");
      await rejectSymlinks(base, path);
    };
    const http = this.data.settings.token ? receiverClient(this.data.settings.server, this.data.settings.token) :
      async () => { throw new SafeError("invalid_settings"); };
    this.receiver = new Receiver(this.data, http, {
      configDir: vault.configDir, guard,
      exists: async path => {
        await guard(path);
        // Include files not yet visible in the Vault cache. Read-only; never write via fs.
        try { await lstat(join(base, path)); return true; }
        catch (e) { if ((e as NodeJS.ErrnoException).code === "ENOENT") return false; throw new SafeError("path_rejected"); }
      },
      read: async path => {
        await guard(path);
        const file = vault.getAbstractFileByPath(path);
        if (!(file instanceof TFile)) throw new SafeError("write_ambiguous");
        return vault.read(file);
      },
      readBinary: async path => {
        await guard(path);
        const file = vault.getAbstractFileByPath(path);
        if (!(file instanceof TFile)) throw new SafeError("write_ambiguous");
        return new Uint8Array(await vault.readBinary(file));
      },
      createBinary: async (path, bytes) => { await vault.createBinary(path, new Uint8Array(bytes).buffer); },
      create: async (path, markdown) => { await vault.create(path, markdown); },
      mkdir: async (path, checkActive) => {
        folder(path, vault.configDir);
        let current = "";
        for (const part of path.split("/")) {
          current = current ? `${current}/${part}` : part;
          await guard(current);
          checkActive();
          const found = vault.getAbstractFileByPath(current);
          if (found && !(found instanceof TFolder)) throw new SafeError("path_rejected");
          if (!found) await vault.createFolder(current);
          checkActive();
        }
      },
    }, () => this.persist(), code => {
      if (!this.closing) new Notice(`UniMem: ${notices[code] ?? code}`);
    });
  }
  private async saveSettings(next: Settings) {
    if (this.changingSettings) throw new SafeError("settings_busy");
    this.changingSettings = true;
    try {
      await this.receiver?.stop();
      if (this.closing) return;
      const previous = this.data.settings;
      this.data.settings = next;
      try { await this.persist(); }
      catch (error) {
        this.data.settings = previous;
        this.settingsError = "Не удалось сохранить настройки. Приём остановлен; повторите сохранение.";
        throw error;
      }
      this.settingsError = "";
      if (this.closing) return;
      this.connect();
      if (next.enabled) this.receiver?.start();
    } finally { this.changingSettings = false; }
  }
  configure(draft: Settings) {
    folder(draft.inbox, this.app.vault.configDir);
    const server = serverAddress(draft.server);
    if (!/^[A-Za-z0-9_-]{43}$/.test(draft.token)) throw new SafeError("invalid_settings");
    return this.saveSettings({ ...draft, server, enabled: false, verified: false, destination_id: "", destination_name: "" });
  }
  setReceiving(enabled: boolean) {
    if (!this.data.settings.verified) throw new SafeError("invalid_settings");
    return this.saveSettings({ ...this.data.settings, enabled });
  }
}

class ConnectorSettings extends PluginSettingTab {
  constructor(private plugin: UniMemConnector) { super(plugin.app, plugin); }
  display() {
    const el = this.containerEl; el.empty(); el.createEl("h2", { text: "UniMem" });
    const plugin = this.plugin, s = plugin.data.settings;
    const draft = { ...s };
    const status = el.createEl("p", { attr: { role: "status" } });
    status.setText(`Сервер: ${plugin.receiver?.status}. Назначение: ${s.destination_name || "не связано"} (${s.destination_id || "—"}). Сохранённый приём: ${s.enabled ? "включён" : "выключен"}.`);
    if (plugin.settingsError) el.createEl("p", { text: plugin.settingsError, attr: { role: "alert" } });
    el.createEl("p", { text: `Последний импорт: ${plugin.data.lastImport?.time ?? "ещё не было"}. Последняя ошибка: ${plugin.receiver?.error || plugin.data.journal[plugin.data.journal.length - 1]?.error || "нет"}.` });
    new Setting(el).setName("Адрес локального UniMem").addText(t => t.setValue(s.server).onChange(v => { draft.server = v; }));
    new Setting(el).setName("Credential получателя").setDesc("Отдельный receiver token. Пустое поле сохраняет прежний. Хранится в data.json без шифрования.")
      .addText(t => { t.inputEl.type = "password"; t.inputEl.autocomplete = "off"; t.setPlaceholder(s.token ? "Сохранён" : "Receiver token"); t.onChange(v => { draft.token = v || s.token; }); });
    new Setting(el).setName("Папка входящих").setDesc("Будет создана при первом импорте. Например Inbox/UniMem. Symlink и junction запрещены.")
      .addText(t => t.setValue(s.inbox).onChange(v => { draft.inbox = v; }));
    new Setting(el).setName("Разрешить PNG/JPEG-вложения").setDesc("По умолчанию выключено. Переносит исходный файл до 16 МиБ, включая EXIF и другую metadata. Сохраните конфигурацию, проверьте и включите приём.")
      .addToggle(t => t.setValue(s.attachments === true).onChange(value => { draft.attachments = value; }));
    new Setting(el).setName("Сохранить конфигурацию").setDesc("Сохранение выключает приём. Затем проверьте подключение и включите приём.")
      .addButton(b => b.setButtonText("Сохранить").onClick(async () => {
        b.setDisabled(true);
        try { await plugin.configure(draft); this.display(); }
        catch { status.setText(plugin.settingsError || "Не удалось сохранить конфигурацию. Проверьте адрес, credential и папку."); b.setDisabled(false); }
      }));
    new Setting(el).setName("Проверить подключение").setDesc("Использует сохранённые настройки. Не создаёт файлы.")
      .addButton(b => b.setButtonText("Проверить подключение").onClick(async () => {
        b.setDisabled(true); await plugin.receiver?.check(); this.display();
      }));
    new Setting(el).setName("Принимать материалы").setDesc("Требует сохранённой и проверенной конфигурации.")
      .addToggle(t => t.setValue(s.enabled).setDisabled(!s.verified).onChange(async enabled => {
        t.setDisabled(true);
        try { await plugin.setReceiving(enabled); }
        catch { new Notice(plugin.settingsError || "Не удалось сохранить настройки приёма."); }
        this.display();
      }));
    new Setting(el).setName("Получить сейчас").addButton(b => b.setButtonText("Получить сейчас").setDisabled(!s.enabled).onClick(async () => {
      b.setDisabled(true); await plugin.receiver?.poll(); this.display();
    }));
  }
}
