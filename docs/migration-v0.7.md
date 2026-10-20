# v0.7 安装与迁移说明

## 重要：不要运行旧更新器

从 **v0.7 以前版本**升级时，禁止运行旧版本仓库中的 `--update` / `-Update`，也禁止使用旧版本的 `--force` / `-Force`。旧更新器会直接重建 `runtime/`，可能删除其中的会话和任务状态。请先切换到 v0.7 源码，再运行本文命令。`--force` 不能把旧更新器变安全。

## 升级命令

```bash
# Linux / macOS
./install.sh --agent pi --project /absolute/product/root --update
# pi 与 Codex 同时接入（共享 AGENTS.md）
./install.sh --agent all --project /absolute/product/root --update
```

```powershell
# Windows
.\install.ps1 -Agent pi -Project "D:\product\root" -Update
.\install.ps1 -Agent all -Project "D:\product\root" -Update
```

项目目录必须已存在；更新项目还必须有 `.mcu-agent/VERSION`。缺少项目、未知 agent、迁移异常或 smoke test 失败都会返回非零，且 smoke test 成功前不会写入新 `VERSION`。

## 数据边界

v0.7 将以下路径视为安装器管理内容：首次安装以及显式 `--update` / `--force`（PowerShell 对应开关）可替换这些路径；普通重复安装只修复统一入口，不重写 runtime：

- `runtime/`, `tools/`, `workflows/`, `templates/`
- `config/`, `registry/`, `schemas/`, `analysis/`
- `knowledge/` 中除 `knowledge/project/` 以外的内容
- `agent-entry.md`, `VERSION`

以下内容属于用户，`--force` / `-Force` 也不会删除或重建：

- `state/`, `custom/`, `config.yaml`
- `artifact-manifest.yaml`
- `knowledge/project/`
- `backups/` 和未被列为 managed 的用户目录

显式 update 或 force 刷新前，安装器把用户状态与配置复制到 `.mcu-agent/backups/<UTC timestamp>/`；普通幂等重装不产生无意义备份。

## 旧 runtime 状态迁移

旧版把状态写在 `.mcu-agent/runtime/`。v0.7 在替换 runtime 前将下列内容迁移到 `.mcu-agent/state/`：

- `intents/`, `work-packages/`, `claims/`, `attestations/`, `sessions/`, `memories/`
- `capabilities.json`, `phase-state.json`, `evidence-debts.json`
- `engineering-assumptions.json`, `execution-ledger.json`
- 早期预览版使用的无扩展名 **文件** `phase-state` / `evidence-debts`、`assumptions` / `ledger` 会映射到上述 canonical 文件名

同一路径冲突时，JSON/YAML 顶层 `revision`（或 `metadata.revision`）更高的文件获胜；相同或缺失 revision 时保留现有 state 文件。若旧路径是目录而 canonical 目标是单一 JSON 文件，安装器不会制造“名为 `.json` 的目录”，而会保存在 `.mcu-agent/state/legacy/<旧名称>/` 并写入报告，供人工归并。报告写入 `.mcu-agent/state/migration-reports/<UTC timestamp>.json`，刷新备份还包含旧 runtime 状态副本。

## AGENTS.md

安装器不会覆盖整个项目 `AGENTS.md`。它只新增或刷新以下幂等区块，区块外的团队规范会保留：

```markdown
<!-- BEGIN UNIVERSAL MCU AGENT -->
Read and follow `.mcu-agent/agent-entry.md` for all Universal MCU Agent work.
<!-- END UNIVERSAL MCU AGENT -->
```

pi 和 Codex 读取相同区块及同一份 `.mcu-agent/agent-entry.md`，不再维护分叉入口。

## 更新后检查

安装器会先执行 `python .mcu-agent/runtime/cli.py --help` smoke test，再写 `.mcu-agent/VERSION`。升级完成后确认：

1. `.mcu-agent/VERSION` 和 `.mcu-agent/knowledge/VERSION` 都为 `0.7.0`；
2. 平台知识位于 `.mcu-agent/knowledge/platform/<platform>/`；
3. 原有 `state/`、`custom/`、`config.yaml`、artifact manifest 与项目知识仍存在；
4. `AGENTS.md` 只有一个 managed block。
