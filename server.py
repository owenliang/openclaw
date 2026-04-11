import os
import asyncio
import json
from contextlib import asynccontextmanager
import fastapi
from agentscope.tool import Toolkit
from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from datamodel import AgentRequest, ChatRequest
from superagent import create_agent_if_not_exists, SESS_MGR, load_agent_states
from tools import load_persona_file, modify_persona_file
from cron_manager import CRON_MGR
from dotenv import load_dotenv
import uvicorn
from superagent import superagent_lifecycle

class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if os.environ.get("SERVER_API_AUTH", "").lower() == "true":
            token = None
            auth_header = request.headers.get("Authorization", "")
            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
            if token is None:
                token = request.query_params.get("token", "").strip()
            if not token:
                return Response(status_code=401, content="Missing or invalid Authorization header")
            if token != os.environ.get("SERVER_API_TOKEN", ""):
                return Response(status_code=403, content="Invalid token")
        return await call_next(request)

@asynccontextmanager
async def lifespan(app):
    async with SESS_MGR,superagent_lifecycle():
        await CRON_MGR.load_from_disk()
        yield

app=fastapi.FastAPI(lifespan=lifespan)

# 配置认证中间件
app.add_middleware(AuthMiddleware)

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def index():
    return FileResponse("chat.html")

@app.get("/music/{filename}")
async def get_music(filename: str, request: Request):
    music_path = os.path.join("./assets/music", filename)
    if not os.path.exists(music_path):
        return {"error": "Music file not found"}
    with open(music_path, "rb") as f:
        content = f.read()
    return Response(
        content=content,
        media_type="audio/mp4",
        headers={
            "Accept-Ranges": "none",
            "Content-Length": str(len(content)),
            "Cache-Control": "public, max-age=3600"
        }
    )

@app.get('/get_commands')
async def get_commands():
    toolkit=Toolkit()
    for skill_dir in os.listdir(".agent/skills"):
        if os.path.isdir(os.path.join(".agent/skills", skill_dir)):
            try:
                toolkit.register_agent_skill(os.path.join(".agent/skills", skill_dir))
            except BaseException as e:
                print(f"Error registering skill {skill_dir}: {e}")
    skills_list = list(toolkit.skills.values())

    # Magic 命令列表
    magic_commands = [
        {"name": "approve", "description": "批准待确认的工具调用"},
        {"name": "reject", "description": "拒绝待确认的工具调用"}
    ]

    return {"skills": skills_list, "magics": magic_commands}

@app.get('/get_personas')
async def get_personas():
    return {
        "agents": load_persona_file("AGENTS.md"),
        "soul": load_persona_file("SOUL.md"),
        "user": load_persona_file("USER.md"),
    }

@app.post('/update_persona')
async def update_persona(request: Request):
    FILE_MAP = {"agents": "AGENTS.md", "soul": "SOUL.md", "user": "USER.md"}
    body = await request.json()
    target = body.get("target", "")
    content = body.get("content", "")
    filename = FILE_MAP.get(target)
    if not filename:
        return {"status": "error", "message": f"unknown target: {target}"}
    modify_persona_file(filename, content)
    return {"status": "success"}

@app.get("/get_crons")
async def get_crons():
    jobs = await CRON_MGR.list_crons()
    return {"status": "success", "jobs": jobs}

@app.post("/chat")
async def chat(request: ChatRequest):
    queue_ok=False
    for _ in range(3):# 为session过期瞬间兜底
        sess = await create_agent_if_not_exists(request.session_id)
        agent_req=AgentRequest(session_id=request.session_id, content=request.content, deepresearch=request.deepresearch)
        if await sess.add_request(agent_req):
            queue_ok=True
            break
        await asyncio.sleep(0.5)
    if not queue_ok:
        return {"error": "queue_error"}

    async def event_generator():
        yield f"data: {json.dumps({'request_id': agent_req.id})}\n\n"   # 首先发送request_id
        
        while True:
            msg = await agent_req.response_queue.get()
            if msg is None:
                break
            yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
    return StreamingResponse(event_generator(), media_type="text/event-stream")
    
@app.get('/stop')
async def stop(session_id: str,request_id: str):
    sess = await SESS_MGR.get_or_create_session(session_id, create=False)
    if sess is None:
        return {"status": "session not exists", "session_id": session_id, "request_id": request_id}
    await sess.cancel_request(request_id)
    return {"status": "canceled", "session_id": session_id, "request_id": request_id}

@app.get('/history')
async def history(session_id: str):
    states=await load_agent_states(session_id)
    if states is None:
        return {"status": "session not exists", "session_id": session_id}
    history=await states.memory.get_memory(exclude_mark='compressed')
    return {"status": "success", "session_id": session_id, "history": history}

if __name__ == "__main__":
    load_dotenv()
    uvicorn.run(app, host="0.0.0.0", port=8000)