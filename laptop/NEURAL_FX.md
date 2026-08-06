# On-device Neural FX (stadium TV)

Broadcast VFX for [`public/tv.html`](public/tv.html) on this Snapdragon X Elite laptop.

## What you get

| Layer | Where | Needs |
|---|---|---|
| **VFX director** | Browser (Adreno Canvas post-FX) | Always — force / spin / strike drive shake, bloom, shockwave, confetti |
| **Hero plate** | `POST /fx/hero` → bullet-time insert | Always — procedural depth-from-skeleton |
| **NPU depth** | ORT + QNN EP · Depth-Anything-V2 | `models/hero_depth.onnx` from AI Hub |

Phone stays on pose / Whisper / coach. Laptop owns the public stadium look.

**Input path:** if `payload.image` (data-URL still) is present, that RGB is resized to the model input; otherwise a **skeleton silhouette** is rasterized from ForcePose joints and fed to Depth-Anything-V2. No TV camera capture required.

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
    "foot": "R",
    "image": "data:image/jpeg;base64,..."
  }
  ```
  → `{ ok, plate, depthPreview, backend, ms, w, h }` (`plate` is a PNG data-URL)

TV calls `/fx/hero` automatically on resolve when a kick has a result and (ideally) skeleton replay frames.

## AI Hub: Depth-Anything-V2 → `hero_depth.onnx`

```powershell
$env:QAI_HUB_API_TOKEN = "<token>"   # never commit
.\laptop\fetch_aihub_models.ps1
```

Or download the Universal float ONNX zip from AI Hub release assets and copy the `.onnx` to:

```
laptop/models/hero_depth.onnx
```

Public asset (v0.59 float ONNX):

`https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/depth_anything_v2/releases/v0.59.0/depth_anything_v2-onnx-float.zip`

For Hexagon QNN EP, keep `QNN_SDK_ROOT` pointed at QAIRT and prefer a QNN-embedded / device export when available. Absent model or `onnxruntime` → **procedural** plates; match never breaks.

Logs: `laptop/logs/fx.jsonl` (`backend`, `ms`).

## Degrade path

| Failure | Behaviour |
|---|---|
| No `/fx/*` (old server) | Badge `FX · OFF`; Canvas VFX director still runs |
| `/fx/hero` error | Silent; classic bullet-time skeleton only |
| No skeleton frames yet | Hero retries when `skel` arrives on the next state |
| Soft kick vs hard kick | Confetti / shake / bloom scale with ForcePose Newtons |

## Files

- [`neural_fx.py`](neural_fx.py) — procedural + ORT Depth-Anything-V2
- [`server.py`](server.py) — hosts `/fx/*`
- [`public/tv.html`](public/tv.html) — VFX director, post-FX, hero composite
- [`models/`](models/) — optional ONNX weights (gitignored binaries OK)
- [`fetch_aihub_models.ps1`](fetch_aihub_models.ps1) — AI Hub pull/export helper
