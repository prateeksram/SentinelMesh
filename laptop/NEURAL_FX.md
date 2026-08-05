# On-device Neural FX (stadium TV)

Broadcast VFX for [`public/tv.html`](public/tv.html) on this Snapdragon X Elite laptop.

## What you get

| Layer | Where | Needs |
|---|---|---|
| **VFX director** | Browser (Adreno Canvas post-FX) | Always — force / spin / strike drive shake, bloom, shockwave, confetti |
| **Hero plate** | `POST /fx/hero` → bullet-time insert | Always — procedural depth-from-skeleton |
| **Optional NPU** | ORT + QNN EP | Drop-in ONNX + `onnxruntime` |

Phone stays on pose / Whisper / coach. Laptop owns the public stadium look.

## Run

```powershell
cd C:\Users\qc_de\SentinelMesh\laptop
python server.py
```

Open `http://localhost:8080/tv.html`. Ticker badge:

- `FX · NPU-READY` — procedural hero (default, no model install)
- `FX · QNN` / `FX · CPU` — onnxruntime loaded with `models/hero_depth.onnx`

## API

- `GET /fx/status` → `{ ok, backend, model, detail }`
- `POST /fx/hero` JSON body:
  ```json
  {
    "frames": [{ "t": 0, "p": [[x,y,z], ...] }],
    "force": 210,
    "result": "goal",
    "height": "H",
    "spin": -0.2,
    "strike": "drive",
    "foot": "R"
  }
  ```
  → `{ ok, plate, depthPreview, backend, ms, w, h }` (`plate` is a PNG data-URL)

TV calls `/fx/hero` automatically on resolve when a kick has a result and (ideally) skeleton replay frames.

## Optional: real ONNX on Hexagon

1. Install an **ARM64** onnxruntime build that matches this Python:
   ```powershell
   python -m pip install onnxruntime
   ```
   For QNN, use a Qualcomm/ORT build that ships `QNNExecutionProvider` and keep `QNN_SDK_ROOT` pointed at QAIRT (already set on this machine if AI Stack is installed).

2. Export or download a small depth/enhance ONNX and place it at:
   ```
   laptop/models/hero_depth.onnx
   ```

3. Restart `python server.py`. Startup log should show `Neural FX : QNN` or `CPU`.

If ORT/QNN/model is missing, the server **always** falls back to procedural plates — the match never breaks.

## Degrade path

| Failure | Behaviour |
|---|---|
| No `/fx/*` (old server) | Badge `FX · OFF`; Canvas VFX director still runs |
| `/fx/hero` error | Silent; classic bullet-time skeleton only |
| No skeleton frames yet | Hero retries when `skel` arrives on the next state |
| Soft kick vs hard kick | Confetti / shake / bloom scale with ForcePose Newtons |

## Files

- [`neural_fx.py`](neural_fx.py) — procedural + optional ORT
- [`server.py`](server.py) — forwards `height` / `spin` / `strike` / `foot`; hosts `/fx/*`
- [`public/tv.html`](public/tv.html) — VFX director, post-FX, hero composite
- [`models/`](models/) — optional ONNX weights (gitignored binaries OK)
