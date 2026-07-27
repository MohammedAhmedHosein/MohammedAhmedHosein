#!/usr/bin/env python3
"""
Generate the theme-aware hero banner (dark.svg + light.svg).

Everything the banner says lives in profile.json; the portrait comes from the
photos listed there. Edit profile.json (or swap the photo) and re-run:

    python3 .github/scripts/generate_banner.py

Left panel  (VISUAL.MAP)  — the photo, ordered-dithered into ~17k single-colour
                            pixels. It develops in over ~2s, then loops: the
                            portrait implodes toward the centre while 900
                            particles fly through three re-framed clouds of the
                            same photo and settle back.
Right panel (SYSTEM.INFO) — dotted-leader rows typed in one after another.

Only Pillow is required.
"""
import json, math, os, random, sys, html
from collections import defaultdict

from PIL import Image, ImageFilter, ImageOps

# ---------------------------------------------------------------- geometry --
W_SVG, H_SVG = 1180, 610
WIN = (2, 2, 1176, 606)          # x, y, w, h
BAR_H = 46

PANEL = (36, 84, 400, 492)       # VISUAL.MAP frame
ART_X, ART_Y = 50, 86            # where the pixel grid is pinned
GRID_W, GRID_H = 300, 340        # pixel-grid resolution
SX = round((PANEL[2] - 28) / GRID_W, 4)          # 1.24
SY = round(PANEL[3] / GRID_H, 4)                 # 1.4471

INFO_X = 470
ROW_LEN = 655                    # textLength for every dotted row
ROW_CHARS = 79                   # every row is padded to this many characters
ROW_Y0, ROW_DY, SECTION_DY = 162, 23, 31
ROW_T0, ROW_DT, SECTION_DT = 0.90, 0.12, 0.22

FONT = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"

# ---------------------------------------------------------------- portrait --
COVERAGE = 0.25                  # ink density over the subject (dark theme)
COVERAGE_LIGHT = 0.55            # light theme inks the shadows, so it runs denser
HEAD_FRAC = 0.44                 # head height as a fraction of the panel
HEAD_TOP = 0.05                  # headroom above the hair
LAYERS = 60                      # develop-in steps
LAYER_T0, LAYER_T1 = 0.20, 2.17
TILES_X, TILES_Y = 11, 13        # implosion tiles
IMPLODE = 0.47                   # how far a tile travels toward the centre
PARTICLES = 900
LOOP_DUR, LOOP_BEGIN = 13.9, 3.2
KEYTIMES = "0.000;0.194;0.288;0.432;0.525;0.669;0.763;0.906;1.000"
# the loop runs portrait -> tight face -> mid -> wide figure -> portrait.
# each variant is (how far to zoom the crop, how big the cloud sits in the panel)
VARIANTS = [(1.55, 0.58), (1.05, 0.76), (0.70, 0.94)]
SEED = 20240306

# ------------------------------------------------------------------ themes --
THEMES = {
    "dark": {
        "OUTER": "#070B16", "GRAD_A": "#0A101F", "GRAD_B": "#0C1426",
        "BAR": "#0B1222", "HAIRLINE": "rgba(255,255,255,0.10)",
        "BAR_TX": "#94A3B8", "MAP_LABEL": "#475569",
        "EDGE": "#22D3EE", "PANEL_BG": "#0A101F",
        "PANEL_STROKE": "rgba(34,211,238,0.35)",
        "INK": "#A78BFA", "TITLE": "#22D3EE", "LIVE": "#34D399",
        "CHIP_BG": "#4C1D95", "CHIP_TX": "#E9D5FF",
        "KEY": "#22D3EE", "DOTS": "rgba(148,163,184,0.35)", "VALUE": "#F8FAFC",
        "MUTED": "#94A3B8", "FOOT": "#94A3B8",
        "ACCENT": ("#7C3AED", "#22D3EE", "#10B981"),
        "ASCII": ("#60A5FA", "#A78BFA", "#22D3EE"),
        "PARTICLE_ID": "tvdark",
    },
    "light": {
        "OUTER": "#FFFFFF", "GRAD_A": "#F8FAFC", "GRAD_B": "#EEF2F7",
        "BAR": "#F1F5F9", "HAIRLINE": "rgba(15,23,42,0.10)",
        "BAR_TX": "#475569", "MAP_LABEL": "#94A3B8",
        "EDGE": "#06B6D4", "PANEL_BG": "#F8FAFC",
        "PANEL_STROKE": "rgba(8,145,178,0.40)",
        "INK": "#6D28D9", "TITLE": "#0891B2", "LIVE": "#059669",
        "CHIP_BG": "#DBEAFE", "CHIP_TX": "#1D4ED8",
        "KEY": "#0891B2", "DOTS": "rgba(15,23,42,0.25)", "VALUE": "#0F172A",
        "MUTED": "#475569", "FOOT": "#475569",
        "ACCENT": ("#2563EB", "#06B6D4", "#10B981"),
        "ASCII": ("#1D4ED8", "#7C3AED", "#0891B2"),
        "PARTICLE_ID": "tvlight",
    },
}


