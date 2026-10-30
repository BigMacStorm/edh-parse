from __future__ import annotations
_TOOLTIP_CSS = '\n.card-link { color: var(--link); cursor: pointer; text-decoration: none; }\n.card-link:hover { text-decoration: underline; color: #bae6fd; }\n.card-tooltip { position: fixed; z-index: 99999; pointer-events: none; width: 240px; height: 335px; border-radius: 12px; box-shadow: 0 16px 36px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.12); background: var(--card); overflow: hidden; display: none; opacity: 0; transition: opacity 0.12s ease; }\n.card-tooltip.visible { display: block; opacity: 1; }\n.card-tooltip img { width: 100%; height: 100%; object-fit: cover; display: block; border-radius: 12px; }\n.card-tooltip .card-tooltip-placeholder { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 1.2rem; text-align: center; font-size: 0.85rem; color: var(--muted); background: var(--surface); }\n'
_TOOLTIP_JS = "<script>\n(function() {\n  const tooltip = document.createElement('div');\n  tooltip.className = 'card-tooltip';\n  const img = document.createElement('img');\n  img.alt = 'Card preview';\n  img.loading = 'eager';\n  const placeholder = document.createElement('div');\n  placeholder.className = 'card-tooltip-placeholder';\n  tooltip.appendChild(placeholder);\n  tooltip.appendChild(img);\n  document.body.appendChild(tooltip);\n\n  let activeEl = null;\n  let timer = null;\n\n  function positionTooltip(e) {\n    const pad = 16;\n    const w = 240;\n    const h = 335;\n    let x = e.clientX + pad;\n    let y = e.clientY + pad;\n\n    if (x + w > window.innerWidth - pad) {\n      x = Math.max(pad, e.clientX - w - pad);\n    }\n    if (y + h > window.innerHeight - pad) {\n      y = Math.max(pad, window.innerHeight - h - pad);\n    }\n\n    tooltip.style.left = x + 'px';\n    tooltip.style.top = y + 'px';\n  }\n\n  function resolveCardImageUrl(cardName) {\n    const clean = cardName.split(' // ')[0].trim();\n    return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(clean) + '&format=image&version=normal';\n  }\n\n  document.addEventListener('mouseover', (e) => {\n    const target = e.target.closest('.card-link, [data-card-name]');\n    if (!target) return;\n    const name = target.getAttribute('data-card-name') || target.textContent.trim();\n    if (!name || name.length < 2) return;\n\n    activeEl = target;\n    clearTimeout(timer);\n    timer = setTimeout(() => {\n      if (activeEl !== target) return;\n      placeholder.textContent = 'Loading ' + name + '…';\n      placeholder.style.display = 'flex';\n      img.style.display = 'none';\n\n      img.onload = () => {\n        if (activeEl === target) {\n          placeholder.style.display = 'none';\n          img.style.display = 'block';\n        }\n      };\n      img.onerror = () => {\n        if (!img.src.includes('&fuzzy=')) {\n          const clean = name.split(' // ')[0].trim();\n          img.src = 'https://api.scryfall.com/cards/named?fuzzy=' + encodeURIComponent(clean) + '&format=image&version=normal';\n        } else if (activeEl === target) {\n          placeholder.textContent = name;\n          placeholder.style.display = 'flex';\n          img.style.display = 'none';\n        }\n      };\n      img.src = resolveCardImageUrl(name);\n\n      positionTooltip(e);\n      tooltip.classList.add('visible');\n    }, 60);\n  });\n\n  document.addEventListener('mousemove', (e) => {\n    if (tooltip.classList.contains('visible')) {\n      positionTooltip(e);\n    }\n  });\n\n  document.addEventListener('mouseout', (e) => {\n    const target = e.target.closest('.card-link, [data-card-name]');\n    if (!target) return;\n    if (activeEl === target) {\n      activeEl = null;\n      clearTimeout(timer);\n      tooltip.classList.remove('visible');\n      img.src = '';\n    }\n  });\n})();\n</script>\n"
"""HTML report: pasted card list → storage buckets with counts."""


import html
from typing import Any, Dict, List, Sequence


