from pathlib import Path

from deepseek_mcp import server
from deepseek_diagnostics import register_diagnostic_tools
from deepseek_recovery import register_recovery_tools
from parallel_deepseek import register_parallel_tools
# Make the shared tool opt-in; the selected skill supplies delegation policy.
server.mcp._mcp_server.instructions = "DeepSeek execution tools. Use only when the user explicitly selects strategy-deepseek or requests DeepSeek delegation. Never use in strategy-openai or strategy-solo. Before delegation, ping and verify the workspace; review mutations and acknowledge exact recovery IDs. Parallel jobs require separate prepared Git worktrees. Merely loading this server is not permission to delegate."
register_parallel_tools(server.mcp, Path(__file__).resolve().parent)
register_diagnostic_tools(server.mcp, Path(__file__).resolve().parent)
register_recovery_tools(server.mcp, Path(__file__).resolve().parent)
server.main()
