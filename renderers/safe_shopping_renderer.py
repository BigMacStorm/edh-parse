from __future__ import annotations

import html
from typing import Any, Dict, List
from urllib.parse import quote


def write_safe_shopping_html(
    *,
    out_path: str,
    rows: List[Dict[str, Any]],
    inclusion_cutoff: float,
    synergy_cutoff: float,
    min_price: float,
    default_sort: str,
    has_ownership_data: bool,
    stock_csv: str,
    binder_names: List[str],
) -> None:
    sort_mode = "price" if default_sort == "price" else "inclusion"
    note = (
        f"Owned status from CSV: <code>{html.escape(stock_csv)}</code> "
        f"(binders: {html.escape(', '.join(binder_names))})."
        if has_ownership_data
        else "No ownership CSV supplied; all cards shown as not owned."
    )

    parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Safe Commander Shopping List</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #cbd5e1; --accent: #7c3aed; --green: #22c55e; --border: #3f3f4a; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; }
h1 { margin: 0 0 0.2rem; font-size: 1.8rem; }
.subtitle { color: var(--muted); margin-bottom: 1rem; }
.controls { display: flex; gap: 0.75rem 1rem; align-items: center; flex-wrap: wrap; margin-bottom: 1rem; }
.controls label { color: var(--muted); font-size: 0.92rem; }
.controls select { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.35rem 0.8rem; }
.controls input[type="number"] { width: 7rem; background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.35rem 0.8rem; }
.controls .slider-wrap { display: inline-flex; align-items: center; gap: 0.45rem; background: var(--card); border: 1px solid var(--border); border-radius: 999px; padding: 0.3rem 0.6rem; }
.controls input[type="range"] { width: 9rem; accent-color: var(--accent); }
.controls .val { font-variant-numeric: tabular-nums; color: var(--text); min-width: 3.5rem; text-align: right; font-size: 0.88rem; }
.controls button { background: var(--card); color: var(--text); border: 1px solid var(--border); border-radius: 999px; padding: 0.35rem 0.85rem; cursor: pointer; }
.controls button:hover { border-color: var(--accent); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 0.6rem; margin: 0.8rem 0 1rem; }
.stat-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 0.6rem 0.8rem; }
.stat-title { color: var(--muted); font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.02em; margin-bottom: 0.15rem; }
.stat-value { font-size: 1.05rem; font-weight: 600; }
.commander-section { background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 1rem 1.1rem; margin-bottom: 1rem; }
.commander-header { display: flex; justify-content: space-between; gap: 0.8rem; flex-wrap: wrap; margin-bottom: 0.5rem; }
.commander-title { margin: 0; font-size: 1.2rem; }
.commander-meta { color: var(--muted); font-size: 0.9rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(235px, 1fr)); gap: 0.7rem; margin-top: 0.65rem; }
.card { background: var(--card); border: 1px solid rgba(255,255,255,0.06); border-radius: 9px; padding: 0.6rem; }
.card.owned { border-color: rgba(34,197,94,0.5); }
.card.hidden-owned { display: none !important; }
.card-name { font-weight: 600; margin-bottom: 0.2rem; }
.meta { color: var(--muted); font-size: 0.84rem; margin: 0.15rem 0; }
.price { color: var(--green); font-size: 0.92rem; margin-top: 0.2rem; }
.links { margin-top: 0.3rem; display: flex; gap: 0.65rem; flex-wrap: wrap; }
.links a { color: var(--accent); text-decoration: none; font-size: 0.87rem; }
.links a:hover { text-decoration: underline; }
.empty { color: var(--muted); font-size: 0.92rem; font-style: italic; }
code { background: rgba(255,255,255,0.08); border-radius: 5px; padding: 0.1rem 0.35rem; }
</style>
</head>
<body>
<h1>Safe Commander Shopping List</h1>
<p class="subtitle">All card data is loaded once; filters below update the view instantly.</p>
<p class="subtitle">"""
        + note
        + """</p>
