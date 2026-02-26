import asyncio
import json
import os
import sys
from datetime import datetime
from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.pipeline import stream_printing_messages
from agentscope.plan import PlanNotebook
from agentscope.session import JSONSession
from model import OpenAIChatModelCached, VLTokenCounter
from session import Session, GlobalSessionManager, SessionStatus
from tools import build_agent_toolkit, build_subagent_tool, FLAGS, AGENT_SYS_PROMPT, SUBAGENT_PROMPT

sess_mgr = GlobalSessionManager(expires=60, enable_sandbox=FLAGS["enable_sandbox"])

async def register_reasoning_hint(agent):
    async def add_reasoning_hint(agent,kwargs):
        now = datetime.now()
        weekday_map = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
        weekday = weekday_map[now.weekday()]
        current_time = now.strftime(f"%Y年%m月%d日 {weekday} %H:%M:%S")
        await agent.memory.add(Msg(name="current_time", content=f"当前时间(内部参考信息,非用户输入)：{current_time}", role="user"), marks='MY_REASONING_HINT')
    async def remove_reasoning_hint(agent,kwargs,output):
        await agent.memory.delete_by_mark(mark='MY_REASONING_HINT')
    agent.register_instance_hook('pre_reasoning','add_reasoning_hint',add_reasoning_hint)
    agent.register_instance_hook('post_reasoning','remove_reasoning_hint',remove_reasoning_hint)

async def register_sess_keepalive(agent,sess):
    async def activate_sess_client(agent,kwargs,output=None):
        await sess.activate()
    for hooks in ['pre_reasoning', 'pre_acting', 'post_acting', 'post_reasoning']:
        agent.register_instance_hook(hooks,'activate_sess_client',activate_sess_client)

async def agent_runner(sess: Session):
    while True:
        request,status = await sess.get_request()
        if status==SessionStatus.INACTIVE:
            print(f'session {sess.session_id} is inactive')
            await sess_mgr.delete_session(sess.session_id) # 内存中淘汰会话，下一个请求正常响应；已经持有session对象的请求add request会立即拒绝；
            await sess.release() # 释放MCP资源
            break
        
        await sess.activate() 

        # 请求处理
        try:
            session_id=request.session_id
            response_q=request.response_queue
            toolkit=await build_agent_toolkit(sess)

            extra_sys_prompt = []
            if FLAGS["enable_subagent"]:
                toolkit.register_tool_function(await build_subagent_tool(sess_mgr))
                extra_sys_prompt.append(SUBAGENT_PROMPT)
            extra_sys_prompt='\n'.join(extra_sys_prompt)

            plan_notebook=None
            if request.deepresearch:
                plan_notebook=PlanNotebook()
            agent=ReActAgent(
                name="Owen",
                sys_prompt=AGENT_SYS_PROMPT.format(extra_prompt=extra_sys_prompt),
                model=OpenAIChatModelCached(
                    model_name="qwen3.5-plus",
                    api_key=os.environ["DASHSCOPE_API_KEY"],
                    stream=True,
                    client_kwargs={
                        'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                    },
                    generate_kwargs={
                        'extra_body': {
                            'enable_thinking': False,
                            'enable_search': True,
                            'search_options': {
                                'enable_search_extension': True,
                                'forced_search': True,
                            },
                        }
                    }
                ),
                formatter=OpenAIChatFormatter(),
                toolkit=toolkit,
                plan_notebook=plan_notebook,
                parallel_tool_calls=True,
                memory=InMemoryMemory(),
                compression_config=ReActAgent.CompressionConfig(
                    enable=True,
                    agent_token_counter=VLTokenCounter(),
                    trigger_threshold=600000,
                    keep_recent=5,
                    compression_model=OpenAIChatModel(
                        model_name="qwen3.5-plus",
                        api_key=os.environ["DASHSCOPE_API_KEY"],
                        stream=False,
                        client_kwargs={
                            'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                        }
                    ),
                ),
                max_iters=sys.maxsize, # 使用系统最大整数，支持长程执行
            )
            session=JSONSession(save_dir="./sessions")
            await session.load_session_state(session_id=session_id,memory=agent.memory) # 只恢复短期记忆

            agent.set_console_output_enabled(False)
            await register_sess_keepalive(agent,sess)
            await register_reasoning_hint(agent)

            inputs = Msg(
                name="user",
                content=request.content,
                role="user",
            )
            q=asyncio.Queue()
            async def streaming():
                try:
                    if request.canceled:
                        return
                    async for msg,last in stream_printing_messages(agents=[agent],coroutine_task=agent(inputs)):
                        msg_id = msg.id if hasattr(msg, 'id') else None
                        msg_ret={'msg_id': msg_id,'last': last,'contents':[],'plan':plan_notebook.current_plan.model_dump() if plan_notebook and plan_notebook.current_plan else None}
                        for content in msg.content:
                            if content['type']=='text':
                                msg_ret['contents'].append({"type": "text", "content": content['text']})
                            elif content['type']=='tool_use':
                                msg_ret['contents'].append({"type": "tool_use", "tool_use_id": content["id"], "content": f'{content["name"]}: {json.dumps(content["input"], ensure_ascii=False)}'})
                            elif content['type']=='tool_result':
                                msg_ret['contents'].append({"type": "tool_result", "tool_use_id": content["id"], "content": f'{content["name"]}: {json.dumps(content["output"], ensure_ascii=False)}'})
                        await q.put(f"data: {json.dumps(msg_ret, ensure_ascii=False)}\n\n")
                    await session.save_session_state(session_id=session_id,memory=agent.memory)
                except asyncio.CancelledError as e:
                    await q.put(f"data: {json.dumps({'msg_id': None,'last': True,'contents':[],'plan':None, 'cancel':True}, ensure_ascii=False)}\n\n")
                except Exception as e:
                    await q.put(f"data: {json.dumps({'msg_id': None,'last': True,'contents':[],'plan':None, 'error':str(e)}, ensure_ascii=False)}\n\n")
                finally:
                    await q.put(None)
            request.stream_task = asyncio.create_task(streaming())
            while True:
                msg=await q.get()
                await response_q.put(msg)
                if msg is None:
                    break
        except Exception as e:
            print(f"Error in agent_runner: {e}")
        finally:
            await response_q.put(None)
            await sess.finish_request(request)

async def create_agent_if_not_exists(session_id: str) -> Session:
    sess=await sess_mgr.get_or_create_session(session_id,create=True,session_main=agent_runner)
    return sess