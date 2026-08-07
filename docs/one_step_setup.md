# One-step game supervisor - `start-game.bat`

[`start-game.bat`](../start-game.bat) is a thin shim over [`tools/start_game.ps1`](../tools/start_game.ps1); every parameter below passes straight through. It owns the complete Windows + UNO Q session:

1. Stops stale recognized processes (`server.py`, `snapkick_bridge.py`, the SnapKick `camera_relay.py`) by checking who owns TCP 8080 / UDP 9999 / UDP 5005 - and **refuses to start** (reporting PID + command line) if an *unrecognized* program holds a port, so it never kills unrelated services.
2. Starts the root [`server.py`](../server.py) host and waits for `http://127.0.0.1:8080/edge/status`.
3. Optionally starts the SnapKick bridge and/or the laptop USB-camera relay.
4. SSHes to the UNO Q: kills old streamers, optionally syncs the current streamer plus its self-contained ONNX backend, launches remote pose inference, and waits for camera + pose health.
5. Prints the TV URL, phone WebSocket address, status URL, and log locations, then supervises everything until Ctrl+C. One press runs the reverse shutdown without CMD's extra `Terminate batch job (Y/N)?` prompt; a crashed session is pre-cleaned on the next launch.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `-UnoQIp <ip>` | prompted (suggests `192.168.150.72`) | UNO Q address |
| `-UnoQUser <user>` | `arduino` | SSH user on the board |
| `-LaptopIp <ip>` | auto-detected | Laptop interface routed to the UNO Q (override if detection picks wrong) |
| `-CameraMode Laptop\|UnoQ` | `UnoQ` | Where the USB camera is plugged in |
| `-CameraIndex <n>` | `1` | Windows capture device index in Laptop mode (`0` if the USB cam is first) |
| `-RemoteCamera <dev>` | `/dev/video0` | Board camera device (UnoQ mode; check `v4l2-ctl --list-devices`) |
| `-UnoQDnnTarget cpu\|opencl\|opencl-fp16` | `cpu` | OpenCV-DNN target; OpenCL modes are diagnostic and much slower on the currently tested board image |
| `-SnapKickRoot <path>` | `%USERPROFILE%\Desktop\snap-kick\snapkick-starter` | External SnapKick checkout providing `camera_relay.py` (Laptop camera mode only) |
| `-RemoteDir <path>` | `/home/arduino/sentinelmesh` | Streamer install dir on the board |
| `-SyncUnoQ` | off | `scp` the current streamer and owned ONNX backend to the board before launch |
| `-SkipUnoQ` | off | Laptop/phone-only: no SSH, no relay, no remote inference |
| `-EnableSnapkickBridge` | off | Also start `snapkick_bridge.py` on UDP 5005 |
| `-IdentityFile <path>` | - | Non-default SSH key; bypasses the password prompt and uses OpenSSH |
| `-AutoStopAfterSeconds <n>` | `0` (forever) | Unattended runs |

## Common invocations

**Laptop / phone only (no UNO Q hardware):**

```powershell
.\start-game.bat -SkipUnoQ
```

**Camera plugged into the UNO Q directly (default):**

```powershell
.\start-game.bat -UnoQIp 192.168.150.72
```

**Sync the streamer first (after editing it, or on a fresh board dir):**

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -SyncUnoQ
```

**Laptop USB camera relayed to the UNO Q for inference:**

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraMode Laptop -CameraIndex 1
```

**Add the independent SnapKick UDP 5005 transport:**

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -EnableSnapkickBridge
```

## The two UNO Q transports

- **Default - raw edge pose:** the board streams `sentinel.edge.pose.v1` landmarks to UDP **9999**; the host forwards them to the phone, which runs its calibrated `EdgeKickEngine` + trajectory estimator. No bridge process needed.
- **Optional - SnapKick:** a producer sends pre-solved `snapkick.pose.v1` kicks to UDP **5005**; `snapkick_bridge.py` (started by `-EnableSnapkickBridge`) converts them into striker messages.

Both share the same TCP 8080 host and can coexist.

## Assumptions on the board

- SSH reachable as `arduino@<UnoQIp>`. Without `-IdentityFile`, the launcher requires PuTTY (`plink.exe` and `pscp.exe`), asks for the board password once, and reuses it only in memory for that run. A first PuTTY connection may separately ask you to verify the board's host key. For unattended runs, set up a key with `ssh-keygen -t ed25519`, install its public key on the board, verify it, and pass the private key path with `-IdentityFile`.
- OpenCV Zoo MediaPipe models at `/home/arduino/models/opencv-mediapipe/` (`pose_estimation_mediapipe_2023mar.onnx`, `person_detection_mediapipe_2023mar.onnx`) and a Python env - see [`unoq_pipeline.md`](unoq_pipeline.md) for board provisioning. The supervisor launches the streamer but does **not** install models.

## Health and logs

On success the script prints:

- TV: `http://localhost:8080/tv.html`
- Host status: `http://localhost:8080/edge/status`
- Phone WebSocket address on the selected laptop IP
- Local logs: `logs\game-run\<name>.{out,err}.log` per child process
- A remote `tail` command for the board's `pose-streamer.log`
