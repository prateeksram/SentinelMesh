# `laptop/` - experimental SceneEngine assets

This directory is **not a second server**. It holds the experimental **agentic scene generator** and its assets; the only match host is the root [`server.py`](../server.py). (An earlier revision of this tree carried a full parallel host - that was removed when the project consolidated on the root engine.)

## What lives here

| File | Purpose |
|---|---|
| [`scene_engine.py`](scene_engine.py) | Agentic venue generation: GenieX drafts whole HTML venue pages (CSS + overlay assembled onto the golden root [`public/tv.html`](../public/tv.html)), each candidate is verified against a functional contract, failures feed a critic reframe loop with a persistent lessons memory, and only verified pages are promoted to `public/scenes/live/` |
| [`scene_contract.py`](scene_contract.py) | The golden contract, extracted from the root `tv.html`: required element ids, required symbols (WebSocket wiring, `onState`, `applyScene`), banned patterns (external script src, `javascript:`, `eval(`) |
| [`debug_scene.py`](debug_scene.py) | CLI driver: contract self-check, generate level 1 + 4, verify the promoted files |
| [`test_scene_gen.py`](test_scene_gen.py) / [`test_scene_upload.py`](test_scene_upload.py) | Smoke tests for generation and the upload/promote path |
| [`fetch_aihub_models.ps1`](fetch_aihub_models.ps1) | AI Hub fetch helper that writes into `laptop/models/` (note: the root Neural FX engine reads the **root** `models/` dir - see [`NEURAL_FX.md`](NEURAL_FX.md)) |
| [`public/`](public/) | QPlay brand assets + the `scenes/` output directory |

Imports resolve against the repo root (`geniex_client`, the golden `tv.html`), so run everything from this folder with the root on the path - the scripts handle that themselves.

## Difference vs the root SceneEngine

The root [`scene_engine.py`](../scene_engine.py) (used by the live game) generates a scene **JSON** that skins the golden TV at runtime. This experimental engine generates **entire TV pages** offline with contract verification and self-improving prompt memory. See [`SCENE_ENGINE.md`](SCENE_ENGINE.md).

## Run

```powershell
cd laptop
python debug_scene.py       # full generate → verify → promote loop
python test_scene_gen.py    # levels differ in atmosphere + fingerprint
python test_scene_upload.py # upload/promote path
```

GenieX optional - without it, generation falls back to template skins. Env: `GF_GENIEX_URL`, `GF_GENIEX_MODEL`, `GF_SCENE_TIMEOUT_S` (default `120` here vs `90` in the root engine), `GF_SCENE_MAX_LEVEL` (5), `GF_SCENE_MAX_ATTEMPTS` (3).
