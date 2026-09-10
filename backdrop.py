"""Backdrop discipline: compose a prompt that yields a usable background plate, then
measure the result locally so a bad one is diagnosed instead of blindly regenerated.

Nothing here spends credits. Inspection is Pillow arithmetic on the file already paid for.
"""

import re
from pathlib import Path

from PIL import Image, ImageChops, ImageColor, ImageFilter, ImageStat

# What makes a background plate usable rather than a picture that happens to be behind things.
RULES = (
    "full-bleed background plate, even edge-to-edge composition, "
    "no text, no words, no letters, no numbers, no logos, no watermark, no signature, "
    "no central focal subject, no people, no faces, no framing borders, "
    "large areas of calm negative space for overlaid UI, consistent colour grading, "
    "smooth gradients, low visual noise, subtle and understated"
)
TILEABLE_RULES = "tiles seamlessly, the pattern continues without a visible join across all four edges"

# Thresholds, calibrated against real generations. Tune these, they are judgement calls.
MIN_CONTRAST = 4.5      # WCAG AA for body text, checked per cell, not just on average
BUSY_LIMIT = 12.0       # mean edge energy; above this, overlaid text starts fighting detail
DRIFT_LIMIT = 30.0      # 0-100 distance of the image's dominant colours from your palette
SEAM_LIMIT = 8.0        # 0-100 difference between opposing edges, only checked if tileable

GRID = 6                # contrast is sampled on a GRID x GRID grid of cells


def parse_hex(colour):
    """"#1a2b3c", "1a2b3c" or "#abc" -> (26, 43, 60). Pillow does the parsing."""
    text = str(colour).strip()
    try:
        return ImageColor.getrgb(text if text.startswith("#") else "#" + text)[:3]
    except ValueError:
        raise ValueError(f"colour must be a hex like #1a2b3c, got {colour!r}") from None


def as_palette(value):
    """Accept what agents actually send: a list, or one string of comma/space-separated hex.

    Some clients stringify array arguments. The strict schema rejects those before our
    own error handling runs, which produces an opaque failure and a blind retry.
    """
    if isinstance(value, str):
        value = [part for part in re.split(r"[,\s]+", value.strip()) if part]
    if not value:
        raise ValueError("palette needs at least one colour, e.g. [\"#0f1115\"]")
    return list(value)


def _luminance(rgb):
    """WCAG relative luminance."""
    channels = []
    for value in rgb[:3]:
        srgb = value / 255
        channels.append(srgb / 12.92 if srgb <= 0.04045 else ((srgb + 0.055) / 1.055) ** 2.4)
    red, green, blue = channels
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first, second):
    """WCAG contrast ratio between two RGB colours: 1.0 (identical) to 21.0 (black on white)."""
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def compose(prompt, palette, overlay_text_colour, tileable=False):
    """Build the generation prompt from the caller's intent plus the backdrop rules."""
    swatches = ", ".join(f"#{'%02x%02x%02x' % parse_hex(c)}" for c in as_palette(palette))
    overlay = parse_hex(overlay_text_colour)
    # The backdrop must sit opposite the overlay text, or nothing on top will be readable.
    if _luminance(overlay) > 0.5:
        tone = "dark, low-key, deep shadow values throughout so light text stays readable on top"
    else:
        tone = "light, high-key, pale values throughout so dark text stays readable on top"
    parts = [
        prompt.strip().rstrip("."),
        f"strictly limited colour palette of {swatches}",
        tone,
        RULES,
    ]
    if tileable:
        parts.append(TILEABLE_RULES)
    return ". ".join(parts) + "."


def _dominant_colours(rgb, count=5):
    quantised = rgb.resize((64, 64), Image.BILINEAR).quantize(colors=count, method=Image.MEDIANCUT)
    palette = quantised.getpalette()[: count * 3]
    return [tuple(palette[i * 3: i * 3 + 3]) for i in range(count)]