<div class="controls">
  <label for="sort-mode">Sort cards by</label>
  <select id="sort-mode">
    <option value="inclusion">Inclusion %</option>
    <option value="price">Price</option>
  </select>
  <label><input type="checkbox" id="hide-owned"> Hide owned cards</label>
  <div class="slider-wrap">
    <label for="min-inclusion">Inclusion</label>
    <input id="min-inclusion" type="range" min="0" max="100" step="0.5" value=""" + f'"{inclusion_cutoff:.1f}"' + """>
    <span class="val" id="min-inclusion-val"></span>
  </div>
  <div class="slider-wrap">
    <label for="min-synergy">Synergy</label>
    <input id="min-synergy" type="range" min="0" max="100" step="0.5" value=""" + f'"{synergy_cutoff:.1f}"' + """>
    <span class="val" id="min-synergy-val"></span>
  </div>
  <div class="slider-wrap">
    <label for="min-price">Min $</label>
    <input id="min-price" type="range" min="0" max="200" step="0.5" value=""" + f'"{min_price:.2f}"' + """>
    <span class="val" id="min-price-val"></span>
  </div>
  <button type="button" id="reset-filters">Reset filters</button>
  <button type="button" id="export-moxfield" title="Plain list: one line per unique card (1 Card Name). Respects current filters.">Download for Moxfield</button>
  <button type="button" id="copy-moxfield" title="Copy the same list to the clipboard">Copy for Moxfield</button>
</div>
<div class="stats">
  <div class="stat-card">
    <div class="stat-title">Baseline (all data)</div>
    <div class="stat-value" id="baseline-stats">—</div>
  </div>
  <div class="stat-card">
    <div class="stat-title">Current filter</div>
    <div class="stat-value" id="current-stats">—</div>
  </div>
</div>
<div id="commander-list">
"""
    ]

    for row in rows:
        name = row["name"]
        slug = row["slug"]
        cards = row["cards"]
        owned_count = row["owned_count"]
        card_count = row["card_count"]
        owned_pct = row["owned_pct"]
        parts.append(
            f'<section class="commander-section" data-commander="{html.escape(name)}">'
            f'<div class="commander-header"><h2 class="commander-title">{html.escape(name)}</h2>'
            f'<div class="commander-meta">{owned_count}/{card_count} owned ({owned_pct:.1f}%)</div></div>'
        )
        parts.append(
            f'<div class="commander-meta"><a href="https://edhrec.com/commanders/{html.escape(slug)}" '
            'target="_blank" rel="noopener">Open EDHREC</a></div>'
        )
        if not cards:
            parts.append('<p class="empty">No cards met the inclusion cutoff for this commander.</p></section>\n')
            continue

        parts.append('<div class="cards">\n')
        for c in cards:
            price = c["price"]
            price_num = float(price) if isinstance(price, (int, float)) and price is not None else -1.0
            price_txt = f"${price_num:.2f}" if price_num >= 0 else "—"
            owned = bool(c["owned"])
            owned_attr = "1" if owned else "0"
            scry = "https://scryfall.com/search?q=" + quote(f'!"{c["name"]}"')
            edh = "https://edhrec.com/cards/" + quote(c["name"].lower().replace(" ", "-"))
            tier_label = ", ".join(c["tiers"])
            parts.append(
                f'<article class="card{" owned" if owned else ""}" data-card-name="{html.escape(c["name"], quote=True)}" '
                f'data-inclusion="{c["inclusion_pct"]:.4f}" '
                f'data-synergy="{(float(c["synergy_pct"]) if c["synergy_pct"] is not None else 0.0):.4f}" '
                f'data-price="{price_num:.4f}" data-owned="{owned_attr}">'
                f'<div class="card-name">{html.escape(c["name"])}</div>'
                f'<div class="meta">Inclusion: {html.escape(c["inclusion_label"])}</div>'
                f'<div class="meta">Synergy: {html.escape(c["synergy_label"])}</div>'
                f'<div class="meta">Source tiers: {html.escape(tier_label)}</div>'
                f'<div class="meta">Owned: {"Yes" if owned else "No"}</div>'
                f'<div class="price">Price: {price_txt}</div>'
                f'<div class="links"><a href="{html.escape(scry)}" target="_blank" rel="noopener">Scryfall</a>'
                f'<a href="{html.escape(edh)}" target="_blank" rel="noopener">EDHREC card</a></div>'
                '</article>\n'
            )
        parts.append("</div></section>\n")

    parts.append(
        """</div>
