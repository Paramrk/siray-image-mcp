"""Siray image generation: submit an async task, poll it, download the results.

Everything else in this project (MCP server, web UI) is a thin wrapper over generate().
"""

import base64
import difflib
import math
import mimetypes
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import httpx

ROOT = Path(__file__).parent
API_BASE = "https://api.siray.ai/v1"

# Model ids end in -t2i (text-to-image), -i2i / -edit / -ref2i (image-to-image).
# The default honours aspect_ratio; override with SIRAY_MODEL in .env.
DEFAULT_MODEL = "google/nano-banana-pro-t2i"
_models_cache = None

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}

# Ratios Siray understands. Anything else is snapped to the nearest of these.
_ASPECTS = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:3": 4 / 3, "3:4": 3 / 4,
            "3:2": 1.5, "2:3": 2 / 3, "21:9": 21 / 9, "9:21": 9 / 21}

# Only these honour an exact pixel `size`. Sending `size` to seedream hangs the task.
_EXACT_SIZE_PREFIXES = ("openai/",)

# Ignores both size and aspect_ratio: always returns 2048x2048.
_GEOMETRY_BLIND = ("bytedance/seedream",)

SUCCESS = ("succeeded", "success", "completed", "finished", "done")
FAILURE = ("failed", "error", "cancelled", "canceled")


def load_env(path=None):
    """Read KEY=VALUE lines from .env. Existing env vars win."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _key():
    load_env()
    key = os.environ.get("SIRAY_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            f"SIRAY_API_KEY is not set. Put a line `SIRAY_API_KEY=<your key>` "
            f"in {ROOT / '.env'} (copy .env.example)."
        )
    return key


def default_model():
    return os.environ.get("SIRAY_MODEL", "").strip() or DEFAULT_MODEL


def list_models(refresh=False):
    """Live list of Siray's active image models. Cached for the process lifetime."""
    global _models_cache
    if _models_cache is None or refresh:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(
                f"{API_BASE}/models", headers={"Authorization": f"Bearer {_key()}"}
            )
            _check(response, "models")
            _models_cache = sorted(
                m["id"]
                for m in response.json().get("data", [])
                if "image-generation" in (m.get("supported_endpoint_types") or [])
                and m.get("status", "active") == "active"
            )
    return _models_cache


def _unwrap(payload):
    """Siray wraps the submit response in {"data": {...}}; status responses are flat."""
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload if isinstance(payload, dict) else {}


def _as_image_ref(image):
    """Pass URLs and data URIs through; turn a local file into a data URI."""
    if image.startswith(("http://", "https://", "data:")):
        return image
    path = Path(image).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"Reference image not found: {path}")
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def parse_size(size):
    """Turn "1280x720" into (1280, 720). Accepts x, X, * or the multiplication sign."""
    match = re.fullmatch(r"\s*(\d{2,5})\s*[xX*×]\s*(\d{2,5})\s*", str(size))
    if not match:
        raise ValueError(f"size must look like 1280x720, got {size!r}")
    return int(match.group(1)), int(match.group(2))


def nearest_aspect(width, height):
    """Closest ratio Siray understands, compared in log space so 2:1 and 1:2 are fair."""
    target = math.log(width / height)
    return min(_ASPECTS, key=lambda name: abs(math.log(_ASPECTS[name]) - target))


def model_kind(model):
    """"t2i", "i2i" or "unknown", from the id suffix (-spicy / -test are decoration)."""
    stem = re.sub(r"-(spicy|test)$", "", model)
    for suffix, kind in (("-t2i", "t2i"), ("-i2i", "i2i"), ("-edit", "i2i"), ("-ref2i", "i2i")):
        if stem.endswith(suffix):
            return kind
    return "unknown"


def size_strategy(model):
    """How a model family handles geometry: exact pixels, fixed square, or ratio only."""
    if model.startswith(_EXACT_SIZE_PREFIXES):
        return "exact"
    if model.startswith(_GEOMETRY_BLIND):
        return "blind"
    return "ratio"


def explain(exc):
    """One taxonomy for both front ends: a refusal cost nothing, a failure may have."""
    if isinstance(exc, (ValueError, FileNotFoundError)):
        return f"REFUSED (nothing was generated, no credit spent): {exc}"
    return f"FAILED: {type(exc).__name__}: {exc}"


def preflight(model, image=None, size=None):
    """Reject requests that are certain to fail or hang, BEFORE they cost a credit.

    Raises ValueError on a doomed request; returns a list of non-fatal warnings.
    Every rule here is something observed failing against the live API.
    """
    warnings = []
    known = list_models()
    if model not in known:
        near = difflib.get_close_matches(model, known, 3)
        raise ValueError(
            f"Unknown model {model!r} — the API answers 503 model_not_found for this."
            + (f" Closest matches: {', '.join(near)}" if near else " Call list_models().")
        )

    kind = model_kind(model)
    if image and kind == "t2i":
        raise ValueError(
            f"{model} is text-to-image and rejects a reference image. "
            f"Use its -i2i/-edit sibling, e.g. {model.replace('-t2i', '-i2i')}."
        )
    if not image and kind == "i2i":
        raise ValueError(f"{model} is image-to-image and needs an `image`. Use a -t2i model instead.")

    if size:
        width, height = parse_size(size)
        strategy = size_strategy(model)
        if strategy == "blind":
            ratio = nearest_aspect(width, height)
            if ratio != "1:1":
                raise ValueError(
                    f"{model} ignores size and aspect ratio (it always returns 2048x2048), so a "
                    f"{ratio} request would be produced as a square and then cropped. "
                    f"Use google/nano-banana-pro-t2i or an openai/* model for {size}."
                )
            warnings.append(f"{model} renders 2048x2048; the file will be resized to {size}.")
        elif strategy == "ratio":
            warnings.append(
                f"{model} picks its own resolution at the nearest ratio "
                f"({nearest_aspect(width, height)}); the file is resized to exactly {size}."
            )
    return warnings


