import { spawn } from "node:child_process";

import { config, harnessEnvironment } from "./config.js";
import { inspectHarnessRuntime } from "./harness-runtime.js";

const state = await inspectHarnessRuntime();
if (!state.available) {
  console.error(JSON.stringify(state, null, 2));
  process.exitCode = 1;
} else {
  const args = [
    "--expose-internals",
    config.harnessCliEntry,
    "--profile",
    "headless",
    "--patch",
    config.harnessPatch,
    "--dump-config",
  ];
  const child = spawn(config.harnessExecutable, args, {
    cwd: config.workspaceRoot,
    env: harnessEnvironment(),
    windowsHide: true,
    stdio: "inherit",
  });
  process.exitCode = await new Promise((resolve, reject) => {
    child.once("error", reject);
    child.once("close", resolve);
  });
}
