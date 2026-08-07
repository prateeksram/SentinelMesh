# AI100 Post-Match Report

This folder contains the Qualcomm Cloud AI100 reporting subsystem for QPlay.
The match host only has two small integration points: root `server.py` queues
reports and registers this package's web adapter, while `public/tv.html` shows
the QR panel.

## What it reads

The report uses the phone telemetry already stored in the host `shotmap`:

- target zone and keeper zone (football);
- normalized power and ForcePose Newtons;
- launch angle, high/low placement, and lateral spin;
- chip/drive strike and kicking foot;
- football results (`goal` / `save` / `post` / `wide` / `over`) or
  darts/basketball results (`hit` / `miss` + `points`).

No new phone message and no camera upload are required.

## What AI100 does

Qualcomm Cloud AI100 SDXL Turbo generates text-free, performance-conditioned
venue artwork (football stadium, darts hall, or basketball arena). The host
calculates and typesets every number, chart, label, and comparison. This keeps
the output fun without asking an image model to reproduce telemetry accurately.

If AI100 is unavailable, report generation continues with procedural artwork.
Successful AI100 artwork is cached by normalized prompt.

## Output and privacy

Each match creates:

- a 1600 x 2200 PNG;
- a one-page PDF;
- a mobile landing page;
- a TV QR code that uses `GF_PUBLIC_BASE_URL` or the host LAN address.

Assets use unguessable tokens and expire after 30 minutes. Runtime files stay
under `ai100/data/reports/` and `ai100/cache/` and are excluded from Git.

## Configuration

```powershell
Copy-Item ai100\.env.example ai100\.env
# Add AI100_API_KEY to ai100\.env
pip install -r requirements.txt
python server.py
```

For an ngrok demo, set `GF_PUBLIC_BASE_URL` to the public HTTPS origin so the
download QR works off-LAN.

## Local simulation

With the server running:

```powershell
Invoke-RestMethod -Method Post http://localhost:8080/api/report/simulate `
  -ContentType application/json -Body '{"playerName":"Demo Striker"}'
```

## Tests

```powershell
py -3.13 -m pytest ai100/test_report_engine.py -q
```
