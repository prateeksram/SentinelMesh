"""
On-device neural FX for the stadium TV (resolve hero pass).

Always available: procedural depth / glow plate from ForcePose skeleton frames.
Optional upgrade: onnxruntime (+ QNN EP when QAIRT is installed) if a model
lives under laptop/models/hero_depth.onnx.
"""

from __future__ import annotations

import base64
import json
import math
import os
import time
from pathlib import Path

ROOT = Path(__file__).parent
MODELS = ROOT / "models"
# Prefer hero_depth.onnx; fall back to AI Hub export basename (external .data sibling).
HERO_ONNX = MODELS / "hero_depth.onnx"
if not HERO_ONNX.exists():
    HERO_ONNX = MODELS / "depth_anything_v2.onnx"
# If hero_depth.onnx was copied without rewriting external-data paths, use the Hub basename.
_ALT = MODELS / "depth_anything_v2.onnx"
if HERO_ONNX.name == "hero_depth.onnx" and _ALT.exists() and (MODELS / "depth_anything_v2.data").exists():
    HERO_ONNX = _ALT


_ORT_SESSION = None
_ORT_BACKEND = None  # "qnn" | "cpu" | None
_STATUS = {
    "ok": True,
    "backend": "procedural",
    "model": None,
    "detail": "procedural depth-from-skeleton",
}


def _try_load_ort():
    """Best-effort ORT (+ QNN) load. Never raises — procedural always works."""
    global _ORT_SESSION, _ORT_BACKEND, _STATUS
    if _ORT_SESSION is not None or not HERO_ONNX.exists():
        return
    try:
        import onnxruntime as ort  # type: ignore
    except ImportError:
        _STATUS = {
            "ok": True,
            "backend": "procedural",
            "model": None,
            "detail": "onnxruntime not installed — using procedural FX",
        }
        return

    providers = []
    qnn_root = os.environ.get("QNN_SDK_ROOT", "")
    if qnn_root and Path(qnn_root).exists():
        providers.append("QNNExecutionProvider")
    providers.append("CPUExecutionProvider")

    try:
        sess = ort.InferenceSession(str(HERO_ONNX), providers=providers)
        used = sess.get_providers()[0] if sess.get_providers() else "CPUExecutionProvider"
        backend = "qnn" if "QNN" in used else "cpu"
        _ORT_SESSION = sess
        _ORT_BACKEND = backend
        _STATUS = {
            "ok": True,
            "backend": backend,
            "model": HERO_ONNX.name,
            "detail": f"onnxruntime · {used}",
        }
    except Exception as e:  # noqa: BLE001 — degrade gracefully
        _STATUS = {
            "ok": True,
            "backend": "procedural",
            "model": None,
            "detail": f"ORT load failed ({e}) — procedural FX",
        }


def status() -> dict:
    _try_load_ort()
    return dict(_STATUS)


def _png_b64(rgba: bytes, w: int, h: int) -> str:
    """Encode raw RGBA into a PNG data-URL (stdlib only)."""
    import struct
    import zlib

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter none
        raw.extend(rgba[y * stride : (y + 1) * stride])
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")

