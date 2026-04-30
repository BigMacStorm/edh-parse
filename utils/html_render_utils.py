import math


def _canonical_color_identity(ci):
    """From 5-char display string (emojis + X), return canonical letters e.g. 'WUB' or '' for colorless."""
    if not ci or len(ci) < 5:
        return ""
    return "".join("WUBRG"[i] for i in range(5) if i < len(ci) and ci[i] != "X")


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
    """Return SVG markup for a donut chart. segments = [(label, count, hex_color), ...]. Hover shows label + count."""
    cx = cy = 40
    ro = 34  # outer radius
    ri = 14  # inner radius (donut hole)
    if total <= 0:
        total = 1
    parts = []
    start = -0.25 * 2 * math.pi  # start from top
    for _label, val, color in segments:
        if val <= 0:
            continue
        ratio = val / total
        end = start + ratio * 2 * math.pi
        large = 1 if ratio > 0.5 else 0
        # Donut segment: inner start -> outer start -> outer arc -> inner end -> inner arc -> close
        x_istart = cx + ri * math.cos(start)
        y_istart = cy + ri * math.sin(start)
        x_ostart = cx + ro * math.cos(start)
        y_ostart = cy + ro * math.sin(start)
        x_oend = cx + ro * math.cos(end)
        y_oend = cy + ro * math.sin(end)
        x_iend = cx + ri * math.cos(end)
        y_iend = cy + ri * math.sin(end)
        d = (
            f"M{x_istart:.2f},{y_istart:.2f} L{x_ostart:.2f},{y_ostart:.2f} "
            f"A{ro},{ro} 0 {large},1 {x_oend:.2f},{y_oend:.2f} "
            f"L{x_iend:.2f},{y_iend:.2f} A{ri},{ri} 0 {large},0 {x_istart:.2f},{y_istart:.2f} Z"
        )
        title = _escape(f"{_label}: {val}")
        parts.append(
            f'<path d="{d}" fill="{_escape(color)}" stroke="rgba(0,0,0,0.25)" stroke-width="0.6">'
            f"<title>{title}</title></path>"
        )
        start = end
    return f'<svg class="pie-svg" viewBox="0 0 80 80" width="80" height="80">{chr(10).join(parts)}</svg>'


def _svg_mana_curve(mana_curve):
    """Return SVG for a small vertical bar chart of CMC distribution. mana_curve = {'1': 8, '2': 18, ...}."""
    if not mana_curve:
        return ""
    values = [mana_curve.get(str(i), 0) for i in range(8)]  # CMC 0-7
    max_val = max(values) or 1
    w, h = 56, 28
    n = 8
    bar_w = (w - (n + 1) * 1) / n
    parts = []
    for i, v in enumerate(values):
        x = 1 + i * (bar_w + 1)
        bar_h = round((v / max_val) * (h - 6)) if max_val else 0
        if bar_h > 0:
            y = h - 3 - bar_h
            parts.append(
                f'<rect x="{x:.1f}" y="{y}" width="{max(1, bar_w - 0.5):.1f}" height="{bar_h}" '
                f'data-cmc="{i}" class="mana-bar" fill="#7c3aed" opacity="0.85"/>'
            )
        # Label underneath each bar with its CMC bucket
        cx = x + max(1, bar_w - 0.5) / 2.0
        parts.append(
            f'<text x="{cx:.1f}" y="{h - 1}" fill="#9ca3af" font-size="4" text-anchor="middle">{i}</text>'
        )
    return f'<svg class="mana-curve-svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">{chr(10).join(parts)}</svg>'


__all__ = [
    "_canonical_color_identity",
    "_escape",
    "_svg_pie",
    "_svg_mana_curve",
]

