# Phone deep dive - QPlay on Galaxy S25 Ultra

**Device:** Samsung Galaxy S25 Ultra · Snapdragon 8 Elite (Hexagon HTP v79)
**App:** QPlay · `com.sentinelmesh.gesturefootball` · v0.1.0 · arm64-v8a · minSdk 28 · target/compileSdk 35

This document covers **only the phone** - the Player 1 edge-AI station. The match host / TV and the UNO Q board are covered by the root [`README.md`](../README.md) and [`unoq_pipeline.md`](unoq_pipeline.md). For build + quick start, see [`../android/README.md`](../android/README.md).

**Pitch line:** the Snapdragon sees you, measures your kick in Newtons, hears you, coaches you privately - and only a tiny JSON shot crosses the LAN.

---

## 1. End-to-end data flow

```
Front camera (CameraX RGBA)
        │
        ▼
 PoseAnalyzer ──► NPU (ORT QNN HTP)  /  GPU/CPU (MediaPipe)  /  UNO Q (remote edge_pose)
        │
        ▼
 BodyGuide (stand-here silhouette, full-body / T-pose gates)
        │
        ├─► Aim (raised wrist → L/C/R) ───────────────► GameClient.sendAim (~200 ms throttle)
        │
        └─► Release detector (exactly one per frame):
              · ForcePoseEngine   (football leg swing → Newtons)
              · HandThrowEngine   (darts/basketball wrist release)
              · EdgeKickEngine    (UNO Q low-FPS time-based swing + optical flow)
                 │
                 ├─► ShotTrajectoryEstimator → kickState + trajectory
                 ├─► GameClient.sendKick {zone,power,force,...,kickState,trajectory}
                 └─► skeleton buffer → GameClient.sendSkeleton (bullet-time for the TV)

Mic ──► VoiceListener ──► WhisperEngine (Hexagon) ──► VoiceCoach intents ──► QwenCoach (private TTS)

Calibration ──► player_profile.json (filesDir - never uploaded)
```

---

## 2. Source map

Package root: `android/app/src/main/java/com/sentinelmesh/gesturefootball/`

| Path | Responsibility |
|---|---|
| `MainActivity.kt` | CameraX lifecycle, HUD, sport picker + onboarding splash, HOST prefs, calibration UI, kick/skel/telem send, voice wiring, phase machine, aim lock on `shoot` |
| `pose/PoseAnalyzer.kt` | Source cycle **NPU → GPU → CPU → UNO Q**; normalization seam: body-streak anti-cheat, aim thresholds, release-detector routing, trajectory stamping |
| `pose/NpuPoseEngine.kt` | Two-stage BlazePose on QNN HTP (detector 128², landmarks 256², w8a8 QAIRT 2.45 contexts). Emits 25 real landmarks and synthesizes ankle/foot points from the hips for MediaPipe-33 layout |
| `pose/BodyGuide.kt` | Stand-here silhouette, torso/full-body acceptance, loose T-pose + corrective coach strings |
| `pose/EdgeKickEngine.kt` | UNO Q-only kick detector: One-Euro-filtered, time-based swing state machine fused with the board's optical-flow packets - works at 8–10 pose FPS |
| `pose/ShotTrajectoryEstimator.kt` | Source-neutral ballistic model (`sentinel.pose-ballistic.v1`); physics identical for all pose backends (unit-tested) |
| `forcepose/ForcePoseEngine.kt` | ForcePose port: median → Savitzky–Golay → torso metres → `F = m_leg × a_peak` → validation → `KickEvent` |
| `forcepose/HandThrowEngine.kt` | Wrist-release twin for darts/basketball (`m_arm = 0.05 × body`, `fMax = 280 N`); emits the same `KickEvent` |
| `calibrate/CalibrationSession.kt` | FSM: biometrics → T-pose → aim L/C/R → 3 practice swings → done; every step thumbs-up-gated |
| `profile/PlayerProfile.kt` | Profile schema + store → `filesDir/player_profile.json` |
| `voice/WhisperEngine.kt` | Whisper-Tiny encoder/decoder on HTP from `files/whisper/` (manual KV-cache greedy decode, ≤32 new tokens) |
| `voice/MelSpectrogram.kt` / `voice/WhisperTokenizer.kt` | 80-bin Slaney mel @16 kHz → 80×3000; HF tokenizer decode (both support files ship in the APK) |
| `voice/VoiceListener.kt` | Energy-gated 16 kHz capture (start RMS ≈600 / stop ≈280, 4 s cap); muted while TTS speaks |
| `voice/VoiceCoach.kt` | Intent parse (READY / LEFT / CENTER / RIGHT / NEXT / SKIP / TRASH) + Android TTS |
| `voice/QwenCoach.kt` | Private coach grounded on profile + last ≤8 kicks; optional GenieX Qwen backend from `files/qwen/` |
| `npu/HtpNative.kt` | `ADSP_LIBRARY_PATH`, QNN HTP sessions (`burst`, fallback `high_performance`); HTP-only - no CPU EP retry |
| `net/GameClient.kt` | OkHttp WebSocket, URL normalize/validate, prefs (`gf_net`/`host_url`), 1.5 s auto-reconnect, `edge_pose` parse |
| `ui/OverlayView.kt` / `ui/RemoteCameraView.kt` | Mirrored skeleton overlay; UNO Q JPEG preview long-poller (`/edge/frame.jpg?after=seq`) |
| `debug/ScreenRecordService.kt` | MediaProjection REC → `Movies/gf_screen_*.mp4` (3 newest kept, 5 min cap) |

