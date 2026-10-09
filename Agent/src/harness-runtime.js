import { access, mkdir, readFile, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import readline from "node:readline";

import { config, harnessEnvironment } from "./config.js";

export class HarnessRuntimeError extends Error {
  constructor(message, { code = "HARNESS_ERROR", events = [], stderr = "" } = {}) {
    super(message);
    this.name = "HarnessRuntimeError";
    this.code = code;
    this.events = events;
    this.stderr = stderr;
  }
}

export async function inspectHarnessRuntime() {
  const missing = [];
  for (const [label, path] of [
    ["executable", config.harnessExecutable],
    ["appAsar", config.harnessCliEntry.split("app.asar")[0] + "app.asar"],
    ["patch", config.harnessPatch],
  ]) {
    try {
      await access(path);
    } catch {
      missing.push({ label, path });
    }
  }
  return {
    available: missing.length === 0,
    executable: config.harnessExecutable,
    cli_entry: config.harnessCliEntry,
    patch: config.harnessPatch,
    missing,
  };
}

const RUNNER_PLACEHOLDER = "__CHAIN_AGENT_RUNNER_URL__";

async function materializeHarnessPatch() {
  const template = await readFile(config.harnessPatch, "utf8");
  if (!template.includes(RUNNER_PLACEHOLDER)) {
    throw new HarnessRuntimeError("Harness 补丁缺少运行器占位符", {
      code: "HARNESS_PATCH_INVALID",
    });
  }
  const outputDir = join(config.agentRoot, "data", "runtime");
  const outputPath = join(outputDir, "chain-neuron-headless.generated.yml");
  const content = template.replaceAll(
    RUNNER_PLACEHOLDER,
    pathToFileURL(config.harnessRunner).href,
  );
  await mkdir(outputDir, { recursive: true });
  await writeFile(outputPath, content, "utf8");
  return outputPath;
}

function eventError(event) {
  const error = event?.reason?.error || event?.error;
  if (!error) return null;
  return {
    code: String(error.code || "HARNESS_ERROR"),
    message: String(error.message || "DeepSeek Harness 执行失败"),
  };
}

export async function runHarness({
  prompt,
  harnessSessionId,
  model,
  runId,
  sessionId,
  attachments = [],
  allowedAttachmentIds = [],
  skillRoot = config.skillRoot,
  signal,
  onEvent,
}) {
  const state = await inspectHarnessRuntime();
  if (!state.available) {
    throw new HarnessRuntimeError("DeepSeek Harness Runtime 文件不完整", {
      code: "HARNESS_NOT_FOUND",
    });
  }

  const harnessPatch = await materializeHarnessPatch();

  const args = [
    "--expose-internals",
    config.harnessCliEntry,
    "--profile",
    "headless",
    "--patch",
    harnessPatch,
    "--json",
  ];
  if (harnessSessionId) args.push("--session-id", harnessSessionId);
  args.push("-");

  const child = spawn(config.harnessExecutable, args, {
    cwd: config.workspaceRoot,
    env: harnessEnvironment({
      ...(model?.environment || {}),
      CHAIN_AGENT_MODEL_PROVIDER: model?.provider_route || "deepseek-official",
      CHAIN_AGENT_MODEL: model?.model || "deepseek-flash",
      CHAIN_AGENT_RUN_ID: runId || "",
      CHAIN_AGENT_SESSION_ID: sessionId || "",
      CHAIN_AGENT_ALLOWED_ATTACHMENTS: JSON.stringify(allowedAttachmentIds),
      CHAIN_AGENT_SKILL_ROOT: skillRoot,
    }),
    windowsHide: true,
    stdio: ["pipe", "pipe", "pipe"],
  });

  const events = [];
  let stderr = "";
  let finalText = "";
  let resolvedSessionId = harnessSessionId || null;
  let reportedError = null;
  let timedOut = false;

  const abort = () => child.kill();
  signal?.addEventListener("abort", abort, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    child.kill();
  }, config.runTimeoutMs);

  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => {
    stderr += chunk;
    if (stderr.length > 32768) stderr = stderr.slice(-32768);
  });

  const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
  lines.on("line", (line) => {
    if (!line.trim()) return;
    let event;
    try {
      event = JSON.parse(line);
    } catch {
      event = { type: "diagnostic", text: line };
    }
    events.push(event);
    if (event.type === "session" && event.sessionId) resolvedSessionId = event.sessionId;
    if (event.type === "final") finalText = String(event.text || "");
    reportedError ||= eventError(event);
    onEvent?.(event);
  });

  const images = attachments.map((item) => ({
    id: item.id,
    name: item.name,
    mimeType: item.mime_type,
    sha256: item.sha256,
    path: join(config.attachmentRoot, "objects", item.sha256.slice(0, 2), item.sha256),
  }));
  child.stdin.end(JSON.stringify({
    version: 1,
    kind: "chain-neuron-agent-input",
    prompt,
    images,
  }), "utf8");

  const exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  }).finally(() => {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abort);
    lines.close();
  });

  if (signal?.aborted) {
    throw new HarnessRuntimeError("Agent 运行已取消", {
      code: "RUN_CANCELLED",
      events,
      stderr,
    });
  }
  if (timedOut) {
    throw new HarnessRuntimeError("Agent 运行超时", {
      code: "RUN_TIMEOUT",
      events,
      stderr,
    });
  }
  if (exitCode !== 0 || reportedError) {
    throw new HarnessRuntimeError(
      reportedError?.message || stderr.trim() || `DeepSeek Harness 退出码 ${exitCode}`,
      { code: reportedError?.code || "HARNESS_EXIT", events, stderr },
    );
  }

  return {
    sessionId: resolvedSessionId,
    text: finalText,
    events,
    exitCode,
  };
}
