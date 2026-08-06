# Ball Arena — phone classifies, UNO Q tracks the kick, laptop plays

Show an object (tennis ball / basketball / football / dart) to your
**phone** — it classifies it on-device and tells the **laptop**, which
generates a themed arena + background. An **Arduino UNO Q** runs a pose
pipeline (camera + MediaPipe) that detects your kick and solves the
trajectory; the laptop uses that predicted impact point to place the
shot, animate it, and score it.

## Architecture

```
Phone browser (TF.js classifier) --HTTPS POST /api/object------> Laptop
UNO Q pose pipeline (snapkick)   --UDP 5005, snapkick.pose.v1--> Laptop
Laptop: Flask + UDP listener --> pygame game (arena, shots, score)
```

The UNO Q's `trajectory` block is authoritative: `predicted_goal_x` /
`predicted_goal_z` (meters at the goal plane) is the impact point;
`shot_power`, `launch_speed`, and `predicted_apex_m` drive the animation.

## Project structure

```
ball-game/
├── README.md
├── requirements.txt
├── game_server.py            # MAIN APP: Flask + UDP + pygame game
├── snapkick_sim.py           # fake UNO Q: emits snapkick.pose.v1 packets
├── phone/
│   └── index.html            # served to the phone; runs the classifier
├── capture.py                # collect training images from a webcam
├── train_ball_classifier.py  # transfer learning (MobileNetV2)
├── export_model.py           # -> web_model/ (TF.js for the phone)
├── predict.py                # laptop-side model debug tool
├── unoq_imu_sender.py        # LEGACY: old IMU-only sender (superseded)
├── data/                     # training images, one folder per class
│   ├── tennis/ basketball/ football/ darts/ nothing/
├── web_model/                # created by export_model.py
├── class_names.txt           # created by training
└── ball_classifier.keras     # created by training
```

## Environment setup (Windows, one-time)

The ML/game stack needs **Python 3.13** — pygame and TensorFlow have no
wheels for 3.14 yet, so installs fail there with build errors. Install
Python 3.13.x from python.org (it coexists with 3.14), then from this
folder:

```
py -3.13 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install pygame flask pyopenssl          # game layer
pip install tensorflow tensorflowjs opencv-python numpy   # classifier layer
```

Re-run `.venv\Scripts\activate` in every new terminal (prompt shows
`(.venv)` when active). On macOS/Linux: `python3.13 -m venv .venv` and
`source .venv/bin/activate`.

## Running & testing, layer by layer

Test in this order — each layer adds one piece, so failures localize.

### Layer 0 — game alone (keyboard, no model, no hardware)
```
python game_server.py
```
Press **1–4** to generate an arena, **arrow keys** to aim, **SPACE** to
shoot. Verifies rendering, physics, and scoring.

### Layer 1 — simulated UNO Q (tests the snapkick pipeline)
Keep the game running (press 3 for the football arena). In a second
terminal:
```
python snapkick_sim.py
```
The crosshair turns **green** (pose-driven) and wanders with the
simulated shot direction; every ~4 s a kick flies in and scores
GOAL! / POST! / wide. The HUD shows `input: pose`, player detection,
kick confidence, and the sender's fps. To run the sim from another
machine: `python snapkick_sim.py <laptop-ip>`.

### Layer 2 — real UNO Q
Point the board's pipeline at the UDP address printed when the game
starts (`UNO Q: send UDP to <ip>:5005`). No laptop changes needed —
the server accepts `snapkick.pose.v1` as-is.

### Layer 3 — phone classifier
1. Put images in `data/<class>/` (include a `nothing` class; add
   phone-camera photos of your actual objects for best accuracy).
   Quick collection: `python capture.py tennis` etc.
2. `python train_ball_classifier.py`
3. `python export_model.py`   → writes `web_model/`
4. Run `python game_server.py`; on the phone (same Wi-Fi) open the
   printed `https://<laptop-ip>:8443`, accept the certificate warning,
   allow camera. A stable, confident detection switches the arena.

All layers run together for the full experience.

## snapkick.pose.v1 — what the laptop consumes

From each UDP packet the game reads:
- `seq` — stale/reordered packets are dropped
- `people[].score` — highest-score person is used
- `kick_candidate` + `kick_confidence` (>= 0.60) — kick trigger,
  debounced 0.8 s per `track_id` (a kick spans several 10 fps frames)
- `trajectory.predicted_goal_x/z` — impact point, meters at goal plane
  (x lateral, negative = left; z = height). Also used as live aim.
- `shot_power`, `launch_speed`, `predicted_apex_m` — animation
- `shot_direction_deg` — aim fallback when no trajectory yet
- `diagnostics.fps` — shown in HUD

Legacy `{yaw,pitch,force,event}` packets are still accepted.

### Calibration constants (top of game_server.py)
- `GOAL_DIST_M = 11.0` — kick spot to goal plane
- `GOAL_HALF_W_M = 3.66`, `GOAL_H_M = 2.44` — regulation goal
- `SNAPKICK_MIN_CONF = 0.60`, `KICK_COOLDOWN_S = 0.8`
Adjust these if the UNO Q calibration differs.

### Scoring
Football: inside the goal mouth = GOAL! (more points toward corners),
within ~15 cm of frame = POST!, else wide. Other arenas: ring-based,
with the metric impact mapped onto the rings.

## Troubleshooting

- **pygame/tensorflow install fails with build errors** — you're on
  Python 3.14; use the 3.13 venv above.
- **Phone page won't load** — different network, or firewall blocking
  inbound TCP 8443 (allow Python through).
- **Phone camera black** — you opened `http://`; camera requires the
  `https://` page.
- **No UNO Q data in HUD** — firewall blocking UDP 5005, wrong target
  IP on the board, or check with `snapkick_sim.py` from the same
  machine first.
- **Kicks double-count** — raise `KICK_COOLDOWN_S`; ghost kicks —
  raise `SNAPKICK_MIN_CONF`.
- **Arena flips randomly (phone layer)** — too few/unvaried training
  images; add a `nothing` class and phone photos, retrain.
