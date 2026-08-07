# State-driven trajectory visualization

SentinelMesh produces one trajectory contract regardless of where pose
inference runs:

```text
phone camera -> NPU / GPU / CPU pose --+
                                      +-> shared kick state -> trajectory -> host -> TV
USB camera -> UNO Q pose + flow ------+
```

The inference backend only produces landmarks and timestamps. Local phone
backends use `ForcePoseEngine`; lower-rate remote backends use the time-based
`EdgeKickEngine`. Both emit the same `KickKinematicState` at the validated
swing peak:

- peak torso-scaled foot speed;
- signed lateral and upward foot velocity;
- swing displacement and lift;
- swing duration and state confidence;
- source label for diagnostics only.

`PoseAnalyzer` is the normalization seam. It attaches the active backend label
and invokes `ShotTrajectoryEstimator`; the estimator never switches behavior
for NPU, GPU, CPU, or UNO Q.

## Visualization model

The model maps foot speed plus normalized effort to an inferred ball speed of
approximately 11-25 m/s. Aim zone establishes a stable lateral target region;
signed lateral motion adjusts placement. Upward motion, high/low aim, and
chip/drive state establish vertical intent. A small deterministic lateral
acceleration uses the existing spin cue.

The ball is then integrated at 20 ms intervals with gravity, speed-dependent
drag, and a damped ground bounce. Samples are sent as
`[time, lateral, forward, height]` until the 11 m goal plane. The TV interpolates
those samples directly, rather than rebuilding a new parabola from the match
result. A cyan dashed guide identifies a state-driven path; the old amber path
is retained whenever the trajectory is missing or confidence is below 0.25.

This first stage deliberately leaves goal/save/post decisions with the existing
referee. A legacy `post` result bends only the end of the predicted path onto
the frame so the visible collision remains coherent. Once recorded kicks have
been calibrated and replay-tested, path/goal-plane geometry can become the
authoritative wide/post layer.

## Run and verify

No new process or port is required. Start the existing laptop host and use any
pose mode in the native phone app:

```powershell
python server.py
```

Open `http://localhost:8080/tv.html`, connect the phone, and play normally.
Cycle the phone `POSE` value among `NPU`, `GPU`, `CPU`, and `UNO Q`; all modes
send the same trajectory schema. UNO Q startup remains documented in
`unoq_pipeline.md`.

Run the regression checks with:

```powershell
python -m unittest laptop.test_edge_pose laptop.test_trajectory

cd android
$env:JAVA_HOME='C:\Program Files\Unity\Hub\Editor\6000.1.4f1\Editor\Data\PlaybackEngines\AndroidPlayer\OpenJDK'
$env:ANDROID_HOME='C:\Program Files\Unity\Hub\Editor\6000.1.4f1\Editor\Data\PlaybackEngines\AndroidPlayer\SDK'
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug
```

The estimator remains visualization-only because a monocular body pose cannot
observe the ball's forward launch velocity or exact foot-ball contact. Those
quantities are explicitly inferred and accompanied by confidence instead of
being presented as measured ground truth.
