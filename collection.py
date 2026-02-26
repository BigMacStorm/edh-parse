from pathlib import Path
import math
import sys
import requests_cache
from progress_utils import progress_bar
import requests
import json
import re
import time
from deck import Deck
from deck import Cost
from itertools import chain
from collections import defaultdict
import csv

# pylint: disable=missing-function-docstring, missing-class-docstring

class Collection:
    def __init__(self, args):
        self.decks = []
        self.args = args
        # Reuse Normal page + average-deck data for Budget/Expensive so we don't 2x requests and hit rate limits
        self._edhr_cache = {}
        # Be sure to cache our responses to make sure that we arent making extra calls for no reason. Expire after 31 days for safety.
        self.session = requests_cache.CachedSession('card_cache', expire_after=3600*24*31)
        if self.args.fresh_cache:
            self.session.cache.clear()

    def add_all_costs(self, just_name: str=None, name_with_type=None, bar=None, bar_value=None):
        edhr_name = None
        deck_type = None
        if just_name:
            edhr_name = just_name
        elif name_with_type:
            if name_with_type[1]:
                edhr_name = f"{str(name_with_type[0])}/{str(name_with_type[1])}"
                deck_type = name_with_type[1]
            else:
                edhr_name = name_with_type[0]
        else:
            return
        info = edhr_name or ""
        for cost in [Cost.Normal, Cost.Budget, Cost.Expensive]:
            if bar is not None and bar_value is not None:
                bar.update(bar_value, info=f"Loading thin deck for {info} ({cost.name})")
                sys.stdout.flush()
            self.decks.append(self.new_deck().init_thin_edhr_deck(edhr_name, cost, deck_type=deck_type))
            if bar is not None and bar_value is not None:
                bar.update(bar_value)
                sys.stdout.flush()
    
    def add_list_deck(self, list_cards):
        card_count = len(list_cards)
        with progress_bar(card_count, initial_value=0) as bar:
            bar.update(0)
            self.decks.append(self.new_deck().generate_deck_from_list(list_cards, bar))

    def lookup_total_card_count(self):
        card_count = 0
        for deck in self.decks:
            card_count += deck.get_thin_count()
        return card_count

    def lookup_deck_data(self):
        card_count = 0
        not_thin_cards = self.lookup_total_card_count()
        if not_thin_cards == 0:
            return
        with progress_bar(not_thin_cards, initial_value=0) as bar:
            for deck in self.decks:
                card_count = deck.lookup_card_data(bar, card_count)
    
    def print_collection(self):
        sorted_decks = sorted(
            (d for d in self.decks if d.commander is not None),
            key=lambda deck: deck.commander.name
        )
        commanders_seen = set()
        for deck in sorted_decks:
            if deck.commander and deck.commander.name not in commanders_seen:
                commanders_seen.add(deck.commander.name)
                print(str(deck.commander))
            print(deck)
    
    def mark_cards_owned(self, manabox_data):
        names = set()
        for row in manabox_data:
            name = row.get("Name") or row.get("Card")
            if name:
                names.add(name)
        for deck in self.decks:
            if deck.commander is None:
                continue
            if deck.commander.name in names:
                deck.commander.owned = True
            for card in chain(deck.mainboard, deck.sideboard):
                 if card.name in names:
                    card.owned = True
                    deck.owned_set.add((card.price, card.name))
    
    def new_deck(self):
        return Deck(self.session, self.args, edhr_cache=self._edhr_cache)
    
    def write_to_file(self):
        fieldnames = [
                        "card_pic",
                        "name",
                        "budget",
                        "deck_type",
                        "popular_tag",
                        "color_identity",
                        "owned_percentage",
                        "game_changer_count",
                        "total_cost",
                        "not_owned_cost",
                        "type_line",
                        "oracle_text",
                        "cmc",
                        "artist",
                        "Lands",
                        "Enchantments",
                        "Planeswalkers",
                        "Artifacts",
                        "Sorceries",
                        "Instants",
                        "Creatures",
                        "Battles"]

        output = []
        for deck in self.decks:
            if deck.commander is not None:
                output.append(deck.build_dict())
        
        filename = self.args.csv_file if self.args.csv_file else "csv_out.csv"

        try:
            with open(filename, 'w', newline='', encoding='utf-8') as csv_file:
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(output)
        except Exception as e:
            print(f"Error writing to CSV: {e}")

    def write_summary_html(self, path="summary.html", used_manabox=False):
        """Write a single self-contained summary webpage of all decks."""
        decks_with_commander = [d for d in self.decks if d.commander is not None]
        if not decks_with_commander:
            return
        # Group by commander, then by variant (Normal/Budget/Expensive) so we show exactly one tile per variant
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

        html_parts = [
            """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>EDH Deck Collection Summary</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #6b7280; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.5; }
h1 { font-size: 1.75rem; margin-bottom: 0.5rem; color: var(--text); font-weight: 600; }
.manabox-note { font-size: 0.85rem; color: var(--muted); margin-bottom: 1.5rem; }
.commander-section { background: var(--surface); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.commander-section h2 { margin: 0 0 1rem; font-size: 1.15rem; font-weight: 600; color: var(--text); }
.flex-row { display: flex; align-items: flex-start; gap: 1.25rem; flex-wrap: wrap; }
.commander-pic-wrap { flex-shrink: 0; }
.commander-pic { width: 280px; height: 390px; object-fit: contain; border-radius: 8px; border: 1px solid var(--card); }
.commander-meta { flex: 1; min-width: 180px; }
.commander-meta p { margin: 0.2rem 0; color: var(--muted); font-size: 0.9rem; }
.deck-tiles { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 1rem; }
.deck-tile { background: var(--card); border-radius: 10px; padding: 1rem; min-width: 160px; flex: 1 1 160px; max-width: 200px; }
.deck-tile h3 { margin: 0 0 0.5rem; font-size: 0.85rem; font-weight: 600; color: var(--accent); text-transform: uppercase; letter-spacing: 0.05em; }
.deck-tile .pie-wrap { width: 80px; height: 80px; margin: 0.5rem auto; cursor: pointer; }
.deck-tile .pie-wrap path { cursor: pointer; transition: filter 0.15s ease; }
.deck-tile .pie-wrap path:hover { filter: brightness(1.25); }
.deck-tile .stat { font-size: 0.8rem; margin: 0.25rem 0; display: flex; justify-content: space-between; }
.deck-tile .stat .val { color: var(--text); font-weight: 500; }
.deck-tile .tag { font-size: 0.75rem; color: var(--green); margin-top: 0.5rem; }
.sort-controls { margin-bottom: 1rem; display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }
.sort-controls label { color: var(--muted); font-size: 0.9rem; }
.sort-controls select { background: var(--card); color: var(--text); border: 1px solid var(--muted); border-radius: 6px; padding: 0.35rem 0.6rem; font-size: 0.9rem; cursor: pointer; }
#commander-list { outline: none; }
.deck-tile .pie-legend { font-size: 0.65rem; color: var(--muted); margin-top: 0.35rem; line-height: 1.3; text-align: center; }
.export-pdf { background: var(--accent); color: var(--bg); border: none; border-radius: 6px; padding: 0.4rem 0.75rem; font-size: 0.9rem; cursor: pointer; font-weight: 500; }
.export-pdf:hover { filter: brightness(1.1); }
@media print {
  body { background: #fff; color: #111; padding: 0.5rem; }
  .sort-controls, .pdf-hint { display: none !important; }
  .manabox-note { margin-bottom: 0.5rem; }
  .commander-section { break-inside: avoid; box-shadow: none; border: 1px solid #ccc; }
  .deck-tile { break-inside: avoid; }
  :root { --bg: #fff; --surface: #f5f5f5; --card: #eee; --text: #111; --muted: #444; }
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
<button type="button" class="export-pdf pdf-hint" onclick="window.print();">Export PDF</button>
</div>
<p class="manabox-note pdf-hint" style="font-size:0.8rem;">To save as PDF: click Export PDF, then choose &quot;Save as PDF&quot; or &quot;Microsoft Print to PDF&quot; as the destination.</p>
<div id="commander-list">
""",
        ]
        for cname in sorted(by_commander.keys()):
            decks = by_commander[cname]
            d0 = decks[0]
            pic = (d0.commander.card_pic or "").replace("/normal/", "/large/").replace("/small/", "/large/")
            if not pic and d0.commander.card_pic:
                pic = d0.commander.card_pic
            type_line = (d0.commander.type_line or "").replace("Legendary Creature — ", "")
            total0 = d0.get_cost()
            not_owned0 = d0.get_cost(only_not_owned=True)
            count0 = d0.get_card_count()
            owned0 = d0.get_owned_count()
            html_parts.append(
                f'<section class="commander-section" data-commander="{_escape(cname)}" '
                f'data-total="{total0:.2f}" data-notowned="{not_owned0:.2f}" data-owned="{owned0}" data-count="{count0}">'
            )
            html_parts.append(f'<h2>{_escape(cname)}</h2>\n')
            html_parts.append('<div class="flex-row">\n')
            html_parts.append('<div class="commander-pic-wrap">\n')
            if pic:
                html_parts.append(f'<img class="commander-pic" src="{_escape(pic)}" alt="{_escape(cname)}" loading="lazy">\n')
            html_parts.append("</div>\n<div class=\"commander-meta\">\n")
            html_parts.append(f'<p><strong>Type</strong> {_escape(type_line)}</p>\n')
            if d0.commander.color_identity:
                html_parts.append(f'<p><strong>Color identity</strong> {_escape(str(d0.commander.color_identity))}</p>\n')
            html_parts.append("</div>\n<div class=\"deck-tiles\">\n")
            for deck in decks:
                cost_label = deck.cost.name if deck.cost else "Normal"
                tag = deck.popular_tag or "—"
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
                legend_parts = [f"{_escape(label)} {val}" for label, val, _ in segments if val > 0]
                pie_legend = " · ".join(legend_parts) if legend_parts else "—"
                html_parts.append(
                    f'<div class="deck-tile"><h3>{_escape(cost_label)}</h3>'
                    f'<div class="pie-wrap">{pie_d}</div>'
                    f'<div class="pie-legend">{pie_legend}</div>'
                    f'<div class="stat"><span>Total</span><span class="val">${total:.2f}</span></div>'
                    f'<div class="stat"><span>Not owned</span><span class="val">${not_owned:.2f}</span></div>'
                    f'<div class="stat"><span>Owned</span><span class="val">{owned_count} / {count}</span></div>'
                    f'<div class="tag">{_escape(tag)}</div></div>\n'
                )
            html_parts.append("</div></div></section>\n")
        html_parts.append("""
</div>
<script>
(function() {
  var list = document.getElementById('commander-list');
  var sel = document.getElementById('sort');
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


def _escape(s):
    """Escape for HTML text content."""
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _svg_pie(segments, total):
    """Return SVG markup for a pie chart. segments = [(label, count, hex_color), ...]. Hover shows label + count."""
    r = 36
    cx = cy = 40
    if total <= 0:
        total = 1
    parts = []
    start = -0.25 * 2 * math.pi  # start from top
    for _label, val, color in segments:
        if val <= 0:
            continue
        ratio = val / total
        end = start + ratio * 2 * math.pi
        x1 = cx + r * math.cos(start)
        y1 = cy + r * math.sin(start)
        x2 = cx + r * math.cos(end)
        y2 = cy + r * math.sin(end)
        large = 1 if ratio > 0.5 else 0
        d = f"M{cx},{cy} L{x1:.2f},{y1:.2f} A{r},{r} 0 {large},1 {x2:.2f},{y2:.2f} Z"
        title = _escape(f"{_label}: {val}")
        parts.append(f'<path d="{d}" fill="{_escape(color)}"><title>{title}</title></path>')
        start = end
    return f'<svg viewBox="0 0 80 80" width="80" height="80">{chr(10).join(parts)}</svg>'
