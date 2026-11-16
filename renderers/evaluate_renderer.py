"""HTML / Markdown / CSV output for the five-score deck evaluator."""

from __future__ import annotations

import csv
import html
from typing import Iterable, List, Sequence
from urllib.parse import quote

from edhparse.evaluate import config as C
from edhparse.evaluate.algorithms.structural_fit import status_label
from edhparse.evaluate.edhrec import edhrec_slug
from edhparse.evaluate.engine import EvaluationResult
from edhparse.evaluate.models import CardScores
from edhparse.evaluate.scryfall_meta import edhrec_display_name


def _e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _pct(x: float) -> str:
    return f"{100.0 * x:.1f}%"


def _price(c: CardScores) -> str:
    return f"${c.meta.price:,.2f}" if c.meta.price_known else "?"


def _roles(c: CardScores) -> str:
    return "/".join(sorted(c.meta.roles))


def _type_roles(c: CardScores) -> str:
    r = _roles(c)
    return c.meta.primary_type + (f" · {r}" if r else "")


def _lookup_scryfall_id(name: str) -> str:
    try:
        from clients import scryfall_bulk
        if scryfall_bulk.is_available():
            card = scryfall_bulk.get_card_by_name_or_face(name)
            if card:
                return str(card.get("id") or "")
    except Exception:
        pass
    return ""


def _card_link(c: CardScores) -> str:
    slug = edhrec_slug(edhrec_display_name(c.meta))
    sid = getattr(c.meta, "scryfall_id", "") or _lookup_scryfall_id(c.name)
    s_attr = f' data-scryfall-id="{_e(sid)}"' if sid else ""
    return (
        f'<a href="https://edhrec.com/cards/{quote(slug)}" target="_blank" rel="noopener" '
        f'class="card-link" draggable="true" data-card-name="{_e(c.name)}"{s_attr}>{_e(c.name)}</a>'
    )


def _card_span(name: str) -> str:
    slug = edhrec_slug(name)
    sid = _lookup_scryfall_id(name)
    s_attr = f' data-scryfall-id="{_e(sid)}"' if sid else ""
    return (
        f'<a href="https://edhrec.com/cards/{quote(slug)}" target="_blank" rel="noopener" '
        f'class="card-link" draggable="true" data-card-name="{_e(name)}"{s_attr}>{_e(name)}</a>'
    )


def _score_cell(v: float) -> str:
    cls = "pos" if v >= 25 else ("neg" if v <= -25 else "mid")
    return f'<td class="num {cls}">{v:+.1f}</td>'


def _tags(c: CardScores) -> str:
    parts = []
    for t in c.tags:
        cls = "tag tag-sb" if t.upper() == "SIDEBOARD" else "tag"
        parts.append(f'<span class="{cls}">{_e(t)}</span>')
    return " ".join(parts)


