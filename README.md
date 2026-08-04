# GESTURE FOOTBALL ⚽ — solo edition

A penalty shootout you play with your body. Your phone's camera tracks you:
your **raised hand aims** the shot (left / centre / right) and a **fast leg
swing kicks** it. On the laptop, **THE WALL** — an AI goalkeeper — watches
your hand, studies your shot history, and dives.

The twist: the keeper reads your aim with human-like reaction lag
(~0.45 s before the kick). Hold a fake direction, switch your hand at the
last moment, then swing — and send the machine the wrong way.

## Quick start

1. **Run the host** (laptop, same Wi-Fi as the phone):
   ```
   pip install -r requirements.txt
   cd laptop
   python server.py
   ```
2. **Open the TV** on the laptop: `http://localhost:8080/tv.html`
3. **Open the striker page** on the phone: `https://<laptop-ip>:8443/phone.html`
   (see HTTPS below), prop the phone up 2–3 m away so it sees your whole
   body — the badge flips to **FULL BODY ✓** — then press **START MATCH**
   on the TV.

## The ForcePose engine

Kick power isn't a made-up number — the phone measures your kick in **real
Newtons**, implementing the pipeline from
[ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363) fully
on-device:

1. MediaPipe pose → 33 landmarks per frame (as in the paper)
2. Savitzky-Golay temporal smoothing of the foot trajectory (paper §III-B)
3. Torso-normalized metric scale, so pixels become metres (paper §III-B)
4. Central-difference velocity + acceleration features (paper §III-D)
5. Force head: the paper's trained BiLSTM and force-plate dataset aren't
   public, so we substitute rigid-body dynamics — **F = m_leg × a_peak**,
   with leg mass from Winter's anthropometric tables (6.18% of body mass)

Pass your weight for calibrated numbers: `phone.html?kg=82` (default 70).
The live readout is the FORCEPOSE badge on the camera view; every shot's
Newtons appear on the TV and feed the AI commentary.

## How to play

- **Aim** — raise a hand; its position steers the target reticle you'll see
  on the TV: left, centre or right.
- **Kick** — when the TV says **KICK!**, swing your leg fast. ForcePose
  measures the strike in Newtons; 380 N is full power, and enough power can
  beat the keeper even in the right corner.
- **Feint** — THE WALL watches your hand, but it reacts late. Point one way,
  flick your hand to the real corner just before you swing.
- 5 kicks per match. Beat the machine.

## Phone camera & HTTPS

Off `localhost`, browsers block the camera on plain HTTP. Make a self-signed
cert next to `server.py` and the server adds `https://<laptop-ip>:8443`
automatically:
```
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
  -days 365 -subj "/CN=gesture-football"
```
Open the `https://` link on the phone and accept the certificate warning once.
(Android alternative: chrome://flags → "Insecure origins treated as secure" →
add `http://<laptop-ip>:8080`.)

## Turn on the AI Desk (optional — templates work without it)

- **Cloud (Claude):** set `ANTHROPIC_API_KEY=sk-ant-…` then run the server.
  Badge shows **CLAUDE DESK**.
- **On-device (Ollama):** set `GF_LLM_URL=http://localhost:11434/v1/messages`
  Badge shows **LOCAL AI DESK**. `GF_MODEL` overrides the model either way.

## Knobs (env vars)

| var | default | meaning |
|---|---|---|
| `GF_KICKS` | 5 | kicks per match |
| `GF_SHOOT_WINDOW` | 4.0 | seconds to swing before the kick is skied |
| `GF_KEEPER_REACTION` | 0.45 | keeper reads your aim this many s before the kick — the feint window |
| `GF_KEEPER_IQ` | 0.75 | 0 = keeper guesses randomly, 1 = near-psychic |
| `GF_ANNOUNCE_S` / `GF_COUNTDOWN_S` / `GF_RESOLVE_S` | 2.2 / 3.0 / 3.8 | phase pacing |

Kick sensitivity lives in `phone.html` (`KICK_MS = 3.0` m/s trigger,
`F_MAX = 380` N = full power).

## Troubleshooting

| symptom | fix |
|---|---|
| START button greyed out | phone must be connected — check the PHONE LED |
| "CAMERA BLOCKED" on phone | use the https:// link (see above) |
| "STEP BACK" badge stays red | move the phone back until shoulders **and** ankles are in frame |
| kicks not registering | swing faster, or lower `KICK_MS` in phone.html |
| keeper saves everything | lower `GF_KEEPER_IQ`, or learn to feint |
| no commentary upgrades | desk badge shows TEMPLATE DESK → set the key/URL and restart |

## Dev

`laptop/test_match.py` simulates a full match headlessly (run the server with
small `GF_*_S` values first):
```
$env:GF_ANNOUNCE_S="0.1"; $env:GF_COUNTDOWN_S="0.2"; $env:GF_SHOOT_WINDOW="1.0"; $env:GF_RESOLVE_S="0.1"
python laptop/server.py     # terminal 1
python laptop/test_match.py # terminal 2
```