<script>
(function() {
  var sortSel = document.getElementById('sort-mode');
  var hideOwned = document.getElementById('hide-owned');
  var minInclusion = document.getElementById('min-inclusion');
  var minSynergy = document.getElementById('min-synergy');
  var minPrice = document.getElementById('min-price');
  var minInclusionVal = document.getElementById('min-inclusion-val');
  var minSynergyVal = document.getElementById('min-synergy-val');
  var minPriceVal = document.getElementById('min-price-val');
  var resetBtn = document.getElementById('reset-filters');
  var exportMoxfieldBtn = document.getElementById('export-moxfield');
  var copyMoxfieldBtn = document.getElementById('copy-moxfield');
  var baselineStats = document.getElementById('baseline-stats');
  var currentStats = document.getElementById('current-stats');
  var baselineComputed = false;
  sortSel.value = '"""
        + sort_mode
        + """';
  var defaultState = {
    sort: sortSel.value || 'inclusion',
    hideOwned: false,
    minInclusion: parseFloat(minInclusion && minInclusion.value ? minInclusion.value : '0'),
    minSynergy: parseFloat(minSynergy && minSynergy.value ? minSynergy.value : '0'),
    minPrice: parseFloat(minPrice && minPrice.value ? minPrice.value : '0')
  };

  function clamp(v, min, max) {
    return Math.min(max, Math.max(min, v));
  }

  function parseOrDefault(raw, fallback) {
    var v = parseFloat(raw);
    return Number.isFinite(v) ? v : fallback;
  }

  function readUrlState() {
    var params = new URLSearchParams(window.location.search || '');
    var sort = params.get('sort');
    var hideOwnedValue = params.get('hideOwned');
    return {
      sort: (sort === 'price' || sort === 'inclusion') ? sort : defaultState.sort,
      hideOwned: hideOwnedValue === '1',
      minInclusion: clamp(parseOrDefault(params.get('minInclusion'), defaultState.minInclusion), 0, 100),
      minSynergy: clamp(parseOrDefault(params.get('minSynergy'), defaultState.minSynergy), 0, 100),
      minPrice: clamp(parseOrDefault(params.get('minPrice'), defaultState.minPrice), 0, 200)
    };
  }

  function applyStateToControls(state) {
    sortSel.value = state.sort;
    hideOwned.checked = !!state.hideOwned;
    if (minInclusion) minInclusion.value = String(state.minInclusion);
    if (minSynergy) minSynergy.value = String(state.minSynergy);
    if (minPrice) minPrice.value = String(state.minPrice);
  }

  function currentStateFromControls() {
    return {
      sort: sortSel.value || 'inclusion',
      hideOwned: !!hideOwned.checked,
      minInclusion: parseFloat(minInclusion && minInclusion.value ? minInclusion.value : '0'),
      minSynergy: parseFloat(minSynergy && minSynergy.value ? minSynergy.value : '0'),
      minPrice: parseFloat(minPrice && minPrice.value ? minPrice.value : '0')
    };
  }

  function writeUrlState(state) {
    try {
      var params = new URLSearchParams();
      if (state.sort !== defaultState.sort) params.set('sort', state.sort);
      if (state.hideOwned !== defaultState.hideOwned) params.set('hideOwned', '1');
      if (Math.abs(state.minInclusion - defaultState.minInclusion) > 1e-9) params.set('minInclusion', state.minInclusion.toFixed(1));
      if (Math.abs(state.minSynergy - defaultState.minSynergy) > 1e-9) params.set('minSynergy', state.minSynergy.toFixed(1));
      if (Math.abs(state.minPrice - defaultState.minPrice) > 1e-9) params.set('minPrice', state.minPrice.toFixed(2));
      var query = params.toString();
      var nextUrl = window.location.pathname + (query ? ('?' + query) : '');
      window.history.replaceState(null, '', nextUrl);
    } catch (e) {
      /* file:// or restricted contexts may forbid replaceState */
    }
  }

  function applyCommander(section) {
    var cardsWrap = section.querySelector('.cards');
    if (!cardsWrap) return;
    var cards = Array.prototype.slice.call(cardsWrap.querySelectorAll('.card'));
    var mode = sortSel.value || 'inclusion';
    cards.sort(function(a, b) {
      if (mode === 'price') {
        return (parseFloat(b.dataset.price || '-1') - parseFloat(a.dataset.price || '-1'));
      }
      return (parseFloat(b.dataset.inclusion || '0') - parseFloat(a.dataset.inclusion || '0'));
    });
    cards.forEach(function(c) { cardsWrap.appendChild(c); });
    var hide = !!hideOwned.checked;
    var minInc = parseFloat(minInclusion && minInclusion.value ? minInclusion.value : '0');
    var minSyn = parseFloat(minSynergy && minSynergy.value ? minSynergy.value : '0');
    var min = parseFloat(minPrice && minPrice.value ? minPrice.value : '0');
    var shown = 0;
    cards.forEach(function(c) {
      var isOwned = (c.dataset.owned === '1');
      var inc = parseFloat(c.dataset.inclusion || '0');
      var syn = parseFloat(c.dataset.synergy || '0');
      var price = parseFloat(c.dataset.price || '-1');
      var hideByThreshold = !(inc >= minInc && syn >= minSyn && price >= min);
      var hidden = (hide && isOwned) || hideByThreshold;
      c.classList.toggle('hidden-owned', hidden);
      if (!hidden) shown += 1;
    });
    return shown;
  }

  function collectStats(onlyVisible) {
    var cards = Array.prototype.slice.call(document.querySelectorAll('.card'));
    var totalCards = 0;
    var ownedCards = 0;
    var totalCost = 0;
    cards.forEach(function(c) {
      if (onlyVisible && c.classList.contains('hidden-owned')) return;
      totalCards += 1;
      if (c.dataset.owned === '1') ownedCards += 1;
      var price = parseFloat(c.dataset.price || '-1');
      if (price >= 0) totalCost += price;
    });
    return {
      totalCards: totalCards,
      ownedCards: ownedCards,
      toBuyCards: Math.max(0, totalCards - ownedCards),
      totalCost: totalCost
    };
  }

  function renderStats(target, stats) {
    if (!target) return;
    target.textContent =
      stats.totalCards + ' cards, ' +
      stats.toBuyCards + ' to buy, $' +
      stats.totalCost.toFixed(2) + ' total';
  }

  function refreshSliderLabels() {
    if (minInclusionVal) minInclusionVal.textContent = parseFloat(minInclusion.value || '0').toFixed(1) + '%';
    if (minSynergyVal) minSynergyVal.textContent = parseFloat(minSynergy.value || '0').toFixed(1) + '%';
    if (minPriceVal) minPriceVal.textContent = '$' + parseFloat(minPrice.value || '0').toFixed(2);
  }

  function applyAll() {
    refreshSliderLabels();
    document.querySelectorAll('.commander-section').forEach(applyCommander);
    if (!baselineComputed) {
      renderStats(baselineStats, collectStats(false));
      baselineComputed = true;
    }
    renderStats(currentStats, collectStats(true));
    writeUrlState(currentStateFromControls());
  }

  function resetFilters() {
    applyStateToControls(defaultState);
    applyAll();
  }

  function buildMoxfieldExportText() {
    var seen = Object.create(null);
    var cards = Array.prototype.slice.call(document.querySelectorAll('.card:not(.hidden-owned)'));
    cards.forEach(function(el) {
      var n = (el.getAttribute('data-card-name') || '').trim();
      if (n) seen[n] = true;
    });
    var names = Object.keys(seen).sort(function(a, b) {
      return a.localeCompare(b, undefined, { sensitivity: 'base' });
    });
    return names.map(function(name) { return '1 ' + name; }).join('\\n') + (names.length ? '\\n' : '');
  }

  function downloadMoxfield() {
    var text = buildMoxfieldExportText();
    if (!text.trim()) {
      window.alert('No cards match the current filters — nothing to export.');
      return;
    }
    var blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'moxfield-shopping-list.txt';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function copyMoxfield() {
    var text = buildMoxfieldExportText();
    if (!text.trim()) {
      window.alert('No cards match the current filters — nothing to copy.');
      return;
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).catch(function() {
        window.prompt('Copy this list:', text);
      });
    } else {
      window.prompt('Copy this list:', text);
    }
  }

  applyStateToControls(readUrlState());
  sortSel.addEventListener('change', applyAll);
  hideOwned.addEventListener('change', applyAll);
  if (minInclusion) minInclusion.addEventListener('input', applyAll);
  if (minSynergy) minSynergy.addEventListener('input', applyAll);
  if (minPrice) minPrice.addEventListener('input', applyAll);
  if (resetBtn) resetBtn.addEventListener('click', resetFilters);
  if (exportMoxfieldBtn) exportMoxfieldBtn.addEventListener('click', downloadMoxfield);
  if (copyMoxfieldBtn) copyMoxfieldBtn.addEventListener('click', copyMoxfield);
  applyAll();
})();
</script>
</body>
</html>"""
    )

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(parts))
        print(f"Wrote safe shopping list to {out_path}")
    except OSError as e:  # pragma: no cover (IO)
        print(f"Error writing safe shopping HTML: {e}")

