import assert from "node:assert/strict";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { SessionStore } from "../src/session-store.js";

test("SessionStore manages pinned archived and deleted sessions", async () => {
  const root = await mkdtemp(join(tmpdir(), "chain-agent-manage-"));
  try {
    const store = new SessionStore(join(root, "sessions.json"));
    const first = await store.create("第一个");
    const second = await store.create("第二个");
    await store.update(first.id, { pinned: true, title: "已重命名" });
    let active = await store.list();
    assert.equal(active[0].id, first.id);
    assert.equal(active[0].title, "已重命名");

    await store.update(first.id, { archived: true });
    active = await store.list();
    const archived = await store.list({ archived: true });
    assert.deepEqual(active.map((item) => item.id), [second.id]);
    assert.deepEqual(archived.map((item) => item.id), [first.id]);

    assert.equal(await store.remove(first.id), true);
    assert.equal(await store.get(first.id), null);
    assert.equal(await store.remove(first.id), false);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("SessionStore persists Harness mapping and messages", async () => {
  const root = await mkdtemp(join(tmpdir(), "chain-agent-"));
  try {
    const path = join(root, "sessions.json");
    const store = new SessionStore(path);
    const session = await store.create("测试会话");
    assert.equal(session.skill_mode, "auto");
    assert.deepEqual(session.selected_skills, []);
    await store.update(session.id, {
      skill_mode: "manual",
      selected_skills: ["monthly-sales-trend"],
    });
    await store.update(session.id, { harness_session_id: "session-harness" });
    await store.appendMessage(session.id, {
      id: "message-1",
      run_id: "run-1",
      role: "user",
      content: "测试问题",
      created_at: new Date().toISOString(),
    });

    await store.appendMessage(session.id, {
      id: "message-2",
      run_id: "run-1",
      role: "assistant",
      content: "测试回答",
      usage: { input_tokens: 10, output_tokens: 2, total_tokens: 12 },
      duration_ms: 350,
      visualizations: [{
        id: "chart-1",
        version: 1,
        type: "line",
        title: "趋势",
        x_axis: { name: "月", categories: ["1月", "2月"] },
        y_axis: { name: "金额", unit: "万元" },
        series: [{ name: "业务金额", data: [1, 2] }],
        source: { datasets: ["sales"], metrics: ["amount"], period: "2026" },
      }],
      feedback: null,
      created_at: new Date().toISOString(),
    });
    const feedback = await store.updateMessage(session.id, "message-2", { feedback: "up" });
    assert.equal(feedback.feedback, "up");
    const branch = await store.branch(session.id, "message-2");
    assert.equal(branch.source_session_id, session.id);
    assert.equal(branch.source_message_id, "message-2");
    assert.equal(branch.branch_context_pending, true);
    assert.equal(branch.harness_session_id, null);
    assert.equal(branch.message_count, 2);
    assert.equal(branch.skill_mode, "manual");
    assert.deepEqual(branch.selected_skills, ["monthly-sales-trend"]);

    const reopened = new SessionStore(path);
    const item = await reopened.get(session.id);
    assert.equal(item.harness_session_id, "session-harness");
    assert.equal(item.message_count, 2);
    assert.equal(item.messages[0].content, "测试问题");
    assert.equal(item.messages[1].feedback, "up");
    assert.equal(item.messages[1].visualizations[0].id, "chart-1");
    assert.equal(item.skill_mode, "manual");
    assert.deepEqual(item.selected_skills, ["monthly-sales-trend"]);

    const raw = JSON.parse(await readFile(path, "utf8"));
    assert.equal(raw.version, 1);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