_CSS = """
:root { --bg:#0f0f14; --surface:#1c1e26; --card:#252830; --text:#e4e4e7; --muted:#a1a1aa; --accent:#7c3aed;
        --green:#22c55e; --red:#ef4444; --amber:#f59e0b; --border:#3f3f4a; }
* { box-sizing:border-box; }
body { font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg); color:var(--text); margin:0; padding:1.5rem 2rem; line-height:1.45; }
h1 { margin:0 0 .2rem; font-size:1.7rem; } h2 { margin:1.6rem 0 .5rem; font-size:1.2rem; }
.subtitle { color:var(--muted); margin:.2rem 0 1rem; }
.panel { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:1rem 1.1rem; margin-bottom:1rem; overflow-x:auto; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:.6rem; margin:.6rem 0; }
.stat { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:.55rem .8rem; }
.stat .t { color:var(--muted); font-size:.78rem; text-transform:uppercase; letter-spacing:.02em; }
.stat .v { font-size:1.05rem; font-weight:600; }
table { border-collapse:collapse; width:100%; font-size:.88rem; }
th, td { padding:.35rem .5rem; border-bottom:1px solid var(--border); text-align:left; white-space:nowrap; }
th { color:var(--muted); font-weight:600; cursor:pointer; user-select:none; position:sticky; top:0; background:var(--surface); }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
td.pos { color:var(--green); } td.neg { color:var(--red); } td.mid { color:var(--text); }
td.master { font-weight:700; }
a { color:#c4b5fd; text-decoration:none; } a:hover { text-decoration:underline; }
.card-link { color:#c4b5fd; text-decoration:none; cursor:grab; }
.card-link:hover { text-decoration:underline; color:#ddd6fe; }
.card-link:active { cursor:grabbing; }
.card-tooltip { position:fixed; z-index:99999; pointer-events:none; width:240px; height:335px; border-radius:12px; box-shadow:0 16px 36px rgba(0,0,0,0.8), 0 0 0 1px rgba(255,255,255,0.12); background:var(--surface); overflow:hidden; display:none; opacity:0; transition:opacity 0.12s ease; }
.card-tooltip, .card-tooltip * { pointer-events:none !important; }
.card-tooltip.visible { display:block; opacity:1; }
.card-tooltip img { width:100%; height:100%; object-fit:cover; display:block; border-radius:12px; }
.card-tooltip .card-tooltip-placeholder { position:absolute; inset:0; display:flex; align-items:center; justify-content:center; padding:1.2rem; text-align:center; font-size:0.85rem; color:var(--muted); background:var(--surface); }
.tag { display:inline-block; background:var(--card); border:1px solid var(--border); border-radius:999px; padding:0 .5rem; margin:0 .15rem .1rem 0; font-size:.75rem; color:var(--amber); }
.tag-sb { background:rgba(56,189,248,0.15) !important; border-color:#38bdf8 !important; color:#7dd3fc !important; font-weight:600; }
.BLOATED { color:var(--red); } .STARVED { color:var(--amber); } .OPTIMAL { color:var(--green); }
.note { color:var(--muted); font-size:.85rem; }
ul.compact { margin:.3rem 0; padding-left:1.2rem; }
.wrap { white-space:normal; min-width:18rem; }
"""

_SORT_JS = """
document.querySelectorAll('table.sortable th').forEach((th, idx) => th.addEventListener('click', () => {
  const tb = th.closest('table').tBodies[0]; const rows = [...tb.rows];
  const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc'; th.dataset.dir = dir;
  const val = r => { const t = r.cells[idx].innerText.replace(/[$,%+]/g,''); const n = parseFloat(t); return isNaN(n) ? t.toLowerCase() : n; };
  rows.sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (dir === 'asc' ? 1 : -1); });
  rows.forEach(r => tb.appendChild(r));
}));
"""