def _drift(rgb, palette):
    """0-100: how far the image's dominant colours sit from the requested palette."""
    wanted = [parse_hex(c) for c in as_palette(palette)]
    distances = []
    for colour in _dominant_colours(rgb):
        nearest = min(sum((a - b) ** 2 for a, b in zip(colour, want)) ** 0.5 for want in wanted)
        distances.append(nearest)
    return round(100 * (sum(distances) / len(distances)) / 441.67, 1)


def _cell_contrasts(rgb, overlay):
    """Contrast ratio of every cell against the overlay colour, worst case first."""
    cells = rgb.resize((GRID, GRID), Image.BOX)
    return sorted(contrast_ratio(cells.getpixel((x, y)), overlay)
                  for y in range(GRID) for x in range(GRID))


def _busyness(rgb):
    """Mean edge energy: a proxy for clutter that would fight overlaid text."""
    grey = rgb.convert("L").resize((256, 256), Image.BILINEAR)
    return round(ImageStat.Stat(grey.filter(ImageFilter.FIND_EDGES)).mean[0], 1)


def _seam(rgb):
    """0-100: how different opposing edges are. Near zero means it tiles."""
    width, height = rgb.size
    pairs = ((rgb.crop((0, 0, 1, height)), rgb.crop((width - 1, 0, width, height))),
             (rgb.crop((0, 0, width, 1)), rgb.crop((0, height - 1, width, height))))
    worst = max(ImageStat.Stat(ImageChops.difference(a, b)).mean[0] for a, b in pairs)
    return round(100 * worst / 255, 1)


def measure(path, palette, overlay_text_colour, tileable=False):
    """Every number we can get from the file itself. One decode, one RGB conversion."""
    overlay = parse_hex(overlay_text_colour)
    with Image.open(Path(path)) as img:
        rgb = img.convert("RGB")
    contrasts = _cell_contrasts(rgb, overlay)
    return {
        "size": f"{rgb.size[0]}x{rgb.size[1]}",
        "worst_cell_contrast": round(contrasts[0], 2),
        "median_cell_contrast": round(contrasts[len(contrasts) // 2], 2),
        "busyness": _busyness(rgb),
        "palette_drift": _drift(rgb, palette),
        "seam": _seam(rgb) if tileable else None,
    }


def grade(measured, overlay_text_colour, tileable=False):
    """Turn measurements into problems worth telling the caller about."""
    problems = []
    if measured["worst_cell_contrast"] < MIN_CONTRAST:
        problems.append(
            f"contrast {measured['worst_cell_contrast']} in the worst region is below "
            f"{MIN_CONTRAST}:1 — {overlay_text_colour} text there will be hard to read. "
            f"Regenerate asking for a flatter, more uniform tone."
        )
    if measured["busyness"] > BUSY_LIMIT:
        problems.append(
            f"busyness {measured['busyness']} exceeds {BUSY_LIMIT} — too much detail behind "
            f"the UI. Ask for softer gradients and less texture."
        )
    if measured["palette_drift"] > DRIFT_LIMIT:
        problems.append(
            f"palette drift {measured['palette_drift']} exceeds {DRIFT_LIMIT} — the colours "
            f"wandered off your palette. Name the hex codes more forcefully in the prompt."
        )
    if tileable and measured["seam"] is not None and measured["seam"] > SEAM_LIMIT:
        problems.append(
            f"seam {measured['seam']} exceeds {SEAM_LIMIT} — the edges do not meet, so this "
            f"will show a visible join when tiled."
        )

    return problems


def inspect(path, palette, overlay_text_colour, tileable=False):
    """Measure a generated backdrop and grade it.

    Returns (ok, report_lines). Never deletes anything: the file is already paid for.
    """
    measured = measure(path, palette, overlay_text_colour, tileable)
    problems = grade(measured, overlay_text_colour, tileable)
    report = [f"{key}: {value}" for key, value in measured.items() if value is not None]
    return not problems, report + problems
