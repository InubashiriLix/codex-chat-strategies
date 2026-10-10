# Codex Chat Strategies

在 **Codex 聊天输入框**里切换策略，保留当前会话。无需用不同终端命令启动 Codex。

| 会话内命令 | 行为 |
| --- | --- |
| `/prompts:openai [任务]` | 当前主模型规划、验收，Luna 实现，复杂任务按需委派 Sol。 |
| `/prompts:deepseek [任务]` | 当前主模型规划、验收，DeepSeek 通过 MCP 独立读写代码。 |
| `/prompts:solo [任务]` | 当前模型直接执行，不委派；用内置 `/model` 自己选模型。 |

不附带任务时只切换策略；附带任务时直接执行。策略持续到当前会话下一次显式切换，不修改其他会话。

也可用 `$strategy-openai`、`$strategy-deepseek`、`$strategy-solo` 调用对应技能。
Codex 已将 custom prompts 标记为弃用，仍提供 `/prompts:名称`；技能是长期兼容入口。
这些是可安装的自定义指令，不是修改 Codex 二进制增加的内置指令。

## 部署

面向 Linux/macOS。需要当前 Codex CLI 和 Python 3.11+；完整安装另需 Git 和 [uv](https://docs.astral.sh/uv/)。Windows 可在 WSL 使用。

```bash
git clone https://github.com/InubashiriLix/codex-chat-strategies.git
cd codex-chat-strategies
./deploy.sh
```

脚本会安装三个自定义命令和显式触发技能，安装固定版本的 DeepSeek MCP 执行器，并只新增 `mcp_servers.strategy_deepseek` 配置。已有账号、模型、权限等配置保留；修改过的文件先备份。不会调用付费模型。

已有本项目安装时可重复运行；已存在的密钥、模型设置和 MCP 自定义参数保留。
如果已有同名 MCP 指向别的安装，脚本会报错而不覆盖。

只安装 OpenAI 和单模型策略（不下载依赖、不更改 MCP）：

```bash
./deploy.sh --openai-only
```

**首次安装后重启 Codex 一次加载入口。**之后直接在同一会话切换：

```text
/prompts:openai 为这个模块增加缓存
/prompts:deepseek 按现有接口补全 CRUD
/prompts:solo 修复这个拼写错误
/model
```

策略不能自动切换主模型。请用 `/model` 选定规划审查用的强模型，或者小任务使用的轻量模型。
子代理模型受账号可用性限制；默认 Luna，复杂任务可用 Sol。模型不可用会报告，不静默换供应商。

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

- 命令：`${CODEX_HOME:-~/.codex}/prompts/`
- 技能：`${CODEX_HOME:-~/.codex}/skills/strategy-*/`
- 执行器和本地备份：`~/.local/share/codex-strategies/`
- 密钥只保存在本机，不随 Git 同步。

可用 `--codex-home /path/to/codex` 和 `--data-dir /path/to/runtime` 自定义目标。执行器目录不能放在本仓库内；也不要把它放进未来交给 DeepSeek 修改的项目里。多账号使用不同 CODEX_HOME 时，对每个账号部署一次。

卸载时移除三个 prompts、三个 strategy 技能目录及 `mcp_servers.strategy_deepseek` 配置即可；运行时、备份和密钥自行保留或清理。

## 验证与来源

```bash
python3 -m unittest discover -s tests -v
```

检查覆盖离线部署、重复部署、备份和保留原有配置。不会调用模型，也不代表实际模型能力或额度已验证。

- [Codex 自定义提示词](https://learn.chatgpt.com/docs/custom-prompts)
- [Codex 技能](https://learn.chatgpt.com/docs/build-skills)
- [Codex 子代理](https://learn.chatgpt.com/docs/agent-configuration/subagents)
- DeepSeek 执行器：[PsChina/deepseek-as-subagent](https://github.com/PsChina/deepseek-as-subagent)，MIT，固定提交 `7e54026392f85ccfc796f947faf9ffac3968a49e`。部署时下载，源码及账号配置不纳入本仓库。