_TOOLTIP_JS = r"""
(function() {
  const tooltip = document.createElement('div');
  tooltip.className = 'card-tooltip';
  const img = document.createElement('img');
  img.alt = 'Card preview';
  img.loading = 'eager';
  const placeholder = document.createElement('div');
  placeholder.className = 'card-tooltip-placeholder';
  tooltip.appendChild(placeholder);
  tooltip.appendChild(img);
  document.body.appendChild(tooltip);

  let activeEl = null;
  let timer = null;
  let lastX = 0, lastY = 0;

  function positionTooltip(clientX, clientY) {
    const pad = 16;
    const w = 240;
    const h = 335;
    let x = clientX + pad;
    let y = clientY + pad;

    if (x + w > window.innerWidth - pad) {
      x = Math.max(pad, clientX - w - pad);
    }
    if (y + h > window.innerHeight - pad) {
      y = Math.max(pad, clientY - h - pad);
    }

    tooltip.style.left = x + 'px';
    tooltip.style.top = y + 'px';
  }

  function resolveCardImageUrl(target, cardName) {
    const sid = target ? (target.getAttribute('data-scryfall-id') || (target.dataset && target.dataset.scryfallId) || '') : '';
    if (sid && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(sid)) {
      return 'https://cards.scryfall.io/normal/front/' + sid[0] + '/' + sid[1] + '/' + sid + '.jpg';
    }
    const clean = cardName.split(' // ')[0].trim();
    return 'https://api.scryfall.com/cards/named?exact=' + encodeURIComponent(clean) + '&format=image&version=normal';
  }

  document.addEventListener('mousemove', (e) => {
    lastX = e.clientX;
    lastY = e.clientY;
    if (tooltip.classList.contains('visible')) {
      positionTooltip(lastX, lastY);
    }
  });

  document.addEventListener('mouseover', (e) => {
    const target = e.target.closest('.card-link, [data-card-name], a[href*="edhrec.com/cards/"]');
    if (!target) return;
    const rawName = target.getAttribute('data-card-name') || target.textContent.trim();
    const name = rawName.replace(/\s+/g, ' ').trim();
    if (!name || name.length < 2) return;

    activeEl = target;
    lastX = e.clientX;
    lastY = e.clientY;
    clearTimeout(timer);
    timer = setTimeout(() => {
      if (activeEl !== target) return;
      placeholder.textContent = 'Loading ' + name + '…';
      placeholder.style.display = 'flex';
      img.style.display = 'none';

      img.onload = () => {
        if (activeEl === target) {
          placeholder.style.display = 'none';
          img.style.display = 'block';
          try {
            const m = (img.currentSrc || img.src).match(/\/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(?:jpg|png|webp)/i);
            if (m && target) {
              target.setAttribute('data-scryfall-id', m[1]);
            }
          } catch (err) {}
        }
      };
      img.onerror = () => {
        if (!img.src.includes('&fuzzy=')) {
          const clean = name.split(' // ')[0].trim();
          img.src = 'https://api.scryfall.com/cards/named?fuzzy=' + encodeURIComponent(clean) + '&format=image&version=normal';
        } else if (activeEl === target) {
          placeholder.textContent = name;
          placeholder.style.display = 'flex';
          img.style.display = 'none';
        }
      };
      img.src = resolveCardImageUrl(target, name);

      positionTooltip(lastX, lastY);
      tooltip.style.display = 'block';
      tooltip.classList.add('visible');
    }, 40);
  });

  document.addEventListener('mouseout', (e) => {
    if (!activeEl) return;
    if (e.relatedTarget && activeEl.contains(e.relatedTarget)) return;
    const target = e.target.closest('.card-link, [data-card-name], a[href*="edhrec.com/cards/"]');
    if (target === activeEl || !target) {
      activeEl = null;
      clearTimeout(timer);
      tooltip.classList.remove('visible');
      tooltip.style.display = 'none';
      img.src = '';
    }
  });

  document.addEventListener('dragstart', (e) => {
    const target = e.target.closest('.card-link, [data-card-name], a[href*="edhrec.com/cards/"]');
    if (!target) return;

    if (activeEl) {
      activeEl = null;
      clearTimeout(timer);
      tooltip.classList.remove('visible');
      tooltip.style.display = 'none';
      img.src = '';
    }

    const rawName = target.getAttribute('data-card-name') || target.textContent.trim();
    const name = rawName.replace(/\s+/g, ' ').trim();
    const scryfallId = target.getAttribute('data-scryfall-id') || (target.dataset ? target.dataset.scryfallId : '');

    if (scryfallId && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(scryfallId)) {
      const c1 = scryfallId[0];
      const c2 = scryfallId[1];
      const edhrecImgUrl = `https://card-images.edhrec.com/grid/front/${c1}/${c2}/${scryfallId}.webp`;
      const scryfallImgUrl = `https://cards.scryfall.io/normal/front/${c1}/${c2}/${scryfallId}.jpg`;

      try {
        e.dataTransfer.setData('application/x-moxfield-card', JSON.stringify({ id: scryfallId }));
      } catch (err) {}
      try {
        e.dataTransfer.setData('text/uri-list', edhrecImgUrl);
      } catch (err) {}
      try {
        e.dataTransfer.setData('text/html', `<img src="${edhrecImgUrl}" alt="${name}">`);
      } catch (err) {}
      try {
        e.dataTransfer.setData('text/plain', edhrecImgUrl);
      } catch (err) {}
      e.dataTransfer.effectAllowed = 'copyMove';
    } else {
      const clean = name.split(' // ')[0].trim();
      const fallbackUrl = `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(clean)}&format=image&version=normal`;
      try {
        e.dataTransfer.setData('text/plain', fallbackUrl);
        e.dataTransfer.setData('text/uri-list', fallbackUrl);
        e.dataTransfer.setData('text/html', `<img src="${fallbackUrl}" alt="${name}">`);
      } catch (err) {}
    }
  });
})();
"""