---

## 3. Capabilities

### Vision

- CameraX `DEFAULT_FRONT_CAMERA`, RGBA analysis, keep-only-latest.
- **NPU (default):** AI Hub `mediapipe_pose` stack via ONNX Runtime QNN HTP. Detector score gates with torso-geometry leniency; sticky fallback to GPU after repeated NPU failures.
- **GPU/CPU fallback:** MediaPipe Tasks PoseLandmarker (`pose_landmarker_lite.task`, real 33 landmarks + world landmarks).
- **UNO Q:** remote `edge_pose` landmarks + optical flow; 2.5 s staleness watchdog restores local pose automatically.
- Overlay mirrors landmarks so the phone behaves like a mirror.

### Identity (local only)

One-time calibration writes `filesDir/player_profile.json`:

| Field | Meaning |
|---|---|
| `heightCm` / `weightKg` | Biometrics; leg/arm mass for the force model |
| `torsoM` | Mid-shoulder→mid-hip metres (`height × 0.288`, refined by T-pose + arm span, clamped 0.35–0.75) |
| `kickMs` / `unoQKickMs` / `throwMs` | Personal release thresholds - phone-camera kicks, UNO Q kicks, and hand throws are calibrated separately |
| `dominantFoot`, `sport` | From practice swings; last chosen sport |
| `aimLMax`, `aimCMin`, `aimCMax`, `aimRMin` | Mirrored wrist-X aim envelope |

**Never uploaded.**

### Biomechanics (ForcePose port)

