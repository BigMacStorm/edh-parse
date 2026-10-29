"""In-process background jobs with live progress for the web UI."""

from __future__ import annotations

import io
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from utils.progress_utils import reset_progress_sink, set_progress_sink

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="edhparse-job")
_jobs: Dict[str, "Job"] = {}
_lock = threading.Lock()


class _LiveLogStream(io.TextIOBase):
    """Capture stdout/stderr into a buffer and mirror into the job log live."""

    def __init__(self, job: "Job", lock: threading.Lock):
        super().__init__()
        self._buf = io.StringIO()
        self._job = job
        self._lock = lock

    def write(self, s: str) -> int:  # type: ignore[override]
        if not s:
            return 0
        self._buf.write(s)
        with self._lock:
            self._job.log = self._buf.getvalue()
        return len(s)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return self._buf.getvalue()

    @property
    def encoding(self) -> str:
        return "utf-8"


@dataclass
class Job:
    id: str
    title: str
    status: str = "running"  # running | done | error
    current: int = 0
    total: int = 0
    message: str = "Starting…"
    result_html: Optional[str] = None
    error: Optional[str] = None
    log: str = ""
    result: Dict[str, Any] = field(default_factory=dict)

    def snapshot(self) -> Dict[str, Any]:
        # Keep API payloads light: only the tail is useful in the overlay.
        log_tail = self.log[-4000:] if self.log else ""
        return {
            "job_id": self.id,
            "title": self.title,
            "status": self.status,
            "current": self.current,
            "total": self.total,
            "message": self.message,
            "error": self.error,
            "log": log_tail,
            "result_html": self.result_html,
            "result": {
                "ok": self.result.get("ok"),
                "message": self.result.get("message"),
                "outputs": self.result.get("outputs") or [],
            }
            if self.result
            else None,
        }


def _apply_progress(job: Job, current: int, total: int, info: str) -> None:
    job.current = max(0, int(current))
    job.total = max(0, int(total))
    if info:
        job.message = info


def start_job(title: str, work: Callable[[], Dict[str, Any]], render_html: Callable[[Dict[str, Any]], str]) -> str:
    """
    Run ``work`` in a background thread. ``work`` should return a result dict.
    ``render_html(result)`` builds the result page HTML when finished.
    """
    job_id = uuid.uuid4().hex
    job = Job(id=job_id, title=title)
    with _lock:
        _jobs[job_id] = job

    def _run() -> None:
        stream = _LiveLogStream(job, _lock)
        token = set_progress_sink(lambda c, t, info: _apply_progress(job, c, t, info))
        try:
            with redirect_stdout(stream), redirect_stderr(stream):
                job.message = "Starting…"
                result = work()
            job.log = stream.getvalue()
            if isinstance(result, dict):
                result = dict(result)
                result.setdefault("log", job.log)
                job.result = result
                if result.get("ok"):
                    job.status = "done"
                    job.message = result.get("message") or "Done"
                    if job.total > 0:
                        job.current = job.total
                else:
                    job.status = "error"
                    job.error = result.get("error") or result.get("message") or "Failed"
                    job.message = job.error
            else:
                job.status = "done"
                job.result = {"ok": True, "message": str(result), "outputs": [], "log": job.log}
                job.message = "Done"
            try:
                job.result_html = render_html(job.result)
            except Exception as render_err:
                job.status = "error"
                job.error = f"Result render failed: {render_err}"
                job.message = job.error
        except Exception as e:
            job.log = stream.getvalue()
            job.status = "error"
            job.error = str(e)
            job.message = str(e)
            job.result = {
                "ok": False,
                "message": str(e),
                "error": str(e),
                "outputs": [],
                "log": job.log,
                "traceback": traceback.format_exc(),
            }
            try:
                job.result_html = render_html(job.result)
            except Exception:
                job.result_html = None
        finally:
            reset_progress_sink(token)

    _executor.submit(_run)
    return job_id


def get_job(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)
