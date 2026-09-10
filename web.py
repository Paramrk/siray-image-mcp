"""Local web UI. Run: uv run web.py  ->  http://127.0.0.1:8765"""

from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import core

OUTPUT = core.ROOT / "output"
OUTPUT.mkdir(exist_ok=True)


async def index(request):
    return FileResponse(core.ROOT / "index.html")


async def models(request):
    try:
        return JSONResponse({"models": core.list_models(), "default": core.default_model()})
    except Exception as exc:
        return JSONResponse({"models": [core.default_model()],
                             "default": core.default_model(), "error": str(exc)})


async def gallery(request):
    files = sorted(
        (p for p in OUTPUT.iterdir() if p.suffix.lower() in core._IMAGE_EXTS),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return JSONResponse({"images": [f"/output/{p.name}" for p in files[:60]]})


async def generate(request):
    form = await request.json()
    warnings: list[str] = []
    try:
        seed = form.get("seed")
        paths = await request.app.state.run(
            core.generate,
            form.get("prompt", ""),
            form.get("out_dir") or OUTPUT,
            form.get("model") or None,
            form.get("image") or None,
            form.get("aspect_ratio") or None,
            int(seed) if str(seed or "").strip() else None,
            form.get("size") or None,
            True,
            300.0,
            warnings,
        )
    except Exception as exc:  # surface the real reason in the UI
        return JSONResponse({"error": core.explain(exc)}, status_code=400)
    return JSONResponse({"paths": [str(p) for p in paths], "warnings": warnings})


app = Starlette(
    routes=[
        Route("/", index),
        Route("/models", models),
        Route("/gallery", gallery),
        Route("/generate", generate, methods=["POST"]),
        Mount("/output", StaticFiles(directory=OUTPUT), name="output"),
    ]
)

# core.generate blocks on network I/O; keep the event loop free.
from anyio import to_thread  # noqa: E402

app.state.run = to_thread.run_sync

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
