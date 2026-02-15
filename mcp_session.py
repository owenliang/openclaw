import asyncio
import time

from agentscope.mcp import HttpStatefulClient


class MCPClient:
    def __init__(self, client):
        self.client = client
        self.close_ev = asyncio.Event()

    def close(self):
        self.close_ev.set()

    async def _do_close(self):
        await self.close_ev.wait()
        await self.client.close()

class Session:
    def __init__(self, session_id):
        self.session_id = session_id
        self.lock = asyncio.Lock()
        self.mcp_map = {}
        self.last_activate = time.time()

    def _activate(self):
        self.last_activate = time.time()

    async def activate(self):
        async with self.lock:
            self._activate()

    async def ensure_mcp_client(self, name, transport, url, **kwargs):
        async with self.lock:
            self._activate()
            if name in self.mcp_map:
                mcp = self.mcp_map[name]
                try:
                    await mcp.client.list_tools()  # 健康检查
                    return mcp
                except Exception:
                    await mcp.close()
                    del self.mcp_map[name]
                return None
            try:
                q = asyncio.Queue()

                async def mcp_lifecycle():
                    try:
                        client = HttpStatefulClient(name, transport, url, **kwargs)
                        await client.connect()
                        mcp_client = MCPClient(client)
                        await q.put(mcp_client)
                        try:
                            await mcp_client._do_close()
                        except Exception:
                            pass
                    except BaseException as e:
                        print(f"Error creating MCP client({name} {url}): {e}")
                        await q.put(None)

                asyncio.create_task(mcp_lifecycle())
                mcp = await q.get()
                if mcp is None:
                    return None
                self.mcp_map[name] = mcp
                return self.mcp_map[name]
            except Exception:
                return None

    async def reset_mcp_client(self, name):
        async with self.lock:
            if name not in self.mcp_map:
                return
            mcp = self.mcp_map[name]
            try:
                await mcp.close()
            except Exception:
                pass
            del self.mcp_map[name]

    async def reset_all_mcp_client(self):
        async with self.lock:
            for mcp in self.mcp_map.values():
                try:
                    await mcp.close()
                except Exception:
                    pass
            self.mcp_map = {}

    async def get_all_mcp_client(self):
        async with self.lock:
            return self.mcp_map.copy()


class StatefulMCPManager:
    def __init__(self):
        self.manager_lock = asyncio.Lock()
        self.sessions = {}

    async def get_or_create_session(self, session_id):
        async with self.manager_lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = Session(session_id)
                asyncio.create_task(self.validate_session(session_id))
            return self.sessions[session_id]

    async def validate_session(self, session_id):
        while True:
            await asyncio.sleep(1)
            free_session = False
            async with self.manager_lock:
                session = self.sessions[session_id]
                if time.time() - session.last_activate > 300:
                    free_session = True
                    del self.sessions[session_id]
            if free_session:
                print("release mcp clients for session {}".format(session_id))
                await session.reset_all_mcp_client()
                break
