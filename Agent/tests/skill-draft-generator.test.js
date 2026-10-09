import assert from "node:assert/strict";
import test from "node:test";

import { generateSkillDraft, parseJsonResponse } from "../src/skill-draft-generator.js";

const MODEL = {
  id: "test-model",
  name: "Test Model",
  model: "test",
};

test("generateSkillDraft returns editable pending-review content", async () => {
  let runtimeInput;
  const result = await generateSkillDraft({
    idea: "把成功的月度业务趋势分析沉淀为 Skill",
    context: "用户：查询月度业务趋势\n助手：已生成表格和折线图",
    model: MODEL,
    runtime: async (input) => {
      runtimeInput = input;
      return {
        sessionId: "draft-session",
        text: JSON.stringify({
          name: "monthly-sales-trend",
          display_name: "月度业务趋势",
          description: "当用户要求月度业务趋势和均线时使用。",
          instructions: "# 流程\n\n调用 chain_neuron MCP 获取权威数据。",
        }),
        events: [],
      };
    },
  });
  assert.equal(result.status, "pending_review");
  assert.equal(result.draft.name, "monthly-sales-trend");
  assert.equal(result.validation.valid, true);
  assert.equal(runtimeInput.skillMode, "manual");
  assert.deepEqual(runtimeInput.selectedSkills, []);
  assert.match(runtimeInput.prompt, /pending_review/);
  assert.match(runtimeInput.prompt, /不要在 description 或 instructions 中写入 pending_review/);
});

test("parseJsonResponse accepts fenced JSON and rejects prose", () => {
  assert.equal(parseJsonResponse('```json\n{"name":"demo"}\n```').name, "demo");
  assert.throws(
    () => parseJsonResponse("这不是 JSON"),
    (error) => error.code === "SKILL_DRAFT_INVALID",
  );
});
