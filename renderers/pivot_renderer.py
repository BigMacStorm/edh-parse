from __future__ import annotations

import html
from typing import Any, Dict, List
from urllib.parse import quote


def write_pivot_html(*, out_path: str, report: Dict[str, Any]) -> None:
    """Write the deck-pivot commander suggestion HTML report."""
    source = html.escape(str(report.get("source_label") or ""))
    excluded = html.escape(str(report.get("excluded_commander") or ""))
    min_price = float(report.get("min_price") or 0.0)
    total_value = float(report.get("total_expensive_value") or 0.0)
    expensive_count = int(report.get("expensive_card_count") or 0)
    commander_count = int(report.get("commander_count") or 0)
    commanders: List[Dict[str, Any]] = list(report.get("commanders") or [])
    expensive_cards: List[Dict[str, Any]] = list(report.get("expensive_cards") or [])

    exclude_note = (
        f" Current commander excluded: <strong>{excluded}</strong>."
        if excluded
        else ""
    )

    parts: List[str] = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Deck Pivot Suggestions</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #cbd5e1; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; --border: #3f3f4a; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; }
h1 { margin: 0 0 0.2rem; font-size: 1.8rem; }
.subtitle { color: var(--muted); margin: 0.35rem 0 1rem; }
.controls { display: flex; gap: 0.75rem 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }
.controls label { color: var(--muted); font-size: 0.92rem; }
.controls select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.35rem 0.8rem; }
.controls .slider-wrap { display: inline-flex; align-items: center; gap: 0.45rem; background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 0.3rem 0.6rem; }
.controls input[type="range"] { width: 9rem; accent-color: var(--accent); }
.controls .val { font-variant-numeric: tabular-nums; color: var(--text); min-width: 3.8rem; text-align: right; font-size: 0.88rem; }
.controls button { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.35rem 0.85rem; cursor: pointer; }
.controls button:hover { border-color: var(--accent); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 0.6rem; margin: 0.8rem 0 1rem; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.65rem 0.85rem; }
.stat-title { color: var(--muted); font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.02em; margin-bottom: 0.15rem; }
.stat-value { font-size: 1.08rem; font-weight: 600; }
.commander-section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.1rem; margin-bottom: 1rem; }
.commander-section.hidden { display: none !important; }
.commander-header { display: flex; align-items: flex-start; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.35rem; }
.commander-pic-link { display: block; flex: 0 0 auto; }
.commander-pic { width: 220px; height: auto; max-width: 100%; border-radius: 10px; border: 1px solid var(--border); background: #000; }
.commander-info { flex: 1; min-width: 220px; }
.commander-title { margin: 0 0 0.25rem; font-size: 1.25rem; }
.commander-type { color: var(--muted); font-size: 0.92rem; margin: 0 0 0.45rem; }
.commander-oracle { color: var(--text); font-size: 0.88rem; white-space: pre-wrap; background: var(--card); border: 1px solid var(--border); border-radius: 8px; padding: 0.55rem 0.7rem; margin: 0.35rem 0 0.55rem; max-width: 42rem; }
.commander-meta { color: var(--muted); font-size: 0.9rem; }
.commander-meta a { color: var(--accent); text-decoration: none; }
.commander-meta a:hover { text-decoration: underline; }
.metrics { display: flex; flex-wrap: wrap; gap: 0.45rem 0.9rem; margin: 0.45rem 0 0.7rem; font-size: 0.9rem; }
.metric strong { color: var(--green); font-weight: 650; }
.metric .amber { color: var(--amber); }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr)); gap: 0.7rem; margin-top: 0.5rem; }
.card { background: var(--card); border: 1px solid rgba(255,255,255,0.06); border-radius: 9px; padding: 0.6rem; }
.card.hidden-card { display: none !important; }
.card-name { font-weight: 600; margin-bottom: 0.2rem; }
.meta { color: var(--muted); font-size: 0.84rem; margin: 0.12rem 0; }
.price { color: var(--green); font-size: 0.92rem; margin-top: 0.2rem; }
.links { margin-top: 0.3rem; display: flex; gap: 0.65rem; flex-wrap: wrap; }
.links a { color: var(--accent); text-decoration: none; font-size: 0.87rem; }
.links a:hover { text-decoration: underline; }
.empty { color: var(--muted); font-size: 0.92rem; font-style: italic; }
details.expensive-list { margin: 0.5rem 0 1.1rem; background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.55rem 0.85rem; }
details.expensive-list summary { cursor: pointer; color: var(--muted); }
.chip-row { display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.55rem; }
.chip { background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 0.2rem 0.65rem; font-size: 0.82rem; }
code { background: rgba(255,255,255,0.08); border-radius: 5px; padding: 0.1rem 0.35rem; }
</style>
</head>
<body>
<h1>Deck Pivot Suggestions</h1>
<p class="subtitle">Source: <code>"""
        + source
        + """</code>. Expensive cards (≥ $"""
        + f"{min_price:.2f}"
        + """) are scored against EDHREC Top Commanders (price × inclusion%)."""
        + exclude_note
        + """</p>
