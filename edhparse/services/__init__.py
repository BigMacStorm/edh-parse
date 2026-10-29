"""Business-logic services invoked by the CLI (and later Typer / web UI)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def ok_result(
    message: str = "",
    outputs: Optional[List[str]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    return {"ok": True, "message": message, "outputs": outputs or [], **extra}


def err_result(
    message: str,
    *,
    error: Optional[str] = None,
    outputs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    return {
        "ok": False,
        "message": message,
        "outputs": outputs or [],
        "error": error or message,
    }
