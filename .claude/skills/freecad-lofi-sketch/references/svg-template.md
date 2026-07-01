# Lo-Fi Concept Sheet: SVG Template

One SVG, three orthographic panels (Top/Front/Left) on a shared graph-paper
grid. This file gives you the exact formulas and a full worked example —
follow them rather than inventing your own layout math each time, so sheets
stay legible and the three views stay mutually aligned.

## 1. Sizing — pick cell counts, not millimetres

Nothing on this sheet is dimensioned. Instead, read the idea for a rough
**width : depth : height** feel and pick three small integers:

- `Wc` — width, in grid cells
- `Dc` — depth, in grid cells
- `Hc` — height, in grid cells

Clamp each to roughly **3–24** — below that a panel reads as a sliver, above
it the sheet gets unwieldy. Distortion at the extremes (e.g. a very flat,
wide plate) is fine — the point is relative proportion, not accuracy.

`CELL = 20` — abstract SVG units per grid square. There is no real-world
scale; `CELL` just sets how chunky the grid reads.

## 2. Graph-paper grid background

Nested `<pattern>`s: a minor line every cell, a heavier major line every 5
cells. Put this in `<defs>` once, then two full-sheet rects (white base, then
the grid pattern) before anything else:

```xml
<defs>
  <pattern id="grid-minor" width="20" height="20" patternUnits="userSpaceOnUse">
    <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dbe6f1" stroke-width="0.5"/>
  </pattern>
  <pattern id="grid-major" width="100" height="100" patternUnits="userSpaceOnUse">
    <rect width="100" height="100" fill="url(#grid-minor)"/>
    <path d="M 100 0 L 0 0 0 100" fill="none" stroke="#aec6de" stroke-width="1"/>
  </pattern>
</defs>
<rect width="{SHEET_W}" height="{SHEET_H}" fill="#ffffff"/>
<rect width="{SHEET_W}" height="{SHEET_H}" fill="url(#grid-major)"/>
```

Draw panel content **on top of** this — don't mask the grid with an opaque
panel background. Seeing the grid through a panel is how proportions stay
readable at a glance.

## 3. Panel layout formulas

Panel sizes fall straight out of the cell counts (`PANEL_W`/`PANEL_H` in SVG
units, `1 cell = CELL` units):

| Panel | Width | Height | Shares with Front |
|---|---|---|---|
| Front | `Wc·CELL` | `Hc·CELL` | — (the anchor) |
| Top | `Wc·CELL` | `Dc·CELL` | width (placed directly **above** Front) |
| Left | `Dc·CELL` | `Hc·CELL` | height (placed directly **left of** Front) |

Layout constants: `MARGIN = 40` (sheet edge), `GUTTER = 40` (between
panels), `LABEL_H = 26` (view-name title space above each panel),
`TITLE_H = 30` (optional one-line sheet caption at the bottom).

Panel origins (top-left corner of each panel's content box, in sheet
coordinates):

```
LEFT.x  = MARGIN
LEFT.y  = MARGIN + LABEL_H + PANEL_H_TOP + GUTTER + LABEL_H
FRONT.x = MARGIN + PANEL_W_LEFT + GUTTER
FRONT.y = LEFT.y
TOP.x   = FRONT.x
TOP.y   = MARGIN + LABEL_H
```

Sheet size:

```
SHEET_W = 2·MARGIN + PANEL_W_LEFT + GUTTER + PANEL_W_FRONT
SHEET_H = 2·MARGIN + 2·LABEL_H + PANEL_H_TOP + GUTTER + PANEL_H_FRONT + TITLE_H
```

