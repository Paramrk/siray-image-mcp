"""One runnable check for the parts that can break without the network."""

import tempfile
from pathlib import Path

import backdrop
import core


def _assert_size_routing():
    """The one rule that must never regress: `size` reaches openai/* and nothing else,
    because seedream accepts it and then hangs forever."""
    sent = {}
    real_run, real_download = core._run_task, core.download
    core._run_task = lambda body, timeout, **kw: sent.update(body) or ["u"]
    core.download = lambda *a, **kw: [__import__("pathlib").Path("x.png")]
    core._models_cache = ["google/nano-banana-pro-t2i", "bytedance/seedream-4.5-t2i",
                          "openai/gpt-image-2-t2i"]
    try:
        core.generate("p", "out", model="openai/gpt-image-2-t2i", size="1536x1024")
        assert sent.get("size") == "1536x1024" and "aspect_ratio" not in sent, sent

        # Square is the only geometry seedream can honour, and even then `size` must
        # not be sent: it accepts the parameter and then never finishes the task.
        sent.clear()
        core.generate("p", "out", model="bytedance/seedream-4.5-t2i", size="800x800")
        assert "size" not in sent, f"size must never reach seedream: {sent}"
        assert sent.get("aspect_ratio") == "1:1", sent

        sent.clear()
        core.generate("p", "out", model="google/nano-banana-pro-t2i", size="1920x1080")
        assert "size" not in sent and sent.get("aspect_ratio") == "16:9", sent

        sent.clear()
        core.generate("p", "out", model="google/nano-banana-pro-t2i", aspect_ratio="4:3")
        assert sent.get("aspect_ratio") == "4:3" and "size" not in sent, sent
    finally:
        core._run_task, core.download = real_run, real_download
        core._models_cache = None


def _assert_preflight():
    """Preflight must reject the combinations that waste a paid request."""
    core._models_cache = ["google/nano-banana-pro-t2i", "google/nano-banana-pro-i2i",
                          "bytedance/seedream-4.5-t2i", "openai/gpt-image-2-t2i"]
    try:
        cases = [
            (dict(model="google/nano-banana-pro-x"), "unknown model"),
            (dict(model="google/nano-banana-pro-t2i", image="a.png"), "t2i given an image"),
            (dict(model="google/nano-banana-pro-i2i"), "i2i given no image"),
            (dict(model="bytedance/seedream-4.5-t2i", size="1920x1080"), "seedream non-square"),
        ]
        for kwargs, why in cases:
            try:
                core.preflight(**kwargs)
            except ValueError:
                pass
            else:
                raise AssertionError(f"preflight must reject: {why}")

        assert core.preflight("openai/gpt-image-2-t2i", size="1024x1024") == []
        assert core.preflight("google/nano-banana-pro-i2i", image="a.png") == []
        assert len(core.preflight("google/nano-banana-pro-t2i", size="1920x1080")) == 1
        assert len(core.preflight("bytedance/seedream-4.5-t2i", size="800x800")) == 1

        assert core.model_kind("openai/gpt-image-2-edit") == "i2i"
        assert core.model_kind("alibaba/qwen-image-3-t2i-spicy") == "t2i"
        assert core.model_kind("bytedance/seedream-4.5-ref2i") == "i2i"
    finally:
        core._models_cache = None


