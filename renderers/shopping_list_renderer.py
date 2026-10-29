"""Simple owned / to-buy report with ManaPool paste list."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict, List


def _esc(text: Any) -> str:
    return html.escape("" if text is None else str(text))


def write_shopping_list_html(
    *,
    out_path: str,
    rows: List[Any],
    removed_basics: Dict[str, int],
    stock_csv: str,
    binders: List[str],
    shopping_list: str,
    manapool_paste: str,
) -> None:
    owned_rows = [r for r in rows if r.to_buy <= 0]
    buy_rows = [r for r in rows if r.to_buy > 0]
    buy_copies = sum(int(r.to_buy) for r in buy_rows)

    basics_bits = "".join(
        f"<li>{_esc(name)} × {qty}</li>"
        for name, qty in sorted(removed_basics.items(), key=lambda t: t[0].casefold())
    ) or "<li class='muted'>None</li>"

    owned_body = []
    for r in sorted(owned_rows, key=lambda x: x.name.casefold()):
        binders_txt = ", ".join(
            f"{_esc(b)} × {q}" for b, q in sorted(r.owned_by_binder.items())
        ) or "—"
        owned_body.append(
            "<tr>"
            f"<td>{_esc(r.name)}</td>"
            f"<td class='num'>{r.needed}</td>"
            f"<td class='num'>{r.owned}</td>"
            f"<td>{binders_txt}</td>"
            "</tr>"
        )

    buy_body = []
    for r in sorted(buy_rows, key=lambda x: x.name.casefold()):
        binders_txt = ", ".join(
            f"{_esc(b)} × {q}" for b, q in sorted(r.owned_by_binder.items())
        ) or "—"
        buy_body.append(
            "<tr>"
            f"<td>{_esc(r.name)}</td>"
            f"<td class='num'>{r.needed}</td>"
            f"<td class='num'>{r.owned}</td>"
            f"<td class='num buy-qty'>{r.to_buy}</td>"
            f"<td>{binders_txt}</td>"
            "</tr>"
        )

    paste_esc = _esc(manapool_paste)

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shopping List</title>
<style>
:root {{
  --bg: #0e1116; --surface: #161b22; --card: #1c2330; --ink: #e8eef7;
  --muted: #9aabbf; --line: rgba(232,238,247,.12); --accent: #3ecf8e;
  --warn: #f0b429; --link: #7dd3fc;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 1.5rem 1.25rem 3rem; color: var(--ink);
  font-family: "Segoe UI", system-ui, sans-serif;
  background:
    radial-gradient(900px 480px at 8% -10%, rgba(62,207,142,.12), transparent 55%),
    var(--bg);
}}
.wrap {{ max-width: 920px; margin: 0 auto; }}
h1 {{ margin: 0 0 .35rem; letter-spacing: -.02em; }}
h2 {{ margin: 1.75rem 0 .7rem; font-size: 1.15rem; }}
.muted {{ color: var(--muted); font-size: .85rem; }}
.meta {{ color: var(--muted); margin-bottom: 1rem; line-height: 1.45; }}
.stats {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: .75rem; margin: 1rem 0 1.25rem;
}}
.stat {{
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; padding: .85rem 1rem;
}}
.stat .label {{ color: var(--muted); font-size: .8rem; }}
.stat .val {{ font-size: 1.25rem; font-weight: 650; margin-top: .2rem; }}
.paste-box {{
  background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; padding: 1rem;
}}
.paste-actions {{
  display: flex; gap: .6rem; flex-wrap: wrap; align-items: center;
  margin-bottom: .65rem;
}}
button.copy {{
  background: var(--accent); color: #062016; border: 0;
  border-radius: 8px; padding: .45rem .85rem; font-weight: 650; cursor: pointer;
}}
button.copy:hover {{ filter: brightness(1.08); }}
a.mp {{ color: var(--link); }}
textarea {{
  width: 100%; min-height: 220px; resize: vertical;
  background: var(--card); color: var(--ink); border: 1px solid var(--line);
  border-radius: 8px; padding: .75rem; font-family: ui-monospace, Consolas, monospace;
  font-size: .9rem; line-height: 1.4;
}}
table {{
  width: 100%; border-collapse: collapse; background: var(--surface);
  border: 1px solid var(--line); border-radius: 10px; overflow: hidden;
}}
th, td {{ padding: .6rem .7rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
th {{
  text-align: left; font-size: .75rem; text-transform: uppercase; letter-spacing: .04em;
  color: var(--muted); background: var(--card);
}}
tr:last-child td {{ border-bottom: 0; }}
.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
.buy-qty {{ color: var(--warn); font-weight: 650; }}
ul.basics {{ margin: .4rem 0 0; padding-left: 1.1rem; color: var(--muted); }}
#copy-status {{ color: var(--muted); font-size: .85rem; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Shopping list</h1>
  <p class="meta">
    Source: <code>{_esc(shopping_list)}</code><br>
    Stock: <code>{_esc(stock_csv)}</code>
    (binders: {_esc(", ".join(binders))})<br>
    Basics removed · duplicates merged · owned copies subtracted.
  </p>

  <div class="stats">
    <div class="stat"><div class="label">Fully owned</div><div class="val">{len(owned_rows)}</div></div>
    <div class="stat"><div class="label">Still to buy</div><div class="val">{len(buy_rows)}</div></div>
    <div class="stat"><div class="label">Buy copies</div><div class="val">{buy_copies}</div></div>
  </div>

  <h2>ManaPool paste list</h2>
  <div class="paste-box">
    <div class="paste-actions">
      <button type="button" class="copy" id="copy-paste">Copy list</button>
      <a class="mp" href="https://manapool.com/add-deck" target="_blank" rel="noopener">Open ManaPool mass entry</a>
      <span id="copy-status"></span>
    </div>
    <textarea id="manapool-paste" readonly>{paste_esc}</textarea>
    <p class="muted" style="margin:.55rem 0 0">Format: <code>qty Card Name</code> — paste into ManaPool mass entry.</p>
  </div>

  <h2>Still need to buy ({len(buy_rows)})</h2>
  <table>
    <thead>
      <tr>
        <th>Card</th><th class="num">Need</th><th class="num">Have</th>
        <th class="num">Buy</th><th>From binders</th>
      </tr>
    </thead>
    <tbody>
      {''.join(buy_body) or '<tr><td colspan="5" class="muted">Nothing left to buy.</td></tr>'}
    </tbody>
  </table>

  <h2>Already owned — no purchase needed ({len(owned_rows)})</h2>
  <table>
    <thead>
      <tr><th>Card</th><th class="num">Need</th><th class="num">Have</th><th>From binders</th></tr>
    </thead>
    <tbody>
      {''.join(owned_body) or '<tr><td colspan="4" class="muted">None fully covered.</td></tr>'}
    </tbody>
  </table>

  <h2>Basic lands removed ({sum(removed_basics.values())} copies)</h2>
  <ul class="basics">{basics_bits}</ul>
</div>
<script>
(function () {{
  var btn = document.getElementById("copy-paste");
  var ta = document.getElementById("manapool-paste");
  var status = document.getElementById("copy-status");
  if (!btn || !ta) return;
  btn.addEventListener("click", async function () {{
    try {{
      await navigator.clipboard.writeText(ta.value);
      if (status) status.textContent = "Copied.";
    }} catch (err) {{
      ta.focus();
      ta.select();
      if (status) status.textContent = "Select-all ready — press Ctrl+C.";
    }}
  }});
}})();
</script>
</body>
</html>
"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(doc, encoding="utf-8")
