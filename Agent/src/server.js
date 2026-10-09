import http from "node:http";
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";

import { config } from "./config.js";
import { AttachmentStore, attachmentLimits, publicAttachment } from "./attachment-store.js";
import { inspectHarnessRuntime } from "./harness-runtime.js";
import { listAgentModels, resolveAgentModel } from "./model-registry.js";
import { receiveSingleFile } from "./multipart.js";
import { RunManager } from "./run-manager.js";
import { SessionStore } from "./session-store.js";
import { generateSkillDraft, skillDraftContext } from "./skill-draft-generator.js";
import { SkillStore, validateSkillDraft } from "./skill-store.js";

const sessions = new SessionStore(config.sessionIndexPath);
const attachments = new AttachmentStore(config.attachmentRoot);
const skills = new SkillStore({
  enabledRoot: config.skillRoot,
  disabledRoot: config.disabledSkillRoot,
  archiveRoot: config.skillArchiveRoot,
  stagingRoot: config.skillStagingRoot,
  runtimeRoot: config.skillRuntimeRoot,
});
const runs = new RunManager(sessions, undefined, {
  attachmentStore: attachments,
  skillStore: skills,
});

function json(res, status, payload) {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
  });
  res.end(JSON.stringify(payload));
}

async function body(req) {
  let raw = "";
  for await (const chunk of req) {
    raw += chunk;
    if (Buffer.byteLength(raw) > config.maxRequestBytes) {
      throw Object.assign(new Error("请求体过大"), { status: 413, code: "REQUEST_TOO_LARGE" });
    }
  }
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    throw Object.assign(new Error("请求体必须是 JSON"), { status: 400, code: "INVALID_JSON" });
  }
}

function cleanMessage(value, { allowEmpty = false } = {}) {
  const message = String(value || "").trim();
  if (!message && !allowEmpty) throw Object.assign(new Error("缺少 message"), { status: 422, code: "MESSAGE_REQUIRED" });
  if (message.length > 12000) throw Object.assign(new Error("message 超过 12000 字符"), { status: 422, code: "MESSAGE_TOO_LONG" });
  return message;
}

function findSessionAttachment(session, attachmentId) {
  for (const message of session?.messages || []) {
    const match = (message.attachments || []).find((item) => item.id === attachmentId);
    if (match) return match;
  }
  return null;
}

async function authorizeAttachment(sessionId, attachmentId, token) {
  const session = await sessions.get(sessionId);
  if (!session) throw Object.assign(new Error("会话不存在"), { code: "SESSION_NOT_FOUND", status: 404 });
  const saved = findSessionAttachment(session, attachmentId);
  if (saved) return saved;
  if (token) {
    const draft = await attachments.draft(sessionId, token, { allowConsumed: true });
    if (draft.attachment_id === attachmentId) {
      const meta = await attachments.metadata(attachmentId);
      return publicAttachment(meta, draft.name);
    }
  }
  throw Object.assign(new Error("该附件不属于当前会话"), { code: "ATTACHMENT_ACCESS_DENIED", status: 403 });
}

async function attachmentContent(res, record) {
  const path = await attachments.objectPath(record.id);
  const info = await stat(path);
  const disposition = record.kind === "image" || record.document_type === "pdf" ? "inline" : "attachment";
  res.writeHead(200, {
    "Content-Type": record.mime_type || "application/octet-stream",
    "Content-Length": String(info.size),
    "Content-Disposition": `${disposition}; filename*=UTF-8''${encodeURIComponent(record.name || "attachment")}`,
    "Cache-Control": "private, max-age=3600",
    "X-Content-Type-Options": "nosniff",
    "Access-Control-Allow-Origin": "*",
  });
  createReadStream(path).pipe(res);
}

function thoughtChain(run) {
  return run.events
    .filter((event) => ["planner.mode.selected", "planner.mode.changed", "run.failed"].includes(event.type))
    .map((event) => ({
      level: event.type === "run.failed" ? "Agent 运行" : "执行模式",
      status: event.type === "run.failed" ? "failed" : "completed",
      message: event.message || event.error?.message || event.mode,
    }));
}

