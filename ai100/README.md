# AI100 post-match scouting report

Self-contained reporting subsystem: after full time the host renders a **1600×2200 scouting-report PNG**, a one-page **PDF**, a mobile **landing page**, and a **TV QR code** for download. Implemented in [`report_engine.py`](report_engine.py) (analytics + rendering + AI100 client) and [`web.py`](web.py) (HTTP routes).

**Integration points:** the host [`server.py`](../server.py) queues a report at full time and registers this package's web adapter, and [`public/tv.html`](../public/tv.html) renders the QR panel. The coupling is three duck-typed members (`game.report_card`, `game.queue_report(...)`, `game.broadcast()`), so the subsystem stays portable.

## What the report reads

Only the shot telemetry already in the host's shotmap - no new phone message, no camera upload:

- target zone and keeper zone; normalized power and ForcePose Newtons;
- launch angle, high/low placement, lateral spin, chip/drive strike, kicking foot;
- goal / save / post / wide / miss result.

Derived analytics include conversion rate, force consistency, aim **unpredictability** (Shannon entropy over L/C/R), keeper-fooled count, a nickname/style classifier, and an S/A/B/C performance grade.

**Sport-aware:** the report adapts to the match's sport (`queue_report(..., sport=)`, inferred from the shotmap when absent). Keeper-related metrics and the pro-benchmark comparison render for football only; darts and basketball get ring-scoring analytics and sport-specific artwork prompts and labels.

## What Cloud AI100 does (and doesn't)

Qualcomm Cloud AI100 (via the Cirrascale AI Suite, OpenAI-compatible `images/generations`, default model `stabilityai/sdxl-turbo`) generates **text-free, performance-conditioned stadium artwork** only. Every number, chart, label, and comparison is computed and typeset locally with PIL - an image model is never asked to reproduce telemetry.

**Offline fallback:** any AI100 failure (no key, network, timeout) silently switches to procedural artwork - the report always renders. Successful artwork is cached by normalized prompt under `ai100/cache/`.

## Output and privacy

Assets are stored under `ai100/data/reports/` with **unguessable tokens** (`secrets.token_urlsafe`) and **expire after 30 minutes**. Note: `ai100/data/`, `ai100/cache/`, and `ai100/.env` are **not yet in `.gitignore`** - don't commit them (see the root README's known limitations). The QR URL uses `GF_PUBLIC_BASE_URL` when set, else the laptop's LAN address - set it to an ngrok HTTPS origin for off-LAN demos.

## Configuration

```powershell
Copy-Item ai100\.env.example ai100\.env    # then set AI100_API_KEY inside
python -m pip install -r requirements.txt  # repo root requirements cover ai100 too
python server.py
```

| Var | Default | Meaning |
|---|---|---|
| `AI100_API_KEY` | - | Without it: procedural artwork (still fully functional) |
| `AI100_BASE_URL` | `https://aisuite.cirrascale.com/apis/v2` | API base |
| `AI100_IMAGE_ENDPOINT` | - | Full endpoint override (wins over base URL) |
| `AI100_MODEL` | `stabilityai/sdxl-turbo` | Aliases (`sdxl-turbo`, `stable-diffusion-xl`) are normalized on Cirrascale |
| `AI100_IMAGE_SIZE` | `512x512` | Artwork size |
| `AI100_TIMEOUT_SECONDS` | `240` (floor 30) | Request timeout |
| `AI100_ENV_FILE` | `ai100/.env` → `<repo>/.env` | Dotenv path override |
| `GF_PUBLIC_BASE_URL` | LAN autodetect | Origin baked into QR links |
| `GF_ENABLE_REPORT_SIM` | `0` | `1` opens `/api/report/simulate` to non-localhost |

`.env` values never override variables already set in the shell.

## Try it without playing a match

With the server running (localhost only, by design):

```powershell
Invoke-RestMethod -Method Post http://localhost:8080/api/report/simulate `
  -ContentType application/json -Body '{"playerName":"Demo Striker"}'
```

Or generate the files directly, no server needed:

```powershell
python ai100\simulate_report.py --player "Demo Striker"
```

The fixture is a deterministic 4/5 performance that exercises every phone metric.

## Tests

```powershell
python -m pip install pytest        # not in requirements.txt
python -m pytest ai100\test_report_engine.py -q
```

Seven cases, no network: telemetry analytics + pro ranking on the fixture, sport-aware darts/basketball analytics, PNG/PDF rendering without AI100, endpoint derivation, model-alias normalization, and tokenized storage + QR + expiry.

## Benchmark note

Football reports compare your short 5-kick sample against career penalty conversion - Cristiano Ronaldo 183/219 and Lionel Messi 116/148 (Transfermarkt snapshot, see `PRO_SNAPSHOT_DATE` in `report_engine.py`) - and explicitly state that the sample sizes aren't equivalent and the output is not a professional scouting assessment. Darts and basketball reports skip the pro comparison.
