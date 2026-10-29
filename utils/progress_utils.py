"""
Progress bar helpers: flush after each update (avoids freeze in some terminals),
and optional simple newline progress when EDHPARSE_SIMPLE_PROGRESS=1 or stdout isn't a TTY.
"""
import contextvars
import os
import sys
from typing import Callable, Optional

import progressbar

# Optional sink for web UI / other UIs: sink(current, total, info)
_progress_sink: contextvars.ContextVar[Optional[Callable[[int, int, str], None]]] = contextvars.ContextVar(
    "progress_sink", default=None
)


def set_progress_sink(sink: Optional[Callable[[int, int, str], None]]):
    """Register a callback invoked on every progress update. Returns a token for reset."""
    return _progress_sink.set(sink)


def reset_progress_sink(token) -> None:
    _progress_sink.reset(token)


def notify_progress(current: int, total: int, info: str = "") -> None:
    sink = _progress_sink.get()
    if sink is not None:
        try:
            sink(int(current), int(total), info or "")
        except Exception:
            pass


def _use_simple_progress():
    return os.environ.get("EDHPARSE_SIMPLE_PROGRESS", "").lower() in ("1", "true", "yes") or not sys.stdout.isatty()


def _console_safe(text):
    """Avoid UnicodeEncodeError when printing card names on Windows cp1252 terminals."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    return text.encode(enc, errors="replace").decode(enc)


class _SimpleProgress:
    """Progress that prints new lines so it never freezes in broken terminals."""

    def __init__(self, maxval, initial_value=0, variables=None):
        self.maxval = maxval
        self.current = initial_value
        self.variables = variables or {}
        self._last_printed = -1
        self._step = max(1, max(1, maxval) // 50) if maxval else 1

    def update(self, value, info=None):
        self.current = value
        if info is not None:
            self.variables["info"] = info
        msg = _console_safe(self.variables.get("info", ""))
        notify_progress(value, self.maxval or 0, msg)
        if value == 0 or value >= self.maxval or value - self._last_printed >= self._step:
            self._last_printed = value
            pct = (100 * value / self.maxval) if self.maxval else 0
            print(f"Progress: {value}/{self.maxval} ({pct:.0f}%) {msg}", flush=True)
        sys.stdout.flush()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FlushProgressBar(progressbar.ProgressBar):
    """ProgressBar that flushes stdout after each update so output isn't buffered."""

    def update(self, value, *args, **kwargs):
        info = kwargs.pop("info", None)
        if info is not None:
            if getattr(self, "variables", None) is None:
                self.variables = {}
            self.variables["info"] = info
        msg = _console_safe((self.variables or {}).get("info", ""))
        total = getattr(self, "maxval", None) or getattr(self, "max_value", None) or 0
        try:
            total = int(total)
        except (TypeError, ValueError):
            total = 0
        notify_progress(int(value), total, msg)
        result = super().update(value, *args, **kwargs)
        sys.stdout.flush()
        return result


def progress_bar(maxval, initial_value=0, variables=None, prefix="{variables.info}"):
    """Context manager that yields a progress bar. Flushes after each update; uses simple mode if requested."""
    variables = variables or {"info": "--"}
    if _use_simple_progress():
        return _SimpleProgress(maxval, initial_value=initial_value, variables=variables)
    return _FlushProgressBar(
        prefix=prefix,
        variables=variables,
        maxval=maxval,
        initial_value=initial_value,
    )
