# Ball classifier - training pipeline (not wired into the game)

Trains an on-device image classifier that recognizes a physical object (tennis ball / basketball / football / dart / nothing) so the phone can one day switch the arena automatically by *showing it a ball*.

**Status: roadmap.** The capture → train → export → predict pipeline below works, but nothing in the game consumes it yet - [`server.py`](../server.py) has no `/api/object` route and imports nothing from this folder. The main game does not need this directory or its heavy dependencies.

## Contents

| File | Purpose |
|---|---|
| [`capture.py`](capture.py) | Collect training images from a webcam: `python capture.py tennis` (SPACE = save, `b` = burst, `q` = quit) → `data/tennis/*.jpg`. Aim for ~150–300 clean frames per class, including a `nothing` class |
| [`train_ball_classifier.py`](train_ball_classifier.py) | MobileNetV2 transfer learning (224², 80/20 split, augmentation; 12 frozen epochs + 6 fine-tune epochs) → `ball_classifier.keras` + `class_names.txt` |
| [`export_model.py`](export_model.py) | Rebuilds an inference-only graph (explicit rescaling in place of the preprocess op) → `web_model/` (TF.js for the phone page) + `ball_classifier.tflite` |
| [`predict.py`](predict.py) | Laptop debug: single-image predict, or live webcam with 8-frame probability averaging (min confidence 0.60) |
| [`phone/index.html`](phone/index.html) | Phone-side TF.js classifier page: rear camera, 224² center crop, classifies every 250 ms with 6-frame smoothing; a label held ≥ 1.2 s at ≥ 0.70 confidence would `POST /api/object` to a host (endpoint **not implemented** in the current server). Loads TF.js from a CDN - needs internet |
| [`requirements.txt`](requirements.txt) | tensorflow, tensorflowjs, opencv-python, numpy (+ flask/pyopenssl/pygame kept for the legacy standalone demo) |

Training artifacts (`data/`, `web_model/`, `*.keras`, `*.tflite`, `class_names.txt`) are generated locally and should not be committed.

## Environment

TensorFlow (and pygame) currently have **no Python 3.14 wheels** - use **Python 3.13** for this folder (the game host itself runs fine on 3.13 or 3.14):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Pipeline

```powershell
python capture.py tennis          # repeat per class, include "nothing"
python train_ball_classifier.py   # → ball_classifier.keras, class_names.txt
python export_model.py            # → web_model/, ball_classifier.tflite
python predict.py [image.jpg]     # sanity-check the trained model
```

## Wiring it into the game (future work)

1. Add a `POST /api/object` route to `server.py` that maps a stable classification to a `sport` change (lobby-only, same rule as the TV buttons).
2. Serve `classifier/phone/index.html` + `web_model/` from the host (HTTPS - the camera needs a secure origin, like `phone.html`).
3. Optionally vendor TF.js locally to keep the no-internet property of the rest of the stack.

Tips if a trained model flip-flops: more varied training images per class, always include `nothing`, and photograph the actual objects with the actual phone camera.
