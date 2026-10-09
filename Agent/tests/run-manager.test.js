import assert from "node:assert/strict";
import { appendFile, mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { RunManager } from "../src/run-manager.js";
import { SessionStore } from "../src/session-store.js";
import { AttachmentStore } from "../src/attachment-store.js";

const MODEL = {
  id: "codex_openai",
  name: "Codex Responses",
  provider: "openai_codex",
  provider_route: "chain-codex",
  model: "gpt-test",
  environment: { OPENAI_API_KEY: "test" },
  supports_images: true,
};

async function fixture(runtime, options = {}) {
  const root = await mkdtemp(join(tmpdir(), "chain-run-"));
  const store = new SessionStore(join(root, "sessions.json"));
  const session = await store.create("测试");
  const queryLogPath = join(root, "engine-query-log.jsonl");
  const artifactLogPath = join(root, "agent-artifact-log.jsonl");
  return {
    root,
    store,
    session,
    queryLogPath,
    artifactLogPath,
    manager: new RunManager(store, runtime, { queryLogPath, artifactLogPath, ...options }),
  };
}

test("generic planner completes without a matching Skill", async () => {
  const fx = await fixture(async ({ onEvent }) => {
    onEvent({ type: "session", sessionId: "harness-1" });
    onEvent({
      type: "status",
      phase: "step_end",
      usage: { inputTokens: 120, outputTokens: 8, totalTokens: 128 },
    });
    onEvent({ type: "final", text: "通用结果" });
    return { sessionId: "harness-1", text: "通用结果", events: [] };
  });
  try {
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "临时分析", model: MODEL });
    const run = await fx.manager.wait(started.id);
    assert.equal(run.status, "completed");
    assert.equal(run.execution_mode, "general");
    assert.equal(run.answer, "通用结果");
    assert.deepEqual(run.usage, { input_tokens: 120, output_tokens: 8, total_tokens: 128 });
    assert.ok(run.duration_ms >= 0);
    const saved = await fx.store.get(fx.session.id);
    assert.deepEqual(saved.messages.map((item) => item.role), ["user", "assistant"]);
    assert.deepEqual(saved.messages[1].usage, run.usage);
    assert.equal(saved.messages[1].duration_ms, run.duration_ms);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("manual Skill selection is isolated and recorded on the run", async () => {
  let runtimeInput;
  let cleaned = false;
  const skillStore = {
    async assertEnabled(names) { return [...new Set(names)]; },
    async createRuntimeView(names) {
      assert.deepEqual(names, ["monthly-sales-trend"]);
      return {
        path: "D:/runtime/selected-skills",
        cleanup: async () => { cleaned = true; },
      };
    },
  };
  const fx = await fixture(async (input) => {
    runtimeInput = input;
    return { sessionId: "skill-session", text: "Skill 结果", events: [] };
  }, { skillStore });
  try {
    await fx.store.update(fx.session.id, {
      skill_mode: "manual",
      selected_skills: ["monthly-sales-trend"],
    });
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "分析趋势", model: MODEL });
    const run = await fx.manager.wait(started.id);
    assert.equal(run.execution_mode, "skill");
    assert.equal(run.skill, "monthly-sales-trend");
    assert.equal(run.skill_mode, "manual");
    assert.deepEqual(run.requested_skills, ["monthly-sales-trend"]);
    assert.equal(runtimeInput.skillRoot, "D:/runtime/selected-skills");
    assert.match(runtimeInput.prompt, /monthly-sales-trend/);
    assert.equal(cleaned, true);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("Harness failure events are not duplicated", async () => {
  const fx = await fixture(async ({ onEvent }) => {
    const event = { type: "status", phase: "turn_end" };
    onEvent(event);
    throw Object.assign(new Error("missing key"), {
      code: "MISSING_CREDENTIAL",
      events: [event],
    });
  });
  try {
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "测试", model: MODEL });
    const run = await fx.manager.wait(started.id);
    assert.equal(run.status, "failed");
    assert.equal(run.error.code, "MISSING_CREDENTIAL");
    assert.equal(run.events.filter((item) => item.type === "harness.event").length, 1);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("switching models starts a new Harness session", async () => {
  const calls = [];
  const fx = await fixture(async ({ harnessSessionId, model }) => {
    calls.push({ harnessSessionId, model: model.id });
    return { sessionId: `session-${model.id}`, text: "完成", events: [] };
  });
  const otherModel = { ...MODEL, id: "other", name: "Other Model", model: "other-model" };
  try {
    const first = await fx.manager.start({ sessionId: fx.session.id, message: "一", model: MODEL });
    await fx.manager.wait(first.id);
    const second = await fx.manager.start({ sessionId: fx.session.id, message: "二", model: otherModel });
    await fx.manager.wait(second.id);
    const third = await fx.manager.start({ sessionId: fx.session.id, message: "三", model: otherModel });
    await fx.manager.wait(third.id);
    assert.deepEqual(calls, [
      { harnessSessionId: null, model: "codex_openai" },
      { harnessSessionId: null, model: "other" },
      { harnessSessionId: "session-other", model: "other" },
    ]);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("branched conversation carries context into a new Harness session", async () => {
  let call;
  const fx = await fixture(async (input) => {
    call = input;
    return { sessionId: "branch-harness", text: "分枝回答", events: [] };
  });
  try {
    await fx.store.appendMessage(fx.session.id, {
      id: "source-user",
      run_id: "source-run",
      role: "user",
      content: "原始问题",
      created_at: new Date().toISOString(),
    });
    await fx.store.appendMessage(fx.session.id, {
      id: "source-answer",
      run_id: "source-run",
      role: "assistant",
      content: "原始回答",
      created_at: new Date().toISOString(),
    });
    const branch = await fx.store.branch(fx.session.id, "source-answer");
    const started = await fx.manager.start({ sessionId: branch.id, message: "继续分析", model: MODEL });
    await fx.manager.wait(started.id);
    assert.equal(call.harnessSessionId, null);
    assert.match(call.prompt, /原始问题/);
    assert.match(call.prompt, /原始回答/);
    assert.match(call.prompt, /继续分析/);
    const saved = await fx.store.get(branch.id);
    assert.equal(saved.branch_context_pending, false);
    assert.equal(saved.harness_session_id, "branch-harness");
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("archived conversation is read-only", async () => {
  const fx = await fixture(async () => ({ sessionId: "unused", text: "", events: [] }));
  try {
    await fx.store.update(fx.session.id, { archived: true });
    await assert.rejects(
      fx.manager.start({ sessionId: fx.session.id, message: "不应发送", model: MODEL }),
      (error) => error.code === "SESSION_ARCHIVED" && error.status === 409,
    );
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("single-scope query exposes real hits and scope disclosure states", async () => {
  const fx = await fixture(async ({ onEvent, runId }) => {
    // 模拟 MCP Server：工具结果可能被 Harness 截断，因此真实状态走 JSONL 旁路日志。
    onEvent({ type: "tool_call", callId: "call-1", tool: "mcp__chain_neuron__engine_query", input: {} });
    await appendFile(fx.queryLogPath, `${JSON.stringify({
      run_id: runId,
      at: Date.now(),
      views: [
        {
          dataset: "oil_monthly_sales",
          label: "业务域甲月报",
          queried: true,
          available: true,
          record_count: 4646,
          route: { dataset: "oil_monthly_sales", steps: [{ value: "202401", status: "active" }] },
          steps: [{ value: "202401", status: "active" }],
        },
        {
          dataset: "non_oil_sales",
          label: "业务域乙明细",
          queried: false,
          available: false,
          record_count: 0,
          route: null,
          steps: [],
          scope_only: true,
        },
      ],
    })}\n`, "utf8");
    onEvent({
      type: "tool_result",
      callId: "call-1",
      status: "completed",
      result: '{"trace_id":"t","actual":{"dataset":"oil_monthly_sales","available":true',
    });
    return { sessionId: "harness-scope", text: "结果", events: [] };
  });
  try {
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "2024年各月业务", model: MODEL });
    const run = await fx.manager.wait(started.id);
    const views = new Map(run.dataset_views.map((item) => [item.dataset, item]));
    assert.equal(views.size, 2);
    assert.equal(views.get("oil_monthly_sales").queried, true);
    assert.equal(views.get("oil_monthly_sales").available, true);
    assert.equal(views.get("oil_monthly_sales").record_count, 4646);
    assert.equal(views.get("non_oil_sales").queried, false);
    assert.equal(views.get("non_oil_sales").available, false);
    assert.equal(views.get("non_oil_sales").scope_only, true);
    assert.ok(run.events.some((item) => item.type === "engine.query.result"));
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("truncated tool results never fabricate zero-hit dataset states", async () => {
  const fx = await fixture(async ({ onEvent, runId }) => {
    onEvent({ type: "tool_call", callId: "call-9", tool: "mcp__chain_neuron__engine_query", input: {} });
    onEvent({
      type: "tool_result",
      callId: "call-9",
      status: "completed",
      result: "",
    });
    assert.ok(runId);
    return { sessionId: "harness-truncated", text: "结果", events: [] };
  });
  try {
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "空结果", model: MODEL });
    const run = await fx.manager.wait(started.id);
    assert.deepEqual(run.dataset_views, []);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("published chart artifacts are attached to runs and persisted messages", async () => {
  const fx = await fixture(async ({ runId }) => {
    await appendFile(fx.artifactLogPath, `${JSON.stringify({
      run_id: runId,
      at: Date.now(),
      visualization: {
        id: "chart-sales",
        version: 1,
        type: "line",
        title: "月度业务金额及均线",
        x_axis: { name: "月份", categories: ["1月", "2月", "3月"] },
        y_axis: { name: "业务金额", unit: "万元" },
        series: [
          { name: "业务金额", data: [1, 2, 3] },
          { name: "3个月均线", data: [null, null, 2] },
        ],
        source: { datasets: ["non_oil_sales"], metrics: ["sale_amount"], period: "2026" },
      },
    })}\n`, "utf8");
    return { sessionId: "harness-chart", text: "图表回答", events: [] };
  });
  try {
    const started = await fx.manager.start({ sessionId: fx.session.id, message: "画折线图", model: MODEL });
    const run = await fx.manager.wait(started.id);
    assert.equal(run.visualizations.length, 1);
    assert.equal(run.visualizations[0].id, "chart-sales");
    assert.deepEqual(run.visualizations[0].series[1].data, [null, null, 2]);
    assert.ok(run.events.some((item) => item.type === "agent.chart.result"));
    const saved = await fx.store.get(fx.session.id);
    assert.deepEqual(saved.messages[1].visualizations, run.visualizations);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("one session cannot run two tasks concurrently", async () => {
  let finish;
  const pending = new Promise((resolve) => { finish = resolve; });
  const fx = await fixture(async () => pending);
  try {
    const first = await fx.manager.start({ sessionId: fx.session.id, message: "第一条", model: MODEL });
    await assert.rejects(
      fx.manager.start({ sessionId: fx.session.id, message: "第二条", model: MODEL }),
      (error) => error.code === "SESSION_BUSY" && error.status === 409,
    );
    finish({ sessionId: "harness-2", text: "完成", events: [] });
    await fx.manager.wait(first.id);
  } finally {
    await rm(fx.root, { recursive: true, force: true });
  }
});

test("image attachments are persisted and sent only to a vision-capable model", async () => {
  let runtimeInput;
  const root = await mkdtemp(join(tmpdir(), "chain-run-image-"));
  try {
    const store = new SessionStore(join(root, "sessions.json"));
    const attachmentStore = new AttachmentStore(join(root, "attachments"));
    const session = await store.create("图片分析");
    const image = await attachmentStore.upload(session.id, {
      name: "现场.jpg",
      mimeType: "image/jpeg",
      data: Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    });
    const manager = new RunManager(store, async (input) => {
      runtimeInput = input;
      return { sessionId: "vision-session", text: "图片结果", events: [] };
    }, {
      attachmentStore,
      queryLogPath: join(root, "query.jsonl"),
      artifactLogPath: join(root, "artifact.jsonl"),
    });
    const started = await manager.start({
      sessionId: session.id,
      message: "",
      model: MODEL,
      attachmentTokens: [image.token],
    });
    const run = await manager.wait(started.id);
    assert.equal(run.status, "completed");
    assert.equal(runtimeInput.attachments.length, 1);
    assert.deepEqual(runtimeInput.allowedAttachmentIds, [image.attachment.id]);
    const saved = await store.get(session.id);
    assert.equal(saved.messages[0].attachments[0].name, "现场.jpg");

    const second = await attachmentStore.upload(session.id, {
      name: "不支持.jpg",
      mimeType: "image/jpeg",
      data: Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    });
    await assert.rejects(
      manager.start({
        sessionId: session.id,
        message: "分析",
        model: { ...MODEL, supports_images: false },
        attachmentTokens: [second.token],
      }),
      (error) => error.code === "MODEL_VISION_UNSUPPORTED" && error.status === 422,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
