# SceneEngine — Pillar 3 (agentic GenieX TV + golden verify + learning)

After full time, GenieX drafts a **new venue TV page** (CSS + overlay assembled onto golden `tv.html`). Every candidate is **validated against the golden functional contract**. Failures are recorded; the director reframes. Only verified pages are promoted to `public/scenes/live/`.

## Flow

1. Load golden contract (`scene_contract.py` ← `public/tv.html`)
2. Load learning memory (`logs/scene_memory.jsonl`)
3. Director drafts venue JSON (`css`, `overlayHtml`, difficulty, copy…)
4. Assemble full candidate HTML from golden + skin
5. Verify vs required IDs / WebSocket / `onState` / `applyScene`
6. On fail → critic reframe (max 3 attempts) + memory lesson
7. Promote to `/scenes/live/level_N.html` or fall back to template skin on golden

TV progress bar (`genProgress` + **`genStep`**) shows each step in plain language.

## Models (AI Hub)

```powershell
$env:QAI_HUB_API_TOKEN = "<your token>"
.\laptop\fetch_aihub_models.ps1
geniex pull ai-hub-models/Qwen3-4B-Instruct-2507
geniex serve   # http://127.0.0.1:18181/v1
```

## Env

| Var | Default | Meaning |
|---|---|---|
| `GF_GENIEX_URL` | `http://127.0.0.1:18181/v1` | GenieX base |
| `GF_GENIEX_MODEL` | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | Model id |
| `GF_SCENE_TIMEOUT_S` | `90` | Director/critic timeout |
| `GF_SCENE_MAX_LEVEL` | `5` | Campaign cap |
| `GF_SCENE_MAX_ATTEMPTS` | `3` | Generate→verify retries |

## HTTP

| Endpoint | Purpose |
|---|---|
| `GET /scene/status` | progress, genStep, fingerprint, attempts, contract |
| `GET /scene/brief` | export brief + lessons for external polish |
| `POST /scene/upload` | `{level, css, overlayHtml}` or `{html}` — same golden verifier |

## TV debug

Open `tv.html?debug=1` for the scene HUD (step, fingerprint, tvUrl).

## Aim lock

On `shoot`, aim freezes (phone + server). Feints only in announce/countdown.

## Smoke tests

```powershell
cd laptop
python debug_scene.py
python test_scene_gen.py
```