function compatibilityResponse(run) {
  return {
    answer: run.answer || "",
    intent: {
      intent: "agent",
      dataset: "",
      datasets: [],
      dataset_scope: "dynamic",
      conditions: {},
      group_by: null,
      detail: false,
      execution_mode: run.execution_mode,
      skill: run.skill,
    },
    thought_chain: thoughtChain(run),
    data: {
      session_id: run.session_id,
      harness_session_id: run.harness_session_id,
      run_id: run.id,
      execution_mode: run.execution_mode,
      events: run.events,
      visualizations: run.visualizations || [],
    },
    llm_backend: run.model ? `${run.model.name} (${run.model.model})` : "deepseek-harness",
  };
}

function sse(req, res, run) {
  res.writeHead(200, {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    Connection: "keep-alive",
    "Access-Control-Allow-Origin": "*",
  });
  const send = (event) => {
    res.write(`id: ${event.id}\n`);
    res.write(`event: ${event.type}\n`);
    res.write(`data: ${JSON.stringify(event)}\n\n`);
  };
  for (const event of run.events) send(event);
  if (run.status !== "running") return res.end();
  const onEvent = (event) => send(event);
  const onSettled = () => res.end();
  run.emitter.on("event", onEvent);
  run.emitter.once("settled", onSettled);
  req.on("close", () => {
    run.emitter.off("event", onEvent);
    run.emitter.off("settled", onSettled);
  });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
    if (req.method === "OPTIONS") return json(res, 204, {});

    if (req.method === "GET" && url.pathname === "/healthz") {
      const runtime = await inspectHarnessRuntime();
      return json(res, runtime.available ? 200 : 503, {
        status: runtime.available ? "ok" : "degraded",
        service: "chain-neuron-agent-gateway",
        runtime: "deepseek-harness-headless",
        harness: runtime,
        engine: config.engineBaseUrl,
        time: new Date().toISOString(),
      });
    }

    if (req.method === "GET" && url.pathname === "/api/v1/agent/models") {
      return json(res, 200, await listAgentModels());
    }
    if (req.method === "GET" && url.pathname === "/api/v1/agent/attachments/limits") {
      return json(res, 200, attachmentLimits);
    }
    if (req.method === "GET" && url.pathname === "/api/v1/agent/skills") {
      return json(res, 200, { items: await skills.list() });
    }
    if (req.method === "POST" && url.pathname === "/api/v1/agent/skills/analyze") {
      const input = await body(req);
      const model = await resolveAgentModel(input.model);
      let context = "";
      if (input.include_context && input.session_id) {
        const session = await sessions.get(String(input.session_id));
        if (!session) return json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
        context = skillDraftContext(session);
      }
      return json(res, 200, await generateSkillDraft({ idea: input.idea, context, model }));
    }
    if (req.method === "POST" && url.pathname === "/api/v1/agent/skills/validate") {
      return json(res, 200, validateSkillDraft(await body(req)));
    }
    if (req.method === "POST" && url.pathname === "/api/v1/agent/skills/publish") {
      return json(res, 201, await skills.publish(await body(req)));
    }

    const skillMatch = url.pathname.match(/^\/api\/v1\/agent\/skills\/([^/]+)$/);
    if (req.method === "GET" && skillMatch) {
      return json(res, 200, await skills.get(decodeURIComponent(skillMatch[1])));
    }
    if (req.method === "DELETE" && skillMatch) {
      return json(res, 200, await skills.archive(decodeURIComponent(skillMatch[1])));
    }
    const skillStatusMatch = url.pathname.match(/^\/api\/v1\/agent\/skills\/([^/]+)\/status$/);
    if (req.method === "POST" && skillStatusMatch) {
      const input = await body(req);
      return json(res, 200, await skills.setEnabled(
        decodeURIComponent(skillStatusMatch[1]),
        Boolean(input.enabled),
      ));
    }

    if (req.method === "POST" && url.pathname === "/api/v1/agent/sessions") {
      const input = await body(req);
      return json(res, 201, await sessions.create(input.title));
    }
    if (req.method === "GET" && url.pathname === "/api/v1/agent/sessions") {
      return json(res, 200, { items: await sessions.list({ archived: url.searchParams.get("archived") === "true" }) });
    }

    const uploadMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/attachments$/);
    if (req.method === "POST" && uploadMatch) {
      const sessionId = decodeURIComponent(uploadMatch[1]);
      const session = await sessions.get(sessionId);
      if (!session) return json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
      if (session.archived) return json(res, 409, { code: "SESSION_ARCHIVED", message: "归档会话不能上传附件" });
      if (runs.hasActiveSession(sessionId)) return json(res, 409, { code: "SESSION_BUSY", message: "运行中不能上传附件" });
      const file = await receiveSingleFile(req, { maxBytes: attachmentLimits.document_max_bytes });
      return json(res, 201, await attachments.upload(sessionId, file));
    }

    const attachmentContentMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/attachments\/([^/]+)\/content$/);
    if (req.method === "GET" && attachmentContentMatch) {
      const sessionId = decodeURIComponent(attachmentContentMatch[1]);
      const attachmentId = decodeURIComponent(attachmentContentMatch[2]);
      const record = await authorizeAttachment(sessionId, attachmentId, url.searchParams.get("token"));
      return attachmentContent(res, record);
    }

    const attachmentMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/attachments\/([^/]+)$/);
    if (req.method === "GET" && attachmentMatch) {
      const sessionId = decodeURIComponent(attachmentMatch[1]);
      const attachmentId = decodeURIComponent(attachmentMatch[2]);
      return json(res, 200, await authorizeAttachment(sessionId, attachmentId, url.searchParams.get("token")));
    }
    if (req.method === "DELETE" && attachmentMatch) {
      const sessionId = decodeURIComponent(attachmentMatch[1]);
      const deleted = await attachments.deleteDraft(sessionId, decodeURIComponent(attachmentMatch[2]));
      return deleted
        ? json(res, 200, { deleted: true })
        : json(res, 404, { code: "ATTACHMENT_DRAFT_NOT_FOUND", message: "待发送附件不存在或已被使用" });
    }

    const sessionMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)$/);
    if (req.method === "GET" && sessionMatch) {
      const item = await sessions.get(decodeURIComponent(sessionMatch[1]));
      return item ? json(res, 200, item) : json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
    }

    if (req.method === "DELETE" && sessionMatch) {
      const sessionId = decodeURIComponent(sessionMatch[1]);
      if (runs.hasActiveSession(sessionId)) {
        return json(res, 409, { code: "SESSION_BUSY", message: "运行中的会话不能删除" });
      }
      const removed = await sessions.remove(sessionId);
      return removed
        ? json(res, 200, { deleted: true })
        : json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
    }

    const renameMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/rename$/);
    if (req.method === "POST" && renameMatch) {
      const input = await body(req);
      const title = String(input.title || "").trim();
      if (!title) return json(res, 422, { code: "TITLE_REQUIRED", message: "会话名称不能为空" });
      const item = await sessions.update(decodeURIComponent(renameMatch[1]), { title: title.slice(0, 80) });
      return item
        ? json(res, 200, item)
        : json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
    }

    const pinMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/pin$/);
    if (req.method === "POST" && pinMatch) {
      const input = await body(req);
      const item = await sessions.update(decodeURIComponent(pinMatch[1]), { pinned: Boolean(input.pinned) });
      return item
        ? json(res, 200, item)
        : json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
    }

    const archiveMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/archive$/);
    if (req.method === "POST" && archiveMatch) {
      const sessionId = decodeURIComponent(archiveMatch[1]);
      if (runs.hasActiveSession(sessionId)) {
        return json(res, 409, { code: "SESSION_BUSY", message: "运行中的会话不能归档" });
      }
      const input = await body(req);
      const item = await sessions.update(sessionId, { archived: Boolean(input.archived) });
      return item
        ? json(res, 200, item)
        : json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
    }

    const sessionSkillsMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/skills$/);
    if (req.method === "POST" && sessionSkillsMatch) {
      const sessionId = decodeURIComponent(sessionSkillsMatch[1]);
      if (runs.hasActiveSession(sessionId)) {
        return json(res, 409, { code: "SESSION_BUSY", message: "运行中不能切换 Skill" });
      }
      const current = await sessions.get(sessionId);
      if (!current) return json(res, 404, { code: "SESSION_NOT_FOUND", message: "会话不存在" });
      const input = await body(req);
      const mode = input.mode === "manual" ? "manual" : input.mode === "auto" ? "auto" : null;
      if (!mode) return json(res, 422, { code: "SKILL_MODE_INVALID", message: "Skill 模式必须是 auto 或 manual" });
      const selected = mode === "manual"
        ? await skills.assertEnabled(Array.isArray(input.skills) ? input.skills.map(String) : [])
        : [];
      return json(res, 200, await sessions.update(sessionId, {
        skill_mode: mode,
        selected_skills: selected,
        harness_session_id: null,
      }));
    }

    const messageMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/messages$/);
    if (req.method === "POST" && messageMatch) {
      const input = await body(req);
      const attachmentTokens = Array.isArray(input.attachments) ? input.attachments.map(String) : [];
      const model = await resolveAgentModel(input.model);
      const started = await runs.start({
        sessionId: decodeURIComponent(messageMatch[1]),
        message: cleanMessage(input.message, { allowEmpty: attachmentTokens.length > 0 }),
        model,
        attachmentTokens,
      });
      return json(res, 202, started);
    }

    const feedbackMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/messages\/([^/]+)\/feedback$/);
    if (req.method === "POST" && feedbackMatch) {
      const input = await body(req);
      if (!["up", "down", null].includes(input.feedback ?? null)) {
        return json(res, 422, { code: "INVALID_FEEDBACK", message: "feedback 必须是 up、down 或 null" });
      }
      const message = await sessions.updateMessage(
        decodeURIComponent(feedbackMatch[1]),
        decodeURIComponent(feedbackMatch[2]),
        { feedback: input.feedback ?? null, feedback_updated_at: new Date().toISOString() },
      );
      return message
        ? json(res, 200, message)
        : json(res, 404, { code: "MESSAGE_NOT_FOUND", message: "消息不存在" });
    }

    const branchMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/messages\/([^/]+)\/branch$/);
    if (req.method === "POST" && branchMatch) {
      const branch = await sessions.branch(
        decodeURIComponent(branchMatch[1]),
        decodeURIComponent(branchMatch[2]),
      );
      return branch
        ? json(res, 201, branch)
        : json(res, 404, { code: "MESSAGE_NOT_FOUND", message: "无法从该消息创建分枝" });
    }

    const eventMatch = url.pathname.match(/^\/api\/v1\/agent\/sessions\/([^/]+)\/events$/);
    if (req.method === "GET" && eventMatch) {
      const session = await sessions.get(decodeURIComponent(eventMatch[1]));
      const runId = url.searchParams.get("run_id") || session?.last_run_id;
      const run = runId ? runs.get(runId) : null;
      if (!run) return json(res, 404, { code: "RUN_NOT_FOUND", message: "运行不存在" });
      return sse(req, res, run);
    }

    const runMatch = url.pathname.match(/^\/api\/v1\/agent\/runs\/([^/]+)$/);
    if (req.method === "GET" && runMatch) {
      const run = runs.get(decodeURIComponent(runMatch[1]));
      return run ? json(res, 200, runs.publicRun(run)) : json(res, 404, { code: "RUN_NOT_FOUND", message: "运行不存在" });
    }

    const cancelMatch = url.pathname.match(/^\/api\/v1\/agent\/runs\/([^/]+)\/cancel$/);
    if (req.method === "POST" && cancelMatch) {
      const cancelled = runs.cancel(decodeURIComponent(cancelMatch[1]));
      return json(res, cancelled ? 202 : 409, { cancelled });
    }

    if (req.method === "POST" && url.pathname === "/api/v1/chat") {
      const input = await body(req);
      const model = await resolveAgentModel(input.model);
      const session = await sessions.create(String(input.message || "").slice(0, 40));
      const started = await runs.start({
        sessionId: session.id,
        message: cleanMessage(input.message),
        model,
      });
      const completed = await runs.wait(started.id);
      if (completed.status !== "completed") {
        return json(res, 502, {
          code: completed.error?.code || "AGENT_FAILED",
          message: completed.error?.message || "Agent 运行失败",
          session_id: completed.session_id,
          run_id: completed.id,
        });
      }
      return json(res, 200, compatibilityResponse(completed));
    }

    return json(res, 404, { code: "NOT_FOUND", message: "接口不存在" });
  } catch (error) {
    return json(res, error.status || 500, {
      code: error.code || "INTERNAL_ERROR",
      message: error.message || "Agent Gateway 内部错误",
    });
  }
});

server.listen(config.port, config.bindHost, () => {
  console.log(`Agent Gateway http://${config.bindHost}:${config.port}`);
  console.log(`Harness patch ${config.harnessPatch}`);
  console.log(`AI data engine ${config.engineBaseUrl}`);
});
