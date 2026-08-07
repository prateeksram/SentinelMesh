# Laptop human retargeting prototype

This branch adds a display-only articulated pose path. It does **not** change
kick detection, force estimation, direction, trajectory, or match outcomes.

## Data path

```
phone NPU / phone GPU / phone CPU / UNO Q
                    |
          PoseAnalyzer.onSkeleton
                    | pose_state (12.5 Hz)
                    v
          server.py PoseRetargeter
          |                     |
  MuJoCo + Mink (optional)   geometric CPU fallback
          |                     |
          +---- retarget_state--+----> TV Canvas athlete
```

The Android mode switch remains dynamic: changing pose inference does not
reconnect or reconfigure the retargeter. The next frame simply has a different
`source` label.

## Calibration and constraints

The phone sends height, weight, and calibrated shoulder-to-hip torso length.
The laptop derives a deliberately small body model:

- stature and torso length set the vertical scale;
- `sqrt(weight / 75 kg)`, clamped to `0.82..1.22`, adjusts shoulder and hip
  breadth;
- upper/lower arm and thigh/shin lengths use fixed adult stature ratios;
- each arm and leg is solved as a two-link chain with the observed elbow/knee
  supplying the SEW bend plane;
- a planted-foot reference prevents the whole model from bouncing when the
  kicking foot lifts;
- one timestamp-aware smoothing stage reduces jitter while opening its
  bandwidth for fast motion.

With no profile, the same path uses a 1.75 m / 75 kg default human. The keeper
still uses a default athlete and the existing animation targets, but its
two-link solver now enforces minimum and maximum reach. Moving keeper target
generation to the laptop rig is the next phase.

## Run

The dependency-free backend is enabled automatically with the normal launch:

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraIndex 1 -SyncUnoQ
```

To try MuJoCo/Mink on a supported Python/platform combination:

```powershell
py -3.13 -m pip install -r requirements-retarget.txt
$env:GF_RETARGET_BACKEND = "mink"
.\start-game.bat -UnoQIp 192.168.150.72 -CameraIndex 1 -SyncUnoQ
```

`GF_RETARGET_BACKEND=geometric` forces the fallback. `auto` (the default)
tries Mink at startup and falls back without interrupting the game. The active
backend is included in `state.retarget.backend` and printed below the live TV
player.

The first Snapdragon X Elite validation should use the geometric backend.
The optional Python wheels need to be checked on the actual Windows-on-Arm
image; x64 emulation or WSL may be required if native Arm64 wheels are absent.

## Reference

The task/bend-plane split is inspired by the MIT-licensed
`benchmark/sew-twist` branch of `kczttm/SEW-Geometric-Teleop`. No G1 assets,
Pinocchio transforms, or robot joint definitions are copied: QPlay uses its
own human proportions and MediaPipe landmark contract.