def _assert_backdrop():
    from PIL import Image

    assert backdrop.parse_hex("#1a2b3c") == (26, 43, 60)
    assert backdrop.parse_hex("abc") == (170, 187, 204)
    for bad in ("#12345", "nope", ""):
        try:
            backdrop.parse_hex(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parse_hex({bad!r}) should reject")

    # Agents stringify array args; all of these shapes must reach our own code.
    assert backdrop.as_palette(["#0f1115", "#6ea8fe"]) == ["#0f1115", "#6ea8fe"]
    assert backdrop.as_palette("#0f1115") == ["#0f1115"]
    assert backdrop.as_palette("#0f1115,#6ea8fe") == ["#0f1115", "#6ea8fe"]
    assert backdrop.as_palette("#0f1115, #6ea8fe") == ["#0f1115", "#6ea8fe"]
    for empty in ("", "  ", []):
        try:
            backdrop.as_palette(empty)
        except ValueError:
            pass
        else:
            raise AssertionError(f"as_palette({empty!r}) should reject")

    assert round(backdrop.contrast_ratio((0, 0, 0), (255, 255, 255)), 1) == 21.0
    assert round(backdrop.contrast_ratio((80, 80, 80), (80, 80, 80)), 1) == 1.0

    # Light overlay text must steer the prompt dark, and vice versa.
    assert "dark, low-key" in backdrop.compose("x", ["#101010"], "#ffffff")
    assert "light, high-key" in backdrop.compose("x", ["#f0f0f0"], "#000000")
    assert "no text, no words" in backdrop.compose("x", ["#101010"], "#ffffff")
    assert "tiles seamlessly" in backdrop.compose("x", ["#101010"], "#ffffff", tileable=True)

    with tempfile.TemporaryDirectory() as tmp:
        # A flat near-black plate: perfect for white text, and it tiles.
        good = Path(tmp) / "good.png"
        Image.new("RGB", (256, 256), (15, 17, 21)).save(good)
        ok, report = backdrop.inspect(good, ["#0f1115"], "#ffffff", tileable=True)
        assert ok, report

        # Mid-grey: too little contrast for white text, and off the requested palette.
        bad = Path(tmp) / "bad.png"
        Image.new("RGB", (256, 256), (128, 128, 128)).save(bad)
        ok, report = backdrop.inspect(bad, ["#0f1115"], "#ffffff")
        assert not ok and any("contrast" in line for line in report), report
        assert bad.exists(), "inspect must never delete a paid-for image"


def demo():
    assert core._slug("A Red Toy Car, on white!") == "a-red-toy-car-on-white"
    assert core._slug("!!!") == "image"
    assert len(core._slug("x" * 200)) == 40

    assert core._ext("https://x/y/a.PNG?sig=1", None) == ".png"
    assert core._ext("https://x/y/a.jpeg", None) == ".jpg"
    assert core._ext("https://x/y/blob", "image/webp; charset=binary") == ".webp"
    assert core._ext("https://x/y/blob", None) == ".png"

    assert core._unwrap({"data": {"task_id": "t1"}})["task_id"] == "t1"
    assert core._unwrap({"task_id": "t1"})["task_id"] == "t1"
    assert core._output_url("http://a/b.png") == "http://a/b.png"
    assert core._output_url({"url": "http://a/b.png"}) == "http://a/b.png"

    assert core.parse_size("1280x720") == (1280, 720)
    assert core.parse_size(" 800 X 600 ") == (800, 600)
    for bad in ("720", "abc", "1280*", ""):
        try:
            core.parse_size(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"parse_size({bad!r}) should reject")

    assert core.nearest_aspect(1920, 1080) == "16:9"
    assert core.nearest_aspect(1080, 1920) == "9:16"
    assert core.nearest_aspect(1000, 1000) == "1:1"
    assert core.nearest_aspect(1024, 768) == "4:3"

    _assert_size_routing()
    _assert_preflight()
    _assert_backdrop()

    assert core._as_image_ref("https://a/b.png") == "https://a/b.png"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "ref.png"
        src.write_bytes(b"\x89PNG\r\n")
        assert core._as_image_ref(str(src)).startswith("data:image/png;base64,")

        nested = Path(tmp) / "deep" / "assets"
        saved = core.download(
            ["https://a/one.png", "https://a/two"],
            nested,
            "A Red Toy Car",
            fetch=lambda url: (b"bytes-" + url.encode(), "image/webp"),
        )
        assert nested.is_dir(), "out_dir must be created"
        assert [p.suffix for p in saved] == [".png", ".webp"]
        assert all(p.name.startswith("a-red-toy-car-") for p in saved)
        assert saved[0].read_bytes() == b"bytes-https://a/one.png"

        try:
            core.download([], nested, "empty", fetch=lambda url: (b"", None))
        except RuntimeError:
            pass
        else:
            raise AssertionError("empty output list must raise")

    print("ok")


if __name__ == "__main__":
    demo()
