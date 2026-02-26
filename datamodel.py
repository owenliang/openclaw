import asyncio
import uuid
from typing import List
from pydantic import BaseModel
from agentscope.message import ImageBlock, Msg, TextBlock 

class ChatRequest(BaseModel):
    session_id: str
    content: List[TextBlock|ImageBlock]
    deepresearch: bool = False

class AgentRequest:
    def __init__(self, session_id: str, content: List[TextBlock|ImageBlock], deepresearch: bool = False) :
        self.id = str(uuid.uuid4())
        self.session_id = session_id
        self.content = content
        self.deepresearch = deepresearch
        self.response_queue = asyncio.Queue()
        self.stream_task = None
        self.canceled = False

    async def cancel(self):
        self.canceled = True
        if self.stream_task:
            try:
                self.stream_task.cancel()
                await self.stream_task
            except:
                pass