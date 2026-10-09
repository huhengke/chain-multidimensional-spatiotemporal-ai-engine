# Compatibility Agent Implementations

> 本目录只保留兼容实现，现行入口是顶级 `Agent/`。当前版本和迁移边界见 [验收、迁移与演进](../../../docs/验收、迁移与演进.md)。

This directory is retained only for historical regression tests and migration comparison.

It is not the production Agent entry point.

- Current Agent Gateway: `../../../Agent/src/server.js`
- Current MCP Server: `../../../Agent/src/mcp-server.js`
- Current Harness Patch: `../../../Agent/presets/chain-neuron-headless.yml`

The fixed Python orchestrator and the project-made Node orchestrator below must not be used for new features. They remain temporarily because the engine regression suite imports their deterministic semantic helpers. Production startup scripts redirect to the top-level `Agent/` project.
