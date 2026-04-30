from __future__ import annotations

import html as html_module
import json
import math
import re
import time
from collections import Counter, defaultdict
from itertools import chain
from typing import Any
from urllib.parse import quote

from utils.html_render_utils import (
    _canonical_color_identity,
    _escape,
    _svg_mana_curve,
    _svg_pie,
)

def _group_decks_by_commander(decks_with_commander):
    """Group decks by commander name, keeping at most one deck per variant."""
    by_commander = defaultdict(list)
    for deck in decks_with_commander:
        by_commander[deck.commander.name].append(deck)

    variant_order = ("Normal", "Budget", "Expensive")
    for name in by_commander:
        # Dedupe by variant: keep first deck per cost so we always show max 3 tiles
        decks = by_commander[name]
        by_variant = {}
        for d in sorted(decks, key=lambda d: (d.cost.value if d.cost else 0)):
            label = d.cost.name if d.cost else "Normal"
            if label not in by_variant:
                by_variant[label] = d
        by_commander[name] = [by_variant.get(v) for v in variant_order if by_variant.get(v)]
    return by_commander

def _build_summary_strip_html(by_commander):
    """Return HTML string containing commander + color identity counts."""
    color_buckets = {
        "Colorless": 0,
        "Mono-color": 0,
        "2-color": 0,
        "3-color": 0,
        "4-color": 0,
        "5-color": 0,
    }
    for cname in by_commander:
        d0 = by_commander[cname][0]
        ci = str(getattr(d0.commander, "color_identity", "") or "")
        color_key = _canonical_color_identity(ci)
        n = len(color_key)
        if n == 0:
            color_buckets["Colorless"] += 1
        elif n == 1:
            color_buckets["Mono-color"] += 1
        elif n == 2:
            color_buckets["2-color"] += 1
        elif n == 3:
            color_buckets["3-color"] += 1
        elif n == 4:
            color_buckets["4-color"] += 1
        else:
            color_buckets["5-color"] += 1

    summary_lines = [f"<strong>{len(by_commander)}</strong> commanders"]
    for label, count in color_buckets.items():
        if count > 0:
            summary_lines.append(f"{label}: {count}")

    return '<div class="summary-strip">' + " · ".join(summary_lines) + "</div>\n"

def _build_global_stats_html(by_commander):
    """Return HTML string containing the global commander mana curve SVG."""
    global_curve = Counter()
    for cname in by_commander:
        d0 = by_commander[cname][0]
        cmc_val = getattr(getattr(d0, "commander", None), "cmc", 0) or 0
        try:
            cmc_f = float(cmc_val)
        except (TypeError, ValueError):
            cmc_f = 0.0
        bucket = int(round(cmc_f))
        if bucket < 0:
            bucket = 0
        if bucket > 7:
            bucket = 7
        global_curve[bucket] += 1

    # _svg_mana_curve expects string keys "0".."7"
    global_curve_dict = {str(k): v for k, v in global_curve.items()}
    global_curve_svg = _svg_mana_curve(global_curve_dict) if global_curve_dict else ""
    if not global_curve_svg:
        return ""

    return (
        '<div class="global-stats">'
        '<div class="global-stats-card">'
        '<h2 class="global-stats-title">Commanders by mana value</h2>'
        f'<div class="mana-curve-wrap global-mana-curve" title="Commander mana value distribution">{global_curve_svg}</div>'
        "</div>"
        "</div>\n"
    )

