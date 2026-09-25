# agent-project-manager

[English](README.md) | [简体中文](README.zh-CN.md)

**先正确完成任务，再让执行、协调、等待与返工保持低成本。**

状态：**v1.1.0 · Apache-2.0 · Benchmark pending**

以 `$apm` 调用的 Codex 技能。默认由单个 agent 完成任务；多 agent 是一项可选能力，而非必需品。全部状态存放在仓库中，任何 agent 都能接手项目——交接信息不依赖聊天记录。

> **管理者的开销，必须小于它替你省下的工作量。**

## 为什么需要它

1. **项目记忆比 agent 更长寿。** 用户的长期要求、决策、约束、经验与已否决方向，以压缩条目存放在 `.agent-project-manager/memory.jsonl`——只记结论，不记对话。换模型，项目不会失忆。
2. **只读当前任务真正需要的内容。** 上下文编译器（`context --role <角色> --task "<当前任务>"`）返回最小必要上下文，而不是整个项目。协调开销应低于任务 token 的 **10%**。
3. **委派需要显式开启。** 默认 `standalone` 模式完全不创建 Worker。只有当某块工作确实独立、且收益大于开销时，才用 `set-project --mode leader` 切换——判断依据见[委派策略](references/delegation-policy.md)。
4. **干净且可恢复的工作空间。** 持久状态文件、原子写入、崩溃恢复，以及有边界的整理机制：隔离可再生的临时产物，同时完整保留证据与交付物。

## standalone 项目只有三个文件

`init` 只创建以下内容，别的一概不建：

```text
.agent-project-manager/
├── STATE.json          # 机器权威状态：阶段、状态、下一步
├── memory.jsonl        # 持久记忆：长期要求、决策、约束、经验
└── PROJECT_STATUS.md   # 面向人的页面，由上面两者渲染生成
```

其余文件都在「真正需要它的那条命令」首次运行时才创建——做计划时才有 `PLAN.md`，开启委派时才有 `workers/`，分配审查时才有 `review/`。没有 `HANDOFF.md`：冷启动时按需从状态、记忆与计划现场渲染交接简报，因此不存在需要人工同步、还会过期的摘要。

面向人阅读的 Markdown 放在**项目根目录**（`README.md`、`README.zh-CN.md`）；项目记忆放在 `.agent-project-manager/`，且只使用单一语言——它是给机器看的。

## 安装与使用

用 Codex 的 skill installer 安装仓库 `jaywavfeng/agent-project-manager`，仓库路径 `.`，安装名 `agent-project-manager`。调用名为 `$apm`。仅依赖 Python 3.9+ 标准库；GitHub 发布不会自动替换已有的全局安装。

```text
$apm 完成这个项目。除非复用独立 Worker 的总成本确实更低，否则用 standalone 直接执行。记录我的长期要求，避免后续 agent 丢失。在宣布项目完成前把工作空间整理干净。
```

`$apm` 仅在明确请求时激活。维护本技能、粘贴上面的示例，或发现已存在的 `.agent-project-manager` 目录，都不会自行启动它。

| 请求 | 行为 |
|---|---|
| `$apm <任务>` | 开始或继续一个项目 |
| `$apm continue lead` | 恢复当前目标、证据、阻塞与下一步 |
| `$apm continue worker-1` | 继续已有范围明确的分派 |
| `$apm continue reviewer-1` | 继续当前审查 |
| `$apm status` | 读取 `PROJECT_STATUS.md` 与状态，不触发整理或其他修改 |

在终端中通过已确认的 Python 解释器和本技能的绝对脚本路径调用状态助手：

```powershell
& "<python.exe 的绝对路径>" "<技能目录的绝对路径>\scripts\statectl.py" context --project-root "<工程目录>" --role lead --task "<当前任务>"
```

务必通过解释器调用。不要启动裸 `.py` 文件，也不要用打开 VS Code（或其他打开文件的工具）来代替执行状态操作。参数不清楚时使用子命令 `--help`，不要通读实现。

## 模式与角色

项目以 `standalone` 启动：单个 agent 读取上下文、完成工作、按验收标准验证、更新状态与记忆，然后结束。简单任务不创建任何角色。

`set-project --mode leader` 才开启协作。只有此时才存在 `worker` 与 `reviewer`：

- **lead** — 掌握方向、分派与 `TRANSPORT.json`；保留最终面向用户的写作、架构决策与验收。
- **worker** — 只对自己的目标、`allowed_scope` 和任务结果负责；仅通过 `--assignment-revision` 写入。
- **reviewer** — 同时记录审查结束与 `--verdict approved|changes-requested`，避免把"审查完成"当成"批准"。

已停止或阻塞的分派可用 `control-worker` 继续、取消或接管：它保留范围与已有部分结果，推进 revision 并隔离过期写入。`reassign-worker` 复用已确认停止的分派；`reopen-project` 在产生新的实质工作前保留完成快照。宿主消息先由 `prepare-message` 预留，宿主发送后再用 `record-message` 关闭，各角色维护自己的 `DELIVERY.json` 回执。详见[状态契约](references/runtime-state.md)、[委派与角色](references/delegation-and-roles.md)、[双向通信](references/host-dispatch.md)、[完整示例](examples/one-worker-flow.md)。

`PROJECT_STATUS.md` 是唯一面向人的页面：简洁、默认双语，并由状态渲染生成（`status-refresh`），而不是手工写日志。`human-intent` 与 `constraint` 两类记忆条目**永远不会**被 `memory-consolidate` 归档——不论保留窗口多小，它们都是 agent 不得悄悄丢弃的需求。

## 项目空间整理

用 `workspace-register` 按任务或运行登记产物目录。`housekeep` 默认只预览、不做任何修改；`housekeep --apply` 只执行已授权操作，`--if-due` 在 7 天内跳过检查。`workspace-restore` 恢复已释放产物，且不覆盖当前文件。

源码、原始输入、证据、交付物与未知目录始终保留；可再生临时产物进入 7 天隔离。不做全项目定期扫描、不按年龄删除证据、不运行后台定时器。完整规则与示例见[项目空间整理](references/workspace-housekeeping.md)。

## 测量与验证

目标：**协调 token／实际任务 token ≤ 10%**。Lead 的实际执行与验收计入任务工作。缺少用途归因即记为*未测量*，不等于零开销——可选字段 `purpose_usage` 与统计口径见[测量说明](benchmarks/README.md)。不建立每日台账；文本篇幅或工具调用次数只是代理指标，不等于已被证明的 token／credit 节省。

```console
python -m unittest discover -s tests -v
python scripts/benchmark.py --help
```

在本仓库中也可以直接驱动助手：

```console
python scripts/statectl.py init --project-root /path/to/project --project-id my-project
python scripts/statectl.py context --project-root /path/to/project --role lead --task "修复解析器"
python scripts/statectl.py memory-add --project-root /path/to/project --kind constraint --text "不引入新的运行时依赖"
python scripts/statectl.py validate --project-root /path/to/project
```

CI 覆盖 Windows／Ubuntu × Python 3.9／3.13。测试使用隔离工程与模拟宿主回执，不证明真实桌面对话通信或计费。发布验证见 [v1.0.0 验证说明](benchmarks/v1.0.0-validation.md) 与 [v1.1.0 验证说明](benchmarks/v1.1.0-validation.md)。评估场景定义不等于已执行独立 Agent 测试。发布、部署与无关的全局修改仍需用户授权。
