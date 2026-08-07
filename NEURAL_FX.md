# Neural FX - Stadium TV hero plates

Broadcast VFX for [`public/tv.html`](public/tv.html) on the Copilot+ PC (Snapdragon X Elite). Implemented in [`neural_fx.py`](neural_fx.py), served by [`server.py`](server.py).

## The three layers

| Layer | Where it runs | Needs |
|---|---|---|
| **VFX director** | Browser (Canvas post-FX on the Adreno GPU) | Nothing - force / spin / strike drive shake, bloom, shockwave, confetti |
| **Hero plate** | `POST /fx/hero` → bullet-time insert | Nothing - procedural depth-from-skeleton, pure stdlib PNG encoding |
| **NPU depth upgrade** | ONNX Runtime (+ QNN EP) · Depth-Anything-V2 | `models/depth_anything_v2.onnx` (or `hero_depth.onnx`), `pip install onnxruntime numpy` |

The phone owns pose / Whisper / coach; the laptop owns the public stadium look. **The match never depends on FX** and every failure degrades to a simpler visual.

**Input path:** if the request carries `payload.image` (a data-URL still; Requires Pillow to decode), that RGB is resized to the model input; otherwise a skeleton silhouette is rasterized from the replay joints and fed to the depth model. No TV camera capture is involved.

## Run

```powershell
python server.py
```

Open http://localhost:8080/tv.html. The ticker badge tells you which path is live:

- `FX · NPU-READY` - procedural hero plates (default, no model installed)
- `FX · QNN` / `FX · CPU` - onnxruntime loaded the depth model (QNN EP used when `QNN_SDK_ROOT` points at an installed QAIRT SDK)

## API

- `GET /fx/status` → `{ ok, backend, model, detail }` - `backend` ∈ `procedural` | `cpu` | `qnn`
- `POST /fx/hero` with:

  ```json
  {
    "frames": [{ "t": 0, "p": [[x, y, z]] }],
    "force": 210,
    "result": "goal",
    "height": "H",
    "spin": -0.2,
    "strike": "drive",
    "foot": "R",
    "image": "data:image/jpeg;base64,..."
  }
  ```

  → `{ ok, plate, depthPreview, backend, ms, w, h }` - `plate` is a PNG data-URL. Plates are tinted by result (goal = amber, save = cyan, other = chalk) with intensity scaled by ForcePose Newtons (`force / 380`, floor 0.35).

The TV calls `/fx/hero` automatically on resolve when a kick has a result and (ideally) skeleton replay frames.

## Installing the depth model

```powershell
python -m pip install onnxruntime numpy
$env:QAI_HUB_API_TOKEN = "<token>"   # never commit tokens
.\fetch_aihub_models.ps1
```

The script downloads the public Depth-Anything-V2 float ONNX release (falling back to a `qai_hub_models` export targeting Snapdragon X Elite) and writes `models\depth_anything_v2.onnx` (+ `.data`) and the `hero_depth.onnx` alias. Model resolution order in `neural_fx.py`: `models/hero_depth.onnx` → `models/depth_anything_v2.onnx`, preferring the latter when its external `.data` file is present.

Public asset (v0.59 float ONNX): `https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models/depth_anything_v2/releases/v0.59.0/depth_anything_v2-onnx-float.zip`

Weights are **gitignored** - every clone starts procedural.

## Degrade path

| Failure | Behaviour |
|---|---|
| No `onnxruntime` / no model / ORT load error | Procedural plates; badge `FX · NPU-READY` |
| `/fx/hero` error | Silent; classic bullet-time skeleton only |
| No skeleton frames yet | Hero retries when `skel` arrives on the next state |
| Soft kick vs hard kick | Confetti / shake / bloom scale with Newtons |

Timing log: `logs/fx.jsonl` (one line per plate: `backend`, `ms`).
