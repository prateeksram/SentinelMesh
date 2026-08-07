# SceneEngine (experimental) - agentic GenieX TV + golden verify + learning

The experimental scene engine kept under [`laptop/`](README.md). Unlike the root engine (which generates a scene **JSON** applied to the golden TV at runtime - see [`../SCENE_ENGINE.md`](../SCENE_ENGINE.md)), this one generates **entire venue TV pages** and verifies each candidate against a functional contract before it can be promoted. It runs as a library / CLI, not inside the game host.

## Flow

1. Load the golden contract ([`scene_contract.py`](scene_contract.py), extracted from the root [`public/tv.html`](../public/tv.html)): required element ids, required symbols (WebSocket wiring, `onState`, `applyScene`), banned patterns (external script src, `javascript:`, `eval(`).
2. Load the learning memory (`logs/scene_memory.jsonl`) and few-shot examples.
3. The **director** (GenieX) drafts a venue JSON: `css`, `overlayHtml`, difficulty, copy.
4. The candidate page is assembled from the golden page + the skin (CSS sanitized, overlay sanitized).
5. **Verify** against the contract - ids present, wiring intact, nothing banned.
6. On failure, a **critic** reframes the prompt and retries (max `GF_SCENE_MAX_ATTEMPTS`), and the failure is appended to memory as a lesson for future generations.
7. Verified pages are **promoted** to `public/scenes/live/level_N.html`; on exhaustion, a template skin on the golden page is used instead.

## Environment

| Var | Default | Meaning |
|---|---|---|
| `GF_GENIEX_URL` | `http://127.0.0.1:18181/v1` | GenieX OpenAI-compatible base |
| `GF_GENIEX_MODEL` | `qualcomm/Qwen3-4B-Instruct-2507:W4A16` | Model id |
| `GF_SCENE_TIMEOUT_S` | `120` | Director/critic timeout (root engine: 90) |
| `GF_SCENE_MAX_LEVEL` | `5` | Campaign cap |
| `GF_SCENE_MAX_ATTEMPTS` | `3` | Generate→verify retries |

## Debug & smoke tests

```powershell
cd laptop
python debug_scene.py       # full loop: contract self-check, generate L1+L4, verify promoted files
python test_scene_gen.py    # levels differ in atmosphere + fingerprint
python test_scene_upload.py # upload/promote path
```

With GenieX down, everything falls back to template skins - generation never hard-fails.
