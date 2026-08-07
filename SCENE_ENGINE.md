# SceneEngine — Pillar 3 (GenieX venue + difficulty)

Designs the next stadium atmosphere and keeper knobs after full time.

## Models (AI Hub)

```powershell
# Token via env only — never commit
$env:QAI_HUB_API_TOKEN = "<your token>"
.\laptop\fetch_aihub_models.ps1

# LLM
geniex pull ai-hub-models/Qwen3-4B-Instruct-2507
geniex serve   # http://127.0.0.1:18181/v1
# Served id (see geniex list): qualcomm/Qwen3-4B-Instruct-2507:W4A16
```

Depth plate for Neural FX is documented in [`NEURAL_FX.md`](NEURAL_FX.md).

## Env

| Var | Default | Meaning |
|---|---|---|
| `GF_GENIEX_URL` | `http://127.0.0.1:18181/v1` | GenieX OpenAI-compatible base |
| `GF_GENIEX_MODEL` | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | Model id (`geniex list`) |
| `GF_GENIEX` | `1` | Set `0` to skip GenieX desk (local/cloud/templates) |
| `GF_SCENE_TIMEOUT_S` | `90` | Scene JSON generation timeout |
| `GF_SCENE_MAX_LEVEL` | `5` | Campaign cap |

## Badges (TV)

| Badge | Meaning |
|---|---|
| `DESK · GENIEX` | Commentary via GenieX |
| `SCENE · READY` | Last scene from GenieX |
| `SCENE · TEMPLATE` | Fallback atmospheres / difficulty |
| `LVL N/5` | Campaign level (never regresses on rematch) |

## Logs

`laptop/logs/scene_gen.jsonl` — one JSON line per generation:

```json
{"t": 1710000000.0, "level": 3, "source": "geniex|template", "total_ms": 4200}
```

Files under `public/scenes/` are for logging / QUAD / replay only. **The WebSocket snapshot is the sole TV source of truth** (browser never fetches `latest.json`).

## Timing vs power

- App logs (`scene_gen.jsonl`, Desk latency) = **UX timing**
- QUAD `/quad-profile` on the bench = **formal power / NPU** profiler

## Smoke test

```powershell
# Root host uses scene_engine.py. Optional agentic assets live under laptop/.
python test_scene_gen.py
# Or: py -3.13 laptop/test_scene_gen.py
```

Score 1/5 vs 3/5 should differ in atmosphere + `keeperIq`. With GenieX down, `source` is `template`.
