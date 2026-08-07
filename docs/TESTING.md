# Testing and verification

Every test in the repo, what it covers, how to run it, and its **current verified status** (all statuses below are from real runs on 2026-08-07, Windows 11 / Python 3.14).

Nothing here needs game hardware - all host tests run on the Copilot+ PC alone.

---

## 0. Setup smoke check (start here)

```powershell
python setup_check\verify_setup.py
```

Verifies the Python version and required packages, imports the server, boots it on an ephemeral port, probes `/edge/status`, `/fx/status`, `/hw/status`, and exercises the referee geometry. Exits 0 on success. See [`setup_check/README.md`](../setup_check/README.md).

---

## 1. Host tests (root tree)

The focused unit suite (referee geometry, edge integration, AI100 reports - **20 tests**):

```powershell
python -m pip install pytest      # not in requirements.txt
python -m pytest -q test_referee.py test_unified_edge.py ai100\test_report_engine.py
```

> Don't run a bare repo-wide `pytest` - the script-style harnesses (`test_combined.py`, `test_match.py`, the `laptop/` scene scripts) open network clients during collection. Use the focused command or the rows below.

| Test | Command | Needs | Status |
|---|---|---|---|
| Referee + goal thirds (8 pytest/unittest cases) | `python -m pytest -q test_referee.py` | nothing running | ✅ passes |
| Unified edge path (5 unittest cases) | `python -m unittest test_unified_edge` | nothing running | ✅ passes |
| SceneEngine smoke | `python test_scene_gen.py` | nothing running; GenieX optional | ✅ passes (offline → `source=template`) |
| AI100 report engine (7 pytest cases, incl. sport-aware analytics) | `python -m pytest ai100\test_report_engine.py -q` | `pip install pytest` (not in requirements.txt) | ✅ passes |
| Full 3-sport end-to-end | `python test_combined.py` | free TCP 8080 + UDP 5005; self-launches the server and bridge | ✅ passes (football geometry gates + keeper, darts/basketball rings, campaign levels, sport switching) |
| Phone-striker regression | `python test_match.py` | `server.py` already running (use the fast pacing below) | ✅ passes (score consistency, deliberate kick-3 timeout, replay + ForcePose fields) |
| Full-stack sim | `python e2e_sim.py` | `server.py` running, GenieX up (it asserts `desk == "geniex"`) | requires GenieX; also set `GF_MIN_SHOOT_WINDOW` low or the deliberate-timeout kick blocks 60 s |

Both scripts dispatch on the WebSocket `type` field (the server interleaves `telem_state` / `edge_pose` frames with `state` snapshots), and `test_combined.py` ignores `end` snapshots from a previous match that can remain queued while the post-game report card re-broadcasts - keep both behaviors in mind when writing new clients.

### Speeding up live-server tests

The shoot window is floored at 60 s by default. For fast runs:

```powershell
$env:GF_ANNOUNCE_S="0.3"; $env:GF_COUNTDOWN_S="0.3"; $env:GF_RESOLVE_S="0.4"
$env:GF_SHOOT_WINDOW="5"; $env:GF_MIN_SHOOT_WINDOW="5"
python server.py
```

(`test_combined.py` sets its own pacing for the server it launches.)

---

## 2. Experimental scene-engine tests (`laptop/`)

Script-style smoke tests for the agentic scene generator ([`laptop/README.md`](../laptop/README.md)); GenieX optional (template fallback):

| Test | Command |
|---|---|
| Full generate → verify → promote loop | `cd laptop; python debug_scene.py` |
| Levels differ in atmosphere + fingerprint | `cd laptop; python test_scene_gen.py` |
| Scene upload/promote | `cd laptop; python test_scene_upload.py` |

---

## 3. UNO Q streamer tests

```powershell
python -m pip install -r unoq\requirements.txt   # numpy + opencv-python-headless
python -m unittest unoq.test_sentinel_pose_streamer
```

✅ passes (2 cases: optical-flow tracking on synthetic frames; anchor retention across detector misses). Must be run **from the repo root** - the test imports `unoq.sentinel_pose_streamer` as a namespace package.

---

## 4. Android unit tests

```powershell
$env:JAVA_HOME = "<jdk-17>"
$env:ANDROID_HOME = "<android-sdk>"    # or set sdk.dir in android\local.properties
cd android
.\gradlew.bat :app:testDebugUnitTest
```

JVM-local JUnit tests (no device needed). Report: `android/app/build/reports/tests/testDebugUnitTest/index.html`.

- `EdgeKickEngineTest` - 3 cases: a 10 fps time-based swing fires without three consecutive threshold frames; optical-flow peaks substitute for missing temporal resolution; gameplay gates surface as diagnostics instead of silently swallowing swings.
- `ShotTrajectoryEstimatorTest` - 3 cases: the sampled path reaches the 11 m goal plane; physics are identical across NPU/GPU/CPU/UNO Q source labels; faster kick states produce faster flight.

*(Not re-run in the latest verification pass - requires a local JDK 17 + Android SDK.)*

---

## 5. Manual verification checklist

1. `python server.py` → open http://localhost:8080/tv.html → lobby renders, sport buttons work.
2. `python snapkick_bridge.py` + `python snapkick_sim.py` → TV UNO Q LED turns green, START MATCH enables, kicks resolve every ~4 s.
3. After 5 kicks → NEXT VENUE generation runs (template offline), difficulty rises, rematch keeps the level.
4. `Invoke-RestMethod -Method Post http://localhost:8080/api/report/simulate -ContentType application/json -Body '{"playerName":"Demo"}'` → TV shows the QR report panel; the QR resolves from a phone on the same LAN.