def _build_filter_strip_html(by_commander):
    """Return HTML for the subtype + color dropdown filter row."""
    subtype_counts = Counter()
    color_counts = Counter()
    for cname in by_commander:
        decks = by_commander[cname]
        d0 = decks[0]
        taglinks = getattr(d0, "taglinks", None) or []
        for t in taglinks[:3]:
            label = (
                t.get("value")
                or t.get("label")
                or (t.get("slug", "").replace("-", " ").title() if t.get("slug") else None)
            )
            if label:
                subtype_counts[label] += 1
        ci = str(getattr(d0.commander, "color_identity", "") or "")
        color_key = _canonical_color_identity(ci)
        color_counts[color_key] += 1

    subtype_opts = ['<option value="">All subtypes</option>']
    for label in sorted(subtype_counts.keys(), key=lambda x: (-subtype_counts[x], x)):
        count = subtype_counts[label]
        subtype_opts.append(
            f'<option value="{_escape(label)}">{_escape(label)} ({count})</option>'
        )

    color_labels = {"": "Colorless", "W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
    color_opts = ['<option value="">All colors</option>']
    for color_key in sorted(color_counts.keys(), key=lambda x: (len(x), x)):
        count = color_counts[color_key]
        label = color_labels.get(color_key, color_key) if color_key else "Colorless"
        color_opts.append(
            f'<option value="{_escape(color_key)}">{_escape(label)} ({count})</option>'
        )

    return (
        '<div class="filter-row pdf-hint">'
        '<label for="filter-subtype">Subtype:</label>'
        '<select id="filter-subtype" class="filter-select" aria-label="Filter by subtype">'
        + "".join(subtype_opts)
        + "</select>"
        '<label for="filter-color">Color:</label>'
        '<select id="filter-color" class="filter-select" aria-label="Filter by color identity">'
        + "".join(color_opts)
        + "</select>"
        "</div>\n"
    )

def _write_summary_html_impl(collection, path="outputs/summary.html", used_manabox=False):
    """Write a single self-contained summary webpage of all decks."""
    decks_with_commander = [d for d in collection.decks if d.commander is not None]
    if not decks_with_commander:
        return
    # Prepare computed data once; rendering is mostly string concatenation below.
    by_commander = _group_decks_by_commander(decks_with_commander)
    summary_html = _build_summary_strip_html(by_commander)
    global_stats_html = _build_global_stats_html(by_commander)
    filter_strip_html = _build_filter_strip_html(by_commander)

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EDH Deck Collection Summary</title>
<style>
:root { --bg: #0f0f14; --surface: #1a1c24; --card: #22242c; --text: #f0f0f5; --muted: #a1a1aa; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; --border: #3f3f4a; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', 'SF Pro Text', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; max-width: 1600px; margin-left: auto; margin-right: auto; }
h1 { font-size: 1.75rem; margin-bottom: 0.2rem; color: var(--text); font-weight: 700; letter-spacing: -0.02em; }
.manabox-note { font-size: 0.85rem; color: var(--muted); margin-bottom: 1rem; line-height: 1.45; }
.commander-section { background: var(--surface); border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.5rem; box-shadow: 0 4px 20px rgba(0,0,0,0.25); border: 1px solid var(--border); transition: opacity 0.2s ease; }
.commander-section:has(.exclude-cb:checked) { opacity: 0.55; }
.commander-section h2 { margin: 0 0 1rem; font-size: 1.25rem; font-weight: 700; color: var(--text); letter-spacing: -0.02em; padding-bottom: 0.4rem; border-bottom: 1px solid var(--border); text-align: left; }
.flex-row { display: flex; align-items: stretch; gap: 1.5rem; flex-wrap: wrap; }
.commander-pic-wrap { flex-shrink: 0; }
.commander-art-shell { display: flex; align-items: center; gap: 0.4rem; }
.commander-art-nav { width: 1.8rem; height: 2rem; border-radius: 999px; border: 1px solid var(--border); background: rgba(24,24,32,0.9); color: var(--muted); cursor: pointer; font-size: 1rem; line-height: 1; }
.commander-art-nav:hover { color: var(--text); border-color: var(--accent); }
.commander-art-nav:disabled { opacity: 0.45; cursor: default; }
.commander-pic { width: 220px; height: 308px; object-fit: contain; border-radius: 8px; border: 1px solid var(--border); box-shadow: 0 2px 10px rgba(0,0,0,0.25); }
.commander-art-count { margin-top: 0.25rem; font-size: 0.72rem; color: var(--muted); text-align: center; min-height: 1em; }
.commander-meta { flex: 0 1 auto; min-width: 220px; max-width: 380px; }
.commander-meta p { margin: 0.25rem 0; color: var(--muted); font-size: 0.85rem; }
.commander-meta a { color: var(--accent); text-decoration: none; font-weight: 500; }
.commander-meta a:hover { text-decoration: underline; }
.oracle-text { font-size: 0.8rem; white-space: pre-wrap; max-width: 100%; line-height: 1.4; margin: 0.5rem 0; padding: 0.6rem 0.75rem; background: var(--card); border-radius: 6px; border: 1px solid var(--border); color: #ffffff !important; }
.commander-meta .oracle-text { color: #ffffff !important; }
.summary-strip { font-size: 0.85rem; color: var(--muted); margin-bottom: 1rem; padding: 0.5rem 0; }
.top-tags { margin-top: 0.4rem; }
.top-tag-line { font-size: 0.95rem; font-weight: 700; color: var(--text); line-height: 1.35; }
.commander-right { flex: 1 1 400px; min-width: 0; max-width: 880px; display: flex; flex-direction: column; gap: 0.75rem; border-left: 1px solid var(--border); padding-left: 1.25rem; }
.deck-tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin: 0; padding: 0; justify-content: flex-start; }
.deck-tile { background: var(--card); border-radius: 10px; padding: 0.75rem 1rem; min-width: 200px; flex: 1 1 220px; max-width: 280px; border: 1px solid var(--border); transition: border-color 0.15s ease; }
.deck-tile:hover { border-color: var(--accent); }
.deck-tile h3 { margin: 0 0 0.4rem; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; }
.deck-tile h3 a { color: var(--accent); text-decoration: none; }
.deck-tile h3 a:hover { text-decoration: underline; }
.tile-charts { display: flex; align-items: center; gap: 0.5rem; justify-content: flex-start; flex-wrap: nowrap; }
.deck-tile .pie-wrap { width: 72px; height: 72px; margin: 0; cursor: pointer; flex-shrink: 0; line-height: 0; }
.deck-tile .pie-wrap .pie-svg { display: block; width: 72px; height: 72px; }
.deck-tile .pie-wrap path { cursor: pointer; transition: filter 0.15s ease; }
.deck-tile .pie-wrap path:hover { filter: brightness(1.15); }
.mana-curve-wrap { flex-shrink: 0; align-self: center; }
.mana-curve-svg { display: block; }
.deck-tile .pie-legend { margin-top: 0.2rem; }
.section-footer { margin-top: 0.75rem; padding-top: 0.5rem; border-top: 1px solid var(--border); text-align: right; }
.exclude-label { font-size: 0.75rem; color: var(--muted); cursor: pointer; }
.exclude-label input { margin-left: 0.25rem; }
.export-urls-btn { background: var(--accent); color: var(--bg); border: none; border-radius: 8px; padding: 0.5rem 1rem; font-size: 0.9rem; cursor: pointer; font-weight: 600; margin-top: 1rem; }
.export-urls-btn:hover { filter: brightness(1.1); }
.toast { position: fixed; bottom: 1.5rem; left: 50%; transform: translateX(-50%); background: var(--card); color: var(--text); padding: 0.5rem 1rem; border-radius: 8px; border: 1px solid var(--border); box-shadow: 0 4px 12px rgba(0,0,0,0.3); font-size: 0.9rem; z-index: 9999; opacity: 0; transition: opacity 0.2s ease; pointer-events: none; }
.toast.show { opacity: 1; }
.back-to-top-btn { position: fixed; right: 1.75rem; bottom: 1.75rem; width: 3rem; height: 3rem; border-radius: 999px; border: 1px solid var(--border); background: var(--accent); color: var(--bg); font-size: 0.85rem; font-weight: 700; display: none; align-items: center; justify-content: center; cursor: pointer; box-shadow: 0 4px 12px rgba(0,0,0,0.4); z-index: 9000; }
.back-to-top-btn.show { display: flex; }
.back-to-top-btn:hover { filter: brightness(1.1); }
.deck-tile .stat { font-size: 0.75rem; margin: 0.2rem 0; display: flex; justify-content: space-between; align-items: center; gap: 0.35rem; }
.deck-tile .stat .val { color: var(--text); font-weight: 600; }
.deck-tile .stat .val a { color: var(--accent); text-decoration: none; font-weight: 600; }
.deck-tile .stat .val a:hover { text-decoration: underline; }
.deck-tile .pie-legend { font-size: 0.6rem; color: var(--muted); margin-top: 0.25rem; line-height: 1.25; text-align: center; }
.combos-section { margin: 0; padding: 0; }
.combos-section h3 { font-size: 0.85rem; margin: 0 0 0.5rem; color: var(--accent); font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; }
.combo-grid { display: flex; flex-wrap: wrap; gap: 0.75rem; list-style: none; padding: 0; margin: 0; }
.combo-box { background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.65rem 0.85rem; font-size: 0.8rem; line-height: 1.4; display: flex; flex-direction: column; gap: 0.35rem; flex: 1 1 220px; min-width: 200px; max-width: 300px; }
.combo-box .combo-cards { font-weight: 600; color: var(--text); }
.combo-box .combo-card-name { cursor: help; }
.combo-box .combo-card-name:hover { color: var(--accent); }
.combo-card-tooltip { position: fixed; z-index: 10000; pointer-events: none; border-radius: 8px; overflow: hidden; box-shadow: 0 8px 24px rgba(0,0,0,0.5); border: 1px solid var(--border); background: var(--card); }
.combo-card-tooltip img { display: block; width: 200px; height: auto; }
.combo-box .combo-results { color: var(--muted); font-size: 0.8rem; }
.combo-box .combo-meta { display: flex; flex-wrap: wrap; gap: 0.5rem 1rem; font-size: 0.8rem; color: var(--muted); }
.combo-box .combo-meta span { white-space: nowrap; }
.combo-box a { color: var(--accent); text-decoration: none; font-weight: 500; margin-top: 0.25rem; }
.combo-box a:hover { text-decoration: underline; }
.combos-empty { font-size: 0.875rem; color: var(--muted); font-style: italic; }
.sort-controls { margin-bottom: 1.25rem; display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; justify-content: space-between; }
.sort-controls-group { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.sort-controls label { color: var(--muted); font-size: 0.9rem; }
.sort-controls select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.4rem 0.85rem; font-size: 0.9rem; cursor: pointer; }
.search-input { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.4rem 0.85rem; font-size: 0.9rem; min-width: 14rem; }
.search-input::placeholder { color: var(--muted); }
.search-input:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 1px rgba(124,58,237,0.5); }
.filter-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 1rem; margin-bottom: 1rem; }
.filter-row label { color: var(--muted); font-size: 0.9rem; }
.filter-row .filter-select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.4rem 0.85rem; font-size: 0.9rem; cursor: pointer; min-width: 10rem; }
.filter-row .filter-select:hover, .filter-row .filter-select:focus { border-color: var(--accent); outline: none; }
.commander-section.filtered-out { display: none !important; }
#commander-list { outline: none; }
.clear-checks-btn, .random-deck-btn { background: rgba(24,24,32,0.9); color: var(--muted); border: 1px solid var(--border); border-radius: 999px; padding: 0.45rem 0.9rem; font-size: 0.9rem; cursor: pointer; font-weight: 500; }
.clear-checks-btn:hover, .random-deck-btn:hover { color: var(--text); border-color: var(--accent); background: var(--card); }
.global-stats { margin-bottom: 1rem; display: flex; flex-wrap: wrap; gap: 1rem; }
.global-stats-card { background: var(--surface); border-radius: 10px; padding: 0.75rem 1rem; border: 1px solid var(--border); flex: 1 1 260px; max-width: 420px; }
.global-stats-title { margin: 0 0 0.25rem; font-size: 0.9rem; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
.global-mana-curve .mana-curve-svg { width: 100%; height: auto; }
.global-mana-curve .mana-curve-svg rect.mana-bar-selected { stroke: var(--accent); stroke-width: 1; }
.scryfall-art-btn { display: inline-block; margin-top: 0.4rem; padding: 0.15rem 0.5rem; font-size: 0.7rem; border-radius: 999px; border: 1px solid var(--border); background: rgba(15,15,20,0.9); color: var(--muted); text-decoration: none; }
.scryfall-art-btn:hover { color: var(--text); border-color: var(--accent); background: var(--card); }
.commander-section.random-highlight { animation: random-highlight 1.5s ease; }
@keyframes random-highlight { 0% { box-shadow: 0 0 0 3px var(--accent); } 100% { box-shadow: 0 4px 20px rgba(0,0,0,0.25); } }
@media print {
  body { background: #fff; color: #111; padding: 0.5rem; }
  .sort-controls, .pdf-hint, .toast, .back-to-top-btn { display: none !important; }
  .manabox-note { margin-bottom: 0.5rem; }
  .commander-section { break-inside: avoid; box-shadow: none; border: 1px solid #ccc; }
  .deck-tile { break-inside: avoid; border-color: #ddd; }
  .oracle-text { background: #f5f5f5; border-color: #ddd; }
  .combo-card-tooltip { display: none !important; }
  :root { --bg: #fff; --surface: #f5f5f5; --card: #eee; --text: #111; --muted: #444; --border: #ddd; }
}
</style>
</head>
<body>
<h1>EDH Deck Collection Summary</h1>
<p class="manabox-note">""" + (
        "Owned cards loaded from Manabox (Stock/Binder)."
        if used_manabox
        else "To mark owned cards and reduce &quot;Not owned&quot; cost, run with <code>-mb your_manabox_export.csv</code>. Use a CSV with columns Name (or Card), Quantity (or Qty), and Binder Name (or Binder); only rows in binders named Stock, Binder, or Secret are included."
    ) + """</p>
<div class="sort-controls">
<div class="sort-controls-group">
<label for="sort">Sort by:</label>
<select id="sort" aria-label="Sort commanders">
<option value="name-asc">Commander name (A–Z)</option>
<option value="name-desc">Commander name (Z–A)</option>
<option value="owned-desc">Most owned cards</option>
<option value="owned-asc">Least owned cards</option>
<option value="total-asc">Lowest total cost</option>
<option value="total-desc">Highest total cost</option>
<option value="notowned-asc">Lowest not-owned cost</option>
<option value="notowned-desc">Highest not-owned cost</option>
</select>
</div>
<div class="sort-controls-group">
<label for="search-box">Search:</label>
<input id="search-box" class="search-input" type="search" placeholder="Search name or rules text">
</div>
<button type="button" class="clear-checks-btn pdf-hint" id="clear-checks-btn">Clear all checks</button>
<button type="button" class="random-deck-btn pdf-hint" id="random-deck-btn">Random deck</button>
</div>
""" + summary_html + "\n" + global_stats_html + filter_strip_html + """
<div id="commander-list">
""",
    ]
    for cname in sorted(by_commander.keys()):
        decks = by_commander[cname]
        d0 = decks[0]
        edhr_slug = getattr(d0, "edhr_slug", None) or ""
        pic = (d0.commander.card_pic or "").replace("/normal/", "/large/").replace("/small/", "/large/")
        if not pic and d0.commander.card_pic:
            pic = d0.commander.card_pic
        type_line = (d0.commander.type_line or "").replace("Legendary Creature — ", "")
        total0 = d0.get_cost()
        not_owned0 = d0.get_cost(only_not_owned=True)
        count0 = d0.get_card_count()
        owned0 = d0.get_owned_count()
        scryfall_base = "https://scryfall.com/search?q=" + quote(f'!"{cname}"')
        scryfall_url = scryfall_base
        scryfall_art_url = scryfall_base + "&unique=art"
        # Top 3 ways this deck is built (from taglinks) - compute before section tag for data-subtypes
        taglinks = getattr(d0, "taglinks", None) or []
        top_tags = []
        for t in taglinks[:3]:
            label = t.get("value") or t.get("label") or (t.get("slug", "").replace("-", " ").title() if t.get("slug") else None)
            if label:
                top_tags.append(label)
        subtypes_attr = _escape("|".join(top_tags)) if top_tags else ""
        ci = str(getattr(d0.commander, "color_identity", "") or "")
        color_key = _canonical_color_identity(ci)
        color_attr = _escape(color_key) if color_key else ""
        cmc_val = getattr(getattr(d0, "commander", None), "cmc", 0) or 0
        try:
            cmc_f = float(cmc_val)
        except (TypeError, ValueError):
            cmc_f = 0.0
        cmc_bucket = int(round(cmc_f))
        if cmc_bucket < 0:
            cmc_bucket = 0
        if cmc_bucket > 7:
            cmc_bucket = 7
        search_chunks = [cname, type_line, ci, getattr(d0.commander, "oracle_text", "") or ""]
        if top_tags:
            search_chunks.extend(top_tags)
        search_text = " ".join(search_chunks).lower()
        html_parts.append(
            f'<section class="commander-section" data-commander="{_escape(cname)}" data-edhr-slug="{_escape(edhr_slug)}" '
            f'data-total="{total0:.2f}" data-notowned="{not_owned0:.2f}" data-owned="{owned0}" data-count="{count0}"'
            + (f' data-subtypes="{subtypes_attr}"' if subtypes_attr else "")
            + (f' data-color-identity="{color_attr}"' if color_attr else "")
            + f' data-cmc="{cmc_bucket}"'
            + f' data-search="{_escape(search_text)}"'
            + ">\n"
        )
        html_parts.append(f'<h2>{_escape(cname)}</h2>\n')
        html_parts.append('<div class="flex-row">\n')
        html_parts.append('<div class="commander-pic-wrap">\n')
        if pic:
            html_parts.append('<div class="commander-art-shell">\n')
            html_parts.append('<button type="button" class="commander-art-nav commander-art-prev pdf-hint" aria-label="Previous commander printing">◀</button>\n')
            html_parts.append(f'<img class="commander-pic" src="{_escape(pic)}" alt="{_escape(cname)}" loading="lazy" data-commander-name="{_escape(cname)}" data-art-index="0" data-art-total="1">\n')
            html_parts.append('<button type="button" class="commander-art-nav commander-art-next pdf-hint" aria-label="Next commander printing">▶</button>\n')
            html_parts.append('</div>\n')
            html_parts.append('<div class="commander-art-count" aria-live="polite"></div>\n')
            html_parts.append(f'<a class="scryfall-art-btn" href="{_escape(scryfall_art_url)}" target="_blank" rel="noopener">Art</a>\n')
        html_parts.append("</div>\n<div class=\"commander-meta\">\n")
        html_parts.append(f'<p><strong>Type</strong> {_escape(type_line)}</p>\n')
        if d0.commander.color_identity:
            html_parts.append(f'<p><strong>Color identity</strong> {_escape(str(d0.commander.color_identity))}</p>\n')
        if d0.commander.oracle_text:
            html_parts.append(f'<p class="oracle-text">{_escape(d0.commander.oracle_text).replace(chr(10), "<br>")}</p>\n')
        html_parts.append(
            f'<p><a href="{_escape(scryfall_url)}" target="_blank" rel="noopener">View on Scryfall</a></p>\n'
        )
        if top_tags:
            html_parts.append('<div class="top-tags">\n')
            for label in top_tags:
                html_parts.append(f'<div class="top-tag-line">{_escape(label)}</div>\n')
            html_parts.append('</div>\n')
        html_parts.append("</div>\n<div class=\"commander-right\">\n<div class=\"deck-tiles\">\n")
        for deck in decks:
            cost_label = deck.cost.name if deck.cost else "Normal"
            chart = deck.chart
            lands = getattr(chart, "Land", 0) if chart else 0
            creatures = getattr(chart, "Creature", 0) if chart else 0
            artifacts = getattr(chart, "Artifact", 0) if chart else 0
            instants = getattr(chart, "Instant", 0) if chart else 0
            sorceries = getattr(chart, "Sorcery", 0) if chart else 0
            enchantments = getattr(chart, "Enchantment", 0) if chart else 0
            planeswalkers = getattr(chart, "Planeswalker", 0) if chart else 0
            battles = getattr(chart, "Battle", 0) if chart else 0
            other = planeswalkers + battles
            total = deck.get_cost()
            not_owned = deck.get_cost(only_not_owned=True)
            count = deck.get_card_count()
            owned_count = deck.get_owned_count()
            segments = [
                ("Land", lands, "#e8c547"),
                ("Creature", creatures, "#7ac74f"),
                ("Artifact", artifacts, "#c9b037"),
                ("Instant", instants, "#5c9dd4"),
                ("Sorcery", sorceries, "#c43b3b"),
                ("Enchantment", enchantments, "#a3c263"),
                ("Other", other, "#9d9da1"),
            ]
            total_seg = sum(s[1] for s in segments)
            if total_seg == 0:
                total_seg = 1
            pie_d = _svg_pie(segments, total_seg)
            mana_curve = getattr(deck, "mana_curve", None) or {}
            curve_svg = _svg_mana_curve(mana_curve)
            legend_parts = [f"{_escape(label)} {val}" for label, val, _ in segments if val > 0]
            pie_legend = " · ".join(legend_parts) if legend_parts else "—"
            slug = getattr(deck, "edhr_slug", None) or edhr_slug
            if cost_label == "Budget":
                edhrec_url = f"https://edhrec.com/commanders/{slug}/budget" if slug else "#"
            elif cost_label == "Expensive":
                edhrec_url = f"https://edhrec.com/commanders/{slug}/expensive" if slug else "#"
            else:
                edhrec_url = f"https://edhrec.com/commanders/{slug}" if slug else "#"
            html_parts.append(
                f'<div class="deck-tile"><h3><a href="{_escape(edhrec_url)}" target="_blank" rel="noopener">{_escape(cost_label)}</a></h3>'
                f'<div class="tile-charts"><div class="pie-wrap">{pie_d}</div>'
                + (f'<div class="mana-curve-wrap" title="Mana curve">{curve_svg}</div>' if curve_svg else '')
                + f'</div><div class="pie-legend">{pie_legend}</div>'
                f'<div class="stat"><span>Total</span><span class="val"><a href="{_escape(edhrec_url)}" target="_blank" rel="noopener">${total:.2f}</a></span></div>'
                f'<div class="stat"><span>Not owned</span><span class="val">${not_owned:.2f}</span></div>'
                f'<div class="stat"><span>Owned</span><span class="val">{owned_count} / {count}</span></div></div>\n'
            )
        html_parts.append("</div>\n")  # close deck-tiles
        # Combos section (per commander) — top 3 only, each in a formatted box
        combos = (collection._fetch_combos(edhr_slug, max_combos=3) if edhr_slug else [])
        html_parts.append('<div class="combos-section"><h3>Combos</h3>\n')
        if combos:
            html_parts.append('<ul class="combo-grid">\n')
            for co in combos:
                first_two = co["cards"][:2]
                if first_two:
                    card_spans = [
                        f'<span class="combo-card-name" data-card-name="{_escape(c)}">{_escape(c)}</span>'
                        for c in first_two
                    ]
                    cards_str = " + ".join(card_spans)
                else:
                    cards_str = "—"
                results_list = co["results"][:5] if co["results"] else []
                results_str = _escape("; ".join(results_list)) if results_list else "—"
                pct_str = f"{co['percentage']}% of decks"
                bracket_str = _escape(str(co["bracket"]))
                url_esc = _escape(co["url"])
                html_parts.append(
                    '<li class="combo-box">\n'
                    f'<div class="combo-cards">{cards_str}</div>\n'
                    f'<div class="combo-results">{results_str}</div>\n'
                    f'<div class="combo-meta"><span>{pct_str}</span><span>Brackets: {bracket_str}</span></div>\n'
                    f'<a href="{url_esc}" target="_blank" rel="noopener">Combo details →</a>\n'
                    '</li>\n'
                )
            html_parts.append("</ul>\n")
        else:
            html_parts.append('<p class="combos-empty">No combo data for this commander on EDHREC.</p>\n')
        html_parts.append("</div>\n")
        html_parts.append(
            '<div class="section-footer">'
            '<label class="exclude-label"><input type="checkbox" class="exclude-cb" aria-label="Exclude from export"> Exclude from export</label></div>\n'
        )
        html_parts.append("</div>\n</div>\n</section>\n")
    html_parts.append("""
</div>
<button type="button" class="export-urls-btn pdf-hint" id="export-urls-btn">Copy EDHREC URLs</button>
<p class="manabox-note pdf-hint" style="font-size:0.8rem; margin-top:0.35rem;">Copies one URL per line for commanders not marked &quot;Exclude from export&quot;.</p>
<div class="toast" id="export-toast" aria-live="polite">Copied to clipboard</div>
<button type="button" class="back-to-top-btn" id="back-to-top-btn" aria-label="Back to top">↑ Top</button>
<script>
(function() {
  var list = document.getElementById('commander-list');
  var sel = document.getElementById('sort');
  var exportBtn = document.getElementById('export-urls-btn');
  var toast = document.getElementById('export-toast');
  var clearChecksBtn = document.getElementById('clear-checks-btn');
  var backToTopBtn = document.getElementById('back-to-top-btn');
  var searchInput = document.getElementById('search-box');
  var selectedCmc = null;
  if (backToTopBtn) {
backToTopBtn.addEventListener('click', function() {
  window.scrollTo({ top: 0, behavior: 'smooth' });
});
window.addEventListener('scroll', function() {
  if (window.scrollY > 200) {
    backToTopBtn.classList.add('show');
  } else {
    backToTopBtn.classList.remove('show');
  }
});
  }
  if (clearChecksBtn) {
clearChecksBtn.addEventListener('click', function() {
  if (confirm('Clear all "Exclude from export" checkboxes?')) {
    var cbs = document.querySelectorAll('.exclude-cb');
    for (var i = 0; i < cbs.length; i++) cbs[i].checked = false;
  }
});
  }
  var randomDeckBtn = document.getElementById('random-deck-btn');
  if (randomDeckBtn && list) {
randomDeckBtn.addEventListener('click', function() {
  var sections = [].slice.call(list.querySelectorAll('.commander-section')).filter(function(s) { return !s.classList.contains('filtered-out'); });
  if (sections.length === 0) return;
  var idx = Math.floor(Math.random() * sections.length);
  var section = sections[idx];
  section.classList.remove('random-highlight');
  section.offsetHeight;
  section.classList.add('random-highlight');
  section.scrollIntoView({ behavior: 'smooth', block: 'center' });
  setTimeout(function() { section.classList.remove('random-highlight'); }, 1500);
});
  }
  if (exportBtn && toast) {
exportBtn.addEventListener('click', function() {
  var sections = list ? list.querySelectorAll('.commander-section') : [];
  var slugs = [];
  for (var i = 0; i < sections.length; i++) {
    var cb = sections[i].querySelector('.exclude-cb');
    if (cb && !cb.checked) {
      var slug = sections[i].getAttribute('data-edhr-slug');
      if (slug) slugs.push('https://edhrec.com/commanders/' + slug);
    }
  }
  var text = slugs.join('\\n');
  navigator.clipboard.writeText(text).then(function() {
    toast.classList.add('show');
    setTimeout(function() { toast.classList.remove('show'); }, 2000);
  });
});
  }
  if (!list || !sel) return;
  function sortSections() {
var opt = sel.value;
var sections = [].slice.call(list.querySelectorAll('.commander-section'));
sections.sort(function(a, b) {
  var an = a.dataset.commander || '', bn = b.dataset.commander || '';
  var at = parseFloat(a.dataset.total) || 0, bt = parseFloat(b.dataset.total) || 0;
  var ano = parseFloat(a.dataset.notowned) || 0, bno = parseFloat(b.dataset.notowned) || 0;
  var aow = parseInt(a.dataset.owned, 10) || 0, bow = parseInt(b.dataset.owned, 10) || 0;
  if (opt === 'name-asc') return an.localeCompare(bn);
  if (opt === 'name-desc') return bn.localeCompare(an);
  if (opt === 'owned-desc') return bow - aow;
  if (opt === 'owned-asc') return aow - bow;
  if (opt === 'total-asc') return at - bt;
  if (opt === 'total-desc') return bt - at;
  if (opt === 'notowned-asc') return ano - bno;
  if (opt === 'notowned-desc') return bno - ano;
  return 0;
});
sections.forEach(function(s) { list.appendChild(s); });
  }
  sel.addEventListener('change', sortSections);

  // Subtype, color, and text search filters: apply together
  function applyFilters() {
var subtypeSel = document.getElementById('filter-subtype');
var colorSel = document.getElementById('filter-color');
var sections = list ? list.querySelectorAll('.commander-section') : [];
var subtype = (subtypeSel && subtypeSel.value) || '';
var color = (colorSel && colorSel.value) || '';
var search = (searchInput && searchInput.value) ? searchInput.value.toLowerCase() : '';
sections.forEach(function(s) {
  var matchSubtype = !subtype || (s.getAttribute('data-subtypes') || '').split('|').indexOf(subtype) !== -1;
  var matchColor = !color || (s.getAttribute('data-color-identity') || '') === color;
  var haystack = (s.getAttribute('data-search') || '').toLowerCase();
  var matchSearch = !search || haystack.indexOf(search) !== -1;
  var cmcAttr = (s.getAttribute('data-cmc') || '');
  var matchCmc = !selectedCmc || cmcAttr === selectedCmc;
  s.classList.toggle('filtered-out', !(matchSubtype && matchColor && matchSearch && matchCmc));
});
  }
  var subtypeSel = document.getElementById('filter-subtype');
  var colorSel = document.getElementById('filter-color');
  if (subtypeSel) subtypeSel.addEventListener('change', applyFilters);
  if (colorSel) colorSel.addEventListener('change', applyFilters);
  if (searchInput) searchInput.addEventListener('input', applyFilters);

  // Clickable commander-CMC bars in global mana curve
  var globalCurve = document.querySelector('.global-mana-curve .mana-curve-svg');
  if (globalCurve) {
var bars = globalCurve.querySelectorAll('rect[data-cmc]');
bars.forEach(function(bar) {
  bar.style.cursor = 'pointer';
  bar.addEventListener('click', function() {
    var val = bar.getAttribute('data-cmc');
    if (selectedCmc === val) {
      selectedCmc = null;
    } else {
      selectedCmc = val;
    }
    bars.forEach(function(b) {
      var isSelected = selectedCmc && b.getAttribute('data-cmc') === selectedCmc;
      b.classList.toggle('mana-bar-selected', !!isSelected);
    });
    applyFilters();
  });
});
  }

  // Commander printing art carousel (uses Scryfall unique=art search).
  var artCache = {};
  function updateCommanderArtUi(section, images, idx) {
var img = section.querySelector('.commander-pic');
var prev = section.querySelector('.commander-art-prev');
var next = section.querySelector('.commander-art-next');
var count = section.querySelector('.commander-art-count');
if (!img || !prev || !next || !count) return;
var total = images.length;
if (total <= 1) {
  prev.disabled = true;
  next.disabled = true;
  count.textContent = '';
  return;
}
if (idx < 0) idx = 0;
if (idx >= total) idx = total - 1;
img.src = images[idx];
img.setAttribute('data-art-index', String(idx));
img.setAttribute('data-art-total', String(total));
count.textContent = (idx + 1) + ' / ' + total;
prev.disabled = false;
next.disabled = false;
  }
  function fetchCommanderArtImages(commanderName, done) {
if (!commanderName) { done([]); return; }
if (artCache[commanderName]) { done(artCache[commanderName]); return; }
var url = 'https://api.scryfall.com/cards/search?q=' + encodeURIComponent('!"' + commanderName + '"') + '&unique=art';
fetch(url).then(function(r) { return r.json(); }).then(function(data) {
  var out = [];
  if (data && data.data && Array.isArray(data.data)) {
    data.data.forEach(function(card) {
      var imgUrl = (card.image_uris && card.image_uris.large) || (card.image_uris && card.image_uris.normal);
      if (!imgUrl && card.card_faces && card.card_faces[0] && card.card_faces[0].image_uris) {
        imgUrl = card.card_faces[0].image_uris.large || card.card_faces[0].image_uris.normal;
      }
      if (imgUrl && out.indexOf(imgUrl) === -1) out.push(imgUrl);
    });
  }
  artCache[commanderName] = out;
  done(out);
}).catch(function() { done([]); });
  }
  document.querySelectorAll('.commander-section').forEach(function(section) {
var img = section.querySelector('.commander-pic');
var prev = section.querySelector('.commander-art-prev');
var next = section.querySelector('.commander-art-next');
if (!img || !prev || !next) return;
var name = img.getAttribute('data-commander-name') || section.getAttribute('data-commander') || '';
if (!name) return;
prev.disabled = true;
next.disabled = true;
fetchCommanderArtImages(name, function(images) {
  if (!images || images.length === 0) {
    updateCommanderArtUi(section, [img.src], 0);
    return;
  }
  updateCommanderArtUi(section, images, 0);
  prev.addEventListener('click', function() {
    var i = parseInt(img.getAttribute('data-art-index') || '0', 10);
    var ni = (i - 1 + images.length) % images.length;
    updateCommanderArtUi(section, images, ni);
  });
  next.addEventListener('click', function() {
    var i = parseInt(img.getAttribute('data-art-index') || '0', 10);
    var ni = (i + 1) % images.length;
    updateCommanderArtUi(section, images, ni);
  });
});
  });

  // Combo card image tooltips via Scryfall
  var tooltipEl = null;
  var tooltipImg = null;
  var tooltipTimeout = null;
  var imageCache = {};
  function ensureTooltip() {
if (tooltipEl) return;
tooltipEl = document.createElement('div');
tooltipEl.className = 'combo-card-tooltip';
tooltipEl.style.display = 'none';
tooltipImg = document.createElement('img');
tooltipImg.alt = '';
tooltipEl.appendChild(tooltipImg);
document.body.appendChild(tooltipEl);
  }
  function showTooltip(url, x, y) {
ensureTooltip();
tooltipImg.src = url;
tooltipEl.style.display = 'block';
tooltipEl.style.left = (x + 15) + 'px';
tooltipEl.style.top = (y + 15) + 'px';
  }
  function hideTooltip() {
if (tooltipTimeout) clearTimeout(tooltipTimeout);
tooltipTimeout = null;
if (tooltipEl) tooltipEl.style.display = 'none';
  }
  function fetchScryfallImage(cardName, cb) {
if (imageCache[cardName]) {
  cb(imageCache[cardName]);
  return;
}
var url = 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(cardName);
fetch(url).then(function(r) { return r.json(); }).then(function(data) {
  var imgUrl = (data.image_uris && data.image_uris.normal) || (data.card_faces && data.card_faces[0] && data.card_faces[0].image_uris && data.card_faces[0].image_uris.normal);
  if (imgUrl) imageCache[cardName] = imgUrl;
  cb(imgUrl || null);
}).catch(function() { cb(null); });
  }
  var hoveredComboEl = null;
  document.addEventListener('mouseover', function(e) {
var el = e.target;
if (!el || !el.classList || !el.classList.contains('combo-card-name')) return;
var name = el.getAttribute('data-card-name');
if (!name) return;
hoveredComboEl = el;
if (tooltipTimeout) clearTimeout(tooltipTimeout);
tooltipTimeout = setTimeout(function() {
  tooltipTimeout = null;
  var x = e.clientX, y = e.clientY;
  fetchScryfallImage(name, function(url) {
    if (url && hoveredComboEl === el) showTooltip(url, x, y);
  });
}, 200);
  });
  document.addEventListener('mouseout', function(e) {
var el = e.target;
if (!el || !el.classList || !el.classList.contains('combo-card-name')) return;
hoveredComboEl = null;
if (tooltipTimeout) clearTimeout(tooltipTimeout);
tooltipTimeout = setTimeout(hideTooltip, 100);
  });
  document.addEventListener('mousemove', function(e) {
if (tooltipEl && tooltipEl.style.display === 'block') {
  tooltipEl.style.left = (e.clientX + 15) + 'px';
  tooltipEl.style.top = (e.clientY + 15) + 'px';
}
  });
})();
</script>
</body>
</html>""")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("".join(html_parts))
        print(f"Wrote summary to {path}")
    except OSError as e:
        print(f"Error writing summary HTML: {e}")

def write_summary_html(collection: Any, path: str = "outputs/summary.html", used_manabox: bool = False) -> None:
    """Renderer entrypoint for EDH deck collection summary HTML."""
    _write_summary_html_impl(collection, path=path, used_manabox=used_manabox)


__all__ = ["write_summary_html"]
