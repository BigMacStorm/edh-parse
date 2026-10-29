"""Thin wrapper around EDHREC commander comparison."""

from __future__ import annotations

from typing import Optional

from clients.session import create_cached_session
from core.config import RunConfig
from edhparse.edhrec_commander_compare import run_compare_edhrec_commanders
from edhparse.services import err_result, ok_result


def run_compare(
    url_a: str,
    url_b: str,
    cfg: Optional[RunConfig] = None,
    *,
    session=None,
    min_inclusion_both_pct: Optional[float] = None,
) -> dict:
    """Compare two EDHREC commander pages and print overlap stats."""
    cfg = cfg or RunConfig()
    sess = session or create_cached_session(fresh_cache=cfg.fresh_cache)
    min_pct = (
        min_inclusion_both_pct
        if min_inclusion_both_pct is not None
        else cfg.compare_min_inclusion_pct
    )
    try:
        run_compare_edhrec_commanders(
            sess,
            url_a,
            url_b,
            min_inclusion_both_pct=min_pct,
        )
    except ValueError as e:
        print(f"Error: {e}")
        return err_result(str(e))
    return ok_result(f"Compared {url_a} vs {url_b}")