def esc(s):
    return html.escape(str(s), quote=True)


# ------------------------------------------------------------ pixel engine --
def _bayer(n=8):
    m = [[0]]
    while len(m) < n:
        k = len(m)
        m = [[4 * v for v in row] + [4 * v + 2 for v in row] for row in m] + \
            [[4 * v + 3 for v in row] + [4 * v + 1 for v in row] for row in m]
        del k
    s = float(n * n)
    return [[(v + 0.5) / s for v in row] for row in m]


BAYER = _bayer(8)


def _crop_to_aspect(im, aspect, zoom=1.0, focus=0.42):
    """Centre-crop to `aspect` (w/h), optionally zooming in on `focus` height."""
    w, h = im.size
    if w / h > aspect:
        nw, nh = int(round(h * aspect)), h
    else:
        nw, nh = w, int(round(w / aspect))
    nw, nh = max(1, int(nw * zoom)), max(1, int(nh * zoom))
    left = (w - nw) // 2
    top = int(round((h - nh) * focus))
    return im.crop((left, top, left + nw, top + nh))


def _head_metrics(alpha):
    """Locate the head in a cut-out subject: (top, height, centre-x)."""
    w, h = alpha.size
    px = alpha.load()
    spans = []
    for y in range(h):
        xs = [x for x in range(0, w, 3) if px[x, y] > 96]
        spans.append((xs[0], xs[-1], len(xs) * 3) if xs else (0, 0, 0))
    top = next((y for y in range(h) if spans[y][2]), 0)
    band = [s[2] for s in spans[top:top + int(h * 0.12)] if s[2]]
    head_w = sorted(band)[len(band) // 2] if band else w // 3
    shoulder = next((y for y in range(top + int(h * 0.06), h)
                     if spans[y][2] > 1.8 * head_w), top + int(h * 0.35))
    rows = [s for s in spans[top:top + int((shoulder - top) * 0.6)] if s[2]]
    cx = sum((l + r) / 2 for l, r, _ in rows) / len(rows) if rows else w / 2
    return top, max(1, shoulder - top), cx


def frame_subject(im, aspect, zoom=1.0):
    """Crop a transparent cut-out so the head sits at a fixed size and height."""
    head_frac, head_top = HEAD_FRAC, HEAD_TOP
    im = im.crop(im.getchannel("A").getbbox())
    top, head_h, cx = _head_metrics(im.getchannel("A"))
    fh = head_h / (head_frac * zoom)
    fw = fh * aspect
    box = (int(cx - fw / 2), int(top - head_top * fh))
    out = Image.new("RGBA", (int(fw), int(fh)), (0, 0, 0, 0))
    out.paste(im, (-box[0], -box[1]))
    return out


def _flatten(im):
    """Composite a cut-out onto black and stretch levels over the subject only."""
    if im.mode != "RGBA":
        g = ImageOps.autocontrast(im.convert("L"), cutoff=2)
        return g.filter(ImageFilter.UnsharpMask(10, 120, 2)), None
    mask = im.getchannel("A")
    g = Image.new("RGB", im.size, (0, 0, 0))
    g.paste(im, (0, 0), mask)
    g = g.convert("L")
    keep = [v for v, a in zip(g.getdata(), mask.getdata()) if a > 128]
    if len(keep) > 32:
        keep.sort()
        lo = keep[int(len(keep) * 0.02)]
        hi = keep[int(len(keep) * 0.98)]
        if hi > lo:
            scale = 255.0 / (hi - lo)
            g = g.point(lambda v: max(0, min(255, int((v - lo) * scale))))
    # local contrast: without it the skin tones flatten into one solid blob
    return g.filter(ImageFilter.UnsharpMask(10, 120, 2)), mask


def dither(im, gw, gh, coverage, invert=False):
    """
    Ordered-dither to a set of lit (x, y) grid cells.

    `coverage` is measured against the subject, not the whole grid, so the
    framing can change without the portrait getting heavier or lighter.
    `invert` inks the dark half of the photo instead of the bright half —
    that is what makes the light theme read as a photo rather than a smudge.
    """
    g, mask = _flatten(im)
    g = g.resize((gw, gh), Image.LANCZOS)
    px = list(g.getdata())
    if mask is None:
        inside = [True] * (gw * gh)
    else:
        m = mask.resize((gw, gh), Image.LANCZOS)
        inside = [v > 110 for v in m.getdata()]
    area = sum(inside)
    if not area:
        return []

    def lit(gamma):
        out = []
        for i, v in enumerate(px):
            if not inside[i]:
                continue
            t = (255 - v) / 255.0 if invert else v / 255.0
            if t ** gamma > BAYER[(i // gw) % 8][(i % gw) % 8]:
                out.append((i % gw, i // gw))
        return out

    lo, hi = 0.15, 12.0                       # bisect gamma onto the target
    target = coverage * area
    best = lit(1.0)
    for _ in range(24):
        mid = (lo + hi) / 2
        best = lit(mid)
        if len(best) > target:
            lo = mid
        else:
            hi = mid
        if abs(len(best) - target) < target * 0.01:
            break
    return best


def photo_points(path, gw, gh, coverage=COVERAGE, zoom=1.0, invert=False):
    """Grid cells lit by one photo. Cut-outs get head-framed, photos centre-cropped."""
    im = Image.open(path)
    aspect = (gw * SX) / (gh * SY)
    if im.mode in ("RGBA", "LA") or "transparency" in im.info:
        im = frame_subject(im.convert("RGBA"), aspect, zoom=zoom)
    else:
        im = _crop_to_aspect(im, aspect, zoom=zoom)
    return dither(im, gw, gh, coverage, invert=invert)


def runs(points):
    """Collapse points into horizontal runs so the path data stays small."""
    rows = defaultdict(list)
    for x, y in points:
        rows[y].append(x)
    out = []
    for y in sorted(rows):
        xs = sorted(rows[y])
        start = prev = xs[0]
        for x in xs[1:]:
            if x == prev + 1:
                prev = x
                continue
            out.append((start, y, prev - start + 1))
            start = prev = x
        out.append((start, y, prev - start + 1))
    return out


def path_d(points):
    return "".join(f"M{x} {y}h{w}v1h-{w}z" for x, y, w in runs(points))


# --------------------------------------------------------------- SVG parts --
def art_still(points, t):
    """The develop-in: the portrait split into LAYERS random slices."""
    rnd = random.Random(SEED)
    pts = list(points)
    rnd.shuffle(pts)
    step = (LAYER_T1 - LAYER_T0) / (LAYERS - 1)
    out = [f'<g transform="translate({ART_X},{ART_Y}) scale({SX:.4f},{SY:.4f})" '
           f'fill="{t["INK"]}" shape-rendering="crispEdges">',
           f'<set attributeName="opacity" to="0" begin="{LOOP_BEGIN}s"/>']
    for i in range(LAYERS):
        begin = LAYER_T0 + i * step
        out.append(
            f'<g opacity="0"><animate attributeName="opacity" values="0;1" '
            f'dur="0.9s" begin="{begin:.2f}s" fill="freeze" calcMode="spline" '
            f'keyTimes="0;1" keySplines=".4 0 .2 1"/>'
            f'<path d="{path_d(pts[i::LAYERS])}"/></g>')
    out.append('</g>')
    return "\n".join(out)


def art_implode(points, t):
    """The looping half: tiles collapse toward the centre and fade out."""
    cells = defaultdict(list)
    cw, ch = GRID_W / TILES_X, GRID_H / TILES_Y
    for x, y in points:
        cells[(min(int(x / cw), TILES_X - 1), min(int(y / ch), TILES_Y - 1))].append((x, y))

    out = [f'<g transform="translate({ART_X},{ART_Y}) scale({SX:.4f},{SY:.4f})" '
           f'fill="{t["INK"]}" shape-rendering="crispEdges" opacity="0">',
           f'<set attributeName="opacity" to="1" begin="{LOOP_BEGIN}s"/>']
    for key in sorted(cells):
        pts = cells[key]
        mx = sum(p[0] for p in pts) / len(pts)
        my = sum(p[1] for p in pts) / len(pts)
        dx = round((GRID_W / 2 - mx) * IMPLODE)
        dy = round((GRID_H / 2 - my) * IMPLODE)
        hold = ";".join([f"{dx} {dy}"] * 6)
        out.append(
            f'<g opacity="1"><animate attributeName="opacity" '
            f'values="1;1;0;0;0;0;0;0;1" keyTimes="{KEYTIMES}" dur="{LOOP_DUR}s" '
            f'begin="{LOOP_BEGIN}s" repeatCount="indefinite"/>'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="0 0;0 0;{hold};0 0" keyTimes="{KEYTIMES}" dur="{LOOP_DUR}s" '
            f'begin="{LOOP_BEGIN}s" repeatCount="indefinite"/>'
            f'<path d="{path_d(pts)}"/></g>')
    out.append('</g>')
    return "\n".join(out)


def art_particles(formations, t):
    """900 dots that fly through the three alternate formations and back."""
    pid = t["PARTICLE_ID"]
    out = [f'<defs><rect id="{pid}" width="2.4" height="1.7" fill="{t["INK"]}"/></defs>',
           f'<g transform="translate({ART_X},{ART_Y}) scale({SX:.4f},{SY:.4f})">']
    rest, f1, f2, f3 = formations
    for i in range(PARTICLES):
        a, b, c, d = rest[i], f1[i], f2[i], f3[i]
        vals = (f"{a[0]} {a[1]};{a[0]} {a[1]};{b[0]} {b[1]};{b[0]} {b[1]};"
                f"{c[0]} {c[1]};{c[0]} {c[1]};{d[0]} {d[1]};{d[0]} {d[1]};"
                f"{a[0]} {a[1]}")
        out.append(
            f'<use href="#{pid}" opacity="0"><animate attributeName="opacity" '
            f'values="0;0;1;1;1;1;1;1;0" keyTimes="{KEYTIMES}" dur="{LOOP_DUR}s" '
            f'begin="{LOOP_BEGIN}s" repeatCount="indefinite"/>'
            f'<animateTransform attributeName="transform" type="translate" '
            f'values="{vals}" keyTimes="{KEYTIMES}" dur="{LOOP_DUR}s" '
            f'begin="{LOOP_BEGIN}s" repeatCount="indefinite"/></use>')
    out.append('</g>')
    return "\n".join(out)


def info_rows(cfg, t):
    """Dotted-leader rows, each sliding in from the left in sequence."""
    out, y, begin = [], ROW_Y0, ROW_T0
    for si, section in enumerate(cfg["sections"]):
        if si:
            y += SECTION_DY - ROW_DY
            begin += SECTION_DT - ROW_DT
        if section.get("header"):
            label = section["header"]
            dashes = "-" * (ROW_CHARS - len(label) - 3)
            out.append(
                f'<g opacity="0"><animate attributeName="opacity" from="0" to="1" '
                f'dur="0.4s" begin="{begin:.2f}s" fill="freeze"/>'
                f'<text x="{INFO_X}" y="{y}" font-size="14" textLength="{ROW_LEN}" '
                f'lengthAdjust="spacingAndGlyphs" xml:space="preserve">'
                f'<tspan fill="{t["MUTED"]}">- {esc(label)} </tspan>'
                f'<tspan fill="{t["DOTS"]}">{dashes}</tspan></text></g>')
            y += ROW_DY
            begin += ROW_DT
        for key, value in section["rows"]:
            dots = "." * max(3, ROW_CHARS - len(key) - len(value) - 2)
            out.append(
                f'<g opacity="0"><animate attributeName="opacity" from="0" to="1" '
                f'dur="0.4s" begin="{begin:.2f}s" fill="freeze"/>'
                f'<animateTransform attributeName="transform" type="translate" '
                f'values="-8 0;0 0" dur="0.4s" begin="{begin:.2f}s" fill="freeze"/>'
                f'<text x="{INFO_X}" y="{y}" font-size="14" textLength="{ROW_LEN}" '
                f'lengthAdjust="spacingAndGlyphs" xml:space="preserve">'
                f'<tspan fill="{t["KEY"]}">{esc(key)} </tspan>'
                f'<tspan fill="{t["DOTS"]}">{dots}</tspan>'
                f'<tspan fill="{t["VALUE"]}" font-weight="600"> {esc(value)}</tspan>'
                f'</text></g>')
            y += ROW_DY
            begin += ROW_DT
    return "\n".join(out), y + (SECTION_DY - ROW_DY), begin + 0.32


# ------------------------------------------------------------------ build ---
def build(cfg, theme, points, formations):
    t = THEMES[theme]
    a0, a1, a2 = t["ACCENT"]
    s0, s1, s2 = t["ASCII"]
    x, y, w, h = WIN
    px, py, pw, ph = PANEL
    chip = cfg["chip"]
    chip_w = round(18 + len(chip) * 8.41)
    rows, foot_y, foot_t = info_rows(cfg, t)

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W_SVG}" height="{H_SVG}" viewBox="0 0 {W_SVG} {H_SVG}" font-family="{FONT}" role="img" aria-label="{esc(cfg['name'])} — profile.sh --live">
<defs>
<linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{a0}"><animate attributeName="stop-color" values="{a0};{a1};{a2};{a0}" dur="10s" repeatCount="indefinite"/></stop>
      <stop offset="0.5" stop-color="{a1}"><animate attributeName="stop-color" values="{a1};{a2};{a0};{a1}" dur="10s" repeatCount="indefinite"/></stop>
      <stop offset="1" stop-color="{a2}"><animate attributeName="stop-color" values="{a2};{a0};{a1};{a2}" dur="10s" repeatCount="indefinite"/></stop>
    </linearGradient>
<linearGradient id="asciiGrad" x1="0" y1="0" x2="0" y2="520" gradientUnits="userSpaceOnUse">
      <stop offset="0" stop-color="{s0}"/>
      <stop offset="0.45" stop-color="{s1}"/>
      <stop offset="1" stop-color="{s2}"/>
      <animateTransform attributeName="gradientTransform" type="translate" values="0 -120; 0 120; 0 -120" dur="9s" repeatCount="indefinite"/>
    </linearGradient>
<linearGradient id="panelGrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{t['GRAD_A']}"/><stop offset="1" stop-color="{t['GRAD_B']}"/></linearGradient>
<filter id="glow8" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="8"/></filter>
<filter id="glow3" x="-60%" y="-60%" width="220%" height="220%"><feGaussianBlur stdDeviation="3"/></filter>
<filter id="txtGlow" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="0.9" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<clipPath id="winClip"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18"/></clipPath>
</defs>
<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="18" fill="{t['OUTER']}"/>
<g clip-path="url(#winClip)">
<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="url(#panelGrad)"/>
<rect x="{x}" y="{y}" width="{w}" height="{BAR_H}" fill="{t['BAR']}"/>
<line x1="{x}" y1="{y + BAR_H}" x2="{x + w}" y2="{y + BAR_H}" stroke="{t['HAIRLINE']}"/>
<circle cx="30" cy="25.0" r="5.5" fill="#ff5f56"/>
<circle cx="50" cy="25.0" r="5.5" fill="#ffbd2e"/>
<circle cx="70" cy="25.0" r="5.5" fill="#27c93f"/>
<text x="{W_SVG / 2:.1f}" y="29.0" text-anchor="middle" font-size="12" fill="{t['BAR_TX']}">{esc(cfg['prompt'])}</text>
<text x="38" y="74" font-size="10" letter-spacing="3" fill="{t['MAP_LABEL']}">VISUAL.MAP</text>
<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="10" fill="none" stroke="{t['EDGE']}" stroke-width="2" opacity="0.45" filter="url(#glow3)"/>
<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="10" fill="{t['PANEL_BG']}" stroke="{t['PANEL_STROKE']}"/>
{art_still(points, t)}
{art_implode(points, t)}
{art_particles(formations, t)}
<path d="M {px + 14} {py} L {px} {py} L {px} {py + 14}" fill="none" stroke="{t['EDGE']}" stroke-width="2" opacity="0.8"/>
<path d="M {px + pw - 14} {py} L {px + pw} {py} L {px + pw} {py + 14}" fill="none" stroke="{t['EDGE']}" stroke-width="2" opacity="0.8"/>
<path d="M {px + 14} {py + ph} L {px} {py + ph} L {px} {py + ph - 14}" fill="none" stroke="{t['EDGE']}" stroke-width="2" opacity="0.8"/>
<path d="M {px + pw - 14} {py + ph} L {px + pw} {py + ph} L {px + pw} {py + ph - 14}" fill="none" stroke="{t['EDGE']}" stroke-width="2" opacity="0.8"/>
<text x="{INFO_X}" y="106" font-size="13" letter-spacing="2" fill="{t['TITLE']}" filter="url(#txtGlow)">SYSTEM.INFO</text>
<line x1="566" y1="102" x2="1061" y2="102" stroke="{t['HAIRLINE']}"/>
<text x="1125" y="106" text-anchor="end" font-size="12" fill="{t['LIVE']}" font-weight="700"><tspan>&#9679;</tspan> LIVE<animate attributeName="opacity" values="1;0.25;1" dur="1.6s" repeatCount="indefinite"/></text>
<g opacity="0"><animate attributeName="opacity" from="0" to="1" dur="0.5s" begin="0.6s" fill="freeze"/>
<rect x="{INFO_X}" y="122" width="{chip_w}" height="20" rx="4" fill="{t['CHIP_BG']}"/>
<text x="{INFO_X + 9}" y="136" font-size="14" font-weight="700" fill="{t['CHIP_TX']}">{esc(chip)}</text>
<line x1="{INFO_X + chip_w + 10}" y1="130" x2="1125" y2="130" stroke="{t['HAIRLINE']}"/>
</g>
{rows}
<g opacity="0"><animate attributeName="opacity" from="0" to="1" dur="0.5s" begin="{foot_t:.2f}s" fill="freeze"/>
<text x="{INFO_X}" y="{foot_y}" font-size="14" fill="{t['FOOT']}">&#9656; {esc(cfg['footer'])} &#8595; <tspan fill="{t['EDGE']}">&#9608;<animate attributeName="fill-opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></tspan></text>
</g>
</g>
<rect x="3" y="3" width="{w - 2}" height="{h - 2}" rx="17" fill="none" stroke="url(#accent)" stroke-width="3" opacity="0.55" filter="url(#glow8)"/>
<rect x="3" y="3" width="{w - 2}" height="{h - 2}" rx="17" fill="none" stroke="url(#accent)" stroke-width="1.6"/>
</svg>
"""


def pick(pts, rnd, offset=(0, 0)):
    """Exactly PARTICLES points, spread over `pts`, shifted by `offset`."""
    if not pts:
        pts = [(GRID_W // 2, GRID_H // 2)]
    chosen = (rnd.sample(pts, PARTICLES) if len(pts) >= PARTICLES
              else [rnd.choice(pts) for _ in range(PARTICLES)])
    rnd.shuffle(chosen)
    return [(p[0] + offset[0], p[1] + offset[1]) for p in chosen]


def formation(path, zoom, scale, rnd, invert):
    """A particle cloud of the photo, centred in the grid at `scale`."""
    gw, gh = max(8, int(GRID_W * scale)), max(8, int(GRID_H * scale))
    pts = photo_points(path, gw, gh, coverage=0.30, zoom=zoom, invert=invert)
    return pick(pts, rnd, ((GRID_W - gw) // 2, (GRID_H - gh) // 2))


def main():
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.chdir(root)
    with open("profile.json") as f:
        cfg = json.load(f)

    photos = cfg["photos"]
    missing = [p for p in photos if not os.path.exists(p)]
    if missing:
        sys.exit(f"missing photo(s): {', '.join(missing)}")

    for theme, name in (("dark", "dark.svg"), ("light", "light.svg")):
        invert = theme == "light"
        coverage = COVERAGE_LIGHT if invert else COVERAGE
        points = photo_points(photos[0], GRID_W, GRID_H, coverage, invert=invert)

        rnd = random.Random(SEED)
        formations = [pick(points, rnd)]
        for i, (zoom, scale) in enumerate(VARIANTS):
            src = photos[i + 1] if len(photos) > i + 1 else photos[0]
            z = 1.0 if len(photos) > i + 1 else zoom
            formations.append(formation(src, z, scale, rnd, invert))

        svg = build(cfg, theme, points, formations)
        with open(name, "w") as f:
            f.write(svg)
        print(f"wrote {name}: {theme}, {len(points)} pixels, {len(svg) // 1024}KB")


if __name__ == "__main__":
    main()
