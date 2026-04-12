FLAGS = {
    "enable_agentrun_browser_mcp":  False,  # 是否启用阿里云agentrun浏览器MCP（http MCP形态）
    "enable_sandbox":               False,  # 是否启用agentscope-runtime沙箱(只支持browser，底层是docker拉起mcp server) --- 需要Linux/Mac安装Docker
    "enable_playwright_mcp":        False,   # 是否启用Playwright MCP（stdio MCP形态）
    "enable_bazi_mcp":              False,   # 是否启用八字算命MCP
    "enable_websearch":             True,   # 是否启用网页搜索TOOL
    "enable_view_text_file":        True,   # 是否启用查看文本文件TOOL
    "enable_write_text_file":       True,   # 是否启用写入文本文件TOOL
    "enable_insert_text_file":      True,   # 是否启用插入文本文件TOOL
    "enable_execute_shell_command": True,   # 是否启用执行Shell命令TOOL
    "enable_subagent":              True,   # 是否启用子代理
    "enable_cron":                  True,   # 是否启用定时任务管理
    "enable_reme":                  False,   # 是否启用ReMe
}

# 需要人工确认的工具列表（ToolGuardMixin 使用）
GUARD_TOOLS = ['write_text_file','insert_text_file','execute_shell_command']