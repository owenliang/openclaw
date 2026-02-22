import os
from contextlib import asynccontextmanager
import fastapi
from agentscope.tool import Toolkit
from fastapi import Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from datamodel import AgentRequest, ChatRequest
from superagent import create_agent_if_not_exists, sess_mgr

@asynccontextmanager
async def lifespan(app):
    async with sess_mgr:
        os.makedirs(".agents/skills/",exist_ok=True)
        yield

app=fastapi.FastAPI(lifespan=lifespan)

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
    for skill_dir in os.listdir(".agents/skills"):
        if os.path.isdir(os.path.join(".agents/skills", skill_dir)):
            toolkit.register_agent_skill(os.path.join(".agents/skills", skill_dir))
    skills_list = list(toolkit.skills.values())
    return {"skills": skills_list}
    
@app.post("/chat")
async def chat(request: ChatRequest):
    sess = await create_agent_if_not_exists(request.session_id)

    agent_req=AgentRequest(session_id=request.session_id, content=request.content, deepresearch=request.deepresearch)
    await sess.add_request(agent_req)

    async def event_generator():
        while True:
            msg = await agent_req.response_queue.get()
            if msg is None:
                break
            yield msg
    return StreamingResponse(event_generator(), media_type="text/event-stream")
    
@app.get('/stop')
async def stop(session_id: str,request_id: str):
    sess = await sess_mgr.get_or_create_session(session_id, create=False)
    if sess is None:
        return {"status": "session not exists", "session_id": session_id, "request_id": request_id}
    await sess.cancel_request(request_id)
    return {"status": "canceled", "session_id": session_id, "request_id": request_id}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)