<div class="stats">
  <div class="stat-card"><div class="stat-title">Expensive cards</div><div class="stat-value">"""
        + str(expensive_count)
        + """</div></div>
  <div class="stat-card"><div class="stat-title">Expensive value</div><div class="stat-value">$"""
        + f"{total_value:.2f}"
        + """</div></div>
  <div class="stat-card"><div class="stat-title">Suggested commanders</div><div class="stat-value" id="visible-commander-count">"""
        + str(commander_count)
        + """</div></div>
  <div class="stat-card"><div class="stat-title">Filter status</div><div class="stat-value" id="filter-status">All shown</div></div>
</div>
"""
    ]

    if expensive_cards:
        parts.append('<details class="expensive-list"><summary>Cards used for scoring</summary><div class="chip-row">')
        for c in expensive_cards:
            price = float(c.get("price") or 0.0)
            qty = int(c.get("quantity") or 1)
            label = html.escape(str(c.get("name") or ""))
            qty_bit = f" ×{qty}" if qty > 1 else ""
            parts.append(f'<span class="chip">{label}{qty_bit} · ${price:.2f}</span>')
        parts.append("</div></details>\n")

    max_cards = max(1, expensive_count)
    parts.append(
        f"""<div class="controls">
  <label for="sort-mode">Sort commanders by</label>
  <select id="sort-mode">
    <option value="score" selected>Weighted score</option>
    <option value="retained">Retained value</option>
    <option value="cards">Card count</option>
    <option value="inclusion">Avg inclusion</option>
  </select>
  <div class="slider-wrap">
    <label for="min-inclusion">Min inclusion</label>
    <input id="min-inclusion" type="range" min="0" max="100" step="0.5" value="0">
    <span class="val" id="min-inclusion-val"></span>
  </div>
  <div class="slider-wrap">
    <label for="min-cards">Min cards</label>
    <input id="min-cards" type="range" min="1" max="{max_cards}" step="1" value="1">
    <span class="val" id="min-cards-val"></span>
  </div>
  <div class="slider-wrap">
    <label for="min-retained-pct">Min retained %</label>
    <input id="min-retained-pct" type="range" min="0" max="100" step="0.5" value="0">
    <span class="val" id="min-retained-pct-val"></span>
  </div>
  <button type="button" id="reset-filters">Reset filters</button>
</div>
<div id="commander-list">
"""
    )

    if not commanders:
        parts.append('<p class="empty">No commander suggestions found. Try lowering --pivot-min-price or using a different decklist.</p>')
    else:
        for row in commanders:
            name = str(row.get("name") or "")
            slug = str(row.get("slug") or "")
            score = float(row.get("weighted_score") or 0.0)
            retained = float(row.get("retained_value") or 0.0)
            retained_pct = float(row.get("retained_pct") or 0.0)
            card_count = int(row.get("card_count") or 0)
            avg_inc = float(row.get("avg_inclusion_pct") or 0.0)
            cards = list(row.get("cards") or [])
            card_pic = str(row.get("card_pic") or "")
            type_line = str(row.get("type_line") or "")
            oracle_text = str(row.get("oracle_text") or "")

            parts.append(
                f'<section class="commander-section" data-score="{score:.6f}" '
                f'data-retained="{retained:.6f}" data-retained-pct="{retained_pct:.6f}" '
                f'data-cards="{card_count}" data-avg-inclusion="{avg_inc:.6f}">'
                f'<div class="commander-header">'
            )
            if card_pic:
                large = card_pic.replace("/normal/", "/large/").replace("/small/", "/large/")
                zoom = card_pic.replace("/small/", "/png/").replace("/normal/", "/png/").replace("/large/", "/png/")
                parts.append(
                    f'<a class="commander-pic-link" href="{html.escape(zoom)}" target="_blank" rel="noopener">'
                    f'<img class="commander-pic" src="{html.escape(large)}" alt="{html.escape(name)}" loading="lazy">'
                    f"</a>"
                )
            parts.append('<div class="commander-info">')
            parts.append(f'<h2 class="commander-title">{html.escape(name)}</h2>')
            if type_line:
                parts.append(f'<p class="commander-type">{html.escape(type_line)}</p>')
            if oracle_text:
                parts.append(f'<div class="commander-oracle">{html.escape(oracle_text)}</div>')
            parts.append(
                f'<div class="commander-meta">'
                f'<a href="https://edhrec.com/commanders/{html.escape(slug)}" target="_blank" rel="noopener">Open EDHREC</a>'
                f'</div>'
                f'<div class="metrics">'
                f'<span class="metric">Score: <strong>${score:.2f}</strong></span>'
                f'<span class="metric">Retained: <strong>${retained:.2f}</strong> '
                f'(<span class="amber">{retained_pct:.1f}%</span> of expensive value)</span>'
                f'<span class="metric">Cards: <strong>{card_count}</strong></span>'
                f'<span class="metric">Avg inclusion: <strong>{avg_inc:.1f}%</strong></span>'
                f'</div>'
                f"</div></div>"
            )

            if not cards:
                parts.append('<p class="empty">No overlapping expensive cards.</p></section>\n')
                continue

            parts.append('<div class="cards">\n')
            for c in cards:
                cname = str(c.get("name") or "")
                price = float(c.get("price") or 0.0)
                inc = float(c.get("inclusion_pct") or 0.0)
                wscore = float(c.get("weighted_score") or 0.0)
                qty = int(c.get("quantity") or 1)
                qty_label = f" ×{qty}" if qty > 1 else ""
                scry = "https://scryfall.com/search?q=" + quote(f'!"{cname}"')
                edh_slug = cname.lower().replace(" ", "-")
                edh = "https://edhrec.com/cards/" + quote(edh_slug)
                parts.append(
                    f'<article class="card" data-inclusion="{inc:.4f}" data-price="{price:.4f}">'
                    f'<div class="card-name">{html.escape(cname)}{qty_label}</div>'
                    f'<div class="meta">Inclusion in this commander: {inc:.1f}%</div>'
                    f'<div class="meta">Contribution score: ${wscore:.2f}</div>'
                    f'<div class="price">Price: ${price:.2f}</div>'
                    f'<div class="links">'
                    f'<a href="{html.escape(scry)}" target="_blank" rel="noopener">Scryfall</a>'
                    f'<a href="{html.escape(edh)}" target="_blank" rel="noopener">EDHREC card</a>'
                    f'</div></article>\n'
                )
            parts.append("</div></section>\n")

    parts.append(
        """</div>
