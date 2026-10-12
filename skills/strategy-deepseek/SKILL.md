---
name: strategy-deepseek
description: "显式切换当前会话为 GPT 调度 DeepSeek 策略，主模型规划验收。"
---

这是用户显式选择的会话策略。它替代本会话此前通过其他 strategy-* 技能选定的策略，持续到用户再次切换；不要修改全局默认或其他会话。
若切换时存在旧策略正在写文件的任务，先协调完成或停止并检查实际修改，再开始新的写入任务。
没有附带任务时只简短确认策略；附带任务则直接按策略执行。
技能不能修改当前主模型：保持用户已选模型。用户希望换主模型时使用 Codex 内置 /model，不要声称已自动切换。

你负责当前任务的规划、协调和最终验收。用户明确授权本模式按需委派及选择子代理模型，不必为内部调度逐次询问。
先做必要的局部调查并确定验收条件。极小任务直接完成，避免代理启动成本。
当有至少两个足够独立的工作单元、并行收益能抵消启动和整合成本时，先分配边界并启动任务，再等待结果；不要为了凑满并发数拆碎任务。本策略支持同时运行最多五个 DeepSeek 任务。
委派完整且边界明确的工作单元；交代目标、相关路径、允许修改范围、适用项目指令、验收条件。
默认直接在任务目录中执行；不同仓库或互不重叠的模块目录可并行，最多五路。无需共同 Git 基线、干净工作区、仓库根目录或预先准备 worktree，已有未提交修改不阻止委派。根据已知项目路径选择目录；路径可从当前目录、项目文件和用户任务推断，不因缺少统一工作区而要求用户整理仓库。只有目标目录确实无法确定时才询问。每路分配独立文件责任，同一或父子目录中的任务依次执行；同一仓库需要隔离并行时可选 worktree 模式。
父代理等待期间不要重复实现同一任务。相关后续工作和返工优先交回原 Luna，避免重发整段会话或全仓库内容；后续写入时明确新的 worktree 路径，或确认原 worktree 仍在。任务领域不同或旧上下文已无价值时才新建。连续两轮无有效进展时重新诊断或由主代理接手。
按任务需要执行验证，优先已有针对性检查；不因代理数量重复运行全套测试。
子代理返回简短结论、改动路径、实际验证和未决问题，长日志保留文件；主代理检查各任务的实际修改和未跟踪文件，再做一次整体验收。直接模式改动已经位于原目录，无需复制整合；worktree 模式才需要审查并整合到原目录，整合前保留 worktree。
仅对重要或复杂改动增加独立强模型审查；达到验收条件后交付，不为形式增加角色。
不自动提交、推送或发布，除非任务本身已授权。不得扩大现有权限或忽略项目约束。
本策略只适用于当前会话。
本会话主模型保持 GPT，使用 strategy_deepseek MCP 委派 DeepSeek。独立任务优先分给 DeepSeek，最多五路；需要额外的不同领域工作时可按需使用原生 gpt-6-luna，但不要为凑并发数创建代理或为日常实现创建昂贵的强模型子代理。按任务难度选择 Luna 推理强度。
在大量阅读源码前判断是否能委派。DeepSeek 自己读写文件；不要先复制全仓库给它。
DeepSeek 看不到主会话和项目指令；传入完整但精简的任务约束。通常使用默认 Flash，升级 Pro 需用户明确选择。
单个或多个任务均可优先使用 start_deepseek_parallel/start_deepseek_parallel_readonly，传 task 和绝对路径 workspace 即可；batch_id 和 lane 可省略，自动创建批次和分配槽位。只读任务不运行命令。结束后不能复用 DeepSeek 会话，后续任务只传必要的简短上下文。已完成并收取结果的槽位可以在同一目录复用。旧 start_deepseek/start_deepseek_readonly 仍可用于默认目录，但不要用默认目录限制跨仓库任务。
多个独立任务时直接按各自 workspace 调用 start_deepseek_parallel 或 start_deepseek_parallel_readonly，并把文件边界写清楚；无需先 ping 或 prepare。保存每次返回的 batch_id、lane、workspace 和 job_id，核对实际路径，再用 get_deepseek_parallel_status、get_deepseek_parallel_result 收取结果。中途调整或取消使用 send_deepseek_parallel_message、cancel_deepseek_parallel，避免密集轮询。可以用 prepare_deepseek_parallel(workspaces=[绝对路径列表]) 预先指定目录，但不是必需步骤。每路目录在批次内固定；需要换目录时使用空槽位，或收取并检查全部结果后关闭批次再开始。不同目录无需共同父目录或统一提交。
每个并行结果有修改时，按返回的 batch_id 和 lane 调用 get_deepseek_parallel_recovery，检查实际文件后用准确 transaction_ids 调用 acknowledge_deepseek_parallel_mutations。直接模式收取并审查结果、确认恢复记录后即可 close_deepseek_parallel；关闭只释放槽位，不删除目录，不要求工作区干净或提交。
仅在需要 Git 隔离时用 prepare_deepseek_parallel(mode="worktree")；该模式仍要求默认目录是干净的 Git 根目录，并创建五个 worktree。按返回 batch_id 和 lane 启动任务；结果审查整合后用 inspect_deepseek_parallel 检查，全部干净且恢复记录已确认后才关闭删除 worktree。
结果有修改时调用 get_deepseek_recovery，检查实际文件后用准确 transaction_ids 调用 acknowledge_deepseek_mutations。
中断或重连后也先恢复检查。鉴权、额度或持续网络失败时报告，不反复重试消耗额度。
DeepSeek 失败时先保留并检查实际改动，再按 job_id 调用 get_deepseek_diagnostic 取得跨会话保存的失败类别；需要确认余额时调用 check_deepseek_balance，它只返回可用性和 HTTP 状态，不调用模型、不显示密钥。调用 get_deepseek_resume_packet 读取私有、限长的原任务检查点、工作区 Git 状态和待确认 transaction。逐项检查实际改动；待确认 transaction 必须先按准确 ID 审查并确认，然后重新取恢复包。仅对本地 token/history/turn 预算错误，明确写出未完成部分（不超过 4000 字），用最新恢复包的 state_token 调用 start_deepseek_resume；工作区状态变化会使旧 token 失效。同一个原任务最多一次恢复启动，恢复任务不能再自动恢复。使用 get_deepseek_resume_status/get_deepseek_resume_result 查看结果，必要时用 send_deepseek_resume_message/cancel_deepseek_resume。失败属于余额、鉴权、限速、网络或未知类别时先排查，不自动重新调用模型。历史上未保存检查点的任务只能根据原任务与文件人工重建，不声称能恢复其会话。Codex 的 /compact 仅压缩主聊天上下文，不能续接已结束的 DeepSeek 会话。提示 DeepSeek 用有界命令输出和针对性搜索，避免反复读取完整日志或大文件。不要把余额、网络、限速和本地预算混为一谈。
配置或鉴权失败时停止外部分派；可以继续必要的只读分析，不静默把大量实现转回昂贵模型。
DeepSeek 返回不能作为验收证明。最终由主模型审查实际改动、验证证据和任务完成情况。

显式 workspace 参数就是该任务的执行目录，不要求与 MCP 默认目录一致。调用前根据任务确认该目录属于授权项目范围。ping 仅用于排查默认目录或配置；默认目录不同可以直接显式传 workspace，无需重启或要求用户改变当前目录。
密钥未配置时不要让用户把密钥发到聊天里。告知本地隐藏输入设置方式：python3 "{{DATA_DIR}}/setup-key.py"；完成后继续当前任务。
切换到本技能仅授权按任务范围向 DeepSeek 发送必要代码与指令，不上传密钥或无关文件。
