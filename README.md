# Siray Image MCP

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Image generation backed by the [Siray](https://www.siray.ai/) API (Flux, Nano Banana,
Seedream, GPT-Image, and 50+ other models behind one key), wired up two ways:

1. **An MCP server** — any MCP-capable AI agent (Claude Code, Codex, Antigravity, Cursor,
   Windsurf, VS Code, Claude Desktop, Gemini CLI...) can generate images and backdrops
   directly into your project.
2. **A local web page** — `uv run web.py` for a prompt box, model picker and gallery.

Built for generating real project assets (hero images, icons, product shots, UI
backdrops) without burning API credits on doomed or unusable generations — see
[Not wasting credits](#not-wasting-credits) and [Backdrops](#backdrops) below.

## Quickstart

```bash
git clone https://github.com/Paramrk/siray-image-mcp.git
cd siray-image-mcp
uv sync
cp .env.example .env        # then paste your Siray key into it
uv run test_core.py         # sanity check, no network
```

Get a key at [siray.ai](https://www.siray.ai/) → console → API keys.

## Use from an AI agent

See **[INSTALL.md](INSTALL.md)** for the exact config for Claude Code, Codex, Antigravity,
Cursor, Windsurf, VS Code, Claude Desktop and Gemini CLI, plus the two mistakes that break
most pastes (a relative `uv` command GUI agents can't find; VS Code's config using
`servers` instead of `mcpServers`).

Once connected, the agent has three tools:

| tool | for |
|---|---|
| `generate_image(prompt, out_dir, model?, image?, size?, aspect_ratio?, seed?)` | any image, saved straight into your project |
| `generate_backdrop(prompt, out_dir, palette, overlay_text_color, ...)` | a background plate that won't clash with your UI — see [Backdrops](#backdrops) |
| `list_models()` | the live model catalogue |

In any project, just ask: *"generate a 1920x1080 hero image of X into `public/images`"*.

## Backdrops

`generate_backdrop` requires `palette` and `overlay_text_color` — the agent has to read
your project's actual colours (CSS variables, tailwind config, theme file) and decide what
text goes on top *before* it can call this tool. That decision is what makes the backdrop
match the first time instead of costing a second generation.

The prompt is rewritten with background-plate rules (no text, no logos, no centre subject,
calm negative space, tone forced opposite the overlay colour), then the saved file is
graded locally — no API call:

| measure | means | fails above/below |
|---|---|---|
| `worst_cell_contrast` | WCAG contrast against your overlay colour, on a 6×6 grid | below 4.5 |
| `busyness` | edge energy; clutter that fights overlaid text | above 12 |
| `palette_drift` | 0–100 distance of the image's dominant colours from your palette | above 30 |
| `seam` | difference between opposing edges, only checked if `tileable=True` | above 8 |

Calibrated against real generations: a purpose-built backdrop measured 7.13 / 2.0 / 7.6.
Ordinary images used as backdrops measured ~1.0 contrast and 25–39 drift. The thresholds
sit in that gap — they're judgement calls, tune them in [`backdrop.py`](backdrop.py).

A `FAILED` verdict never deletes the file and never auto-retries — it's already paid for,
so it's kept, and the report says exactly what to change if you regenerate.

## Not wasting credits

`core.preflight()` refuses requests that are certain to fail or hang, *before* anything is
submitted — every rule below is something observed failing against the live API:

- an unknown model id (the API answers `503`) — suggests the closest real ids
- a `-t2i` model given a reference `image`, or an `-i2i` model given none
- `size` on `bytedance/seedream-*`, which accepts the parameter and then **never finishes**
- a malformed `size` string, a bad palette hex, or a reference file that doesn't exist

Refusals return as `REFUSED (nothing was generated, no credit spent): <what to change>` in
under a second — both the MCP tools and the web UI surface the reason, not just "error",
because an opaque failure is what causes an agent to retry blindly and waste the credit
this exists to save.

## Dimensions

Pass `size="1920x1080"` and you get exactly that, on any model — how it gets there differs
by family, handled automatically:

| model family | what happens |
|---|---|
| `openai/gpt-image-*` | rendered at that exact size by the API |
| `google/nano-banana-*`, `black-forest-labs/flux-*` | nearest aspect ratio requested, then resized locally to your exact pixels |
| `bytedance/seedream-*` | ignores size and aspect ratio entirely (always 2048²) — `preflight` refuses anything non-square rather than silently cropping it |

## Security: no key reaches the browser or the agent's context

The Siray API key lives in `.env` (gitignored) and is read once, server-side, by
[`core._key()`](core.py). Nothing else touches it:

- **Web UI**: the browser only ever calls this project's own local server (`/generate`,
  `/models`, `/gallery`). `index.html` never talks to `api.siray.ai` and contains no key,
  token, or Authorization header — [`web.py`](web.py) does that server-side, in Python,
  and returns only file paths and text back to the page.
- **MCP server**: the agent talks to `server.py` over a local stdio pipe — no network
  endpoint, no browser, nothing an agent's own context window or a webpage could read the
  key out of. The key never appears in a tool's input, output, or error text (checked: see
  `core.explain()` and every `raise` in `core.py` — they carry paths and model ids, never
  headers or the key itself).
