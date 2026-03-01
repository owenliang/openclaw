import asyncio
import json
import os
import uuid
from typing import Callable, Dict, List, Optional
from datetime import datetime
from datamodel import AgentRequest
from agentscope.message import TextBlock
from croniter import croniter


CRON_SESSION_ID = "cronjob"  # Fixed session ID for all cron jobs


class CronJob:
    """Represents a scheduled cron job."""
    
    def __init__(self, job_id: str, cron_expr: str, task_description: str):
        self.id = job_id
        self.cron_expr = cron_expr
        self.task_description = task_description
        self.task: asyncio.Task | None = None
        self._cancelled = False
    
    def to_dict(self) -> dict:
        """Serialize job to dict for persistence."""
        return {
            "id": self.id,
            "cron_expr": self.cron_expr,
            "task_description": self.task_description,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "CronJob":
        """Create job from dict."""
        return cls(
            job_id=data["id"],
            cron_expr=data["cron_expr"],
            task_description=data["task_description"],
        )


class CronManager:
    """Manages scheduled cron jobs with persistence.
    
    All cron jobs are executed in a dedicated "cronjob" session.
    
    Supports full cron expression syntax including:
    - Standard 5-field format: minute hour day month weekday (minute-level precision)
    - Extended 6-field format: second minute hour day month weekday (second-level precision)
    - Special strings: @minutely, @hourly, @daily, @weekly, @monthly, @yearly
    - Complex expressions: */5, 0-30/5, 1,2,3, *, etc.
    
    Examples:
    - "*/20 * * * * *" - Every 20 seconds (6-field with seconds)
    - "0 */5 * * * *" - Every 5 minutes at 0 seconds (6-field)
    - "*/5 * * * *" - Every 5 minutes (5-field, minute precision)
    - "0 15 10 * * MON-FRI" - Weekdays at 10:15 (5-field)
    - "@minutely" - Every minute
    - "@hourly" - Every hour
    - "@daily" - Every day at midnight
    """
    
    # Special cron expressions mapping to standard format
    SPECIAL_EXPRESSIONS = {
        "@minutely": "* * * * * *",      # Every minute (6-field)
        "@hourly": "0 * * * * *",         # Every hour at 0 seconds
        "@daily": "0 0 * * * *",          # Every day at midnight
        "@weekly": "0 0 * * 0 *",         # Every week on Sunday at midnight
        "@monthly": "0 0 1 * * *",        # Every month on 1st at midnight
        "@yearly": "0 0 1 1 * *",         # Every year on Jan 1st at midnight
        "@annually": "0 0 1 1 * *",       # Alias for @yearly
    }
    
    def __init__(self, session_manager, persistence_path: str = "./cron_jobs.json"):
        self._session_manager = session_manager
        self._jobs: Dict[str, CronJob] = {}  # job_id -> CronJob
        self._lock = asyncio.Lock()
        self._persistence_path = persistence_path
    
    async def load_from_disk(self):
        """Load persisted cron jobs from disk and schedule them."""
        if not os.path.exists(self._persistence_path):
            return
        
        try:
            with open(self._persistence_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for job_data in data.get("jobs", []):
                job = CronJob.from_dict(job_data)
                
                async with self._lock:
                    self._jobs[job.id] = job
                
                # Schedule the job
                next_delay = self._get_next_delay(job.cron_expr)
                job.task = asyncio.create_task(self._run_cron_job(job, next_delay))
                print(f"[CronManager] Restored cron job {job.id}: {job.cron_expr}")
            
            print(f"[CronManager] Loaded {len(self._jobs)} jobs from {self._persistence_path}")
        except Exception as e:
            print(f"[CronManager] Failed to load jobs from disk: {e}")
    
    async def _save_to_disk(self):
        """Persist current jobs to disk."""
        try:
            async with self._lock:
                data = {"jobs": [job.to_dict() for job in self._jobs.values()]}
            
            with open(self._persistence_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[CronManager] Failed to save jobs to disk: {e}")
    
    def _normalize_cron_expr(self, cron_expr: str) -> str:
        """Normalize cron expression to 6-field format (with seconds)."""
        expr = cron_expr.strip()
        
        # Handle special expressions
        if expr.startswith("@"):
            if expr.lower() in self.SPECIAL_EXPRESSIONS:
                return self.SPECIAL_EXPRESSIONS[expr.lower()]
            else:
                raise ValueError(f"Unknown special cron expression: {expr}")
        
        parts = expr.split()
        
        # Validate and normalize to 6 fields
        if len(parts) == 5:
            # 5-field format (minute hour day month weekday) - add * for seconds to match minute-level precision
            return f"* {expr}"
        elif len(parts) == 6:
            # Already 6-field format (second minute hour day month weekday)
            return expr
        else:
            raise ValueError(f"Invalid cron expression: expected 5 or 6 fields, got {len(parts)}")
    
    def _get_next_delay(self, cron_expr: str) -> float:
        """Calculate delay in seconds until next execution."""
        try:
            normalized = self._normalize_cron_expr(cron_expr)
            now = datetime.now()
            cron = croniter(normalized, now)
            next_time = cron.get_next(datetime)
            delay = (next_time - now).total_seconds()
            # Ensure minimum delay of 0.1 seconds to avoid immediate execution
            return max(delay, 0.1)
        except Exception as e:
            print(f"[CronManager] Error parsing cron expression '{cron_expr}': {e}")
            raise ValueError(f"Invalid cron expression: {cron_expr}") from e
    
    async def add_cron(self, cron_expr: str, task_description: str, job_id: str = None) -> str:
        """
        Add a new cron job.
        
        Args:
            cron_expr: Cron expression string (supports 5-field, 6-field, and special formats)
            task_description: Description of the task to execute
            job_id: Optional job ID (for restore from disk)
            
        Returns:
            The job ID
            
        Raises:
            ValueError: If cron expression is invalid
        """
        # Validate cron expression first
        try:
            self._get_next_delay(cron_expr)
        except ValueError as e:
            raise e
        
        if job_id is None:
            job_id = str(uuid.uuid4())
        
        job = CronJob(job_id, cron_expr, task_description)
        
        async with self._lock:
            self._jobs[job.id] = job
        
        # Calculate initial delay and create scheduled task
        next_delay = self._get_next_delay(cron_expr)
        print(f"[CronManager] Job {job.id} next execution in {next_delay:.2f}s")
        job.task = asyncio.create_task(self._run_cron_job(job, next_delay))
        
        # Persist to disk
        await self._save_to_disk()
        
        print(f"[CronManager] Added cron job {job.id}: {cron_expr} -> {task_description}")
        return job.id
    
    async def del_cron(self, job_id: str) -> bool:
        """
        Delete a cron job by ID.
        
        Args:
            job_id: The job ID to delete
            
        Returns:
            True if deleted, False if not found
        """
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            
            job._cancelled = True
            if job.task and not job.task.done():
                job.task.cancel()
            del self._jobs[job_id]
        
        # Persist to disk
        await self._save_to_disk()
        
        print(f"[CronManager] Deleted cron job {job_id}")
        return True
    
    async def _run_cron_job(self, job: CronJob, initial_delay: float):
        """
        The actual coroutine that runs the cron job.
        Captures CancelledError to properly cancel any pending requests.
        """
        print(f"[CronManager] Job {job.id} starting, initial_delay={initial_delay:.2f}s, expr='{job.cron_expr}'")
        try:
            # Wait for initial delay
            print(f"[CronManager] Job {job.id} waiting {initial_delay:.2f}s for first execution")
            await asyncio.sleep(initial_delay)
            print(f"[CronManager] Job {job.id} first delay complete, starting loop")
            
            while not job._cancelled:
                try:
                    print(f"[CronManager] Job {job.id} executing task...")
                    # Execute the task
                    await self._execute_task(job)
                    print(f"[CronManager] Job {job.id} task execution complete")
                    
                    if job._cancelled:
                        print(f"[CronManager] Job {job.id} cancelled after execution")
                        break
                    
                    # Calculate next delay and wait
                    next_delay = self._get_next_delay(job.cron_expr)
                    print(f"[CronManager] Job {job.id} waiting {next_delay:.2f}s until next execution")
                    await asyncio.sleep(next_delay)
                    
                except asyncio.CancelledError:
                    # Job was cancelled during execution or sleep
                    print(f"[CronManager] Cron job {job.id} cancelled")
                    raise
                except Exception as e:
                    print(f"[CronManager] Error executing cron job {job.id}: {e}")
                    import traceback
                    traceback.print_exc()
                    # On error, still calculate next delay to continue schedule
                    try:
                        next_delay = self._get_next_delay(job.cron_expr)
                        await asyncio.sleep(next_delay)
                    except Exception as e2:
                        print(f"[CronManager] Job {job.id} failed to calculate next delay: {e2}")
                        break
                    
        except asyncio.CancelledError:
            # This catches cancellation during sleep or task execution
            print(f"[CronManager] Cron job {job.id} coroutine cancelled")
            raise
    
    async def list_crons(self) -> List[dict]:
        """
        List all cron jobs.
        
        Returns:
            List of job info dicts
        """
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
    
    async def get_next_run(self, cron_expr: str, count: int = 1) -> List[datetime]:
        """
        Get the next run time(s) for a cron expression.
        
        Args:
            cron_expr: Cron expression string
            count: Number of next run times to return
            
        Returns:
            List of datetime objects representing next run times
        """
        try:
            normalized = self._normalize_cron_expr(cron_expr)
            now = datetime.now()
            cron = croniter(normalized, now)
            return [cron.get_next(datetime) for _ in range(count)]
        except Exception as e:
            print(f"[CronManager] Error getting next run times: {e}")
            raise ValueError(f"Invalid cron expression: {cron_expr}") from e
    
    async def _execute_task(self, job: CronJob):
        """
        Execute the cron task by sending it to the job's associated session.
        Uses retry logic similar to chat interface.
        """
        request_id = None
        try:
            # Get or create session with retry logic (similar to chat interface)
            session = None
            request = None
            success = False
            
            for attempt in range(3):
                # Import agent_runner here to avoid circular import
                from superagent import agent_runner
                session = await self._session_manager.get_or_create_session(
                    CRON_SESSION_ID, 
                    create=True,
                    session_main=agent_runner
                )
                
                if session is None:
                    print(f"[CronManager] Session '{CRON_SESSION_ID}' not available for job {job.id}, attempt {attempt + 1}/3")
                    await asyncio.sleep(0.5)
                    continue
                
                # Create AgentRequest with task description
                if request is None:
                    content = [TextBlock(type="text", text=job.task_description)]
                    request = AgentRequest(
                        session_id=CRON_SESSION_ID,
                        content=content,
                        deepresearch=False
                    )
                    request_id = request.id
                
                # Add request to session
                if await session.add_request(request):
                    success = True
                    break
                
                print(f"[CronManager] Failed to add request for job {job.id}, attempt {attempt + 1}/3")
                await asyncio.sleep(0.5)
            
            if not success or session is None:
                print(f"[CronManager] Failed to execute job {job.id} after 3 attempts")
                return
            
            print(f"[CronManager] Executing job {job.id} for session {CRON_SESSION_ID}: {job.task_description}")
            
            # Wait for response
            response_chunks = []
            while True:
                try:
                    msg = await asyncio.wait_for(request.response_queue.get(), timeout=300.0)
                    if msg is None:
                        break
                    response_chunks.append(msg)
                except asyncio.TimeoutError:
                    print(f"[CronManager] Timeout waiting for response for job {job.id}")
                    break
                except asyncio.CancelledError:
                    # If we're cancelled while waiting for response, cancel the request
                    if request_id:
                        await session.cancel_request(request_id)
                    raise
            
            # Log the response
            response_text = "".join(response_chunks)
            print(f"[CronManager] Job {job.id} response: {response_text[:200]}...")
            
        except asyncio.CancelledError:
            # Propagate cancellation
            if request_id:
                try:
                    session = await self._session_manager.get_or_create_session(CRON_SESSION_ID, create=False)
                    if session:
                        await session.cancel_request(request_id)
                except Exception:
                    pass
            raise
        except Exception as e:
            print(f"[CronManager] Error in _execute_task for job {job.id}: {e}")
