import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import readline from "node:readline";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import * as XLSX from "xlsx";

import { AttachmentStore } from "../src/attachment-store.js";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function requestMcp(messages, environment = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(process.execPath, [resolve(root, "src", "mcp-server.js")], {
      cwd: root,
      env: { ...process.env, ...environment },
      stdio: ["pipe", "pipe", "inherit"],
    });
    const replies = [];
    const lines = readline.createInterface({ input: child.stdout, crlfDelay: Infinity });
    lines.on("line", (line) => {
      replies.push(JSON.parse(line));
      if (replies.length === messages.length) child.stdin.end();
    });
    child.once("error", reject);
    child.once("close", (code) => {
      lines.close();
      if (code !== 0) reject(new Error(`MCP process exited ${code}`));
      else resolvePromise(replies);
    });
    for (const message of messages) child.stdin.write(`${JSON.stringify(message)}\n`);
  });
}

test("MCP server exposes the approved engine and display tool surface", async () => {
  const replies = await requestMcp([
    { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
    { jsonrpc: "2.0", id: 2, method: "tools/list", params: {} },
  ]);
  assert.equal(replies[0].result.serverInfo.name, "chain-neuron-engine");
  assert.deepEqual(
    replies[1].result.tools.map((tool) => tool.name),
    [
      "engine_healthz",
      "engine_list_datasets",
      "engine_dataset_contract",
      "engine_query",
      "engine_records_query",
      "agent_read_attachment",
      "agent_publish_chart",
    ],
  );
  assert.ok(replies[1].result.tools.every((tool) => !/(delete|clear|write|grow|import)/i.test(tool.name)));
});

test("MCP chart tool validates and records a structured line chart", async () => {
  const temp = await mkdtemp(join(tmpdir(), "chain-chart-"));
  const artifactLog = join(temp, "artifacts.jsonl");
  try {
    const chart = {
      version: 1,
      type: "line",
      title: "2026年业务趋势",
      x_axis: { name: "月份", categories: ["1月", "2月", "3月"] },
      y_axis: { name: "业务金额", unit: "万元" },
      series: [
        { name: "业务金额", data: [180.31, 844.88, 39.68] },
        { name: "3个月均线", data: [null, null, 354.96] },
      ],
      source: { datasets: ["non_oil_sales"], metrics: ["sale_amount"], period: "2026" },
    };
    const replies = await requestMcp([
      { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "agent_publish_chart", arguments: chart } },
    ], {
      CHAIN_AGENT_RUN_ID: "run-chart-test",
      CHAIN_AGENT_ARTIFACT_LOG: artifactLog,
    });
    assert.equal(replies[0].result.isError, false);
    assert.equal(replies[0].result.structuredContent.accepted, true);
    const entry = JSON.parse((await readFile(artifactLog, "utf8")).trim());
    assert.equal(entry.run_id, "run-chart-test");
    assert.equal(entry.visualization.title, chart.title);
    assert.deepEqual(entry.visualization.series[1].data, [null, null, 354.96]);
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test("MCP chart tool rejects mismatched series lengths without writing an artifact", async () => {
  const temp = await mkdtemp(join(tmpdir(), "chain-chart-invalid-"));
  const artifactLog = join(temp, "artifacts.jsonl");
  try {
    const replies = await requestMcp([
      {
        jsonrpc: "2.0",
        id: 1,
        method: "tools/call",
        params: {
          name: "agent_publish_chart",
          arguments: {
            version: 1,
            type: "line",
            title: "错误图表",
            x_axis: { categories: ["1月", "2月"] },
            y_axis: { unit: "万元" },
            series: [{ name: "业务金额", data: [1] }],
            source: { datasets: ["sales"], metrics: ["amount"] },
          },
        },
      },
    ], {
      CHAIN_AGENT_RUN_ID: "run-invalid-chart",
      CHAIN_AGENT_ARTIFACT_LOG: artifactLog,
    });
    assert.equal(replies[0].result.isError, true);
    assert.equal(replies[0].result.structuredContent.code, "INVALID_CHART_ARTIFACT");
    await assert.rejects(readFile(artifactLog, "utf8"), (error) => error.code === "ENOENT");
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});

test("MCP attachment tool reads only attachments authorized for the current session", async () => {
  const temp = await mkdtemp(join(tmpdir(), "chain-mcp-attachment-"));
  try {
    const workbook = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.aoa_to_sheet([["月份", "金额"], ["1月", 10]]), "汇总");
    const store = new AttachmentStore(temp);
    const uploaded = await store.upload("session-mcp", {
      name: "汇总.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      data: Buffer.from(XLSX.write(workbook, { type: "buffer", bookType: "xlsx" })),
    });
    const request = {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: {
        name: "agent_read_attachment",
        arguments: { attachment_id: uploaded.attachment.id, mode: "sheets" },
      },
    };
    const allowed = await requestMcp([request], {
      CHAIN_AGENT_ATTACHMENT_ROOT: temp,
      CHAIN_AGENT_ALLOWED_ATTACHMENTS: JSON.stringify([uploaded.attachment.id]),
    });
    assert.equal(allowed[0].result.isError, false);
    assert.equal(allowed[0].result.structuredContent.sheets[0].name, "汇总");

    const denied = await requestMcp([request], {
      CHAIN_AGENT_ATTACHMENT_ROOT: temp,
      CHAIN_AGENT_ALLOWED_ATTACHMENTS: "[]",
    });
    assert.equal(denied[0].result.isError, true);
    assert.equal(denied[0].result.structuredContent.code, "ATTACHMENT_ACCESS_DENIED");
  } finally {
    await rm(temp, { recursive: true, force: true });
  }
});
