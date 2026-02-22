import asyncio
import time
import uuid
from enum import Enum
from typing import Dict, Literal

from agentscope.mcp import HttpStatefulClient, StdIOStatefulClient
from agentscope.tool import Toolkit
from agentscope_runtime.adapters.agentscope.tool import sandbox_tool_adapter
from agentscope_runtime.engine.services.sandbox import SandboxService
from agentscope_runtime.sandbox.box.sandbox import Sandbox

from datamodel import AgentRequest

BROWSER_TOOLS=[
    "browser_close",
    "browser_resize",
    "browser_console_messages",
    "browser_handle_dialog",
    "browser_file_upload",
    "browser_press_key",
    "browser_navigate",
    "browser_navigate_back",
    "browser_navigate_forward",
    "browser_network_requests",
    "browser_pdf_save",
    "browser_take_screenshot",
    "browser_snapshot",
    "browser_click",
    "browser_drag",
    "browser_hover",
    "browser_type",
    "browser_select_option",
    "browser_tab_list",
    "browser_tab_new",
    "browser_tab_select",
    "browser_tab_close",
    "browser_wait_for",
]

class SessionStatus(Enum):
    INIT = "init"
    ACTIVE = "active"
    INACTIVE = "inactive"

class MCPWrapper:
    def __init__(self, client):
        self.client = client
        self.close_ev = asyncio.Event()

    def trigger_close(self):
        self.close_ev.set()

    async def handle_close(self):
        await self.close_ev.wait()
        await self.client.close()

class Session:
    def __init__(self, session_id, sandbox_service):
        self.session_id = session_id
        self.lock = asyncio.Lock()
        self.mcp_wrappers: Dict[str, MCPWrapper] = {}
        self.sandbox: Sandbox | None = None
        self.sandbox_service: SandboxService = sandbox_service
        self.req_queue=asyncio.Queue()
        self.last_activate = time.time()
        self.status=SessionStatus.INIT
        self.pending_req: Dict[str, AgentRequest] = {} 

    def _activate(self):
        self.last_activate = time.time()

    async def activate(self):
        async with self.lock:
            if self.status==SessionStatus.INACTIVE:
                return
            self._activate()

    async def transition_to_active(self) -> bool:
        async with self.lock:
            if self.status==SessionStatus.INIT:
                self.status=SessionStatus.ACTIVE
                return True
        return False

    async def add_request(self,request: AgentRequest):
        async with self.lock:
            if self.status!=SessionStatus.ACTIVE:
                await request.response_queue.put(None)
                return
            self.pending_req[request.id] = request
            await self.req_queue.put(request)
        return True

    async def get_request(self) -> tuple[AgentRequest|None, SessionStatus]:
        async with self.lock:
            status = self.status
        return await self.req_queue.get(), status

    async def finish_request(self,request: AgentRequest):
        async with self.lock:
            if request.id in self.pending_req:
                del self.pending_req[request.id]

    async def cancel_request(self,request_id: str):
        async with self.lock:
            if request_id in self.pending_req:
                request = self.pending_req[request_id]
                try:
                    request.cancel()
                except:
                    pass

    async def register_sandbox(self,toolkit: Toolkit) -> bool:
        async with self.lock:
            if self.status!=SessionStatus.ACTIVE:
                return False
            self._activate()
            if self.sandbox is None:
                sandboxes = self.sandbox_service.connect(session_id=self.session_id,sandbox_types=["browser"])
                self.sandbox = sandboxes[0] # browser
            for toolname in BROWSER_TOOLS:
                tool=getattr(self.sandbox,toolname)
                toolkit.register_tool_function(sandbox_tool_adapter(tool))
        return True

    async def register_stateful_mcp(self, toolkit: Toolkit, type: Literal["stdio", "http"], name, **kwargs) -> bool:
        async with self.lock:
            if self.status!=SessionStatus.ACTIVE:
                return False
            self._activate()
            if name not in self.mcp_wrappers:
                q = asyncio.Queue()
                async def mcp_lifecycle():
                    try:
                        if type == "http":
                            client = HttpStatefulClient(name,**kwargs)
                        else:
                            client = StdIOStatefulClient(name,**kwargs)
                        await client.connect()
                        mcp_wrapper = MCPWrapper(client)
                        await q.put(mcp_wrapper)
                        try:
                            await mcp_wrapper.handle_close()
                        except Exception:
                            pass
                    except BaseException as e:
                        print(f'{name} MCP Lifecycle Error: {e}')
                        await q.put(None)
                asyncio.create_task(mcp_lifecycle())
                mcp_wrapper = await q.get()
                if mcp_wrapper is None:
                    return False
                self.mcp_wrappers[name] = mcp_wrapper
            mcp_wrapper=self.mcp_wrappers[name]
            try:
                await toolkit.register_mcp_client(mcp_wrapper.client)
            except:
                mcp_wrapper.trigger_close()
                del self.mcp_wrappers[name]
                return False 
        return True

    async def release(self):
        async with self.lock:
            if self.status==SessionStatus.INACTIVE:
                return
            self.status=SessionStatus.INACTIVE
            # mcp
            for mcp_wrapper in self.mcp_wrappers.values():
                try:
                    mcp_wrapper.trigger_close()
                except:
                    pass
            self.mcp_wrappers = {}
            # sandbox
            if self.sandbox is not None:
                await self.sandbox_service.release(self.session_id)
            # notify consumer
            await self.req_queue.put(None)

class GlobalSessionManager:
    def __init__(self, expires: float = 600, enable_sandbox: bool = True):
        self.expires = expires
        self.manager_lock = asyncio.Lock()
        self.sessions: Dict[str, Session] = {}
        self.sandbox_service = SandboxService()
        self.enable_sandbox = enable_sandbox

    async def __aenter__(self):
        if self.enable_sandbox:
            await self.sandbox_service.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return

    async def get_or_create_session(self, session_id, create=True) -> Session: 
        async with self.manager_lock: 
            if session_id not in self.sessions:
                if create:
                    self.sessions[session_id] = Session(session_id, self.sandbox_service)
                    asyncio.create_task(self.validate_session(session_id))
            return self.sessions.get(session_id,None)

    def temp_session(self):
        session_id = str(uuid.uuid4())
        return Session(session_id, self.sandbox_service)

    async def validate_session(self, session_id):
        while True:
            await asyncio.sleep(0.1)
            free_session = False
            async with self.manager_lock:
                session = self.sessions[session_id]
                if time.time() - session.last_activate > self.expires:
                    free_session = True
                    del self.sessions[session_id]
            if free_session:
                print(f"Release Session ID {session_id}")
                await session.release()
                break