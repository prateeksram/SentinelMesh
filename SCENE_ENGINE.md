# SceneEngine - GenieX venue design + campaign difficulty

After every full time, the host designs the **next venue**: atmosphere (sky, floodlights, crowd, tint), commentary copy, and a harder difficulty tune. Implemented in [`scene_engine.py`](scene_engine.py) + [`geniex_client.py`](geniex_client.py), driven from [`server.py`](server.py) during the `generating` phase.

> The [`laptop/`](laptop/) directory keeps a different, experimental scene engine (library/CLI, not part of the game host) that generates and contract-verifies whole HTML venue pages — see [`laptop/SCENE_ENGINE.md`](laptop/SCENE_ENGINE.md). This document covers the **root** engine used by the live game, which generates a scene JSON applied to the golden `tv.html`.

## Flow

1. Full time → the server computes the next campaign level from your score (never regresses; capped at `GF_SCENE_MAX_LEVEL`).
2. A match context (zone histogram, forces, feints, chip/drive mix, shotmap) is sent to **GenieX** — a local OpenAI-compatible LLM endpoint.
3. GenieX returns a scene JSON: atmosphere + difficulty + copy. Values are clamped to safe ranges, and partial output is merged over per-level default atmospheres.
4. If GenieX is unreachable or times out (`GF_SCENE_TIMEOUT_S`, default 90 s), a built-in **template venue** for that level is used instead — same difficulty curve, `source: "template"`.
5. The scene is applied to the match knobs and broadcast to the TV; JSON copies land in `public/scenes/` and a log line in `logs/scene_gen.jsonl`. **The WebSocket snapshot is the sole TV source of truth** — the browser never fetches `latest.json`.

## Difficulty curve (levels 1 → 5)

| Level | keeperIq | keeperReaction | powerBeat | ringScale | shootWindow* |
|---|---|---|---|---|---|
| 1 | 0.65 | 0.50 | 0.85 | 1.00 | 0 |
| 2 | 0.72 | 0.46 | 0.84 | 0.90 | 0 |
| 3 | 0.78 | 0.42 | 0.83 | 0.80 | 3.0 |
| 4 | 0.85 | 0.38 | 0.82 | 0.70 | 2.6 |
| 5 | 0.92 | 0.34 | 0.80 | 0.60 | 2.2 |

`keeperIq`/`keeperReaction`/`powerBeat` tune football's AI keeper; `ringScale` shrinks the darts/basketball scoring rings.

\* **Known limitation:** the server floors every shoot window at `GF_MIN_SHOOT_WINDOW` (default 60 s), so the level 3+ `shootWindow` values are currently overridden and have no effect in play.

LLM output is clamped: `keeperIq` 0–1, `keeperReaction` 0.30–0.60, `powerBeat` 0.78–0.90, `ringScale` 0.50–1.00; invalid values fall back to the table above.

## Serving GenieX (optional)

```powershell
# Token via env only — never commit
$env:QAI_HUB_API_TOKEN = "<your token>"

geniex pull ai-hub-models/Qwen3-4B-Instruct-2507
geniex serve   # http://127.0.0.1:18181/v1
# Served id (see `geniex list`): qualcomm/Qwen3-4B-Instruct-2507:W4A16
```

The same endpoint powers the commentary desk. Without it, both fall back gracefully (templates).

## Environment

| Var | Default | Meaning |
|---|---|---|
| `GF_GENIEX_URL` | `http://127.0.0.1:18181/v1` | GenieX OpenAI-compatible base |
| `GF_GENIEX_MODEL` | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | Model id |
| `GF_GENIEX` | `1` | `0` skips GenieX (templates + other desks) |
| `GF_SCENE_TIMEOUT_S` | `90` | Scene generation timeout |
| `GF_SCENE_MAX_LEVEL` | `5` | Campaign cap |

## TV badges

| Badge | Meaning |
|---|---|
| `DESK · GENIEX` | Commentary via GenieX |
| `SCENE · READY` | Last scene came from GenieX |
| `SCENE · TEMPLATE` | Template atmosphere + difficulty (offline path) |
| `LVL N/5` | Campaign level (kept on rematch, wiped on abort) |

## Smoke test

```powershell
python test_scene_gen.py
```

Generates scenes for a 1/5 and a 3/5 score, they should differ in atmosphere and `keeperIq`. With GenieX down, `source` is `template` and generation is instant.
