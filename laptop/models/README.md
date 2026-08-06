# AI Hub models for the laptop stack

Depth-Anything-V2 lives here for ORT/QNN hero plates:

- `depth_anything_v2.onnx` + `depth_anything_v2.data` (canonical — external weights)
- `hero_depth.onnx` alias (optional)

```powershell
$env:QAI_HUB_API_TOKEN = "<token>"   # never commit
..\fetch_aihub_models.ps1
```

See [`../NEURAL_FX.md`](../NEURAL_FX.md) and [`../SCENE_ENGINE.md`](../SCENE_ENGINE.md).
