# Codex Chat Strategies

在 **Codex 聊天输入框**里用技能切换策略，保留当前会话。无需用不同终端命令启动 Codex。

| Codex 输入框入口 | 行为 |
| --- | --- |
| `$strategy-openai [任务]` | 当前主模型规划、验收；适合时最多三个原生子代理并行，日常工作优先 Luna。 |
| `$strategy-deepseek [任务]` | 当前主模型规划、验收；适合时最多五个 DeepSeek 任务在不同仓库或模块目录中并行，可选 Git worktree 隔离。 |
| `$strategy-solo [任务]` | 当前模型直接执行，不委派；用内置 `/model` 自己选模型。 |

不附带任务时只切换策略；附带任务时直接执行。策略持续到当前会话下一次显式切换，不修改其他会话。

也可输入内置命令 `/skills`，选择 **List skills**，再选择 **OpenAI 自动分工**、**DeepSeek 协作**或**单模型直接执行**。选择后会插入技能，再按 Enter 发送。
这是 Codex 自带的技能调用入口。`$strategy-*` 是在 Codex 输入框中输入，不是在终端运行。

**不要把 `/prompts:*` 当作默认入口。**本机 Codex CLI `0.161.0`（后台服务 `0.162.1`）实测对 `/prompts:openai` 返回 `Unrecognized command`；`/skills` 和 `$strategy-*` 可正常找到三个技能。
官方也已将 custom prompts 标记为弃用并推荐技能。安装脚本仍写入旧 prompt 文件以兼容旧客户端，但文件存在不代表 CLI 支持该命令。项目不修改 Codex 二进制。

## 部署

