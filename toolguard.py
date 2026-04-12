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
        print(f"[ToolGuard][_reasoning] 开始执行, tool_choice={tool_choice}")
        while True:
            pending_tool = await self.sess.get_pending_tool()
            print(f"[ToolGuard][_reasoning] 获取到 pending_tool={pending_tool}")

            if pending_tool:
                tool_name = pending_tool.tool_use["name"]
                tool_input = pending_tool.tool_use["input"]
                tool_status = pending_tool.status

                if tool_status == PendingToolUse.PENDING: # 需要人工确认
                    print(f"[ToolGuard][_reasoning] 工具 {tool_name} 需要人工确认, input={tool_input}")
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
                    print(f"[ToolGuard][_reasoning] 工具 {tool_name} 已APPROVED, 原id={tool_use_block.get('id')}")
                    tool_use_block['id'] = str(uuid.uuid4()) # 不能和之前denied的tool call id重复
                    print(f"[ToolGuard][_reasoning] 生成新的tool_use id={tool_use_block['id']}")
                    msg=Msg(role='assistant', content=[tool_use_block], name='tool_guard')
                elif tool_status == PendingToolUse.REJECTED: # 人工拒绝执行，看是否有下一个pending tool
                    print(f"[ToolGuard][_reasoning] 工具 {tool_name} 已REJECTED, 检查下一个pending tool")
                    await self.sess.pop_pending_tool()
                    pending_tool = await self.sess.get_pending_tool() 
                    if pending_tool: # 拿到下一个pending的tool use继续让用户确认
                        print(f"[ToolGuard][_reasoning] 发现下一个pending tool, 继续循环")
                        continue
                    # 没有更多pending tool，让模型思考下一步
                    print(f"[ToolGuard][_reasoning] 没有更多pending tool, 调用父类_reasoning")
                    rejected_hint = TOOL_REJECTED_TEMPLATE.format(tool_name=tool_name, tool_input=tool_input)
                    await self.memory.add(Msg(role='user', content=[TextBlock(type="text", text=rejected_hint)], name='tool_guard'), marks=['TOOL_REJECTED'])
                    try:
                        return await super()._reasoning(tool_choice)
                    finally:
                        await self.memory.delete_by_mark('TOOL_REJECTED')
                await self.memory.add(msg)
                await self.print(msg,last=True)
                print(f"[ToolGuard][_reasoning] 返回消息, role={msg.role}, name={msg.name}")
                return msg
            print(f"[ToolGuard][_reasoning] 没有pending_tool, 调用父类_reasoning")
            return await super()._reasoning(tool_choice)

    async def _acting(self:ReActAgent, tool_call: ToolUseBlock) -> dict | None:
        print(f"[ToolGuard][_acting] 开始执行, tool_call id={tool_call.get('id')}, name={tool_call.get('name')}")
        pending_call = await self.sess.get_pending_tool()
        pending_status = pending_call.status if pending_call else None
        pending_id = pending_call.tool_use["id"] if pending_call else None
        print(f"[ToolGuard][_acting] 获取到 pending_call id={pending_id}, status={pending_status}")
        if pending_call and pending_call.tool_use["id"] == tool_call["id"] and pending_call.status == PendingToolUse.APPROVED:
            print(f"[ToolGuard][_acting] 工具 {tool_call['name']} 已APPROVED, 执行调用")
            await self.sess.pop_pending_tool()
            result = await super()._acting(tool_call)
            print(f"[ToolGuard][_acting] 工具执行完成, result={result}")
            return result

        if tool_call['name'] in GUARD_TOOLS:
            print(f"[ToolGuard][_acting] 工具 {tool_call['name']} 在GUARD_TOOLS中, 需要人工确认")
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
            print(f"[ToolGuard][_acting] 已添加pending_tool, id={tool_call['id']}")
            return None
        print(f"[ToolGuard][_acting] 工具 {tool_call['name']} 不在GUARD_TOOLS中, 直接执行")
        result = await super()._acting(tool_call)
        print(f"[ToolGuard][_acting] 工具执行完成, result={result}")
        return result