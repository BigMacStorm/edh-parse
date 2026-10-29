"""HTML renderer for artist signings reports."""

from __future__ import annotations

import html as html_module
from urllib.parse import quote


def write_artist_signings_html(out_path, grouped, binder_names):
    """Render a pretty HTML report grouped by artist and sorted by value."""
    artist_totals = {}
    global_total = 0.0
    global_count = 0

    normalized = {}
    for artist, cards in grouped.items():
        sorted_cards = sorted(cards, key=lambda c: c["total_value"], reverse=True)
        normalized[artist] = sorted_cards
        total = sum(c["total_value"] for c in sorted_cards)
        artist_totals[artist] = total
        global_total += total
        global_count += len(sorted_cards)

    artists_sorted = sorted(
        normalized.keys(),
        key=lambda a: (artist_totals.get(a, 0.0), a.lower()),
        reverse=True,
    )
    binder_text = ", ".join(binder_names)

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artist Signings Report</title>
<style>
:root {
  --bg: #0f1115;
  --surface: #171b22;
  --card: #1f2530;
  --text: #e7edf6;
  --muted: #9eabc2;
  --accent: #6aa9ff;
  --green: #3ddc97;
  --border: #2b3443;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: "Segoe UI", system-ui, sans-serif;
  line-height: 1.45;
  padding: 1.25rem;
}
.container { max-width: 1200px; margin: 0 auto; }
h1 { margin: 0; font-size: 1.8rem; }
.sub { color: var(--muted); margin-top: 0.4rem; }
.meta {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
  margin-top: 0.9rem;
}
.pill {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0.35rem 0.75rem;
  color: var(--muted);
  font-size: 0.86rem;
}
.artist {
  margin-top: 1.1rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
}
.artist-head {
  padding: 0.7rem 0.9rem;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
}
.artist-name { font-size: 1.05rem; font-weight: 600; }
.artist-total { color: var(--green); font-weight: 600; }
table { width: 100%; border-collapse: collapse; }
th, td {
  text-align: left;
  padding: 0.55rem 0.7rem;
  border-bottom: 1px solid var(--border);
  font-size: 0.9rem;
}
.art-col { width: 352px; }
.art-thumb {
  width: 336px;
  height: auto;
  border-radius: 6px;
  border: 1px solid var(--border);
  display: block;
}
th {
  color: var(--muted);
  font-weight: 600;
  background: var(--card);
}
tr:hover td { background: rgba(255,255,255,0.02); }
.right { text-align: right; }
.money { color: var(--green); font-variant-numeric: tabular-nums; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
@media print {
  body { background: #fff; color: #111; }
  .artist, .pill { border-color: #ccc; }
  th { background: #f1f1f1; color: #222; }
}
</style>
</head>
<body>
<div class="container">
  <h1>Artist Signings Report</h1>
""",
        '  <div class="sub">Cards in selected binders with artists from your convention list.</div>\n',
        '  <div class="meta">\n',
        f'    <div class="pill">Binders: {html_module.escape(binder_text)}</div>\n',
        f'    <div class="pill">Matched artists: {len(artists_sorted)}</div>\n',
        f'    <div class="pill">Matched cards: {global_count}</div>\n',
        f'    <div class="pill">Total value: ${global_total:.2f}</div>\n',
        "  </div>\n",
    ]

    for artist in artists_sorted:
        cards = normalized[artist]
        html_parts.append('<section class="artist">\n')
        html_parts.append('  <div class="artist-head">\n')
        html_parts.append(f'    <div class="artist-name">{html_module.escape(artist)}</div>\n')
        html_parts.append(f'    <div class="artist-total">${artist_totals[artist]:.2f}</div>\n')
        html_parts.append("  </div>\n")
        html_parts.append("  <table>\n")
        html_parts.append(
            "    <thead><tr>"
            "<th class='art-col'>Art</th><th>Card</th><th>Binder</th><th class='right'>Qty</th>"
            "<th class='right'>Cost</th><th class='right'>Value</th>"
            "</tr></thead>\n"
        )
        html_parts.append("    <tbody>\n")
        for entry in cards:
            if entry.get("set_code") and entry.get("collector_number"):
                scryfall_url = (
                    f"https://scryfall.com/card/"
                    f"{quote(entry['set_code'].lower(), safe='')}/"
                    f"{quote(str(entry['collector_number']), safe='')}/"
                    f"{quote(entry['card'].lower().replace(' ', '-'), safe='-')}"
                )
            else:
                scryfall_query = quote(
                    f'!"{entry["card"]}" artist:"{artist}" unique:prints',
                    safe="",
                )
                scryfall_url = f"https://scryfall.com/search?q={scryfall_query}"
            html_parts.append(
                "      <tr>"
                f"<td>{('<img class=\"art-thumb\" src=\"' + html_module.escape(entry['card_art']) + '\" alt=\"' + html_module.escape(entry['card']) + '\">') if entry.get('card_art') else ''}</td>"
                f"<td><a href=\"{html_module.escape(scryfall_url)}\" target=\"_blank\" rel=\"noopener\">"
                f"{html_module.escape(entry['card'])}</a></td>"
                f"<td>{html_module.escape(entry['binder'])}</td>"
                f"<td class='right'>{entry['qty']}</td>"
                f"<td class='right money'>${entry['unit_price']:.2f}</td>"
                f"<td class='right money'>${entry['total_value']:.2f}</td>"
                "</tr>\n"
            )
        html_parts.append("    </tbody>\n")
        html_parts.append("  </table>\n")
        html_parts.append("</section>\n")

    html_parts.append("</div>\n</body>\n</html>")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
