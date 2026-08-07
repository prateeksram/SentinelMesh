# `models/` — optional Neural FX depth weights

Home of the Depth-Anything-V2 ONNX model that upgrades the TV's hero plates from procedural to NPU depth ([`../NEURAL_FX.md`](../NEURAL_FX.md)). **Weights are gitignored** — a fresh clone has only this README, and the game runs fine that way (procedural plates).

Expected files (resolution order in [`../neural_fx.py`](../neural_fx.py)):

- `depth_anything_v2.onnx` + `depth_anything_v2.data` — canonical (external-weights format)
- `hero_depth.onnx` — alias, also accepted

Fetch (writes both names):

```powershell
python -m pip install onnxruntime numpy
$env:QAI_HUB_API_TOKEN = "<token>"   # never commit tokens
.\fetch_aihub_models.ps1             # run from the repo root
```