面向 Linux/macOS。需要当前 Codex CLI 和 Python 3.11+；完整安装另需 Git 和 [uv](https://docs.astral.sh/uv/)。Windows 可在 WSL 使用。

```bash
git clone https://github.com/InubashiriLix/codex-chat-strategies.git
cd codex-chat-strategies
./deploy.sh
```

脚本会安装三个显式触发技能、旧客户端兼容 prompt 文件和诊断脚本，安装固定版本的 DeepSeek MCP 执行器，并只新增 `mcp_servers.strategy_deepseek` 配置。已有账号、模型、权限等配置保留；修改过的文件先备份。不会调用付费模型。

已有本项目安装时可重复运行；已存在的密钥、模型设置和 MCP 自定义参数保留。
如果已有同名 MCP 指向别的安装，脚本会报错而不覆盖。

只安装 OpenAI 和单模型策略（不下载依赖、不更改 MCP）：

```bash
./deploy.sh --openai-only
```

安装后输入 `/skills` 或 `$strategy-` 检查入口；看不到时退出安装前打开的 Codex 会话，再启动新会话。之后可在同一会话切换：

```text
$strategy-openai 为这个模块增加缓存
$strategy-deepseek 按现有接口补全 CRUD
$strategy-solo 修复这个拼写错误
/model
```

如果看到 `Unrecognized command '/prompts:deepseek'`，改用 `$strategy-deepseek`：输入 `$strategy` 后从列表选择「DeepSeek 协作」。`/prompt` 不是本项目命令。若新会话的 `$` 技能列表仍没有这些入口，检查 `CODEX_HOME` 是否指向安装时使用的目录，或用 `./deploy.sh --codex-home /实际目录` 重新安装。

策略不能自动切换主模型。请用 `/model` 选定规划审查用的强模型，或者小任务使用的轻量模型。
子代理模型受账号可用性限制；默认 Luna，复杂任务可用 Sol。模型不可用会报告，不静默换供应商。

## 并行与用量

并发数是上限，不是每次任务的目标。小任务直接完成；只有工作可独立推进、节省的时间足以抵消额外模型用量和整合成本时才并行。相关返工优先交给原来的 Luna 子代理，复用它已掌握的上下文；不再相关的任务才新建代理。委派只传必要路径、约束和验收条件，避免重复发送长日志与完整会话。

DeepSeek 默认直接在指定任务目录中读写。不同仓库、互不重叠的模块目录可以并行；无需共同 Git 基线、干净工作区、仓库根目录或先创建 worktree。已有未提交改动和非 Git 目录均可使用。最多五路；同一或父子目录的任务依次执行，主代理审查实际修改并完成整体验收。DeepSeek API 调用独立计费；主代理的调度与验收仍会消耗 Codex Pro 周额度。

例如输入 `$strategy-deepseek 在 /projects/frontend 和 /projects/backend 并行实现登录功能`。Codex 可直接调用：

```text
start_deepseek_parallel(task="实现登录界面", workspace="/projects/frontend")
start_deepseek_parallel(task="实现登录接口", workspace="/projects/backend")
```

`batch_id` 和 `lane` 自动分配；启动返回实际目录和 job ID。目录可根据已知项目结构推断，无需用户先整理成一个工作区。默认 MCP 目录与目标仓库不同也可显式指定 `workspace`。每个槽位在批次内绑定固定目录；收取结果并审查恢复记录后可在同目录复用。关闭直接模式批次只释放槽位，不删除文件，也不要求提交修改。

需要同仓库隔离并行时，显式调用 `prepare_deepseek_parallel(mode="worktree")` 使用原来的五路 Git worktree 模式；仅此模式要求干净的 Git 根目录与已提交 HEAD。准备操作不调用模型；每路改动需要审查整合，工作区干净且恢复记录已确认后才可关闭删除 worktree。直接模式不需要准备步骤，也可用 `prepare_deepseek_parallel(workspaces=["/projects/frontend", "/projects/backend"])` 提前绑定目录。

DeepSeek 后台任务失败时，本地适配层按 job ID 保存不含密钥和原始任务内容的错误分类。`get_deepseek_diagnostic` 可在另一个 Codex 会话中读取该分类；`check_deepseek_balance` 只检查官方 API 余额是否可用，不发起模型请求。单任务 token 预算、余额不足（HTTP 402）、鉴权失败（401）和限速（429）会分别报告。

新任务还会保存私有、限长的原任务检查点（仅在本机，文件权限 0600）。预算或轮次限制导致失败时，`get_deepseek_resume_packet(job_id)` 汇总原目标、工作区改动与待审查事务；Codex 检查已完成部分后，用 `start_deepseek_resume(job_id, remaining_task, state_token)` 启动一次精简续做。`state_token` 来自最新恢复包；工作区状态变化后需重新检查。每条任务链最多自动恢复一次；工作区身份不符、仍有待确认事务、任务仍在运行或错误属于余额/鉴权/网络等情况均阻止恢复。`/compact` 只整理 Codex 主会话，不能续接已结束的 DeepSeek 会话。安装前发生的旧任务没有检查点，需人工结合文件重建剩余工作。

## DeepSeek 密钥

完成部署后，在本地终端执行一次隐藏输入设置（不要把 key 发到聊天或写进仓库）：

```bash
python3 scripts/setup-key.py
```

也可以通过环境变量 `DEEPSEEK_API_KEY` 提供。密钥文件为 `~/.deepseek-mcp/config.json`，权限 `0600`。DeepSeek API 独立计费，首次配置默认 Flash、20 轮、15 分钟。

MCP 在 Codex 启动时加载，但明确要求只有用户选择 DeepSeek 策略时才委派。单模型策略是行为指令，不是进程级禁用工具。DeepSeek 执行器有自己的文件和命令控制，不直接继承 Codex 沙箱；委派前会核对工作目录，主模型必须传递适用的项目约束。

## 同步与路径

```bash
git pull --ff-only
./deploy.sh
```

- 旧客户端兼容 prompt：`${CODEX_HOME:-~/.codex}/prompts/`
- 技能：`${CODEX_HOME:-~/.codex}/skills/strategy-*/`
- 执行器和本地备份：`~/.local/share/codex-strategies/`
- 密钥只保存在本机，不随 Git 同步。

可用 `--codex-home /path/to/codex` 和 `--data-dir /path/to/runtime` 自定义目标。执行器目录不能放在本仓库内；也不要把它放进未来交给 DeepSeek 修改的项目里。多账号使用不同 CODEX_HOME 时，对每个账号部署一次。

卸载时移除三个 prompts、三个 strategy 技能目录及 `mcp_servers.strategy_deepseek` 配置即可；运行时、备份和密钥自行保留或清理。

## 验证与来源

部署后在终端执行诊断，它启动独立 Codex app-server，通过原生 `skills/list` 检查技能是否被发现并启用，不发送模型请求，也不重启正在运行的服务：

```bash
python3 scripts/doctor.py
# 只验证 OpenAI 和单模型策略：
python3 scripts/doctor.py --openai-only
```

诊断支持 `--codex-home`、`--data-dir` 和 `--cwd`。完整诊断还检查 DeepSeek 运行时、MCP 注册和密钥是否存在；缺少密钥会返回非零退出码并明确显示 `NOT READY`，不影响 OpenAI/单模型使用。密钥存在也不代表 API 可访问；模型额度与实际委派需要真实任务验证。
部署后的诊断脚本也位于 `~/.local/share/codex-strategies/doctor.py`。

```bash
python3 -m unittest discover -s tests -v
```

检查覆盖离线部署、重复部署、备份和保留原有配置。不会调用模型，也不代表实际模型能力或额度已验证。

- [Codex 自定义提示词](https://learn.chatgpt.com/docs/custom-prompts)
- [Codex 技能](https://learn.chatgpt.com/docs/build-skills)
- [Codex 子代理](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- DeepSeek 执行器：[PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent)，MIT，固定提交 `7e54026392f85ccfc796f947faf9ffac3968a49e`。部署时下载，源码及账号配置不纳入本仓库。
