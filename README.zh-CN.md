# tiered-agent-orchestrator

[English](README.md) | [简体中文](README.zh-CN.md)

**先正确完成，再降低执行、协调、等待与返工的总成本。**

项目状态：**v0.7.0 · Apache-2.0 · Benchmark pending**

TAO 通过 `$tao` 调用。Lead 根据总成本选择直接执行或复用经济模型 Worker；工程状态承接任务，无需复制上一段聊天。简单任务直接完成，不为了流程建立角色。

## v0.7.0 更新

- Lead 可以实现、诊断、测试、集成和安全接管已停止的工作。`control-worker` 保留范围与已有证据，同时更新 revision；`reassign-worker` 支持复用已确认停止的任务。
- 修复已完成任务的历史依赖阻碍角色复用的问题。区分审查结束与批准，`cancel-review` 允许回到执行修复，同时保留审查要求。
- `prepare-message`、`record-message` 支持 Lead 派发和 Worker／Reviewer 回报，各角色维护持久化发送回执，处理重复、过期和不确定投递。
- 独立对话支持显式模型与推理设置时可自动创建；缺少实际回执标为路由未验证，明确冲突则纠正。禁用自动子代理；请求参数不能证明正确计费。
- 按里程碑，或距上次检查超过 7 天后的实质续接，整理已登记产物。可再生临时产物先隔离 7 天；证据和未知文件保留。不运行后台定时器。
- Lead 上下文分页展示活跃工作，不展开历史 Worker 的全部验证摘要。已解决反馈退出活跃收件目录，仍可通过事件 ID 查找。

## 安装与使用

通过 Codex 的 skill installer 安装仓库 `jaywavfeng/tiered-agent-orchestrator`，仓库路径 `.`，安装名 `tiered-agent-orchestrator`，调用名仍为 `tao`。仅依赖 Python 3.9+ 标准库；GitHub 发布不会自动替换已有全局安装。

```text
$tao 完成这个项目，按总成本选择 Lead 直接执行或复用独立 Worker。自动发送已选择对话之间的任务和结果通知。需要新建且宿主支持时，在当前项目目录创建 gpt-5.6-luna、xhigh 的独立对话；否则一次性告诉我如何创建。不使用自动子代理。Lead 保持 gpt-5.6-sol，优先使用 gpt-5.6-terra 处理能力升级。
```

已有授权持续有效。用户选择的对话保留其设置；看不到模型指示或人工选择了不同 reasoning 时，不进入反复证明循环。遵守实际宿主权限与能力；绑定最终对话 ID 和同一个解析后的项目目录，不把另一个 worktree 当成相同运行状态。

| 请求 | 行为 |
|---|---|
| `$tao continue lead` | 恢复当前目标、证据、阻塞与下一步 |
| `$tao continue worker-1` | 继续已有范围明确的分派 |
| `$tao continue reviewer-1` | 继续当前审查 |
| `$tao status` | 只读查询，不触发整理或修改 |

维护 TAO 或发现 `.tiered-agent` 都不会隐式启动编排。

## CLI 与工程状态

在本仓库中：

```console
python scripts/statectl.py --help
python scripts/statectl.py init --project-root /path/to/project --project-id my-project
python scripts/statectl.py context --project-root /path/to/project --role lead
python scripts/statectl.py control-worker --help
python scripts/statectl.py prepare-message --help
python scripts/statectl.py housekeep --project-root /path/to/project
python scripts/statectl.py validate --project-root /path/to/project
```

Windows 全局安装调用：

```powershell
& "<python.exe 的绝对路径>" "<技能目录的绝对路径>\scripts\statectl.py" context --project-root "<工程目录>" --role lead
```

替换占位符并正确引用路径。通过解释器运行，不启动裸 `.py` 文件，不以打开 VS Code 代替执行。参数不清楚时使用子命令 `--help`。

Lead 维护全局方向、分派和 `TRANSPORT.json`；当前执行者维护任务结果；发送者维护自己的 `DELIVERY.json`。`HANDOFF.md` 是简短接续包，`OWNER_STATUS.md` 是可选人类报告。`reassign-worker` 保留真实旧状态；`reopen-project` 在实质新工作前保留完成快照。旧 schema-v1 工程无需批量迁移。新控制流程和审查写入要求 revision；旧审查必须核对报告并补记批准结论，才能用于新的完成决策。

详见 [状态契约](references/runtime-state.md)、[双向通信](references/host-dispatch.md)、[单 Worker 流程](examples/one-worker-flow.md)。

## 项目空间整理

用 `workspace-register` 按任务／运行目录登记已有产物。临时产物需要再生成方法；中间产物搬移需要明确分类授权。释放前确认生产者、后台进程及消费者均已停止，且没有必须保持原路径的外部引用。工具进一步检查当前引用、写范围、Git 跟踪内容和不安全路径；不会声称能够自动发现所有外部依赖。

`housekeep` 默认只预览；`housekeep --apply` 执行已授权操作；`--if-due` 在 7 天内跳过。`workspace-restore` 恢复已保存产物，禁止覆盖当前文件。源码、原始输入、证据、交付物和未知目录保留；不全项目定期通读、不按年龄删除证据、不清理无关的全局备份。最终整理在项目完成冻结之前进行。

完整规则见 [项目空间整理](references/workspace-housekeeping.md)。

## 测量与验证

目标为 **协调 token／实际任务 token ≤ 10%**。Lead 的实际执行与验收属于任务工作。缺少用途归因时标记未测量，不等于零开销。`purpose_usage` 与统计口径见 [测量规则](benchmarks/README.md)。不建立每日台账，不把文本篇幅或工具调用次数说成真实 token／credit 节省。

```console
python -m unittest discover -s tests -v
python scripts/benchmark.py --help
```

CI 覆盖 Windows／Ubuntu × Python 3.9／3.13。测试使用隔离工程和模拟宿主回执，不证明真实桌面对话通信或计费。发布验证见 [v0.7.0 验证说明](benchmarks/v0.7.0-validation.md)。评估场景定义不等于已执行独立 Agent 测试。发布、部署和无关全局修改仍需用户授权。