def _check(response, what):
    if response.status_code >= 400:
        raise RuntimeError(f"Siray {what} failed [{response.status_code}]: {response.text[:400]}")


def _run_task(body, timeout, poll_interval=2.0):
    """Submit the generation task and poll until it produces output URLs."""
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60.0) as client:
        response = client.post(f"{API_BASE}/images/generations/async", json=body, headers=headers)
        _check(response, "submit")
        task_id = _unwrap(response.json()).get("task_id")
        if not task_id:
            raise RuntimeError(f"Siray returned no task_id: {response.text[:400]}")

        deadline = time.monotonic() + timeout
        status = "pending"
        while time.monotonic() < deadline:
            response = client.get(f"{API_BASE}/images/generations/async/{task_id}", headers=headers)
            _check(response, "poll")
            task = _unwrap(response.json())
            status = str(task.get("status", "")).lower()
            outputs = task.get("outputs") or []
            # Siray can flip to SUCCESS a beat before outputs land, so require both.
            if status in SUCCESS and outputs:
                return [_output_url(o) for o in outputs]
            if status in FAILURE:
                raise RuntimeError(f"Siray task {task_id} failed: {task.get('fail_reason')}")
            time.sleep(poll_interval)
        raise TimeoutError(f"Siray task {task_id} still '{status}' after {timeout:.0f}s")


def _output_url(output):
    if isinstance(output, str):
        return output
    return output.get("url") or output.get("image_url") or ""


def _slug(prompt):
    return re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40] or "image"


def _ext(url, content_type):
    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix in _IMAGE_EXTS:
        return ".jpg" if suffix == ".jpeg" else suffix
    guessed = mimetypes.guess_extension((content_type or "").split(";")[0].strip()) or ""
    return {".jpe": ".jpg", ".jpeg": ".jpg"}.get(guessed, guessed) or ".png"


def _http_fetch(url, hops=5):
    """Fetch, following redirects by hand: Siray's /redirect/ links have been seen
    handing back a Location that httpx's own redirect follower chokes on."""
    with httpx.Client(timeout=120.0) as client:
        for _ in range(hops):
            response = client.get(url)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise RuntimeError(f"Redirect with no Location from {url}")
                url = location if "://" in location else urljoin(url, location)
                continue
            response.raise_for_status()
            return response.content, response.headers.get("content-type")
    raise RuntimeError(f"Too many redirects fetching {url}")


def _resize_exact(path, size):
    """Force the saved file to exactly `size`, cropping to fill if the ratio differs."""
    from PIL import Image, ImageOps

    with Image.open(path) as img:
        if img.size == size:
            return
        is_jpeg = path.suffix == ".jpg"
        fitted = ImageOps.fit(img.convert("RGB") if is_jpeg else img, size,
                              method=Image.LANCZOS)
        fitted.save(path, quality=95) if is_jpeg else fitted.save(path)


def download(urls, out_dir, prompt, fetch=_http_fetch, size=None):
    """Save each URL into out_dir as <slug>-<timestamp>-<n>.<ext>. Returns the paths."""
    out = Path(out_dir).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = _slug(prompt)
    saved = []
    for index, url in enumerate(urls, 1):
        if not url:
            continue
        data, content_type = fetch(url)
        path = out / f"{slug}-{stamp}-{index}{_ext(url, content_type)}"
        path.write_bytes(data)
        if size:
            _resize_exact(path, size)
        saved.append(path)
    if not saved:
        raise RuntimeError("Siray reported success but returned no image URLs.")
    return saved


def generate(prompt, out_dir, model=None, image=None, aspect_ratio=None, seed=None,
             size=None, exact=True, timeout=300.0, warnings_out=None):
    """Generate image(s) and save them to out_dir. Returns the saved paths.

    size: e.g. "1280x720" for specific dimensions. Only openai/* models take exact
        pixels from the API; for every other family the nearest aspect ratio is
        requested and the downloaded file is resized to hit `size` on the nose
        (pass exact=False to keep the model's own resolution at that ratio).
    image: URL or local path -> image-to-image. Needs an -i2i / -edit model.
    """
    if not prompt or not prompt.strip():
        raise ValueError("prompt is required")

    model = model or default_model()
    # Fail here rather than burning a request on a combination that cannot work.
    found = preflight(model, image=image, size=size)
    if warnings_out is not None:
        warnings_out.extend(found)

    body = {"model": model, "prompt": prompt.strip()}
    wanted = parse_size(size) if size else None

    if wanted and size_strategy(model) == "exact":
        body["size"] = f"{wanted[0]}x{wanted[1]}"
    elif wanted:
        # Never send `size` to these: seedream accepts it and then never finishes.
        body["aspect_ratio"] = aspect_ratio or nearest_aspect(*wanted)
    elif aspect_ratio:
        body["aspect_ratio"] = aspect_ratio

    if image:
        body["image"] = _as_image_ref(image)
    if seed is not None:
        body["seed"] = int(seed)

    return download(_run_task(body, timeout), out_dir, prompt,
                    size=wanted if (wanted and exact) else None)
