import html as html_module
import re
from urllib.parse import quote
from typing import Any, Dict, List


def write_new_cards_html(
    *,
    out_path: str,
    filtered_output: List[Dict[str, Any]],
    eligible_sets_info: Dict[str, Dict[str, Any]],
    set_card_counts: Dict[str, int],
    set_codes_sorted: List[str],
    set_label: str,
) -> None:
    """Build + write the `--new-cards-html` page."""

    def _edhrec_card_slug(name: str) -> str:
        text = (name or "").strip().lower()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("-")
        return text

    def _inclusion_pct(card: Any) -> float:
        val = getattr(card, "edh_inclusion", None)
        if val is None:
            return -1.0
        try:
            return float(str(val).replace("%", "").strip())
        except ValueError:
            return -1.0

    def _theme_token(theme_slug: str) -> str:
        return theme_slug if theme_slug else "hub"

    def _build_commander_cards(panes, theme_options, tier_ids):
        """Union cards across tiers/themes; inclusion stats from Average hub only."""
        average_hub_by_name = {}
        for card in panes.get("average::") or []:
            name = getattr(card, "name", None) or ""
            if name:
                average_hub_by_name[name] = card

        entries = {}
        theme_slugs = [opt.get("slug") or "" for opt in theme_options]
        for theme_slug in theme_slugs:
            for tier_id in tier_ids:
                pane_key = f"{tier_id}::{theme_slug}"
                for card in panes.get(pane_key) or []:
                    name = getattr(card, "name", None) or ""
                    if not name:
                        continue
                    row = entries.get(name)
                    if row is None:
                        entries[name] = {
                            "base": card,
                            "themes": {_theme_token(theme_slug)},
                        }
                    else:
                        row["themes"].add(_theme_token(theme_slug))

        merged = []
        for name, row in entries.items():
            hub = average_hub_by_name.get(name)
            if hub:
                display = hub
            else:
                display = row["base"]
                display.edh_inclusion = None
                display.edh_synergy = None
            merged.append({"card": display, "themes": row["themes"]})

        merged.sort(
            key=lambda row: (
                -_inclusion_pct(row["card"]),
                (getattr(row["card"], "name", "") or "").lower(),
            )
        )
        return merged

    def _render_card_tile(card: Any, theme_tokens: List[str]) -> str:
        parts: List[str] = []
        scode = (getattr(card, "set_code", None) or "").lower()
        inc_pct = _inclusion_pct(card)
        themes_attr = html_module.escape(" ".join(sorted(theme_tokens)))
        parts.append(
            f'<article class="card-tile" data-set-code="{html_module.escape(scode)}" '
            f'data-inclusion="{inc_pct:.4f}" data-themes="{themes_attr}">\n'
        )
        parts.append(f'<div class="card-title">{card.name or ""}</div>\n')

        meta_bits: List[str] = []
        if card.type_line:
            meta_bits.append(card.type_line)
        if card.rarity:
            meta_bits.append(card.rarity.title())
        if card.set_name and card.set_code:
            meta_bits.append(f"{card.set_name} ({card.set_code.upper()})")
        if meta_bits:
            joined = " · ".join(meta_bits)
            parts.append(f'<div class="card-meta">{joined}</div>\n')

        stats_bits: List[str] = []
        if getattr(card, "edh_inclusion", None):
            stats_bits.append(f"Inclusion rate: {card.edh_inclusion}")
        if getattr(card, "edh_synergy", None):
            stats_bits.append(f"Synergy: {card.edh_synergy}")
        if stats_bits:
            parts.append(f'<div class="card-meta">{" · ".join(stats_bits)}</div>\n')

        if card.price is not None:
            parts.append(f'<div class="card-price">${card.price:.2f}</div>\n')
        if card.card_pic:
            card_img_raw = card.card_pic
            zoom_img_raw = card_img_raw.replace("/small/", "/png/").replace("/normal/", "/png/")
            card_img = html_module.escape(card_img_raw)
            zoom_img = html_module.escape(zoom_img_raw)
            card_alt = html_module.escape(card.name or "")
            parts.append(
                f'<a href="{zoom_img}" target="_blank" rel="noopener" class="card-image-link">'
                f'<img class="card-image" src="{card_img}" alt="{card_alt}" loading="lazy"></a>\n'
            )
        if card.oracle_text:
            text_esc = html_module.escape(card.oracle_text).replace("\n", "<br>")
            parts.append(f'<div class="card-oracle">{text_esc}</div>\n')

        scryfall_url = "https://scryfall.com/search?q=" + quote(f'!"{card.name or ""}"')
        edhrec_slug = _edhrec_card_slug(card.name or "")
        edhrec_url = f"https://edhrec.com/cards/{edhrec_slug}" if edhrec_slug else "https://edhrec.com/cards"
        parts.append('<div class="card-link-row">\n')
        parts.append(
            f'<a href="{html_module.escape(scryfall_url)}" class="card-scryfall-link" target="_blank" rel="noopener">Scryfall</a>\n'
        )
        parts.append(
            f'<a href="{html_module.escape(edhrec_url)}" class="card-edhrec-link" target="_blank" rel="noopener">EDHREC</a>\n'
        )
        parts.append("</div>\n")
        parts.append("</article>\n")
        return "".join(parts)

    html_parts = [
        """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recent-Set Cards by Commander</title>
<style>
:root { --bg: #0f0f14; --surface: #1c1e26; --card: #252830; --text: #e4e4e7; --muted: #cbd5e1; --accent: #7c3aed; --green: #22c55e; --amber: #f59e0b; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 1.5rem 2rem; line-height: 1.55; font-size: 1.05rem; }
h1 { font-size: 2rem; margin-bottom: 0.3rem; color: var(--text); font-weight: 600; }
.subtitle { font-size: 1rem; color: var(--muted); margin-bottom: 1.5rem; }
.commander-section { background: var(--surface); border-radius: 12px; padding: 1.25rem; margin-bottom: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.commander-header { display: flex; align-items: flex-start; gap: 1rem; flex-wrap: wrap; margin-bottom: 1rem; }
.commander-pic { width: 200px; height: 280px; object-fit: contain; border-radius: 8px; border: 1px solid var(--card); }
.commander-meta { flex: 1; min-width: 180px; }
.commander-meta h2 { margin: 0 0 0.4rem; font-size: 1.35rem; }
.commander-meta p { margin: 0.15rem 0; font-size: 0.95rem; color: var(--muted); }
.pane-count-line { font-size: 0.95rem; color: var(--muted); margin: 0.25rem 0 0.75rem; }
.pane-count-line .pane-card-count { color: var(--text); font-weight: 600; }
.pane-empty { color: var(--muted); font-size: 0.95rem; padding: 0.5rem 0; }
.theme-filter-wrap { margin: 0.5rem 0 0.75rem; }
.theme-filter-title { font-size: 0.95rem; font-weight: 600; color: var(--muted); margin-bottom: 0.45rem; }
.theme-checkboxes { display: flex; flex-wrap: wrap; gap: 0.5rem 0.75rem; }
.theme-option { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.92rem; color: var(--text); background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 0.2rem 0.55rem; }
.theme-option input { accent-color: var(--accent); }
.card-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; margin-top: 0.5rem; }
.card-tile { background: var(--card); border-radius: 10px; padding: 0.6rem; display: flex; flex-direction: column; gap: 0.3rem; }
.card-title { font-size: 1.02rem; font-weight: 600; }
.card-meta { font-size: 0.88rem; color: var(--muted); }
.card-price { font-size: 0.92rem; margin-top: 0.2rem; color: var(--green); white-space: nowrap; }
.card-image { width: 100%; border-radius: 6px; border: 1px solid #111; object-fit: contain; margin-top: 0.25rem; }
.card-image-link { display: block; margin-top: 0.25rem; }
.card-image-link:hover .card-image { border-color: var(--accent); box-shadow: 0 0 0 1px rgba(124,58,237,0.45); }
.card-oracle { font-size: 0.88rem; color: var(--muted); margin-top: 0.35rem; line-height: 1.4; }
.card-scryfall-link { font-size: 0.92rem; color: var(--accent); margin-top: 0.3rem; display: inline-block; }
.card-link-row { margin-top: 0.3rem; display: flex; gap: 0.7rem; flex-wrap: wrap; align-items: center; }
.card-edhrec-link { font-size: 0.92rem; color: var(--amber); display: inline-block; }
.badge { display: inline-block; padding: 0.1rem 0.4rem; border-radius: 999px; font-size: 0.7rem; background: rgba(124,58,237,0.15); color: var(--accent); margin-left: 0.35rem; }
.set-filter-wrap { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 0.75rem 1rem; margin: 0.5rem 0 1rem; }
.set-filter-title { font-size: 1.05rem; font-weight: 600; color: var(--muted); margin-bottom: 0.5rem; }
.set-checkboxes { display: flex; flex-wrap: wrap; gap: 0.6rem 0.9rem; }
.set-option { display: inline-flex; align-items: center; gap: 0.35rem; font-size: 0.95rem; color: var(--text); background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 999px; padding: 0.25rem 0.6rem; }
.set-option input { accent-color: var(--accent); }
.set-pill-meta { font-size: 0.9rem; color: var(--muted); }
.card-tile.filtered-out { display: none !important; }
@media print {
  body { background: #fff; color: #111; padding: 0.5rem; }
  :root { --bg: #fff; --surface: #f5f5f5; --card: #eee; --text: #111; --muted: #444; }
  .set-filter-wrap, .theme-filter-wrap { display: none !important; }
}
</style>
</head>
<body>
<h1>Recent-Set Cards by Commander</h1>
<p class="subtitle">EDHREC recommendations across all price tiers and archetypes. Inclusion and synergy are from each commander&apos;s <strong>Average</strong> deck (same as edhrec.com&apos;s main page). Sorted by that inclusion rate. Filter by set and archetype below. Showing sets: <strong>"""
        + html_module.escape(set_label)
        + """</strong>.</p>
<div>
"""
    ]

    checkbox_parts: List[str] = []
    for code in set_codes_sorted:
        meta = eligible_sets_info.get(code) or {}
        set_name = meta.get("name") or code.upper()
        cnt = set_card_counts.get(code, 0)
        checked_attr = "checked" if cnt >= 2 else ""
        released_at = meta.get("released_at", "")
        checkbox_parts.append(
            f'<label class="set-option" title="{html_module.escape(released_at)}">'
            f'<input type="checkbox" class="set-cb" value="{html_module.escape(code)}" {checked_attr}> '
            f"{html_module.escape(set_name)} ({code.upper()}) "
            f'<span class="set-pill-meta">{cnt}</span></label>\n'
        )

    html_parts.append(
        '<div class="set-filter-wrap">\n'
        '<div class="set-filter-title">Sets (last ~6 months + Secret Lair “sld”)</div>\n'
        '<div class="set-checkboxes">\n'
        + "".join(checkbox_parts)
        + "\n"
        "</div>\n"
        '<div class="set-pill-meta" style="margin-top:0.5rem;">Tip: uncheck sets to hide cards instantly.</div>\n'
        "</div>\n"
    )

    for cmd_idx, entry in enumerate(filtered_output):
        name = entry["name"]
        commander_card = entry["commander_card"]
        panes: Dict[str, List[Any]] = entry.get("panes") or {}
        tier_options = entry.get("tier_options") or [
            {"id": "average", "label": "Average"},
            {"id": "budget", "label": "Budget"},
            {"id": "expensive", "label": "Expensive"},
        ]
        theme_options = entry.get("theme_options") or [{"slug": "", "label": "Average"}]
        tier_ids_loop = [str(o.get("id", "")) for o in tier_options]

        html_parts.append(f'<section class="commander-section" data-cmd-idx="{cmd_idx}">\n')
        html_parts.append('<div class="commander-header">\n')
        if commander_card and commander_card.card_pic:
            pic = (commander_card.card_pic or "").replace("/normal/", "/large/").replace("/small/", "/large/")
            html_parts.append(f'<img class="commander-pic" src="{pic}" alt="{name}" loading="lazy">\n')
        html_parts.append('<div class="commander-meta">\n')
        html_parts.append(f"<h2>{name}</h2>\n")
        if commander_card and commander_card.type_line:
            html_parts.append(f'<p>{commander_card.type_line.replace("Legendary Creature — ", "")}</p>\n')
        if commander_card and commander_card.color_identity:
            html_parts.append(f"<p>Color identity: {commander_card.color_identity}</p>\n")
        html_parts.append("</div>\n</div>\n")

        if len(theme_options) > 1:
            html_parts.append('<div class="theme-filter-wrap">\n')
            html_parts.append('<div class="theme-filter-title">Archetypes</div>\n')
            html_parts.append('<div class="theme-checkboxes">\n')
            for opt in theme_options:
                slug = opt.get("slug") or ""
                token = _theme_token(slug)
                olab = html_module.escape(str(opt.get("label", slug or "Average")))
                html_parts.append(
                    f'<label class="theme-option">'
                    f'<input type="checkbox" class="theme-cb" value="{html_module.escape(token)}" checked> '
                    f"{olab}</label>\n"
                )
            html_parts.append("</div>\n</div>\n")

        card_rows = _build_commander_cards(panes, theme_options, tier_ids_loop)
        html_parts.append(
            '<p class="pane-count-line">Visible in this view (after filters): '
            f'<span class="pane-card-count">{len(card_rows)}</span></p>\n'
        )

        if not card_rows:
            html_parts.append(
                '<p class="pane-empty">No recommended cards from recent sets for this commander.</p>\n'
            )
        else:
            html_parts.append('<div class="card-grid">\n')
            for row in card_rows:
                html_parts.append(
                    _render_card_tile(row["card"], sorted(row["themes"]))
                )
            html_parts.append("</div>\n")
        html_parts.append("</section>\n")

    html_parts.append(
        """</div>
<script>
(function() {
  var setCheckboxes = Array.prototype.slice.call(document.querySelectorAll('input.set-cb'));

  function cardThemes(card) {
    return (card.getAttribute('data-themes') || '').trim().split(/\\s+/).filter(Boolean);
  }

  function applyFilters(sec) {
    var selectedSets = new Set(
      setCheckboxes.filter(function(cb) { return cb.checked; }).map(function(cb) {
        return (cb.value || '').toLowerCase();
      })
    );
    var themeBoxes = sec.querySelectorAll('input.theme-cb');
    var selectedThemes = new Set(
      Array.prototype.slice.call(themeBoxes).filter(function(cb) { return cb.checked; }).map(function(cb) {
        return cb.value || '';
      })
    );
    var filterThemes = themeBoxes.length > 0;

    sec.querySelectorAll('.card-tile').forEach(function(card) {
      var scode = (card.getAttribute('data-set-code') || '').toLowerCase();
      var setOk = selectedSets.has(scode);
      var themeOk = true;
      if (filterThemes) {
        if (selectedThemes.size === 0) {
          themeOk = false;
        } else {
          themeOk = cardThemes(card).some(function(t) { return selectedThemes.has(t); });
        }
      }
      card.classList.toggle('filtered-out', !(setOk && themeOk));
    });

    var visible = sec.querySelectorAll('.card-tile:not(.filtered-out)').length;
    var cntEl = sec.querySelector('.pane-card-count');
    if (cntEl) cntEl.textContent = String(visible);
  }

  function applyAllFilters() {
    document.querySelectorAll('.commander-section').forEach(applyFilters);
  }

  setCheckboxes.forEach(function(cb) {
    cb.addEventListener('change', applyAllFilters);
  });
  document.querySelectorAll('.commander-section').forEach(function(sec) {
    sec.querySelectorAll('input.theme-cb').forEach(function(cb) {
      cb.addEventListener('change', function() { applyFilters(sec); });
    });
    applyFilters(sec);
  });
})();
</script>
</body>
</html>"""
    )

    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("".join(html_parts))
        print(f"Wrote new-cards summary to {out_path}")
    except OSError as e:  # pragma: no cover (IO)
        print(f"Error writing new-cards HTML: {e}")


def run_new_cards_html(args: Any, impl_fn=None) -> None:
    """Backward-compatible entrypoint wrapper for `--new-cards-html`."""
    if impl_fn is not None:
        impl_fn(args)
        return
    raise ValueError("run_new_cards_html(args) requires impl_fn in this mode.")


__all__ = ["run_new_cards_html", "write_new_cards_html"]
