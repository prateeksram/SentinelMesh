# `laptop/models/` - legacy Neural FX weight drop

[`../fetch_aihub_models.ps1`](../fetch_aihub_models.ps1) downloads Depth-Anything-V2 ONNX weights into this folder, but the game's Neural FX engine reads the **root** [`models/`](../../models/) directory only ([`../../neural_fx.py`](../../neural_fx.py)).

To enable NPU depth plates, either run the root `fetch_aihub_models.ps1` (recommended - see [`../../models/README.md`](../../models/README.md) and [`../../NEURAL_FX.md`](../../NEURAL_FX.md)), or copy any `.onnx`/`.data` files from here into the root `models/`. Weights are gitignored either way.