**Front-face convention**: the edge where Top touches Front (Top's bottom
edge) and the edge where Left touches Front (Left's right edge) both
represent the object's front face. Depth increases *away* from that shared
edge in both the Top and Left panels. Keep this in mind when placing
front-facing features (openings, controls, cable entries) near those shared
edges so the three views read as the same object.

**Each panel is its own group** — this is the rule that keeps per-shape math
sane:

```xml
<g transform="translate({PANEL.x},{PANEL.y})">
  <!-- draw this view's shapes in LOCAL coords: 0..PANEL_W, 0..PANEL_H -->
</g>
```

## 4. Style

| Element | Style |
|---|---|
| Panel border | `fill:none; stroke:#64748b; stroke-width:1.5` |
| Primary mass (the base body) | `fill:#e2e8f0; stroke:#1e293b; stroke-width:1.75` |
| Called-out feature (lip, boss, slot...) | `fill:#bfdbfe; stroke:#1e3a5f; stroke-width:1.25` |
| View title ("TOP"/"FRONT"/"LEFT") | 15px bold, uppercase, `#334155`, positioned in the `LABEL_H` gap above its panel |
| Feature label | 11px, `#475569`, placed just outside/below the shape it names |

**Never a numeric label.** Feature labels name what something is
("mount boss", "cable slot ×3"), never a size.

Give the root `<svg>` both an explicit `width`/`height` *and* a matching
`viewBox` (`0 0 {SHEET_W} {SHEET_H}`) so it opens at a sane size in a plain
image viewer or browser — nothing here rasterizes it for you.

## 5. Worked example

A small wall-mounted cable-organizer tray: a shallow rounded-rect body, a
raised lip around the rim, three cable-entry notches cut into the front lip,
and one mounting boss in a back corner.

`Wc=16, Dc=10, Hc=4` → `PANEL_W_FRONT=320, PANEL_H_FRONT=80,
PANEL_W_TOP=320, PANEL_H_TOP=200, PANEL_W_LEFT=200, PANEL_H_LEFT=80` →
`SHEET_W=640, SHEET_H=482`. Panel origins: `LEFT=(40,332)`,
`FRONT=(280,332)`, `TOP=(280,66)`.

```xml
<svg xmlns="http://www.w3.org/2000/svg" width="640" height="482" viewBox="0 0 640 482">
  <defs>
    <pattern id="grid-minor" width="20" height="20" patternUnits="userSpaceOnUse">
      <path d="M 20 0 L 0 0 0 20" fill="none" stroke="#dbe6f1" stroke-width="0.5"/>
    </pattern>
    <pattern id="grid-major" width="100" height="100" patternUnits="userSpaceOnUse">
      <rect width="100" height="100" fill="url(#grid-minor)"/>
      <path d="M 100 0 L 0 0 0 100" fill="none" stroke="#aec6de" stroke-width="1"/>
    </pattern>
  </defs>
  <rect width="640" height="482" fill="#ffffff"/>
  <rect width="640" height="482" fill="url(#grid-major)"/>

  <!-- TOP panel: 320x200 at (280,66) -->
  <text x="280" y="58" font-size="15" font-weight="bold" fill="#334155">TOP</text>
  <g transform="translate(280,66)">
    <rect x="0" y="0" width="320" height="200" fill="none" stroke="#64748b" stroke-width="1.5"/>
    <rect x="10" y="10" width="300" height="180" rx="4" fill="#e2e8f0" stroke="#1e293b" stroke-width="1.75"/>
    <rect x="25" y="25" width="270" height="150" rx="3" fill="none" stroke="#1e3a5f" stroke-width="1.25"/>
    <text x="25" y="20" font-size="11" fill="#475569">lip (rim)</text>
    <rect x="70" y="180" width="16" height="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <rect x="152" y="180" width="16" height="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <rect x="234" y="180" width="16" height="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <text x="70" y="198" font-size="11" fill="#475569">cable slots x3 (front edge)</text>
    <circle cx="270" cy="35" r="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <text x="245" y="20" font-size="11" fill="#475569">mount boss</text>
  </g>

  <!-- LEFT panel: 200x80 at (40,332) -->
  <text x="40" y="324" font-size="15" font-weight="bold" fill="#334155">LEFT</text>
  <g transform="translate(40,332)">
    <rect x="0" y="0" width="200" height="80" fill="none" stroke="#64748b" stroke-width="1.5"/>
    <rect x="10" y="15" width="180" height="60" rx="3" fill="#e2e8f0" stroke="#1e293b" stroke-width="1.75"/>
    <rect x="10" y="15" width="180" height="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <text x="10" y="10" font-size="11" fill="#475569">lip</text>
  </g>

  <!-- FRONT panel: 320x80 at (280,332) -->
  <text x="280" y="324" font-size="15" font-weight="bold" fill="#334155">FRONT</text>
  <g transform="translate(280,332)">
    <rect x="0" y="0" width="320" height="80" fill="none" stroke="#64748b" stroke-width="1.5"/>
    <rect x="5" y="15" width="310" height="60" rx="3" fill="#e2e8f0" stroke="#1e293b" stroke-width="1.75"/>
    <rect x="5" y="15" width="310" height="10" fill="#bfdbfe" stroke="#1e3a5f" stroke-width="1.25"/>
    <rect x="60" y="15" width="16" height="10" fill="#ffffff" stroke="#1e3a5f" stroke-width="1.25"/>
    <rect x="152" y="15" width="16" height="10" fill="#ffffff" stroke="#1e3a5f" stroke-width="1.25"/>
    <rect x="244" y="15" width="16" height="10" fill="#ffffff" stroke="#1e3a5f" stroke-width="1.25"/>
    <text x="5" y="10" font-size="11" fill="#475569">lip w/ cable slots</text>
  </g>

  <text x="320" y="470" font-size="12" fill="#64748b" text-anchor="middle">cable-organizer tray -- lo-fi concept</text>
</svg>
```

Notice the pattern each panel follows: a view-title `<text>` sitting in the
`LABEL_H` gap above it, a bordered `<g transform="translate(...)">` at the
panel's origin, a primary-mass shape, one or more feature shapes layered on
top in the lighter blue, and short labels naming each feature — never a
number.

## 6. Pre-write checklist

- [ ] Picked `Wc`/`Dc`/`Hc` from the idea's rough proportions (not copied from
      the worked example above).
- [ ] Grid background covers the full sheet, panels drawn on top of it.
- [ ] Top/Front/Left panels use the shared-edge placement (Top above,
      Left beside Front) with the formulas above — not eyeballed positions.
- [ ] Every feature shape has a text label; no shape is unlabeled and no
      label contains a number.
- [ ] Root `<svg>` has both `width`/`height` and a matching `viewBox`.