def _procedural_plate(frames: list, meta: dict, size: int = 192) -> dict:
    """Synthesize a depth-ish glow plate from skeleton joints (always works)."""
    w = h = size
    # Accumulate joint heat into a soft depth field.
    depth = [0.0] * (w * h)
    if not frames:
        frames = []

    # Prefer mid / peak-swing frame.
    mid = frames[len(frames) // 2] if frames else None
    samples = []
    if mid and isinstance(mid.get("p"), list):
        samples.append(mid["p"])
    for fr in frames[:: max(1, len(frames) // 6)] if frames else []:
        p = fr.get("p") if isinstance(fr, dict) else None
        if isinstance(p, list):
            samples.append(p)

    def splat(nx: float, ny: float, amp: float, rad: float):
        # nx, ny roughly in [-1, 1] / screen-ish; MediaPipe-style y down.
        cx = int((0.5 + nx * 0.35) * (w - 1))
        cy = int((0.35 + ny * 0.45) * (h - 1))
        r = max(2, int(rad * size))
        r2 = r * r
        for dy in range(-r, r + 1):
            yy = cy + dy
            if yy < 0 or yy >= h:
                continue
            for dx in range(-r, r + 1):
                xx = cx + dx
                if xx < 0 or xx >= w:
                    continue
                d2 = dx * dx + dy * dy
                if d2 > r2:
                    continue
                fall = 1.0 - d2 / r2
                depth[yy * w + xx] += amp * fall * fall

    for pts in samples:
        for i, q in enumerate(pts):
            if not q or len(q) < 2:
                continue
            try:
                x, y = float(q[0]), float(q[1])
            except (TypeError, ValueError):
                continue
            # Feet / kicking leg hotter.
            amp = 1.35 if i in (27, 28, 31, 32, 25, 26) else 0.55
            splat(x, y, amp, 0.12 if i in (31, 32) else 0.09)

    # Force / result tint the plate.
    force = float(meta.get("force") or 0)
    result = (meta.get("result") or "").lower()
    intensity = max(0.35, min(1.0, force / 380.0))
    if result == "goal":
        tint = (255, 196, 0)  # amber
    elif result == "save":
        tint = (62, 199, 244)  # cyan
    else:
        tint = (244, 247, 241)

    mx = max(depth) or 1.0
    rgba = bytearray(w * h * 4)
    for i, v in enumerate(depth):
        n = (v / mx) ** 0.72
        a = int(min(255, n * 220 * intensity + (18 if n > 0.02 else 0)))
        # Soft radial vignette so the plate reads as a broadcast insert.
        y, x = i // w, i % w
        rx = (x / (w - 1) - 0.5) * 2
        ry = (y / (h - 1) - 0.5) * 2
        vig = max(0.0, 1.0 - math.sqrt(rx * rx + ry * ry) * 0.85)
        a = int(a * (0.35 + 0.65 * vig))
        o = i * 4
        rgba[o] = tint[0]
        rgba[o + 1] = tint[1]
        rgba[o + 2] = tint[2]
        rgba[o + 3] = a

    return {
        "depthPreview": _png_b64(bytes(rgba), w, h),
        "plate": _png_b64(bytes(rgba), w, h),
        "backend": "procedural",
        "w": w,
        "h": h,
    }


def _result_tint(meta: dict) -> tuple[int, int, int]:
    result = (meta.get("result") or "").lower()
    if result == "goal":
        return (255, 196, 0)
    if result == "save":
        return (62, 199, 244)
    return (244, 247, 241)


def _decode_image_rgb(image_b64: str | None, size: int) -> list[list[tuple[int, int, int]]] | None:
    """Decode optional data-URL / base64 still → size×size RGB rows. None on failure."""
    if not image_b64 or not isinstance(image_b64, str):
        return None
    try:
        raw = image_b64
        if "," in raw and raw.strip().startswith("data:"):
            raw = raw.split(",", 1)[1]
        data = base64.b64decode(raw)
    except Exception:
        return None
    # Prefer Pillow when present; otherwise skip still path.
    try:
        from io import BytesIO
        from PIL import Image  # type: ignore

        im = Image.open(BytesIO(data)).convert("RGB").resize((size, size))
        px = list(im.getdata())
        return [px[y * size : (y + 1) * size] for y in range(size)]
    except Exception:
        return None


def _silhouette_rgb(frames: list, size: int = 518) -> list[list[tuple[int, int, int]]]:
    """Rasterize a white-on-black joint silhouette for Depth-Anything input."""
    heat = [0.0] * (size * size)
    mid = frames[len(frames) // 2] if frames else None
    samples = []
    if mid and isinstance(mid.get("p"), list):
        samples.append(mid["p"])
    step = max(1, len(frames) // 6) if frames else 1
    for fr in frames[::step] if frames else []:
        p = fr.get("p") if isinstance(fr, dict) else None
        if isinstance(p, list):
            samples.append(p)

    def splat(nx: float, ny: float, amp: float, rad: float):
        cx = int((0.5 + nx * 0.35) * (size - 1))
        cy = int((0.35 + ny * 0.45) * (size - 1))
        r = max(2, int(rad * size))
        r2 = r * r
        for dy in range(-r, r + 1):
            yy = cy + dy
            if yy < 0 or yy >= size:
                continue
            for dx in range(-r, r + 1):
                xx = cx + dx
                if xx < 0 or xx >= size:
                    continue
                d2 = dx * dx + dy * dy
                if d2 > r2:
                    continue
                fall = 1.0 - d2 / r2
                heat[yy * size + xx] += amp * fall * fall

    for pts in samples:
        for i, q in enumerate(pts):
            if not q or len(q) < 2:
                continue
            try:
                x, y = float(q[0]), float(q[1])
            except (TypeError, ValueError):
                continue
            amp = 1.35 if i in (27, 28, 31, 32, 25, 26) else 0.55
            splat(x, y, amp, 0.12 if i in (31, 32) else 0.09)

    mx = max(heat) or 1.0
    rows: list[list[tuple[int, int, int]]] = []
    for y in range(size):
        row = []
        for x in range(size):
            n = heat[y * size + x] / mx
            v = int(min(255, n * 255))
            row.append((v, v, v))
        rows.append(row)
    return rows


def _parse_hw(shape) -> tuple[int, int]:
    """Best-effort H,W from ORT input shape (NCHW or NHWC). Default 518."""
    try:
        dims = [int(d) if isinstance(d, (int, float)) and int(d) > 0 else None for d in shape]
    except Exception:
        return 518, 518
    nums = [d for d in dims if d is not None and d > 3]
    if len(nums) >= 2:
        return nums[-2], nums[-1]
    if len(nums) == 1:
        return nums[0], nums[0]
    return 518, 518


def _nchw_float(rgb_rows: list[list[tuple[int, int, int]]], h: int, w: int):
    """ImageNet-ish normalized NCHW float32 contiguous list → numpy if available."""
    import array

    # Resize nearest if needed
    src_h, src_w = len(rgb_rows), len(rgb_rows[0]) if rgb_rows else 0
    mean = (0.485, 0.456, 0.406)
    std = (0.229, 0.224, 0.225)
    chw = [0.0] * (3 * h * w)
    for y in range(h):
        sy = min(src_h - 1, int(y * src_h / h)) if src_h else 0
        for x in range(w):
            sx = min(src_w - 1, int(x * src_w / w)) if src_w else 0
            r, g, b = rgb_rows[sy][sx] if src_h else (0, 0, 0)
            for c, val in enumerate((r, g, b)):
                chw[c * h * w + y * w + x] = ((val / 255.0) - mean[c]) / std[c]
    try:
        import numpy as np  # type: ignore

        return np.asarray(chw, dtype=np.float32).reshape(1, 3, h, w)
    except ImportError:
        # ORT can accept nested lists on some builds; prefer numpy.
        raise RuntimeError("numpy required for ORT depth inference")


def _depth_to_plate(depth, meta: dict, out_w: int = 192, out_h: int = 192) -> dict:
    """Colorize a 2D depth array into the hero PNG plate."""
    try:
        import numpy as np  # type: ignore

        arr = np.asarray(depth, dtype=np.float32).squeeze()
        if arr.ndim != 2:
            arr = arr.reshape(arr.shape[-2], arr.shape[-1])
        # Downsample nearest to plate size
        ys = (np.linspace(0, arr.shape[0] - 1, out_h)).astype(np.int32)
        xs = (np.linspace(0, arr.shape[1] - 1, out_w)).astype(np.int32)
        small = arr[ys][:, xs]
        lo, hi = float(small.min()), float(small.max())
        span = (hi - lo) or 1.0
        norm = (small - lo) / span
    except Exception:
        return _procedural_plate([], meta, size=out_w)

    force = float(meta.get("force") or 0)
    intensity = max(0.35, min(1.0, force / 380.0))
    tint = _result_tint(meta)
    rgba = bytearray(out_w * out_h * 4)
    for y in range(out_h):
        for x in range(out_w):
            n = float(norm[y, x]) ** 0.72
            a = int(min(255, n * 220 * intensity + (18 if n > 0.02 else 0)))
            rx = (x / (out_w - 1) - 0.5) * 2
            ry = (y / (out_h - 1) - 0.5) * 2
            vig = max(0.0, 1.0 - math.sqrt(rx * rx + ry * ry) * 0.85)
            a = int(a * (0.35 + 0.65 * vig))
            o = (y * out_w + x) * 4
            rgba[o] = tint[0]
            rgba[o + 1] = tint[1]
            rgba[o + 2] = tint[2]
            rgba[o + 3] = a
    return {
        "depthPreview": _png_b64(bytes(rgba), out_w, out_h),
        "plate": _png_b64(bytes(rgba), out_w, out_h),
        "backend": _ORT_BACKEND or "cpu",
        "w": out_w,
        "h": out_h,
        "detail": _STATUS.get("detail"),
    }


def _log_fx(backend: str, ms: int) -> None:
    try:
        log_dir = ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / "fx.jsonl").open("a") as f:
            f.write(json.dumps({"t": time.time(), "backend": backend, "ms": ms}) + "\n")
    except Exception:
        pass


def _ort_hero(image_b64: str | None, frames: list, meta: dict) -> dict | None:
    """Run Depth-Anything-V2 (or compatible) via ORT; else None → procedural."""
    _try_load_ort()
    if _ORT_SESSION is None:
        return None
    try:
        inp = _ORT_SESSION.get_inputs()[0]
        h, w = _parse_hw(inp.shape)
        rgb = _decode_image_rgb(image_b64, max(h, w))
        if rgb is None:
            rgb = _silhouette_rgb(frames, size=max(h, w))
        tensor = _nchw_float(rgb, h, w)
        outs = _ORT_SESSION.run(None, {inp.name: tensor})
        if not outs:
            return None
        return _depth_to_plate(outs[0], meta)
    except Exception as e:  # noqa: BLE001
        _STATUS["detail"] = f"ORT infer failed ({e}) — procedural FX"
        return None


def hero(payload: dict) -> dict:
    """
    Build a resolve hero plate.

    Expected JSON keys (all optional except we need *something*):
      frames: skeleton replay frames [{t, p:[[x,y,z],...]}, ...]
      force, result, height, spin, strike, foot
      image: optional data-URL / base64 still (preferred over silhouette)
    """
    t0 = time.perf_counter()
    frames = payload.get("frames") if isinstance(payload.get("frames"), list) else []
    meta = {
        "force": payload.get("force"),
        "result": payload.get("result"),
        "height": payload.get("height"),
        "spin": payload.get("spin"),
        "strike": payload.get("strike"),
        "foot": payload.get("foot"),
    }
    out = _ort_hero(payload.get("image"), frames, meta)
    if out is None:
        out = _procedural_plate(frames, meta)
    out["ms"] = int((time.perf_counter() - t0) * 1000)
    out["ok"] = True
    if "detail" not in out:
        out["detail"] = _STATUS.get("detail")
    _log_fx(out.get("backend", "procedural"), out["ms"])
    return out
