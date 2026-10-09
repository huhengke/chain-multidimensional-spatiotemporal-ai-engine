import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";

export class SessionStore {
  constructor(path) {
    this.path = path;
    this.sessions = new Map();
    this.loaded = false;
    this.writeChain = Promise.resolve();
  }

  async load() {
    if (this.loaded) return;
    this.loaded = true;
    try {
      const payload = JSON.parse(await readFile(this.path, "utf8"));
      for (const item of payload.sessions || []) {
        if (item?.id) this.sessions.set(item.id, {
          pinned: false,
          archived: false,
          skill_mode: "auto",
          selected_skills: [],
          ...item,
          selected_skills: Array.isArray(item.selected_skills) ? item.selected_skills : [],
        });
      }
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  async create(title = "新会话") {
    await this.load();
    const now = new Date().toISOString();
    const item = {
      id: `local-${randomUUID()}`,
      harness_session_id: null,
      model_id: null,
      model_name: null,
      pinned: false,
      archived: false,
      skill_mode: "auto",
      selected_skills: [],
      title: String(title || "新会话").slice(0, 80),
      created_at: now,
      updated_at: now,
      message_count: 0,
      last_run_id: null,
      messages: [],
    };
    this.sessions.set(item.id, item);
    await this.persist();
    return item;
  }

  async list({ archived = false } = {}) {
    await this.load();
    return [...this.sessions.values()]
      .filter((item) => Boolean(item.archived) === archived)
      .sort((a, b) => {
        if (Boolean(a.pinned) !== Boolean(b.pinned)) return a.pinned ? -1 : 1;
        return String(b.updated_at).localeCompare(String(a.updated_at));
      });
  }

  async get(id) {
    await this.load();
    return this.sessions.get(id) || null;
  }

  async update(id, patch) {
    await this.load();
    const current = this.sessions.get(id);
    if (!current) return null;
    const next = { ...current, ...patch, id, updated_at: new Date().toISOString() };
    this.sessions.set(id, next);
    await this.persist();
    return next;
  }

  async remove(id) {
    await this.load();
    if (!this.sessions.has(id)) return false;
    this.sessions.delete(id);
    await this.persist();
    return true;
  }

  async appendMessage(id, message) {
    await this.load();
    const current = this.sessions.get(id);
    if (!current) return null;
    const messages = [...(current.messages || []), message].slice(-200);
    return this.update(id, {
      messages,
      message_count: messages.length,
      last_run_id: message.run_id || current.last_run_id,
    });
  }

  async updateMessage(id, messageId, patch) {
    await this.load();
    const current = this.sessions.get(id);
    if (!current) return null;
    const index = (current.messages || []).findIndex((item) => item.id === messageId);
    if (index < 0 || current.messages[index].role !== "assistant") return null;
    const messages = [...current.messages];
    messages[index] = { ...messages[index], ...patch, id: messageId };
    await this.update(id, { messages });
    return messages[index];
  }

  async branch(id, messageId) {
    await this.load();
    const source = this.sessions.get(id);
    if (!source) return null;
    const index = (source.messages || []).findIndex(
      (item) => item.id === messageId && item.role === "assistant",
    );
    if (index < 0) return null;
    const branch = await this.create(`分枝 · ${source.title}`);
    const messages = source.messages.slice(0, index + 1).map((item) => ({ ...item }));
    return this.update(branch.id, {
      model_id: source.model_id,
      model_name: source.model_name,
      skill_mode: source.skill_mode || "auto",
      selected_skills: [...(source.selected_skills || [])],
      messages,
      message_count: messages.length,
      last_run_id: null,
      source_session_id: source.id,
      source_message_id: messageId,
      branch_context_pending: true,
    });
  }

  async persist() {
    this.writeChain = this.writeChain.then(async () => {
      await mkdir(dirname(this.path), { recursive: true });
      const temp = `${this.path}.tmp`;
      const body = JSON.stringify({ version: 1, sessions: await this.listUnsafe() }, null, 2);
      await writeFile(temp, body, "utf8");
      await rename(temp, this.path);
    });
    return this.writeChain;
  }

  async listUnsafe() {
    return [...this.sessions.values()].sort((a, b) =>
      String(b.updated_at).localeCompare(String(a.updated_at)),
    );
  }
}
