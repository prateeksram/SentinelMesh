# AI100 Post-Match Report

This folder contains the complete Qualcomm Cloud AI100 reporting subsystem for
Gesture Football. The rest of the game only has two small integration points:
`laptop/server.py` queues reports and registers this package's web adapter,
while `laptop/public/tv.html` shows the QR panel.

## What it reads

The report uses the phone telemetry already stored in the laptop `shotmap`:

- target zone and keeper zone;
- normalized power and ForcePose Newtons;
- launch angle, high/low placement, and lateral spin;
- chip/drive strike and kicking foot;
- goal, save, post, or miss result.

No new phone message and no camera upload are required.

## What AI100 does

Qualcomm Cloud AI100 SDXL Turbo generates text-free, performance-conditioned
stadium artwork. The laptop calculates and typesets every number, chart, label,
and comparison. This keeps the output fun without asking an image model to
reproduce telemetry accurately.

If AI100 is unavailable, report generation continues with procedural artwork.
Successful AI100 artwork is cached by normalized prompt.

## Output and privacy

Each match creates:

- a 1600 x 2200 PNG;
- a one-page PDF;
- a mobile landing page;
- a TV QR code that uses `GF_PUBLIC_BASE_URL` or the laptop LAN address.

Assets use unguessable tokens and expire after 30 minutes. Runtime files stay
under `ai100/data/reports/` and `ai100/cache/` and are excluded from Git.

## Configuration

```powershell
Copy-Item ai100\.env.example ai100\.env
# Add AI100_API_KEY to ai100\.env
pip install -r requirements.txt
python laptop\server.py
```

For an ngrok demo, set `GF_PUBLIC_BASE_URL` to the public HTTPS origin so the
download QR works off-LAN.

## Local simulation

With the server running:

```powershell
Invoke-RestMethod -Method Post http://localhost:8080/api/report/simulate `
  -ContentType application/json -Body '{"playerName":"Demo Striker"}'
```

Or create the files directly:

```powershell
python ai100\simulate_report.py --player "Demo Striker"
```

The fixture represents a 4/5 performance and exercises every phone metric.

## Tests

```powershell
python -m pytest ai100\test_report_engine.py -q
```

The tests cover telemetry analytics, pro ranking, AI100 endpoint/model
normalization, PNG/PDF rendering, tokenized storage, QR generation, and the
offline fallback.

## Benchmark note

The report compares the short game sample only with career penalty conversion:
Cristiano Ronaldo 183/219 and Lionel Messi 116/148, using a Transfermarkt
snapshot dated 2026-08-06. It explicitly says that the sample sizes are not
equivalent and that the output is not a professional scouting assessment.