Pose-only port of [ForcePose (arXiv:2503.22363)](https://arxiv.org/abs/2503.22363) - the paper's BiLSTM weights are not public, so the port uses Winter-table segment masses with peak acceleration from smoothed foot kinematics:

1. Ankle/foot tracks (MediaPipe indices 27/28/31/32), visibility ≥ 0.4; 3-tap median into a 12-sample ring buffer.
2. Savitzky–Golay smoothing (`[-2,3,6,7,6,3,-2]/21`).
3. Torso scale: EMA of shoulder-mid↔hip-mid length; `mPerUnit = torsoM / torsoEma` converts to metres.
4. `F = m_leg × a_peak`, `m_leg = 0.0618 × bodyKg`; `power = min(1, F / 380 N)`.
5. Acceptance: speed > threshold for 3 consecutive frames **and** a real path (lift > 0.04 m, or dominant horizontal motion, or displacement ≥ 0.18 m). Near-misses coach back "soft" / "short". Wrists are deliberately ignored so balance-arm motion can't veto a kick.
6. `KickEvent` enrichment: `force`, `power`, `foot`, `height` H/L, `spin` ∈ [−1,1], `strike` chip/drive, `dirDeg`; `zone` comes from the current hand aim.

Threshold floors: match 1.7 m/s, practice 1.5 m/s, pre-calibration default 3.0 m/s; personalization = `max(floor, median practice peak × 0.55)`. The hand-throw twin uses 1.2 / 1.0 / 2.2 m/s and always emits `strike: "drive"`.

### Hearing and talking back

- **Whisper Tiny** on the HTP - weights pushed post-install (`files/whisper/`; badge `VOICE · NO MODEL` until then).
- Intents: "ready" (starts the match from the lobby), left / center / right (aim), next / skip (calibration), trash-talk (coach reply).
- **Android TTS** announces phases and results; the mic is muted while TTS speaks so it never transcribes itself.
- **Private coach**: default `COACH` backend needs no weights (grounded lines from profile + recent kicks, including a "you're looping <zone>" detector). Optional `QWEN` backend when GenieX Qwen3 0.6B (~752 MB) is pushed to `files/qwen/`.

### Integrity

- 8 consecutive full-body frames (shoulders **and** ankles in the guide) before kicks arm; 900 ms cooldown; tracker-jump buffer reset; practice swings must be real (soft/short rejected with hints).
- Aim freezes when the phase hits `shoot` - feints only work before the whistle.

### Telemetry

NEURAL LOAD strip (POSE / ASR / LLM ms + force chip) on-device, plus ~1 Hz `telem` scalars to the host (`busy_pct`, `pose_ms` / `asr_ms` / `llm_ms`, backend labels) for the TV rail. Schema: [`phone_protocol.md`](phone_protocol.md). No transcripts, frames, or profile data.

---

## 4. Models & on-device storage

### In the APK (`android/app/src/main/assets/`, ~14 MB)

| Asset | Role |
|---|---|
| `npu/pose_detector.onnx` + `pose_detector_qairt_context.bin` (~1.3 MB) | Stage-1 person detector, 128², w8a8 |
| `npu/pose_landmark_detector.onnx` + `pose_landmark_detector_qairt_context.bin` (~4.2 MB) | Stage-2 landmarks, 256² |
| `npu/anchors_pose.bin`, `npu/metadata.json` | 896 BlazePose anchors; AI Hub export metadata (QAIRT 2.45, 8 Elite for Galaxy) |
| `pose_landmarker_lite.task` (~5.8 MB) | MediaPipe fallback for GPU/CPU |
| `whisper/tokenizer.json` (~2.6 MB), `whisper/mel_filters.bin` (64 KB) | Whisper support files (the heavy encoder/decoder are **not** in the APK) |

On first NPU use the runtime copies the `npu/` assets into `filesDir/npu/`.

### Pushed after install (not in git - large)

| Bundle | Script (run from repo root) | Device path | Size |
|---|---|---|---|
| Whisper Tiny QNN (`encoder/decoder.onnx` + `*_qairt_context.bin` + `metadata.json` + `tokenizer.json`) | `.\tools\push_whisper_models.ps1` | `files/whisper/` via `/data/local/tmp` + `run-as` | ~112 MB |
| Qwen3 0.6B GenieX w4a16 | `.\tools\push_qwen_models.ps1 -Source <dir>` | `files/qwen/` | ~752 MB |

Staging goes through `/data/local/tmp` + `run-as` because adb-pushed external dirs are shell-owned and invisible to the app UID. Model acquisition options: [`../tools/README.md`](../tools/README.md).

### Runtimes (Gradle)

`onnxruntime-android-qnn 1.28.0` (ORT + QNN EP) · `qnn-runtime 2.45.0` (must stay in sync with the QAIRT 2.45 context binaries) · MediaPipe Tasks Vision `0.10.14` · OkHttp `4.12.0` · CameraX `1.4.1`. The manifest requires the vendor library `libcdsprpc.so` (Hexagon RPC); `HtpNative` opens QNN sessions at performance profile `burst`, falling back to `high_performance` - there is no CPU-EP fallback for QNN sessions, so an HTP failure disables NPU pose *and* Whisper together (MediaPipe still plays).

### Indicative latency (S25 Ultra)

| Block | Delegate | Typical |
|---|---|---|
| Pose landmark | QNN HTP | ~15–40 ms / frame |
| Whisper Tiny utterance | QNN HTP | ~1–10 s (length-dependent) |
| Coach line | grounded / Qwen | <50 ms grounded |

---

## 5. Calibration - step by step

Entry: every launch runs splash → **sport picker**; calibration starts on first play (or via CALIBRATE, blocked mid-match). Every capture step is gated on a **thumbs-up hold** (~700 ms), the "I'M READY" button, or voice "ready" - nothing auto-accepts a wrong pose.

| Step | Player action | Capture rules |
|---|---|---|
| `BIOMETRICS` | Height (120–230 cm) & weight (35–160 kg) | Seeds `torsoM = height × 0.288` |
| `TPOSE` | Thumbs-up, then hold arms out 2.5 s | Refines `torsoM` from pose + arm span |
| `AIM_L/C/R` | Thumbs-up, raise hand above shoulder into the zone, hold 1.5 s | Must leave the zone ≥ 300 ms between steps; averaged wrist X personalizes the aim envelope |
| `PRACTICE` | 3 hard swings (or throws) | Validated events only; median peak → threshold; majority foot → `dominantFoot`. In UNO Q mode the result is stored as `unoQKickMs`, leaving the phone-camera threshold untouched |
| `DONE` | Profile persisted → play | |

Default aim envelope before personalization: L ≤ 0.34, C 0.40–0.60, R ≥ 0.66 (mirrored X).

**Ball note:** the engine is pose-only - a real ball, a soft ball, or an air-kick all work; the camera tracks the leg, not the ball.

---

## 6. Permissions & platform requirements

| Permission / feature | Why |
|---|---|
| `CAMERA` (+ front camera **required** features) | Pose |
| `RECORD_AUDIO` | Whisper |
| `INTERNET` | LAN WebSocket to the host |
| `VIBRATE`, `WAKE_LOCK` | Haptics; keep the session alive |
| `FOREGROUND_SERVICE` + `MEDIA_PROJECTION` | Debug screen recording |
| `libcdsprpc.so` required | Hexagon HTP / QNN rpcmem |
| Cleartext traffic allowed (network security config) | Plain `ws://` on the LAN - the native app never needs TLS |

---

## 7. Troubleshooting (phone)

| Symptom | Likely fix |
|---|---|
| HOST Failed / TV PHONE red | Same Wi-Fi; correct LAN IP; `server.py` running; retap HOST. A malformed HOST silently reverts to `127.0.0.1` - retype it |
| `VOICE · NO MODEL` | `.\tools\push_whisper_models.ps1`, then force-stop + relaunch |
| HOLD LIKE A MIRROR / STEP BACK | Full body in the outline - shoulders **and** ankles |
| Kicks not registering | Faster swing + follow-through; keep the full-body streak; or recalibrate |
| Soft / short hints | The path gate wants a real swing - harder / longer |
| NPU badge errors | Snapdragon with `libcdsprpc.so` required; GPU/CPU delegates still play |
| UNO Q mode snaps back to NPU | Board stream stale >2.5 s - check the streamer and UDP 9999 |
| Connect refused (browser page only) | The **native app** uses `ws://` - HTTPS/8443 only applies to `phone.html` |

---

## 8. Privacy boundary (hard rule)

**Never leaves the phone:** camera/video frames, `player_profile.json`, Whisper transcripts, coach lines, the predictability HUD.

**May leave the phone (LAN WebSocket only):** `hello`, `aim`, `kick` scalars/enums (+ `kickState`/`trajectory` kinematics), `skel` landmark samples, `start`, `sport`, and 1 Hz `telem` duty-cycle scalars.

On-device AI (pose / Whisper / coach) works in airplane mode; only the match link needs the LAN.
