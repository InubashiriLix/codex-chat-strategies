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
单个 DeepSeek 执行器对同一目录有独占锁；五路并行通过五个独立 Git worktree 实现，不能让两个任务指向同一目录。并行工作区从干净的已提交 HEAD 创建；主工作区有未提交改动时，先解决基线问题或改为串行。每路分配独立文件责任，同一文件的最终改动由主代理协调。
父代理等待期间不要重复实现同一任务。相关后续工作和返工优先交回原 Luna，避免重发整段会话或全仓库内容；后续写入时明确新的 worktree 路径，或确认原 worktree 仍在。任务领域不同或旧上下文已无价值时才新建。连续两轮无有效进展时重新诊断或由主代理接手。
按任务需要执行验证，优先已有针对性检查；不因代理数量重复运行全套测试。
子代理返回简短结论、改动路径、实际验证和未决问题，长日志保留文件；主代理逐份检查 worktree 差异和未跟踪文件，确认无覆盖后依次整合，再做一次整体验收。并行 worktree 存在于本地数据目录；未检查和整合前不要删除。
仅对重要或复杂改动增加独立强模型审查；达到验收条件后交付，不为形式增加角色。
不自动提交、推送或发布，除非任务本身已授权。不得扩大现有权限或忽略项目约束。
本策略只适用于当前会话。
本会话主模型保持 GPT，使用 strategy_deepseek MCP 委派 DeepSeek。独立任务优先分给 DeepSeek，最多五路；需要额外的不同领域工作时可按需使用原生 gpt-6-luna，但不要为凑并发数创建代理或为日常实现创建昂贵的强模型子代理。按任务难度选择 Luna 推理强度。
在大量阅读源码前判断是否能委派。DeepSeek 自己读写文件；不要先复制全仓库给它。
DeepSeek 看不到主会话和项目指令；传入完整但精简的任务约束。通常使用默认 Flash，升级 Pro 需用户明确选择。
单个任务优先 start_deepseek/start_deepseek_readonly 后 get_deepseek_status/get_deepseek_result；只读任务不运行命令。运行中的任务可用 send_deepseek_message 补充指令。结束后不能复用 DeepSeek 会话，后续任务只传必要的简短上下文。
多个独立任务时，先 ping 确认主工作区，再调用 prepare_deepseek_parallel 得到 batch_id 和五个 lane 路径；它要求位于干净的 Git worktree 根目录且不调用模型。按 lane 1–5 分别调用 start_deepseek_parallel 或 start_deepseek_parallel_readonly，并把任务的文件边界写清楚；随后用各 job_id 调用 get_deepseek_parallel_status、get_deepseek_parallel_result。需要中途调整或取消时使用 send_deepseek_parallel_message、cancel_deepseek_parallel。状态无变化时避免密集轮询。
每个并行结果有修改时，按 batch_id 和 lane 调用 get_deepseek_parallel_recovery，检查该 lane 实际文件后用准确 transaction_ids 调用 acknowledge_deepseek_parallel_mutations。使用 inspect_deepseek_parallel 核对各 lane 的 Git 状态；主代理逐路整合并验证。所有 lane 的改动均已整合、工作区干净且无未确认恢复记录后，调用 close_deepseek_parallel 释放 worktree；条件不满足时保留现场。不要把五个 lane 当成共享文件夹，也不要从未整合的 lane 静默覆盖主工作区。
结果有修改时调用 get_deepseek_recovery，检查实际文件后用准确 transaction_ids 调用 acknowledge_deepseek_mutations。
中断或重连后也先恢复检查。鉴权、额度或持续网络失败时报告，不反复重试消耗额度。
配置或鉴权失败时停止外部分派；可以继续必要的只读分析，不静默把大量实现转回昂贵模型。
DeepSeek 返回不能作为验收证明。最终由主模型审查实际改动、验证证据和任务完成情况。

调用前先 ping 并确认返回 workspace 与本会话实际项目相符。目录不一致时停止委派，报告目录，不能对错误目录执行修改。
密钥未配置时不要让用户把密钥发到聊天里。告知本地隐藏输入设置方式：python3 "{{DATA_DIR}}/setup-key.py"；完成后继续当前任务。
切换到本技能仅授权按任务范围向 DeepSeek 发送必要代码与指令，不上传密钥或无关文件。
