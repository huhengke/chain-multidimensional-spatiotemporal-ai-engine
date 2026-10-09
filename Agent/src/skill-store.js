import { randomUUID } from "node:crypto";
import {
  access,
  cp,
  mkdir,
  readdir,
  readFile,
  rename,
  rm,
  stat,
  writeFile,
} from "node:fs/promises";
import { isAbsolute, join, relative, resolve } from "node:path";

const NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const WINDOWS_RESERVED = new Set([
  "con", "prn", "aux", "nul",
  "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
  "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
]);

function apiError(message, code, status = 422) {
  return Object.assign(new Error(message), { code, status });
}

function cleanText(value) {
  return String(value || "").replace(/\r\n/g, "\n").trim();
}

function parseScalar(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  if ((text.startsWith('"') && text.endsWith('"')) || (text.startsWith("'") && text.endsWith("'"))) {
    try {
      return text.startsWith('"') ? JSON.parse(text) : text.slice(1, -1).replaceAll("''", "'");
    } catch {
      return text.slice(1, -1);
    }
  }
  return text;
}

export function parseSkillMarkdown(source) {
  const text = String(source || "").replace(/\r\n/g, "\n");
  if (!text.startsWith("---\n")) {
    throw apiError("SKILL.md 缺少 YAML 头信息", "SKILL_FRONTMATTER_REQUIRED");
  }
  const boundary = text.indexOf("\n---\n", 4);
  if (boundary < 0) {
    throw apiError("SKILL.md 的 YAML 头信息未闭合", "SKILL_FRONTMATTER_INVALID");
  }
  const header = text.slice(4, boundary);
  const fields = {};
  for (const line of header.split("\n")) {
    const match = line.match(/^([a-zA-Z0-9_-]+):\s*(.*)$/);
    if (match) fields[match[1]] = parseScalar(match[2]);
  }
  return {
    name: cleanText(fields.name),
    description: cleanText(fields.description),
    instructions: cleanText(text.slice(boundary + 5)),
  };
}

function yamlString(value) {
  return JSON.stringify(cleanText(value));
}

function renderSkillMarkdown({ name, description, instructions, version }) {
  return [
    "---",
    `name: ${name}`,
    `description: ${yamlString(description)}`,
    "metadata:",
    "  created_by: agent-workbench",
    `  version: ${version}`,
    "---",
    "",
    cleanText(instructions),
    "",
  ].join("\n");
}

function assertSafeName(value) {
  const name = cleanText(value);
  if (!NAME_PATTERN.test(name) || name.length > 63 || WINDOWS_RESERVED.has(name)) {
    throw apiError(
      "Skill 名称只能使用小写字母、数字和连字符，长度不超过 63 个字符",
      "SKILL_NAME_INVALID",
    );
  }
  return name;
}

function controlledPath(root, ...segments) {
  const base = resolve(root);
  const target = resolve(base, ...segments);
  const rel = relative(base, target);
  if (rel.startsWith("..") || isAbsolute(rel)) {
    throw apiError("Skill 路径超出受控目录", "SKILL_PATH_DENIED", 403);
  }
  return target;
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function readJson(path, fallback = {}) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch (error) {
    if (error.code === "ENOENT" || error instanceof SyntaxError) return fallback;
    throw error;
  }
}

function timestampId() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

