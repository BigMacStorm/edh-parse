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
<p class="subtitle">Cards from average EDHREC lists with inclusion &ge; """
        + f"{inclusion_cutoff:.1f}%"
        + """ and synergy &ge; """
        + f"{synergy_cutoff:.1f}%"
        + """ and price &ge; $"""
        + f"{min_price:.2f}"
        + """.</p>
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
  <label for="min-price">Min $</label>
  <input id="min-price" type="number" min="0" step="0.01" value=""" + f'"{min_price:.2f}"' + """>
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
                f'<article class="card{" owned" if owned else ""}" data-inclusion="{c["inclusion_pct"]:.4f}" '
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
  var minPrice = document.getElementById('min-price');
  sortSel.value = '"""
        + sort_mode
        + """';

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
    var min = parseFloat(minPrice && minPrice.value ? minPrice.value : '0');
    cards.forEach(function(c) {
      var isOwned = (c.dataset.owned === '1');
      var price = parseFloat(c.dataset.price || '-1');
      var hideByPrice = !(price >= min);
      c.classList.toggle('hidden-owned', (hide && isOwned) || hideByPrice);
    });
  }

  function applyAll() {
    document.querySelectorAll('.commander-section').forEach(applyCommander);
  }
  sortSel.addEventListener('change', applyAll);
  hideOwned.addEventListener('change', applyAll);
  if (minPrice) minPrice.addEventListener('input', applyAll);
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

