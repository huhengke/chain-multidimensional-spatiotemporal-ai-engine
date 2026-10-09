# Third-Party License Boundary

## Scope of the root license

The root [Apache License 2.0](LICENSE) applies only to project-owned source
code and documentation for which `huhengke` is the copyright holder. It does
not replace, amend, or relicense third-party software, services, data, models,
or materials included by reference.

If a file contains its own copyright notice, license header, or adjacent
license file, that notice remains effective for the applicable material.

## Dependency manifests

The repository does not treat dependency declarations as project-owned source.
Dependencies are resolved by the following package manifests and lock files:

| Runtime | Manifest and lock locations |
| --- | --- |
| Python | `AI数据引擎/chain-neuron-engine/requirements*.txt`, `自研AI数据引擎/pyproject.toml` |
| Node.js | `Agent/package.json`, `Agent/package-lock.json`, `AI数据引擎/chain-neuron-engine/frontend/package.json`, `AI数据引擎/chain-neuron-engine/frontend/package-lock.json`, `AI数据引擎/chain-neuron-engine/agent/harness/package.json`, `演示动画/remotion/package.json`, `演示动画/remotion/package-lock.json` |
| Rust | `自研AI数据引擎/Cargo.toml`, `自研AI数据引擎/Cargo.lock`, and `自研AI数据引擎/crates/*/Cargo.toml` |

Package manager installations, build caches, generated binaries and runtime
data are excluded from this release repository. Their licenses remain those of
their upstream authors.

## Redistribution checklist

Before distributing a binary, container image, or hosted product built from
this repository:

1. Resolve dependencies from the committed manifests and lock files.
2. Generate an environment-specific software bill of materials and dependency
   license inventory.
3. Preserve all upstream notices required by the resolved dependencies.
4. Confirm the provenance and license of any newly copied code, model, dataset,
   template, or media asset before including it in a release.

This notice records the repository boundary; it is not an exhaustive audit of
all runtime dependencies or a substitute for legal review of a specific
distribution.
