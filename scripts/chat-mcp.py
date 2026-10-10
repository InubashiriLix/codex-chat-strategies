from deepseek_mcp import server
# Make the shared tool opt-in; the selected skill supplies delegation policy.
server.mcp._mcp_server.instructions = "DeepSeek execution tools. Use only when the user explicitly selects strategy-deepseek or requests DeepSeek delegation. Never use in strategy-openai or strategy-solo. Before delegation, ping and verify the workspace; review mutations and acknowledge exact recovery IDs. Merely loading this server is not permission to delegate."
server.main()
