import asyncio
import json
import os
import uuid
from typing import Dict, List
from datetime import datetime
from datamodel import AgentRequest
from agentscope.message import TextBlock
from croniter import croniter
from session import SESS_MGR
from superagent import create_agent_if_not_exists
from agentscope.tool import ToolResponse

CRON_SESSION_ID = "cronjob"

class CronJob:
    def __init__(self, job_id: str, cron_expr: str, task_description: str):
        self.id = job_id
        self.cron_expr = cron_expr
        self.task_description = task_description
        self.task: asyncio.Task | None = None
        self._cancelled = False
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "cron_expr": self.cron_expr,
            "task_description": self.task_description,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        return cls(
            job_id=data["id"],
            cron_expr=data["cron_expr"],
            task_description=data["task_description"],
        )


class CronManager:
    SPECIAL_EXPRESSIONS = {
        "@minutely": "* * * * * *",
        "@hourly": "0 * * * * *",
        "@daily": "0 0 * * * *",
        "@weekly": "0 0 * * 0 *",
        "@monthly": "0 0 1 * * *",
        "@yearly": "0 0 1 1 * *",
        "@annually": "0 0 1 1 * *",
    }
    
    def __init__(self, persistence_path: str = "./cron_jobs.json"):
        self._jobs: Dict[str, CronJob] = {}
        self._lock = asyncio.Lock()
        self._persistence_path = persistence_path
    
    async def load_from_disk(self):
        if not os.path.exists(self._persistence_path):
            return
        try:
            with open(self._persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for job_data in data.get("jobs", []):
                job = CronJob.from_dict(job_data)
                async with self._lock:
                    self._jobs[job.id] = job
                next_delay = self._get_next_delay(job.cron_expr)
                job.task = asyncio.create_task(self._run_cron_job(job, next_delay))
            print(f"[CronManager] Loaded {len(self._jobs)} jobs from {self._persistence_path}")
        except Exception as e:
            print(f"[CronManager] Failed to load jobs from disk: {e}")
    
    async def _save_to_disk(self):
        try:
            async with self._lock:
                data = {"jobs": [job.to_dict() for job in self._jobs.values()]}
            with open(self._persistence_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CronManager] Failed to save jobs to disk: {e}")
    
    def _normalize_cron_expr(self, cron_expr: str) -> str:
        expr = cron_expr.strip()
        if expr.startswith("@"):
            if expr.lower() in self.SPECIAL_EXPRESSIONS:
                return self.SPECIAL_EXPRESSIONS[expr.lower()]
            raise ValueError(f"Unknown special cron expression: {expr}")
        parts = expr.split()
        if len(parts) == 5:
            return f"* {expr}"
        elif len(parts) == 6:
            return expr
        raise ValueError(f"Invalid cron expression: expected 5 or 6 fields, got {len(parts)}")
    
    def _get_next_delay(self, cron_expr: str) -> float:
        try:
            normalized = self._normalize_cron_expr(cron_expr)
            now = datetime.now()
            parts = normalized.split()
            is_second_level = len(parts) == 6
            cron = croniter(normalized, now, second_at_beginning=is_second_level)
            next_time = cron.get_next(datetime)
            delay = (next_time - now).total_seconds()
            return max(delay, 0.1)
        except Exception as e:
            raise ValueError(f"Invalid cron expression: {cron_expr}") from e
    
    async def add_cron(self, cron_expr: str, task_description: str, job_id: str = None) -> str:
        try:
            self._get_next_delay(cron_expr)
        except ValueError as e:
            raise e
        if job_id is None:
            job_id = str(uuid.uuid4())
        job = CronJob(job_id, cron_expr, task_description)
        async with self._lock:
            self._jobs[job.id] = job
        next_delay = self._get_next_delay(cron_expr)
        job.task = asyncio.create_task(self._run_cron_job(job, next_delay))
        await self._save_to_disk()
        return job.id
    
    async def del_cron(self, job_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            job._cancelled = True
            if job.task and not job.task.done():
                job.task.cancel()
            del self._jobs[job_id]
        await self._save_to_disk()
        return True
    
    async def _run_cron_job(self, job: CronJob, initial_delay: float):
        try:
            await asyncio.sleep(initial_delay)
            while not job._cancelled:
                try:
                    await self._execute_task(job)
                    if job._cancelled:
                        break
                    next_delay = self._get_next_delay(job.cron_expr)
                    await asyncio.sleep(next_delay)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    try:
                        next_delay = self._get_next_delay(job.cron_expr)
                        await asyncio.sleep(next_delay)
                    except Exception:
                        break
        except asyncio.CancelledError:
            raise
    
    async def list_crons(self) -> List[dict]:
        async with self._lock:
            return [
                {
                    "id": job.id,
                    "cron_expr": job.cron_expr,
                    "task_description": job.task_description,
                    "running": job.task is not None and not job.task.done(),
                }
                for job in self._jobs.values()
            ]
    
    async def _execute_task(self, job: CronJob):   
        request = AgentRequest(
            session_id=CRON_SESSION_ID,
            content=[TextBlock(type="text", text=job.task_description)],
            deepresearch=False
        )

        try:
            success = False
            for _ in range(3):
                session=await create_agent_if_not_exists(CRON_SESSION_ID)
                if await session.add_request(request):
                    success=True
                    break
                await asyncio.sleep(0.5)
            if not success:
                return

            while True:
                msg = await request.response_queue.get()
                if msg is None:
                    break
        except Exception as e:
            print(f"[CronManager] Error in _execute_task for job {job.id}: {e}")
        finally:
            try:
                session = await SESS_MGR.get_or_create_session(CRON_SESSION_ID, create=False)
                if session:
                    await session.cancel_request(request.id)
            except:
                pass

async def build_cron_tools():
    """构建定时任务管理工具"""

    async def add_cron(cron_expr: str, task_description: str) -> ToolResponse:
        '''
        添加一个定时任务，会按照指定的时间周期执行给定的任务描述。

        Args:
            cron_expr: Cron表达式字符串，支持以下格式：
                - "*/5 * * * *" - 每5分钟执行一次
                - "@minutely" - 每分钟执行一次
                - "@hourly" - 每小时执行一次
                - "@daily" - 每天执行一次
            task_description: 当定时任务触发时要发送给AI助手的任务描述

        Returns:
            ToolResponse，包含创建成功的定时任务唯一ID
        '''
        try:
            job_id = await CRON_MGR.add_cron(cron_expr, task_description)
        except Exception as e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"定时任务创建失败: {e}",
                    ),
                ],
            )
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"定时任务创建成功，任务ID: {job_id}",
                ),
            ],
        )

    async def del_cron(job_id: str) -> ToolResponse:
        '''
        根据任务ID删除指定的定时任务。

        Args:
            job_id: add_cron返回的唯一任务ID

        Returns:
            ToolResponse，包含删除成功或失败的消息
        '''
        success = await CRON_MGR.del_cron(job_id)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"定时任务 {job_id} 删除成功" if success else f"定时任务 {job_id} 不存在",
                ),
            ],
        )

    async def list_crons() -> ToolResponse:
        '''
        列出所有已创建的定时任务。

        Returns:
            ToolResponse，包含所有定时任务的格式化列表
        '''
        jobs = await CRON_MGR.list_crons()
        if not jobs:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="暂无定时任务",
                    ),
                ],
            )

        lines = ["定时任务列表:", "-" * 80]
        for job in jobs:
            status = "运行中" if job["running"] else "已停止"
            lines.append(f"ID: {job['id']}")
            lines.append(f"  表达式: {job['cron_expr']}")
            lines.append(f"  任务: {job['task_description'][:50]}...")
            lines.append(f"  状态: {status}")
            lines.append("")

        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="\n".join(lines),
                ),
            ],
        )

    return add_cron, del_cron, list_crons

CRON_MGR = CronManager()