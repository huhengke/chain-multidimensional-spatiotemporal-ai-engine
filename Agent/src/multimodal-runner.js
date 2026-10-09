import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";

const runtimeRoot = process.env.DSH_RUNTIME_ROOT;
if (!runtimeRoot) throw new Error("multimodal-runner: DSH_RUNTIME_ROOT is required");

const runtimeImport = (packageName, entry = "lib/index.js") => import(pathToFileURL(
  resolve(runtimeRoot, "node_modules", packageName, entry),
).href);

const [
  { default: z },
  { brandString },
  { installModelSelection },
  { createUserMessage },
  { SessionSeq },
  { SessionQueryError },
  { lastAssistantStreamChunk },
] = await Promise.all([
  runtimeImport("@deepseek-ai/schemastery", "lib/index.mjs"),
  runtimeImport("@deepseek-ai/dsh-brand"),
  runtimeImport("@deepseek-ai/dsh-agent"),
  runtimeImport("@deepseek-ai/dsh-llm"),
  runtimeImport("@deepseek-ai/dsh-session"),
  runtimeImport("@deepseek-ai/dsh-session-query"),
  runtimeImport("@deepseek-ai/dsh-llm", "lib/types/assistant-stream.js"),
]);

export const name = "chain-neuron-multimodal-runner";
export const inject = ["agentDefaultModel", "agents", "sessions"];
export const Config = z.object({ task: z.string(), sessionId: z.string(), json: z.boolean() });

