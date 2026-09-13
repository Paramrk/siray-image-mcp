"""MCP server exposing Siray image generation to Claude Code."""

import functools

from mcp.server.mcpserver import MCPServer

import core

mcp = MCPServer("siray", version="0.1.0")


def _explain(fn):
    """Return the reason as text instead of raising.

    An exception reaches the caller as a bare "Error executing tool", which loses the
    part that matters: what to change. A caller that cannot see the reason retries
    blindly, and blind retries are exactly the wasted credits preflight exists to stop.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            return core.explain(exc)

    return wrapper


@mcp.tool()
@_explain
def generate_image(
    prompt: str,
    out_dir: str,
    model: str | None = None,
    image: str | None = None,
    size: str | None = None,
    aspect_ratio: str | None = None,
    seed: int | None = None,
) -> str:
    """Generate an image with Siray and save it into the current project.

    Args:
        prompt: What to draw. Be specific about subject, style, lighting, background.
        out_dir: Absolute path of the folder to save into, e.g. the project's
            assets/images folder. Created if missing.
        model: Siray model id, e.g. "bytedance/seedream-4.5-t2i",
            "google/nano-banana-pro-t2i", "black-forest-labs/flux-1.1-pro-ultra-t2i".
            Omit for the default. Call list_models() for the live list.
        image: Optional reference image for image-to-image / editing — a URL or a
            local file path. When you pass this you MUST also pass an -i2i / -edit
            model (e.g. "google/nano-banana-pro-i2i"); -t2i models reject it.
            Note: bytedance/seedream-* ignores size and aspect ratio entirely.
        size: exact output dimensions, e.g. "1920x1080". Use this whenever the
            user names dimensions. openai/* models render at that size natively;
            for other models the nearest aspect ratio is requested and the file
            is resized to exactly this.
        aspect_ratio: e.g. "16:9", "1:1", "9:16". Ignore when passing size.
        seed: Integer for reproducible output.

    Returns the saved file path(s).
    """
    warnings: list[str] = []
    paths = core.generate(prompt, out_dir, model, image, aspect_ratio, seed, size,
                          warnings_out=warnings)
    lines = [str(p) for p in paths] + [f"note: {w}" for w in warnings]
    lines.append(f"credits used this session — {core.usage_summary()}")
    return "\n".join(lines)


@mcp.tool()
@_explain
def generate_backdrop(
    prompt: str,
    out_dir: str,
    palette: list[str] | str,
    overlay_text_color: str,
    size: str | None = None,
    model: str | None = None,
    tileable: bool = False,
    seed: int | None = None,
) -> str:
    """Generate a background plate that the project's UI can sit on top of.

    STOP: before calling this, read the project's actual colours — the CSS custom
    properties, tailwind.config, theme file, or an existing screenshot — and pass
    them as `palette`. Do not guess them, and do not invent a palette from the
    prompt. Whatever text or UI will sit on this backdrop determines
    `overlay_text_color`. Getting these two right is what stops the backdrop from
    clashing and needing a second paid generation.

    The prompt is rewritten to enforce background-plate discipline (no text, no
    logos, no centre subject, calm negative space, tone set opposite the overlay
    colour), then the saved file is measured locally: worst-region contrast against
    `overlay_text_color`, clutter, drift from `palette`, and edge seam if tileable.

    The file is ALWAYS kept — it is already paid for. A FAILED verdict tells you
    exactly what to change if you choose to regenerate; it is not an automatic retry.

    Args:
        prompt: The scene or texture, e.g. "soft abstract mesh gradient".
        out_dir: Absolute path to save into. Created if missing.
        palette: Hex colours from the project, e.g. ["#0f1115", "#6ea8fe"].
            A single comma-separated string works too.
        overlay_text_color: Hex colour of the text/UI going on top, e.g. "#e7e9ee".
        size: Exact dimensions, e.g. "1920x1080".
        model: Omit for the default. Do not use bytedance/seedream-* for a
            non-square backdrop — it only renders 2048x2048.
        tileable: True for a repeating pattern; the edge seam is then measured.
        seed: Integer for reproducible output.

    Returns the saved path, the measurements, and a PASS/FAILED verdict.
    """
    import backdrop

    warnings: list[str] = []
    paths = core.generate(
        backdrop.compose(prompt, palette, overlay_text_color, tileable),
        out_dir, model, None, None, seed, size, warnings_out=warnings,
    )
    lines = [f"note: {w}" for w in warnings]
    for path in paths:
        ok, report = backdrop.inspect(path, palette, overlay_text_color, tileable)
        lines += [str(path), "PASS" if ok else "FAILED — usable but flawed:"] + report
    lines.append(f"credits used this session — {core.usage_summary()}")
    return "\n".join(lines)


@mcp.tool()
@_explain
def list_models() -> str:
    """List Siray's active image models, live from the API. Default is marked.

    Ids ending in -t2i are text-to-image; -i2i / -edit / -ref2i need an `image`.
    """
    default = core.default_model()
    return "\n".join(
        m + ("  (default)" if m == default else "") for m in core.list_models()
    )


if __name__ == "__main__":
    mcp.run()