- **`.gitignore`** excludes `.env`, `.venv/`, `output/`, `__pycache__/` and `uv.lock`.
  Only `.env.example` (empty) is committed.

If you fork this and add a *hosted* deployment (not local), put the key behind your own
backend the same way — never ship it in client-side JS.

## Models

Ids end in `-t2i` (text-to-image) or `-i2i` / `-edit` / `-ref2i` (image-to-image, needs
`image`). Default is `google/nano-banana-pro-t2i`. A few worth knowing:

- `openai/gpt-image-2-t2i`, `openai/gpt-image-2.5-flare-t2i` — exact sizes, ChatGPT's models
- `black-forest-labs/flux-1.1-pro-ultra-t2i` — very high resolution
- `bytedance/seedream-4.5-t2i` — square only
- `google/nano-banana-pro-i2i` — best for editing an existing image

`list_models()` (tool or `core.list_models()`) always reflects the live catalogue.

## Local web UI

```bash
uv run web.py
```

→ <http://127.0.0.1:8765>. Prompt box, live model picker, exact size, aspect ratio, seed,
optional reference image, and a gallery of everything in `output/`.

## Testing

```bash
uv run test_core.py
```

No network. Covers filename slugging, extension sniffing, size parsing, aspect snapping,
preflight refusals, palette normalisation (agents send arrays, comma strings, and JSON
strings — all three are accepted), contrast math, and the seedream size guard.

## Project layout

| file | purpose |
|---|---|
| [`core.py`](core.py) | Siray API client: submit, poll, download, preflight |
| [`backdrop.py`](backdrop.py) | prompt composition + local image grading for backdrops |
| [`server.py`](server.py) | MCP server — the three tools above |
| [`web.py`](web.py) / [`index.html`](index.html) | local web UI |
| [`test_core.py`](test_core.py) | the test suite |
| [`INSTALL.md`](INSTALL.md) | per-agent connection config |

## API notes (learned the hard way)

- Async only: `POST /v1/images/generations/async` → `task_id`, then poll
  `GET .../async/{task_id}`.
- `status` can flip to `SUCCESS` a beat *before* `outputs` is populated — poll until both.
- On success, `fail_reason` (confusingly) contains the image URL. Ignore it.
- Output URLs are `api.siray.ai/redirect/...`; redirects are followed by hand in
  [`core._http_fetch`](core.py) because httpx's own follower choked on one.

## Contributing

Issues and PRs welcome. Keep additions to the same shape: a rule in `core.preflight()` or
`backdrop.py` should trace back to something actually observed failing against the live
API, with a test in `test_core.py`.

## License

[MIT](LICENSE)
