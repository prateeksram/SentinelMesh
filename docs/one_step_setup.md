# One-step game supervisor

`start-game.bat` owns the complete Windows + UNO Q game session. At startup it:

1. stops stale recognized `server.py`, `snapkick_bridge.py`, and SnapKick `camera_relay.py` processes;
2. verifies that unrelated applications are not using TCP 8080 or the selected UDP ports;
3. stops any previous `sentinel_pose_streamer.py` on the UNO Q;
4. starts the unified root `server.py` host;
5. starts the laptop USB-camera relay when requested;
6. detects the laptop address routed to the UNO Q and launches remote pose inference;
7. waits for camera and pose health before reporting the game ready.

The default path is raw edge pose: `sentinel.edge.pose.v1` arrives on UDP
`9999`, the host forwards landmarks to the native phone, and the phone runs
the calibrated `EdgeKickEngine` plus trajectory estimator. The newer
pre-solved SnapKick path is preserved independently on UDP `5005`; enable its
local bridge only when a producer sends `snapkick.pose.v1` packets:

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -EnableSnapkickBridge
```

Both transports share the same TCP `8080` host and can coexist. The standard
`sentinel_pose_streamer.py` uses UDP `9999`, so it does not require the bridge.

Pressing Ctrl+C runs the reverse sequence and waits for local ports to be
released. If PowerShell or the laptop is forcibly terminated and cannot run its
cleanup block, the next launch performs the same scoped pre-clean automatically.

## Normal laptop-camera run

From the repository root:

```powershell
.\start-game.bat
```

Enter the UNO Q address when prompted. Press Enter to accept
`192.168.150.72`, or provide it directly:

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraIndex 1
```

Use `-CameraIndex 0` if the USB camera is the first Windows capture device.
The supervisor automatically selects the laptop interface that routes to the
specified UNO Q, so Wi-Fi and direct USB/Ethernet addresses do not have to be
copied manually.

The UNO Q wrapper is normally assumed to already exist at
`/home/arduino/sentinelmesh`. To copy the current repository version before
starting it:

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraIndex 1 -SyncUnoQ
```

## Camera connected directly to UNO Q

```powershell
.\start-game.bat -UnoQIp 192.168.150.72 -CameraMode UnoQ -RemoteCamera /dev/video0
```

This skips the Windows camera relay. Change `/dev/video0` after checking
`v4l2-ctl --list-devices` on the board.

## Laptop/phone-only fallback

```powershell
.\start-game.bat -SkipUnoQ
```

This starts the game host without SSH, the camera relay, or remote inference.
Phone NPU/GPU/CPU pose continues to use the same game and trajectory pipeline.

## SSH authentication

The supervisor uses the installed Windows OpenSSH `ssh` and `scp` clients. With
password authentication, SSH may prompt while stopping, starting, or syncing
the board. An SSH key makes the command genuinely unattended:

```powershell
ssh-keygen -t ed25519
```

Install the generated public key for `arduino` on the UNO Q, then verify that
`ssh arduino@UNO_Q_IP` opens without a password. A non-default key can be passed
with `-IdentityFile C:\path\to\key`.

## Health and logs

When startup succeeds the script prints:

- TV: `http://localhost:8080/tv.html`
- host status: `http://localhost:8080/edge/status`
- phone WebSocket address using the selected laptop IP
- local logs: `logs/game-run/`
- remote log tail command for the UNO Q

If TCP 8080 belongs to an unrecognized program, the supervisor reports its PID
and command instead of terminating it. This prevents the cleanup step from
killing unrelated development services.