export function validateSkillDraft(input = {}) {
  const normalized = {
    name: cleanText(input.name),
    display_name: cleanText(input.display_name || input.name),
    description: cleanText(input.description),
    instructions: cleanText(input.instructions),
  };
  const errors = [];
  const warnings = [];

  if (!NAME_PATTERN.test(normalized.name) || normalized.name.length > 63 || WINDOWS_RESERVED.has(normalized.name)) {
    errors.push("名称只能使用小写字母、数字和连字符，长度不超过 63 个字符");
  }
  if (!normalized.display_name) errors.push("显示名称不能为空");
  if (normalized.display_name.length > 80) errors.push("显示名称不能超过 80 个字符");
  if (!normalized.description) errors.push("触发说明不能为空");
  if (normalized.description.length > 500) errors.push("触发说明不能超过 500 个字符");
  if (!normalized.instructions) errors.push("Skill 指令不能为空");
  if (normalized.instructions.length > 20_000) errors.push("Skill 指令不能超过 20000 个字符");

  const placeholderPattern = /\b(?:todo|tbd|placeholder)\b|待补充|待完善/iu;
  if (placeholderPattern.test(normalized.instructions)) warnings.push("指令中仍有待补充或占位内容");
  if (/\bpending_review\b/iu.test(normalized.instructions)) {
    warnings.push("pending_review 只属于 Skill 草稿流程，不应写入发布后的运行指令");
  }
  const unsafePattern = /忽略(?:之前|系统).*指令|ignore\s+(?:all\s+)?previous|rm\s+-rf|git\s+reset\s+--hard/iu;
  if (unsafePattern.test(normalized.instructions)) warnings.push("指令中存在高风险覆盖或破坏性操作表述");
  const directDataPattern = /(?:绕过|跳过).{0,32}(?:mcp|引擎)|(?:直接)?(?:读取|访问|连接|查询|打开|修改|写入|操作|使用).{0,48}(?:sqlite|\.db\b|excel|json|数据库文件)|(?:sqlite|\.db\b|excel|json|数据库文件).{0,28}(?:读取|访问|连接|查询|打开|修改|写入|操作)/iu;
  const prohibitionPattern = /(?:禁止|不得|不要|不可|不能|不允许|严禁|避免|只允许|仅允许|do\s+not|don't|never|must\s+not).{0,96}(?:绕过|跳过|直接|读取|访问|连接|查询|打开|修改|写入|操作|使用|sqlite|\.db\b|excel|json|数据库文件)/iu;
  const contrastPattern = /(?:但|但是|不过|而是|仍可|可以|允许|except|unless)/iu;
  const hasDirectDataBypass = normalized.instructions
    .split(/[\n。；;]/u)
    .some((segment) => directDataPattern.test(segment)
      && (!prohibitionPattern.test(segment) || contrastPattern.test(segment)));
  if (hasDirectDataBypass) warnings.push("指令可能绕过 AI 数据引擎的权威 MCP 查询边界");

  return { valid: errors.length === 0, errors, warnings, normalized };
}

export class SkillStore {
  constructor({ enabledRoot, disabledRoot, archiveRoot, stagingRoot, runtimeRoot }) {
    this.enabledRoot = resolve(enabledRoot);
    this.disabledRoot = resolve(disabledRoot);
    this.archiveRoot = resolve(archiveRoot);
    this.stagingRoot = resolve(stagingRoot);
    this.runtimeRoot = resolve(runtimeRoot || join(stagingRoot, "runtime"));
    this.writeChain = Promise.resolve();
  }

  async initialize() {
    await Promise.all([
      mkdir(this.enabledRoot, { recursive: true }),
      mkdir(this.disabledRoot, { recursive: true }),
      mkdir(this.archiveRoot, { recursive: true }),
      mkdir(this.stagingRoot, { recursive: true }),
      mkdir(this.runtimeRoot, { recursive: true }),
    ]);
  }

  rootFor(enabled) {
    return enabled ? this.enabledRoot : this.disabledRoot;
  }

  async readAt(root, enabled, name) {
    const safeName = assertSafeName(name);
    const directory = controlledPath(root, safeName);
    const source = await readFile(controlledPath(directory, "SKILL.md"), "utf8");
    const parsed = parseSkillMarkdown(source);
    if (parsed.name !== safeName) {
      throw apiError(`Skill 目录 ${safeName} 与 SKILL.md 名称不一致`, "SKILL_NAME_MISMATCH", 409);
    }
    const info = await stat(directory);
    const meta = await readJson(controlledPath(directory, ".skill-meta.json"), {});
    return {
      name: safeName,
      display_name: cleanText(meta.display_name || safeName),
      description: parsed.description,
      instructions: parsed.instructions,
      version: Number(meta.version || 1),
      source: cleanText(meta.source || "project"),
      enabled,
      created_at: meta.created_at || info.birthtime.toISOString(),
      updated_at: meta.updated_at || info.mtime.toISOString(),
    };
  }

  async scan(root, enabled) {
    await mkdir(root, { recursive: true });
    const entries = await readdir(root, { withFileTypes: true });
    const items = [];
    for (const entry of entries) {
      if (!entry.isDirectory() || !NAME_PATTERN.test(entry.name)) continue;
      try {
        items.push(await this.readAt(root, enabled, entry.name));
      } catch {
        // Broken folders are not exposed as loadable Skills.
      }
    }
    return items;
  }

  async list() {
    await this.initialize();
    const items = [
      ...(await this.scan(this.enabledRoot, true)),
      ...(await this.scan(this.disabledRoot, false)),
    ];
    return items
      .map(({ instructions, ...item }) => item)
      .sort((a, b) => Number(b.enabled) - Number(a.enabled) || a.display_name.localeCompare(b.display_name, "zh-CN"));
  }

  async locate(name) {
    const safeName = assertSafeName(name);
    const enabledPath = controlledPath(this.enabledRoot, safeName);
    if (await exists(enabledPath)) return { root: this.enabledRoot, path: enabledPath, enabled: true, name: safeName };
    const disabledPath = controlledPath(this.disabledRoot, safeName);
    if (await exists(disabledPath)) return { root: this.disabledRoot, path: disabledPath, enabled: false, name: safeName };
    return null;
  }

  async get(name) {
    await this.initialize();
    const location = await this.locate(name);
    if (!location) throw apiError("Skill 不存在", "SKILL_NOT_FOUND", 404);
    return this.readAt(location.root, location.enabled, location.name);
  }

  async assertEnabled(names) {
    const unique = [...new Set((names || []).map((item) => assertSafeName(item)))];
    const enabled = new Set((await this.scan(this.enabledRoot, true)).map((item) => item.name));
    const missing = unique.filter((name) => !enabled.has(name));
    if (missing.length) {
      throw apiError(`以下 Skill 不存在或未启用：${missing.join("、")}`, "SKILL_NOT_ENABLED", 409);
    }
    return unique;
  }

  async publish(input = {}) {
    if (input.confirmed !== true) {
      throw apiError("发布 Skill 前需要人工确认", "SKILL_CONFIRMATION_REQUIRED", 409);
    }
    const validation = validateSkillDraft(input);
    if (!validation.valid) {
      throw apiError(validation.errors.join("；"), "SKILL_VALIDATION_FAILED");
    }
    if (validation.warnings.length && input.acknowledge_warnings !== true) {
      throw apiError("请先确认 Skill 校验警告", "SKILL_WARNINGS_UNCONFIRMED", 409);
    }

    return this.enqueue(async () => {
      await this.initialize();
      const draft = validation.normalized;
      const currentLocation = await this.locate(draft.name);
      const current = currentLocation
        ? await this.readAt(currentLocation.root, currentLocation.enabled, draft.name)
        : null;
      if (current && Number(input.expected_version) !== current.version) {
        throw apiError("Skill 已被其他操作更新，请重新载入后再保存", "SKILL_VERSION_CONFLICT", 409);
      }
      const version = current ? current.version + 1 : 1;
      const now = new Date().toISOString();
      const enabled = current?.enabled ?? true;
      const targetRoot = this.rootFor(enabled);
      const target = controlledPath(targetRoot, draft.name);
      const staging = controlledPath(this.stagingRoot, `${draft.name}-${randomUUID()}`);
      const metadata = {
        name: draft.name,
        display_name: draft.display_name,
        version,
        source: cleanText(input.source || current?.source || "agent-workbench"),
        enabled,
        created_at: current?.created_at || now,
        updated_at: now,
      };
      await mkdir(staging, { recursive: false });
      await writeFile(
        controlledPath(staging, "SKILL.md"),
        renderSkillMarkdown({ ...draft, version }),
        "utf8",
      );
      await writeFile(
        controlledPath(staging, ".skill-meta.json"),
        `${JSON.stringify(metadata, null, 2)}\n`,
        "utf8",
      );

      let archivedPath = null;
      try {
        if (currentLocation) {
          archivedPath = controlledPath(
            this.archiveRoot,
            `${draft.name}-v${current.version}-${timestampId()}-${randomUUID().slice(0, 8)}`,
          );
          await rename(currentLocation.path, archivedPath);
        }
        await rename(staging, target);
      } catch (error) {
        await rm(staging, { recursive: true, force: true }).catch(() => {});
        if (archivedPath && !(await exists(target)) && await exists(archivedPath)) {
          await rename(archivedPath, target).catch(() => {});
        }
        throw error;
      }
      return this.get(draft.name);
    });
  }

  async setEnabled(name, enabled) {
    return this.enqueue(async () => {
      await this.initialize();
      const location = await this.locate(name);
      if (!location) throw apiError("Skill 不存在", "SKILL_NOT_FOUND", 404);
      if (location.enabled === Boolean(enabled)) return this.get(location.name);
      const target = controlledPath(this.rootFor(Boolean(enabled)), location.name);
      if (await exists(target)) throw apiError("目标 Skill 已存在", "SKILL_ALREADY_EXISTS", 409);
      await rename(location.path, target);
      const metaPath = controlledPath(target, ".skill-meta.json");
      const meta = await readJson(metaPath, {});
      await writeFile(metaPath, `${JSON.stringify({
        ...meta,
        name: location.name,
        enabled: Boolean(enabled),
        updated_at: new Date().toISOString(),
      }, null, 2)}\n`, "utf8");
      return this.get(location.name);
    });
  }

  async archive(name) {
    return this.enqueue(async () => {
      await this.initialize();
      const location = await this.locate(name);
      if (!location) throw apiError("Skill 不存在", "SKILL_NOT_FOUND", 404);
      const current = await this.readAt(location.root, location.enabled, location.name);
      const target = controlledPath(
        this.archiveRoot,
        `${location.name}-v${current.version}-${timestampId()}-${randomUUID().slice(0, 8)}`,
      );
      await rename(location.path, target);
      return { archived: true, name: location.name, recoverable_path: target };
    });
  }

  async createRuntimeView(names, runId = randomUUID()) {
    await this.initialize();
    const selected = await this.assertEnabled(names);
    const viewName = `view-${String(runId).replace(/[^a-zA-Z0-9-]/g, "-")}-${randomUUID().slice(0, 8)}`;
    const target = controlledPath(this.runtimeRoot, viewName);
    await mkdir(target, { recursive: false });
    try {
      for (const name of selected) {
        await cp(
          controlledPath(this.enabledRoot, name),
          controlledPath(target, name),
          { recursive: true, errorOnExist: true },
        );
      }
    } catch (error) {
      await rm(target, { recursive: true, force: true }).catch(() => {});
      throw error;
    }
    return {
      path: target,
      names: selected,
      cleanup: () => rm(target, { recursive: true, force: true }),
    };
  }

  enqueue(operation) {
    const next = this.writeChain.then(operation, operation);
    this.writeChain = next.catch(() => {});
    return next;
  }
}

export const skillNamePattern = NAME_PATTERN;
export { assertSafeName, controlledPath };