def _telemetry(res: EvaluationResult) -> str:
    t = res.telemetry
    stats = [
        ("Commander", " + ".join(_card_span(c) for c in res.commanders)),
        ("EDHREC slug", res.slug),
        ("Colour identity", "".join(res.color_identity) or "C"),
        ("Baseline decks (N₀)", f"{res.n0:,}"),
        ("Expensive / budget decks", f"{res.cohort_sizes.get('D_high', 0):,} / {res.cohort_sizes.get('D_low', 0):,}"),
        ("EDHREC requests / cache hits", f"{t.edhrec_requests} / {t.edhrec_cache_hits}"),
        ("Scryfall tag requests / cache hits", f"{t.scryfall_requests} / {t.scryfall_cache_hits}"),
        ("Filter mode", t.filter_mode),
        ("Weights S1–S4", " / ".join(f"{w:.2f}" for w in res.weights)),
    ]
    out = ['<div class="stats">'] + [
        f'<div class="stat"><div class="t">{_e(k)}</div><div class="v">{v if k == "Commander" else _e(v)}</div></div>' for k, v in stats
    ] + ["</div>"]
    rows = "".join(
        f"<tr><td>{i + 1}</td><td>{' + '.join(_card_span(a) for a in tc.anchors)}</td><td>{'A · live filter' if tc.tier == 'A' else 'B · theme ' + _e(tc.theme)}</td>"
        f'<td class="num">{tc.n_eff:,}</td><td class="num">{tc.weight:.2f}</td></tr>'
        for i, tc in enumerate(res.tuples)
    )
    out.append('<h3>Anchor tuples</h3><table><thead><tr><th>#</th><th>Anchors</th><th>Source</th><th class="num">Decks (Nᵢ)</th>'
               f'<th class="num">Weight</th></tr></thead><tbody>{rows or "<tr><td colspan=5>none</td></tr>"}</tbody></table>')
    hubs = ", ".join(f"{_card_span(h)} ({n:,} decks)" for h, n in res.hubs) or "none"
    excl = ", ".join(_card_span(x) for x in res.exclusions) or "none"
    out.append(f'<p class="note"><strong>Engine hubs:</strong> {hubs}<br><strong>Precon-detox exclusions:</strong> {excl}</p>')
    notes = list(res.warnings) + list(t.log)
    if t.blocked:
        notes.insert(0, "EDHREC blocked requests during this run; results use cached data only.")
    if t.budget_exhausted:
        notes.insert(0, "The request budget ran out; some cohorts were skipped (raise --max-requests).")
    if notes:
        out.append('<ul class="compact note">' + "".join(f"<li>{_e(n)}</li>" for n in notes[:30]) + "</ul>")
    return "".join(out)


def _structure(res: EvaluationResult) -> str:
    s = res.structure
    n = max(s.nonland_count, 1)
    rows = []
    for b in C.CMC_BUCKETS:
        st = status_label(s.user_cmc[b], s.target_cmc[b])
        rows.append(f"<tr><td>MV {b}</td><td class='num'>{s.user_cmc_counts.get(b, 0)}</td><td class='num'>{_pct(s.user_cmc[b])}</td>"
                    f"<td class='num'>{s.target_cmc[b] * n:.1f}</td><td class='num'>{_pct(s.target_cmc[b])}</td><td class='{st}'>{st}</td></tr>")
    for t in C.CARD_TYPES:
        st = status_label(s.user_type[t], s.target_type[t])
        rows.append(f"<tr><td>{t}</td><td class='num'>{s.user_type_counts.get(t, 0):g}</td><td class='num'>{_pct(s.user_type[t])}</td>"
                    f"<td class='num'>{s.target_type[t] * n:.1f}</td><td class='num'>{_pct(s.target_type[t])}</td><td class='{st}'>{st}</td></tr>")
    for r in C.ROLES:
        have, floor = s.role_counts.get(r, 0), s.role_floors[r]
        st = "STARVED" if have < floor else "OPTIMAL"
        rows.append(f"<tr><td>{r}</td><td class='num'>{have}</td><td class='num'>—</td><td class='num'>≥ {floor}</td><td class='num'>—</td><td class='{st}'>{st}</td></tr>")
    return ("<table><thead><tr><th>Bucket</th><th class='num'>Deck</th><th class='num'>Deck %</th><th class='num'>Target</th>"
            "<th class='num'>Target %</th><th>Status</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>"
            f"<p class='note'>{n} non-land cards, {s.land_count} lands, {s.mdfc_count} spell/land MDFCs. Targets blend the "
            "optimised average deck (70%) with the commander page (30%) and keep at least 36% of spells at MV ≤ 2.</p>")


