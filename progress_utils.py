"""
Progress bar helpers: flush after each update (avoids freeze in some terminals),
and optional simple newline progress when EDHPARSE_SIMPLE_PROGRESS=1 or stdout isn't a TTY.
"""
import os
import sys

import progressbar

# Use simple one-line-per-update progress when env is set or when not a real TTY
# (Cursor's terminal sometimes freezes with \r-based progress bars)
def _use_simple_progress():
    return os.environ.get("EDHPARSE_SIMPLE_PROGRESS", "").lower() in ("1", "true", "yes") or not sys.stdout.isatty()


class _SimpleProgress:
    """Progress that prints new lines so it never freezes in broken terminals."""

    def __init__(self, maxval, initial_value=0, variables=None):
        self.maxval = maxval
        self.current = initial_value
        self.variables = variables or {}
        self._last_printed = -1
        # Print at most every N updates to avoid spam when maxval is huge
        self._step = max(1, max(1, maxval) // 50) if maxval else 1

    def update(self, value, info=None):
        self.current = value
        if info is not None:
            self.variables["info"] = info
        msg = self.variables.get("info", "")
        # Print on first, last, or every _step updates
        if value == 0 or value >= self.maxval or value - self._last_printed >= self._step:
            self._last_printed = value
            pct = (100 * value / self.maxval) if self.maxval else 0
            # Newline each time so we don't rely on \r (avoids freeze in some terminals)
            print(f"Progress: {value}/{self.maxval} ({pct:.0f}%) {msg}", flush=True)
        sys.stdout.flush()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FlushProgressBar(progressbar.ProgressBar):
    """ProgressBar that flushes stdout after each update so output isn't buffered."""

    def update(self, value, *args, **kwargs):
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
