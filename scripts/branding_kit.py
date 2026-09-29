# Creating a branding kit: several SVG assets, a brand guideline JSON, and a short usage README.
from pathlib import Path, PurePosixPath
import json, zipfile, os

out_dir = Path("data/marcs_branding_kit")
out_dir.mkdir(parents=True, exist_ok=True)

# SVG: primary logo (wordmark + mark)
logo_svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="360" viewBox="0 0 1200 360">
  <defs>
    <linearGradient id="g" x1="0" x2="1">
      <stop offset="0" stop-color="#0b67ff"/>
      <stop offset="1" stop-color="#4cc1ff"/>
    </linearGradient>
  </defs>
  <rect width="100%" height="100%" fill="none"/>
  <!-- Mark -->
  <g transform="translate(60,60)">
    <rect x="0" y="0" width="120" height="120" rx="20" fill="url(#g)"/>
    <text x="60" y="78" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="800" font-size="64" fill="#fff" text-anchor="middle" alignment-baseline="middle">M</text>
  </g>

  <!-- Wordmark -->
  <g transform="translate(220,120)">
    <text x="0" y="0" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="800" font-size="56" fill="#0b67ff">MARCS</text>
    <text x="0" y="36" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="500" font-size="18" fill="#6b7280">Multi-Agent Code Review System</text>
  </g>
</svg>
"""

mark_svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="240" height="240" viewBox="0 0 240 240">
  <defs>
    <linearGradient id="g2" x1="0" x2="1">
      <stop offset="0" stop-color="#0b67ff"/>
      <stop offset="1" stop-color="#4cc1ff"/>
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="240" height="240" rx="32" fill="url(#g2)"/>
  <circle cx="120" cy="90" r="38" fill="#fff" opacity="0.08"/>
  <text x="120" y="140" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="800" font-size="96" fill="#fff" text-anchor="middle" alignment-baseline="middle">M</text>
</svg>
"""

social_card_svg = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="630" viewBox="0 0 1200 630">
  <rect width="1200" height="630" fill="#0b67ff"/>
  <g transform="translate(60,60)">
    <rect width="1080" height="510" rx="20" fill="#fff" opacity="0.06"/>
    <g transform="translate(40,40)">
      <text x="0" y="0" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="800" font-size="48" fill="#fff">MARCS — Multi-Agent Code Review System</text>
      <text x="0" y="64" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="500" font-size="20" fill="#e6f2ff">Crash-safe • Journaled • Deterministic • Safety-first</text>
      <rect x="0" y="120" width="420" height="8" rx="4" fill="#4cc1ff" />
      <text x="0" y="200" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="600" font-size="20" fill="#fff">Highlights</text>
      <text x="0" y="240" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="400" font-size="16" fill="#e6f2ff">• Crash-safe apply with journaling</text>
      <text x="0" y="270" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="400" font-size="16" fill="#e6f2ff">• Human-friendly HTML reports</text>
      <text x="0" y="300" font-family="Inter, Roboto, system-ui, sans-serif" font-weight="400" font-size="16" fill="#e6f2ff">• Safety engine & CLI demo</text>
    </g>
  </g>
</svg>
"""

brand_guidelines = {
    "name": "MARCS",
    "tagline": "Multi-Agent Code Review System — Crash-safe • Journaled • Deterministic",
    "colors": {
        "primary": "#0b67ff",
        "primary_gradient_start": "#0b67ff",
        "primary_gradient_end": "#4cc1ff",
        "accent": "#4cc1ff",
        "muted": "#6b7280",
        "background": "#f7f9fc"
    },
    "fonts": {
        "primary": "Inter, Roboto, system-ui, -apple-system, 'Segoe UI', sans-serif",
        "monospace": "ui-monospace, SFMono-Regular, Menlo, Monaco, 'Courier New', monospace"
    },
    "usage": {
        "logo_clear_space": "Keep at least 16px space on all sides for the mark; for horizontal wordmark, 24px to left",
        "logo_min_size": "Mark: 32x32px, Wordmark: width >= 160px",
        "do_not": [
            "Do not stretch logo non-uniformly",
            "Do not recolor the wordmark other than palette",
            "Do not place logo on noisy backgrounds without padding"
        ]
    },
    "file_list": [
        "marcs-logo.svg",
        "marcs-mark.svg",
        "marcs-social-card.svg",
        "brand-guidelines.json",
        "README_BRANDING_SNIPPET.md"
    ]
}

readme_snippet = """# MARCS Branding Guidelines (Short)

**Primary colors**: #0b67ff (primary), #4cc1ff (accent), #6b7280 (muted).  
**Primary font**: Inter / Roboto / system-ui.  
**Logo**: marcs-logo.svg (use for headers).  
**Mark**: marcs-mark.svg (use for favicon or small UI).

Usage examples:

- Hero header: use `marcs-logo.svg` at 120-200px width.
- Social card: `marcs-social-card.svg` (1200x630) for OpenGraph.
- Favicon: crop `marcs-mark.svg` to 32x32 px (or convert to .ico).

Keep a clear margin around the mark equal to 16px at minimum.
"""

# write files
files = {
    "marcs-logo.svg": logo_svg,
    "marcs-mark.svg": mark_svg,
    "marcs-social-card.svg": social_card_svg,
    "brand-guidelines.json": json.dumps(brand_guidelines, indent=2),
    "README_BRANDING_SNIPPET.md": readme_snippet
}

for name, content in files.items():
    p = out_dir / name
    p.write_text(content, encoding="utf-8")

# create a ZIP
zip_path = Path("data/marcs_branding_kit.zip")
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    for f in out_dir.iterdir():
        zf.write(f, arcname=f.name)

# Print list of created files for user
created = { "files": [str(p) for p in out_dir.iterdir()], "zip": str(zip_path) }
print(created)