function writeEvent(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function summarize(session, firstSeq) {
  let started = false;
  let text = "";
  let reason;
  for (let seq = firstSeq; seq < session.seq; seq += 1) {
    const event = session.eventAt(SessionSeq(seq));
    if (!event) continue;
    if (event.type === "turn/start") {
      started = true;
      continue;
    }
    if (!started) continue;
    if (event.type === "assistant/message") {
      const joined = event.data.message.content
        .filter((block) => block.type === "text")
        .map((block) => block.text)
        .join("");
      if (joined) text = joined;
    }
    if (event.type === "turn/end") reason = event.data.reason;
  }
  return { text, reason };
}

function* liveEvents(session) {
  for (let seq = 0; seq < session.seq; seq += 1) {
    const event = session.eventAt(SessionSeq(seq));
    if (event) yield event;
  }
}

function currentPreset(header, events) {
  let preset = header.agentPreset;
  for (const event of events) {
    if (event.type === "agent-preset/selected") preset = event.data?.agentPreset;
  }
  return preset;
}

function assertAdoptable(header, events, sessionId, cwd) {
  if (currentPreset(header, events) !== undefined) throw new Error(`session "${sessionId}" uses an incompatible agent preset`);
  if (header.origin === "subagent" || header.parentSession !== undefined) throw new Error(`session "${sessionId}" is not a direct session`);
  if (!header.cwd || header.cwd !== cwd) throw new Error(`session "${sessionId}" belongs to a different working directory`);
}

async function resolveAgent(ctx, agents, sessionId, agentOptions, setup, cwd) {
  if (ctx.get("sessionPersistence") === undefined) throw new Error("session persistence is unavailable");
  const query = ctx.get("sessionQuery");
  if (query === undefined) throw new Error("session query is unavailable");
  if (agents.get(sessionId) !== undefined) throw new Error(`session "${sessionId}" is already active`);
  try {
    const observation = await query.observeSession(sessionId);
    try {
      assertAdoptable(observation.header, observation.events, sessionId, cwd);
      const { agent } = await agents.resume({ resumeSessionId: sessionId, agentOptions, setup });
      assertAdoptable(agent.session.header, liveEvents(agent.session), sessionId, cwd);
      return agent;
    } finally {
      observation[Symbol.dispose]?.();
    }
  } catch (error) {
    if (!(error instanceof SessionQueryError) || error.code !== "SESSION_QUERY_SESSION_NOT_FOUND") throw error;
    throw new Error(`session "${sessionId}" does not exist`);
  }
}

function parseArguments(raw) {
  if (!raw) return {};
  try { return JSON.parse(raw); } catch { return raw; }
}

function resultText(blocks) {
  return blocks.filter((block) => block.type === "text").map((block) => block.text || "").join("");
}

function addUsage(current, next) {
  if (!next) return current;
  const inputTokens = Number(current?.inputTokens || 0) + Number(next.inputTokens || 0);
  const outputTokens = Number(current?.outputTokens || 0) + Number(next.outputTokens || 0);
  return {
    inputTokens,
    outputTokens,
    totalTokens: Number(current?.totalTokens || 0) + Number(next.totalTokens ?? next.inputTokens + next.outputTokens),
  };
}

function projectJsonRun(ctx, agent, cwd) {
  let stepUsage;
  const stop = ctx.on("session/event", (session, event) => {
    if (session !== agent.session) return;
    if (event.type === "turn/start") writeEvent({ type: "status", phase: "turn_start", turn: event.data.turn });
    if (event.type === "step/start") writeEvent({ type: "status", phase: "step_start", turn: event.data.turn, step: event.data.step });
    if (event.type === "assistant/attempt") stepUsage = addUsage(stepUsage, lastAssistantStreamChunk(event.data.stream, "usage")?.usage);
    if (event.type === "assistant/message") {
      stepUsage = addUsage(stepUsage, event.data.usage ?? lastAssistantStreamChunk(event.data.stream, "usage")?.usage);
      for (const block of event.data.message.content) {
        if (block.type === "reasoning") writeEvent({ type: "thinking", text: block.text });
        if (block.type === "text") writeEvent({ type: "text", text: block.text });
      }
    }
    if (event.type === "step/end") {
      writeEvent({ type: "status", phase: "step_end", turn: event.data.turn, step: event.data.step, ...(stepUsage ? { usage: stepUsage } : {}) });
      stepUsage = undefined;
    }
    if (event.type === "turn/end") writeEvent({ type: "status", phase: "turn_end", turn: event.data.turn, reason: event.data.reason });
    if (event.type === "tool/call") writeEvent({ type: "tool_call", callId: event.data.callId, tool: event.data.name, input: parseArguments(event.data.arguments) });
    if (event.type === "tool/result" && event.surfaceOp === "append") {
      const message = event.data.message;
      writeEvent({ type: "tool_result", callId: message.toolCallId, status: message.isError ? "error" : "completed", result: resultText(message.content) });
    }
  });
  writeEvent({ type: "session", sessionId: agent.id, cwd });
  return { stop, finish: (text) => writeEvent({ type: "final", text }) };
}

async function stdinText() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

function parsePayload(raw) {
  try {
    const parsed = JSON.parse(raw);
    if (parsed?.version === 1 && parsed?.kind === "chain-neuron-agent-input") {
      return { prompt: String(parsed.prompt || ""), images: Array.isArray(parsed.images) ? parsed.images : [] };
    }
  } catch {
    // Plain text remains supported for manual Harness use.
  }
  return { prompt: raw, images: [] };
}

async function imageParts(images) {
  const root = resolve(process.env.CHAIN_AGENT_ATTACHMENT_ROOT || "");
  const rootPrefix = root.endsWith(sep) ? root : `${root}${sep}`;
  if (images.length > 4) throw new Error("a message may contain at most four images");
  const parts = [];
  for (const image of images) {
    const path = resolve(String(image.path || ""));
    if (!root || (!path.startsWith(rootPrefix) && path !== root)) throw new Error("image path is outside the managed attachment store");
    const data = await readFile(path);
    if (image.sha256 && createHash("sha256").update(data).digest("hex") !== image.sha256) throw new Error("image attachment digest mismatch");
    parts.push({ type: "image", data: data.toString("base64"), mediaType: String(image.mimeType || ""), name: String(image.name || "image") });
  }
  return parts;
}

async function run(ctx, config) {
  await ctx.get("loader")?.await();
  const agents = ctx.get("agents");
  const defaultModel = ctx.get("agentDefaultModel");
  const sessions = ctx.get("sessions");
  if (!agents || !defaultModel || !sessions) return;
  const raw = config.task === undefined || config.task === "-" ? await stdinText() : config.task;
  const payload = parsePayload(raw);
  if (!payload.prompt.trim() && !payload.images.length) throw new Error("a task or attachment is required");

  const selection = defaultModel.currentSelection();
  const agentOptions = { provider: selection.provider, model: selection.model };
  const setup = (agentCtx) => {
    installModelSelection(agentCtx, { current: selection, assembled: undefined });
  };
  const sessionId = brandString(config.sessionId ?? `session-${randomUUID()}`);
  const fs = ctx.get("fs");
  const cwd = fs === undefined ? process.cwd() : fs.processPath(await fs.resolve("."));
  const agent = config.sessionId === undefined
    ? (await agents.create({ sessionId, meta: { cwd }, agentOptions, setup })).agent
    : await resolveAgent(ctx, agents, sessionId, agentOptions, setup, cwd);
  await agent.whenIdle();
  const firstSeq = agent.session.seq;
  const projection = config.json ? projectJsonRun(ctx, agent, cwd) : null;
  try {
    const uploaded = await imageParts(payload.images);
    const prompt = payload.prompt.trim() || "请说明并分析用户提交的附件。";
    const proposed = [{ type: "text", text: prompt }, ...uploaded];
    const attachmentService = ctx.get("attachments");
    if (uploaded.length && !attachmentService) throw new Error("Harness attachment service is unavailable");
    const content = uploaded.length ? await attachmentService.admitPromptContent(proposed) : proposed;
    agent.followup(createUserMessage({ content, source: { kind: "user" } }));
    await agent.whenIdle();
    await sessions.flush(agent.session);
    const outcome = summarize(agent.session, firstSeq);
    if (projection) projection.finish(outcome.text);
    else process.stdout.write(`${outcome.text}\n`);
    if (outcome.reason?.kind === "error") process.stderr.write(`dsh: ${outcome.reason.error.code}: ${outcome.reason.error.message}\n`);
    ctx.get("appExit")?.(outcome.reason?.kind === "completed" ? 0 : 1);
  } finally {
    projection?.stop();
  }
}

export function apply(ctx, config) {
  const exit = ctx.get("appExit");
  if (!exit) throw new Error("multimodal-runner: appExit is unavailable");
  run(ctx, config).catch((cause) => {
    const message = cause instanceof Error ? cause.message : String(cause);
    if (config.json) writeEvent({ type: "error", message });
    process.stderr.write(`dsh: ${message}\n`);
    if (process.env.CHAIN_AGENT_DEBUG === "1" && cause instanceof Error && cause.stack) {
      process.stderr.write(`${cause.stack}\n`);
    }
    exit(1);
  });
}
