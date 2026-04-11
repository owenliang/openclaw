import uuid
from agentscope.message import Msg, ToolUseBlock, ToolResultBlock, TextBlock
from agentscope.agent import ReActAgent
from typing import Literal
from datamodel import PendingToolUse
from conf import GUARD_TOOLS
from tools import TOOL_REJECTED_TEMPLATE

class ToolGuardMixin:
    def __init__(self, *args, **kwargs) -> None:
        self.sess = kwargs.pop("sess", None)
        super().__init__(*args, **kwargs)

    async def _reasoning(self:ReActAgent,tool_choice: Literal["auto", "none", "required"] | None = None,) -> Msg:
        while True:
            pending_tool = await self.sess.get_pending_tool()

            if pending_tool:
                tool_name = pending_tool.tool_use["name"]
                tool_input = pending_tool.tool_use["input"]
                tool_status = pending_tool.status

                if tool_status == PendingToolUse.PENDING: # 需要人工确认
                    content = (
                        f"🔒 **工具调用需要确认**\n\n"
                        f"**工具名称:** `{tool_name}`\n"
                        f"**输入参数:** `{tool_input}`\n\n"
                        f"请输入指令:\n"
                        f"• `/approve` - 允许执行\n"
                        f"• `/reject` - 拒绝执行"
                    )
                    msg=Msg(role='assistant', content=[TextBlock(type="text", text=content)], name='tool_guard')
                elif tool_status == PendingToolUse.APPROVED: # 人工确认执行, 生成tool use
                    tool_use_block = pending_tool.tool_use
                    tool_use_block['id'] = str(uuid.uuid4()) # 不能和之前denied的tool call id重复
                    msg=Msg(role='assistant', content=[tool_use_block], name='tool_guard')
                elif tool_status == PendingToolUse.REJECTED: # 人工拒绝执行，看是否有下一个pending tool
                    await self.sess.pop_pending_tool()
                    pending_tool = await self.sess.get_pending_tool() 
                    if pending_tool: # 拿到下一个pending的tool use继续让用户确认
                        continue
                    # 没有更多pending tool，让模型思考下一步
                    rejected_hint = TOOL_REJECTED_TEMPLATE.format(tool_name=tool_name, tool_input=tool_input)
                    await self.memory.add(Msg(role='user', content=[TextBlock(type="text", text=rejected_hint)], name='tool_guard'), marks=['TOOL_REJECTED'])
                    try:
                        return await super()._reasoning(tool_choice)
                    finally:
                        await self.memory.delete_by_mark('TOOL_REJECTED')
                await self.memory.add(msg)
                await self.print(msg,last=True)
                return msg
            return await super()._reasoning(tool_choice)

    async def _acting(self:ReActAgent, tool_call: ToolUseBlock) -> dict | None:
        pending_call = await self.sess.get_pending_tool()
        if pending_call and pending_call.tool_use["id"] == tool_call["id"] and pending_call.status == PendingToolUse.APPROVED:
            await self.sess.pop_pending_tool()
            return await super()._acting(tool_call)

        if tool_call['name'] in GUARD_TOOLS:
            tool_res_msg = Msg(
                "system",
                [
                    ToolResultBlock(
                        type="tool_result",
                        id=tool_call["id"],
                        name=tool_call["name"],
                        output=f'❌[工具调用|人工确认] tool_name={tool_call["name"]} tool_input={tool_call["input"]}'
                    ),
                ],
                "system",
            )
            await self.memory.add(tool_res_msg)
            await self.print(tool_res_msg,last=True)
            await self.sess.add_pending_tool(PendingToolUse(tool_call))
            return None
        return await super()._acting(tool_call)