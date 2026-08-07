# State-driven trajectory pipeline

One trajectory contract, regardless of where pose inference runs:

```
phone camera → NPU / GPU / CPU pose ──┐
                                      ├─► KickKinematicState ─► ShotTrajectoryEstimator ─► host ─► TV
USB camera → UNO Q pose + flow ───────┘
```

## The contract

Pose backends only produce landmarks and timestamps. Full-rate local backends feed `ForcePoseEngine` (football) or `HandThrowEngine` (darts/basketball); the low-rate UNO Q feed uses the time-based `EdgeKickEngine`. All three emit the same **`KickKinematicState`** at the validated swing peak:

- peak torso-scaled foot (or wrist) speed,
- signed lateral and upward velocity,
- swing displacement and lift,
- swing duration and confidence,
- a `source` label - **diagnostics only**.

`PoseAnalyzer` (Android) is the normalization seam: it stamps the active backend label and invokes `ShotTrajectoryEstimator`. A dedicated unit test asserts the estimator's physics are byte-identical across `NPU`/`GPU`/`CPU`/`UNO Q` labels.

## The model (`sentinel.pose-ballistic.v1`)

Foot speed plus normalized effort maps to an inferred ball speed of ~11–25 m/s. The aim zone establishes the lateral target region; signed lateral motion adjusts placement inside it; upward motion, high/low aim, and chip/drive set vertical intent; the spin cue becomes a small constant lateral acceleration. The ball is integrated at 20 ms steps with gravity, speed-dependent drag, and a damped ground bounce, and sampled every 60 ms as `[time, lateral, forward, height]` until the 11 m goal plane (≤ 48 samples on the wire - schema in [`phone_protocol.md`](phone_protocol.md)).

The TV interpolates those samples directly instead of rebuilding a parabola from the match result. A cyan dashed guide marks a state-driven path; the legacy amber arc is used whenever the trajectory is missing or confidence < 0.25.

## Deliberate scope limits

- **Visualization-only:** a monocular body pose cannot observe the ball's true launch velocity or the exact foot-ball contact, so those quantities are *inferred* and shipped with a confidence value - never presented as measured ground truth.
- **The referee stays authoritative:** goal/save/post decisions come from the server's referee (using the kick's `goalX`/`goalZ` when present). A legacy `post` result only bends the end of the drawn path onto the frame so the visible collision stays coherent.

## Run and verify

No new process or port. Start the host, connect the native phone app, and play in any pose mode - `NPU`, `GPU`, `CPU`, and `UNO Q` all send the same schema:

```powershell
python server.py
```

Regression tests:

```powershell
python -m pytest -q test_referee.py test_unified_edge.py          # host-side referee + edge normalization

cd android                                                        # phone-side physics
.\gradlew.bat :app:testDebugUnitTest
```

(Android tests need JDK 17 + Android SDK - see [`android/README.md`](../android/README.md).)
