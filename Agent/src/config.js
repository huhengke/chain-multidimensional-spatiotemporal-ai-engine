import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const agentRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const workspaceRoot = resolve(agentRoot, "..");
const defaultHarnessHome = "D:\\deepseek_Harness";
const harnessHome = process.env.DSH_INSTALL_ROOT || defaultHarnessHome;

export const config = Object.freeze({
  agentRoot,
  workspaceRoot,
  port: Number(process.env.AGENT_PORT || 8001),
  bindHost: process.env.AGENT_BIND_HOST || "127.0.0.1",
  engineBaseUrl: (process.env.ENGINE_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, ""),
  serviceToken: process.env.ENGINE_SERVICE_TOKEN || "dev-service-token-please-rotate",
  harnessExecutable:
    process.env.DSH_EXECUTABLE || join(harnessHome, "DeepSeek Harness.exe"),
  harnessCliEntry:
    process.env.DSH_CLI_ENTRY ||
    join(
      harnessHome,
      "resources",
      "app.asar",
      "dsh",
      "node_modules",
      "@deepseek-ai",
      "dsh-desktop-host",
      "lib",
      "cli.js",
    ),
  harnessPatch:
    process.env.DSH_AGENT_PATCH || join(agentRoot, "presets", "chain-neuron-headless.yml"),
  harnessRuntimeRoot: join(harnessHome, "resources", "app.asar", "dsh"),
  harnessRunner: join(agentRoot, "src", "multimodal-runner.js"),
  runTimeoutMs: Number(process.env.AGENT_RUN_TIMEOUT_MS || 180000),
  maxRequestBytes: Number(process.env.AGENT_MAX_REQUEST_BYTES || 65536),
  sessionIndexPath: process.env.AGENT_SESSION_INDEX || join(agentRoot, "data", "sessions.json"),
  queryLogPath: process.env.AGENT_QUERY_LOG || join(agentRoot, "data", "engine-query-log.jsonl"),
  artifactLogPath:
    process.env.AGENT_ARTIFACT_LOG || join(agentRoot, "data", "agent-artifact-log.jsonl"),
  attachmentRoot:
    process.env.AGENT_ATTACHMENT_ROOT || join(agentRoot, "data", "attachments", "v1"),
  skillRoot: process.env.AGENT_SKILL_ROOT || join(agentRoot, "skills"),
  disabledSkillRoot:
    process.env.AGENT_DISABLED_SKILL_ROOT || join(agentRoot, "data", "skills-disabled"),
  skillArchiveRoot:
    process.env.AGENT_SKILL_ARCHIVE_ROOT || join(agentRoot, "data", "skill-archive"),
  skillStagingRoot:
    process.env.AGENT_SKILL_STAGING_ROOT || join(agentRoot, "data", "skill-staging"),
  skillRuntimeRoot:
    process.env.AGENT_SKILL_RUNTIME_ROOT || join(agentRoot, "data", "runtime", "skills"),
});

export function harnessEnvironment(overrides = {}) {
  return {
    ...process.env,
    ...overrides,
    ELECTRON_RUN_AS_NODE: "1",
    CHAIN_AGENT_ROOT: config.agentRoot,
    CHAIN_WORKSPACE_ROOT: config.workspaceRoot,
    ENGINE_BASE_URL: config.engineBaseUrl,
    ENGINE_SERVICE_TOKEN: config.serviceToken,
    CHAIN_AGENT_QUERY_LOG: config.queryLogPath,
    CHAIN_AGENT_ARTIFACT_LOG: config.artifactLogPath,
    CHAIN_AGENT_ATTACHMENT_ROOT: config.attachmentRoot,
    CHAIN_AGENT_SKILL_ROOT: config.skillRoot,
    CHAIN_AGENT_RUNNER_URL: pathToFileURL(config.harnessRunner).href,
    DSH_RUNTIME_ROOT: config.harnessRuntimeRoot,
    DSH_PERMISSION_MODE: "read-only",
  };
}