def _esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def write_find_in_binders_html(
    *,
    out_path: str,
    groups: Dict[str, Dict[str, List[Any]]],
    missing: Sequence[Any],
    stock_csv: str,
    binders: Sequence[str],
    requested: int,
    found: int,
) -> None:
    rarity_order = ["Rares", "Uncommons", "Commons", "Other"]
    color_order = ["Colorless", "White", "Blue", "Black", "Red", "Green", "Multicolor"]
    sections: List[str] = []
    for rarity in rarity_order:
        color_groups = groups.get(rarity) or {}
        color_sections: List[str] = []
        for color in color_order:
            cards = color_groups.get(color) or []
            if not cards:
                continue
            rows = []
            for hit in cards:
                binder_bits = ", ".join(
                    f"{_esc(binder)} x{qty}" for binder, qty in (getattr(hit, "binders", None) or [])
                ) or "—"
                rows.append(
                    "<tr>"
                    f"<td><span class='card-link' data-card-name='{_esc(getattr(hit, 'name', ''))}'>{_esc(getattr(hit, 'name', ''))}</span></td>"
                    f"<td class='num'>{getattr(hit, 'needed', 0)}</td>"
                    f"<td class='num'>{getattr(hit, 'owned', 0)}</td>"
                    f"<td class='loc'>{binder_bits}</td>"
                    "</tr>"
                )
            color_sections.append(
                f"<section class='color-section'>"
                f"<h3>{_esc(color)} <span class='count'>{len(cards)} cards</span></h3>"
                f"<table><thead><tr><th>Card</th><th>Need</th><th>Have</th><th>Location</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"
                f"</section>"
            )
        if color_sections:
            sections.append(
                f"<section class='rarity-section'>"
                f"<h2>{_esc(rarity)}</h2>"
                f"{''.join(color_sections)}"
                f"</section>"
            )

    missing_block = ""
    if missing:
        missing_rows = "".join(
            f"<li>{_esc(getattr(hit, 'name', ''))} "
            f"<span class='count'>({getattr(hit, 'needed', 0)}/0)</span></li>"
            for hit in missing
        )
        missing_block = (
            f"<section class='rarity-section missing-section'>"
            f"<h2>Not found <span class='count'>{len(missing)} cards</span></h2>"
            f"<ul>{missing_rows}</ul>"
            f"</section>"
        )

    binders_txt = ", ".join(_esc(b) for b in binders) or "—"
    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Find in binders</title>
<style>
:root {{
  --bg: #0e1116; --surface: #161b22; --card: #1c2330; --ink: #e8eef7;
  --muted: #9aabbf; --line: rgba(232,238,247,.12); --accent: #7dd3fc;
  --ok: #3ecf8e; --miss: #f87171;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 1.5rem 1.25rem 3rem; color: var(--ink);
  font-family: "Segoe UI", system-ui, sans-serif;
  background: var(--bg); line-height: 1.5;
}}
h1 {{ margin: 0 0 0.35rem; font-size: 1.6rem; }}
.sub {{ color: var(--muted); margin-bottom: 1rem; }}
.stats {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.25rem; }}
.stat {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 0.5rem 0.85rem; }}
.rarity-section {{ margin-bottom: 1.5rem; }}
.rarity-section h2 {{ margin: 0 0 0.75rem; font-size: 1.2rem; }}
.color-section {{ margin-bottom: 1rem; }}
.color-section h3 {{ margin: 0 0 0.5rem; font-size: 1rem; color: var(--accent); }}
.count {{ color: var(--muted); font-size: 0.9rem; font-weight: normal; }}
.missing-section h2 {{ color: var(--miss); }}
.missing-section ul {{ margin: 0; padding-left: 1.25rem; color: var(--muted); }}
table {{ width: 100%; border-collapse: collapse; background: var(--surface); border-radius: 10px; overflow: hidden; }}
th, td {{ padding: 0.55rem 0.75rem; text-align: left; border-bottom: 1px solid var(--line); }}
th {{ color: var(--muted); font-weight: 600; font-size: 0.9rem; }}
.num {{ width: 4rem; text-align: right; }}
.loc {{ color: var(--muted); font-size: 0.95rem; }}
{_TOOLTIP_CSS}</style>
</head>
<body>
<h1>Find in binders</h1>
<p class="sub">Stock: <code>{_esc(stock_csv)}</code><br>Binders searched: {binders_txt}</p>
<div class="stats">
  <div class="stat"><strong>{found}</strong> found</div>
  <div class="stat"><strong>{len(missing)}</strong> missing</div>
  <div class="stat"><strong>{requested}</strong> requested</div>
</div>
{"".join(sections)}
{missing_block}
{_TOOLTIP_JS}</body>
</html>"""

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"Wrote binder lookup to {out_path}")