def _card_table(cards: Sequence[CardScores], with_price: bool) -> str:
    head = ["Rank", "Card", "MV", "Type / roles"] + (["USD"] if with_price else []) + [
        "Base %", "Context %", "S1 Bag", "S2 Surv", "S3 Hub", "S4 Struct", "S5 MASTER", "Diagnostic"]
    num = {"Rank", "MV", "USD", "Base %", "Context %", "S1 Bag", "S2 Surv", "S3 Hub", "S4 Struct", "S5 MASTER"}
    th = "".join(f'<th class="{"num" if h in num else ""}">{h}</th>' for h in head)
    rows = []
    for i, c in enumerate(cards, 1):
        cells = [f'<td class="num">{i}</td>', f"<td>{_card_link(c)}</td>", f'<td class="num">{c.meta.cmc:g}</td>',
                 f"<td>{_e(_type_roles(c))}</td>"]
        if with_price:
            cells.append(f'<td class="num">{_price(c)}</td>')
        cells += [f'<td class="num">{_pct(c.p_raw_d0)}</td>', f'<td class="num">{_pct(c.e_context)}</td>']
        cells += [_score_cell(v) for v in c.s]
        cells.append(_score_cell(c.s5).replace('class="num', 'class="num master'))
        cells.append(f'<td class="wrap">{_tags(c)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="sortable"><thead><tr>{th}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def _swaps(res: EvaluationResult) -> str:
    if not res.swaps:
        return "<p class='note'>No swap cleared the gain thresholds (gain &gt; 15 and S5 delta ≥ 12).</p>"
    rows = []
    for i, p in enumerate(res.swaps, 1):
        cut, add = p.cut, p.add
        rows.append(
            f"<tr><td class='num'>{i}</td><td class='note'>{_e(p.confidence)}</td><td>{_card_link(cut)} <span class='note'>MV {cut.meta.cmc:g} · {_e(_roles(cut) or '—')} · "
            f"S5 {cut.s5:+.1f}</span> {_tags(cut)}</td><td>→</td><td>{_card_link(add)} <span class='note'>MV {add.meta.cmc:g} · "
            f"{_e(_roles(add) or '—')} · {_price(add)} · S5 {add.s5:+.1f}</span> {_tags(add)}</td>"
            f"<td class='num pos'>{p.delta:+.1f}</td><td class='wrap note'>{_e(p.rationale)}</td></tr>")
    return ("<table><thead><tr><th class='num'>#</th><th>Confidence</th><th>Cut</th><th></th><th>Add</th><th class='num'>Net S5</th>"
            "<th>Rationale</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table>")


def _lands(res: EvaluationResult) -> str:
    if not res.land_upgrades:
        return "<p class='note'>No utility-land upgrade within budget scored higher than your weakest non-basic lands.</p>"
    rows = "".join(
        f"<tr><td>{_card_span(u.cut)}</td><td class='num'>{u.cut_score:.3f}</td><td>&rarr;</td><td>{_card_span(u.add)}</td>"
        f"<td class='num'>{u.add_score:.3f}</td><td class='num'>${u.price:,.2f}</td></tr>"
        for u in res.land_upgrades
    )
    return ("<table><thead><tr><th>Cut land</th><th class='num'>Score</th><th></th><th>Add land</th><th class='num'>Score</th>"
            f"<th class='num'>USD</th></tr></thead><tbody>{rows}</tbody></table>"
            "<p class='note'>Score = ½·smoothed inclusion on the commander page + ½·on expensive decks.</p>")


def render_html(res: EvaluationResult) -> str:
    title = f"Deck evaluation · {' + '.join(res.commanders)}"
    budget_note = (
        f"Adds are limited to ${res.max_add_budget:,.2f} and to cards with a known price."
        if (res.max_add_budget is not None and res.max_add_budget > 0)
        else "Adds are unconstrained by budget (any price)."
    )
    deck_total = getattr(res, "deck_total", 100)
    deck_size_note = ""
    if deck_total > 100:
        deck_size_note = (
            f" <strong>Note: Deck has {deck_total} cards ({deck_total - 100} over 100). "
            f"The top {len(res.cuts)} cuts below are ranked to help trim your deck down to 100 cards.</strong>"
        )
    body = [
        f"<h1>{_e(title)}</h1>",
        f"<p class='subtitle'>Scores run from −100 to +100; 0 is the median non-land card already in your deck. "
        f"{budget_note}{deck_size_note}</p>",
        "<h2>1 · Telemetry &amp; cohorts</h2><div class='panel'>", _telemetry(res), "</div>",
        "<h2>2 · Structure, curve &amp; roles</h2><div class='panel'>", _structure(res), "</div>",
        f"<h2>3 · Top {len(res.cuts)} cuts</h2><div class='panel'>", _card_table(res.cuts, False), "</div>",
        f"<h2>4 · Top {len(res.adds)} adds</h2><div class='panel'>", _card_table(res.adds, True), "</div>",
    ]
    sb_scores = getattr(res, "sideboard_scores", [])
    if sb_scores:
        body.extend([
            f"<h2>5 · Sideboard &amp; considering evaluation ({len(sb_scores)} cards)</h2><div class='panel'>",
            _card_table(sb_scores, True),
            "<p class='note'>All cards from your sideboard / considering list scored against your deck context. "
            "Cards with positive S5 score above your deck median (+0.0) are strong additions.</p></div>",
            "<h2>6 · Best 1-for-1 swaps</h2><div class='panel'>", _swaps(res), "</div>",
            "<h2>7 · Utility land upgrades</h2><div class='panel'>", _lands(res), "</div>",
        ])
    else:
        body.extend([
            "<h2>5 · Best 1-for-1 swaps</h2><div class='panel'>", _swaps(res), "</div>",
            "<h2>6 · Utility land upgrades</h2><div class='panel'>", _lands(res), "</div>",
        ])
    return (f"<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>{''.join(body)}<script>{_SORT_JS}</script><script>{_TOOLTIP_JS}</script></body></html>")


# --------------------------------------------------------------------------- markdown


def _md_cards(cards: Sequence[CardScores], with_price: bool) -> List[str]:
    head = ["#", "Card", "MV", "Type/Roles"] + (["USD"] if with_price else []) + [
        "Base %", "Context %", "S1", "S2", "S3", "S4", "S5", "Tag"]
    lines = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for i, c in enumerate(cards, 1):
        row = [str(i), c.name, f"{c.meta.cmc:g}", _type_roles(c)] + ([_price(c)] if with_price else []) + [
            _pct(c.p_raw_d0), _pct(c.e_context)] + [f"{v:+.1f}" for v in c.s] + [f"**{c.s5:+.1f}**", "; ".join(c.tags)]
        lines.append("| " + " | ".join(x.replace("|", "/") for x in row) + " |")
    return lines


def render_markdown(res: EvaluationResult) -> str:
    t = res.telemetry
    s = res.structure
    out = [f"# Deck evaluation: {' + '.join(res.commanders)}", "",
           "## 1. Telemetry", "",
           f"- Slug `{res.slug}`, colour identity {''.join(res.color_identity) or 'C'}, baseline {res.n0:,} decks",
           f"- EDHREC requests {t.edhrec_requests} (cache hits {t.edhrec_cache_hits}); Scryfall tag requests {t.scryfall_requests}",
           f"- Filter mode: {t.filter_mode}; weights {' / '.join(f'{w:.2f}' for w in res.weights)}",
           "- Tuples: " + "; ".join(f"{' + '.join(tc.anchors)} (N={tc.n_eff:,}, tier {tc.tier})" for tc in res.tuples),
           "- Hubs: " + (", ".join(h for h, _ in res.hubs) or "none"),
           "- Precon exclusions: " + (", ".join(res.exclusions) or "none"), ""]
    out += [f"- Note: {w}" for w in res.warnings]
    out += ["", "## 2. Structure", "", "| Bucket | Deck | Deck % | Target % | Status |", "|---|---|---|---|---|"]
    for b in C.CMC_BUCKETS:
        out.append(f"| MV {b} | {s.user_cmc_counts.get(b, 0)} | {_pct(s.user_cmc[b])} | {_pct(s.target_cmc[b])} | {status_label(s.user_cmc[b], s.target_cmc[b])} |")
    for ty in C.CARD_TYPES:
        out.append(f"| {ty} | {s.user_type_counts.get(ty, 0):g} | {_pct(s.user_type[ty])} | {_pct(s.target_type[ty])} | {status_label(s.user_type[ty], s.target_type[ty])} |")
    for r in C.ROLES:
        out.append(f"| {r} | {s.role_counts.get(r, 0)} | | ≥ {s.role_floors[r]} | {'STARVED' if s.role_counts.get(r, 0) < s.role_floors[r] else 'OPTIMAL'} |")
    out += ["", "## 3. Top cuts", ""] + _md_cards(res.cuts, False)
    out += ["", "## 4. Top adds", ""] + _md_cards(res.adds, True)
    out += ["", "## 5. Swaps", "", "| # | Confidence | Cut | Add | Net S5 | Rationale |", "|---|---|---|---|---|---|"]
    for i, p in enumerate(res.swaps, 1):
        out.append(f"| {i} | {p.confidence} | {p.cut.name} ({p.cut.s5:+.1f}) | {p.add.name} ({_price(p.add)}, {p.add.s5:+.1f}) | {p.delta:+.1f} | {p.rationale} |")
    out += ["", "## 6. Utility lands", ""]
    out += [f"- {u.cut} ({u.cut_score:.3f}) → {u.add} ({u.add_score:.3f}, ${u.price:,.2f})" for u in res.land_upgrades] or ["- none"]
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- csv

CSV_FIELDS = ["name", "in_deck", "pool", "mv", "type", "roles", "price_usd", "P_raw_D0", "Lift_D0", "E_context", "BagLift",
              "ORR", "k_hubs", "SlotPressure", "RoleBoost", "S1", "S2", "S3", "S4", "S5", "tags"]


def csv_rows(res: EvaluationResult) -> Iterable[list]:
    for c in list(res.deck_scores) + list(res.candidate_scores):
        yield [c.name, int(c.in_deck), c.pool, c.meta.cmc, c.meta.primary_type, _roles(c),
               f"{c.meta.price:.2f}" if c.meta.price_known else "", f"{c.p_raw_d0:.4f}", f"{c.lift_d0:.3f}",
               f"{c.e_context:.4f}", f"{c.bag_lift:.3f}", f"{c.orr:.3f}", c.k_hubs, f"{c.slot_pressure:.3f}",
               f"{c.role_boost:.3f}", *[f"{v:.1f}" for v in c.s], f"{c.s5:.1f}", "; ".join(c.tags)]


def write_reports(res: EvaluationResult, html_path: str = "", md_path: str = "", csv_path: str = "") -> List[str]:
    from pathlib import Path

    written = []
    if html_path:
        Path(html_path).parent.mkdir(parents=True, exist_ok=True)
        Path(html_path).write_text(render_html(res), encoding="utf-8")
        written.append(html_path)
    if md_path:
        Path(md_path).parent.mkdir(parents=True, exist_ok=True)
        Path(md_path).write_text(render_markdown(res), encoding="utf-8")
        written.append(md_path)
    if csv_path:
        Path(csv_path).parent.mkdir(parents=True, exist_ok=True)
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(CSV_FIELDS)
            w.writerows(csv_rows(res))
        written.append(csv_path)
    return written


def console_summary(res: EvaluationResult) -> str:
    lines = [f"Deck evaluation: {' + '.join(res.commanders)}  ({res.telemetry.filter_mode})", "", "Top cuts:"]
    lines += [f"  {c.s5:+6.1f}  {c.name}  {'; '.join(c.tags)}" for c in res.cuts[:10]]
    lines += ["", "Top adds:"]
    lines += [f"  {c.s5:+6.1f}  {c.name}  ({_price(c)})" for c in res.adds[:10]]
    lines += ["", "Swaps:"]
    lines += [f"  [{p.confidence}] {p.cut.name} -> {p.add.name}  (+{p.delta:.1f})" for p in res.swaps] or ["  none"]
    return "\n".join(lines)


def render_batch_summary_html(summaries: Sequence[dict], folder: str = "inputs/moxfield") -> str:
    from pathlib import Path

    title = f"Batch Deck Evaluation · {len(summaries)} Decks"
    successful = [s for s in summaries if s.get("status") == "ok"]
    total_swaps = sum(s.get("swaps", 0) for s in successful)

    stat_cards = f"""
    <div class="stats">
      <div class="stat"><div class="t">Total Decks</div><div class="v">{len(summaries)}</div></div>
      <div class="stat"><div class="t">Evaluated OK</div><div class="v" style="color:var(--green)">{len(successful)}</div></div>
      <div class="stat"><div class="t">Total Swaps Found</div><div class="v" style="color:var(--accent)">{total_swaps}</div></div>
      <div class="stat"><div class="t">Source Folder</div><div class="v" style="font-size:0.9rem">{_e(folder)}</div></div>
    </div>
    """

    rows = []
    for i, s in enumerate(summaries, 1):
        is_ok = s.get("status") == "ok"
        cmdrs = ", ".join(s.get("commanders") or []) or s.get("title") or s.get("file")
        status_html = '<span style="color:var(--green); font-weight:600;">OK</span>' if is_ok else f'<span style="color:var(--red); font-weight:600;" title="{_e(str(s.get("status", "")))}">{_e(str(s.get("status", "")))}</span>'
        swaps_html = str(s.get("swaps", 0)) if is_ok else "—"
        cuts_html = str(s.get("cuts", 0)) if is_ok else "—"
        adds_html = str(s.get("adds", 0)) if is_ok else "—"
        html_file = Path(s.get("html_path", "")).name
        if is_ok and html_file:
            report_link = f'<a href="{_e(html_file)}" target="_blank" rel="noopener" class="card-link" style="font-weight:600; text-decoration:underline;">View Report ↗</a>'
        else:
            report_link = '<span style="color:var(--muted)">—</span>'

        rows.append(
            f"<tr>"
            f"<td class='num'>{i}</td>"
            f"<td><strong>{_e(s.get('title') or s.get('file'))}</strong> <span class='note'>({_e(s.get('file'))})</span></td>"
            f"<td>{_e(cmdrs)}</td>"
            f"<td>{status_html}</td>"
            f"<td class='num' style='color:var(--green); font-weight:600;'>{swaps_html}</td>"
            f"<td class='num'>{cuts_html}</td>"
            f"<td class='num'>{adds_html}</td>"
            f"<td>{report_link}</td>"
            f"</tr>"
        )

    body = [
        f"<h1>{_e(title)}</h1>",
        f"<p class='subtitle'>Evaluated {len(summaries)} decks from <code>{_e(folder)}</code> with five-score EDHREC analysis.</p>",
        "<div class='panel'>", stat_cards, "</div>",
        "<h2>Evaluated Decklists</h2><div class='panel'>",
        f'<table class="sortable"><thead><tr>'
        f'<th class="num">#</th><th>Deck</th><th>Commander(s)</th><th>Status</th>'
        f'<th class="num">Swaps</th><th class="num">Cuts</th><th class="num">Adds</th><th>Report</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>',
        "</div>",
    ]
    return (
        f"<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_e(title)}</title><style>{_CSS}</style></head><body>{''.join(body)}<script>{_SORT_JS}</script><script>{_TOOLTIP_JS}</script></body></html>"
    )
