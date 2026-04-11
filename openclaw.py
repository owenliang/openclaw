
from agentscope.agent import ReActAgent
from toolguard import ToolGuardMixin

# MOR链：ToolGuardMixin(_reasoning, _acting) -> ReActAgent(_reasoning, _acting)
class OpenClaw(ToolGuardMixin,ReActAgent):
    pass