<script>
(function() {
  var sortSel = document.getElementById('sort-mode');
  var minInclusion = document.getElementById('min-inclusion');
  var minCards = document.getElementById('min-cards');
  var minRetainedPct = document.getElementById('min-retained-pct');
  var minInclusionVal = document.getElementById('min-inclusion-val');
  var minCardsVal = document.getElementById('min-cards-val');
  var minRetainedPctVal = document.getElementById('min-retained-pct-val');
  var resetBtn = document.getElementById('reset-filters');
  var visibleCount = document.getElementById('visible-commander-count');
  var filterStatus = document.getElementById('filter-status');
  var list = document.getElementById('commander-list');

  var defaults = {
    sort: 'score',
    minInclusion: 0,
    minCards: 1,
    minRetainedPct: 0
  };

  function fmtPct(v) { return Number(v).toFixed(1) + '%'; }
  function fmtNum(v) { return String(Math.round(Number(v))); }

  function readState() {
    return {
      sort: sortSel.value || 'score',
      minInclusion: parseFloat(minInclusion.value || '0'),
      minCards: parseInt(minCards.value || '1', 10),
      minRetainedPct: parseFloat(minRetainedPct.value || '0')
    };
  }

  function updateLabels(state) {
    minInclusionVal.textContent = fmtPct(state.minInclusion);
    minCardsVal.textContent = fmtNum(state.minCards);
    minRetainedPctVal.textContent = fmtPct(state.minRetainedPct);
  }

  function sortKey(section, mode) {
    if (mode === 'retained') return parseFloat(section.dataset.retained || '0');
    if (mode === 'cards') return parseFloat(section.dataset.cards || '0');
    if (mode === 'inclusion') return parseFloat(section.dataset.avgInclusion || '0');
    return parseFloat(section.dataset.score || '0');
  }

  function apply() {
    var state = readState();
    updateLabels(state);
    var sections = Array.prototype.slice.call(list.querySelectorAll('.commander-section'));
    var shown = 0;

    sections.forEach(function(section) {
      var cards = Array.prototype.slice.call(section.querySelectorAll('.card'));
      var visibleCards = 0;
      cards.forEach(function(card) {
        var inc = parseFloat(card.dataset.inclusion || '0');
        var ok = inc >= state.minInclusion;
        card.classList.toggle('hidden-card', !ok);
        if (ok) visibleCards += 1;
      });

      var retainedPct = parseFloat(section.dataset.retainedPct || '0');
      var show = visibleCards >= state.minCards && retainedPct >= state.minRetainedPct;
      section.classList.toggle('hidden', !show);
      if (show) shown += 1;
    });

    sections.sort(function(a, b) {
      return sortKey(b, state.sort) - sortKey(a, state.sort);
    });
    sections.forEach(function(section) { list.appendChild(section); });

    visibleCount.textContent = String(shown);
    filterStatus.textContent = shown === sections.length ? 'All shown' : (shown + ' matching');
  }

  sortSel.addEventListener('change', apply);
  minInclusion.addEventListener('input', apply);
  minCards.addEventListener('input', apply);
  minRetainedPct.addEventListener('input', apply);
  resetBtn.addEventListener('click', function() {
    sortSel.value = defaults.sort;
    minInclusion.value = String(defaults.minInclusion);
    minCards.value = String(defaults.minCards);
    minRetainedPct.value = String(defaults.minRetainedPct);
    apply();
  });

  apply();
})();
</script>
</body>
</html>
"""
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
