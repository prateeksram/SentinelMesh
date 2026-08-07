[CmdletBinding()]
param(
    [string]$UnoQIp,
    [string]$UnoQUser = "arduino",
    [string]$LaptopIp,
    [ValidateSet("Laptop", "UnoQ")]
    [string]$CameraMode = "Laptop",
    [int]$CameraIndex = 1,
    [string]$SnapKickRoot = "$env:USERPROFILE\Desktop\snap-kick\snapkick-starter",
    [string]$RemoteDir = "/home/arduino/sentinelmesh",
    [string]$RemoteCamera = "/dev/video0",
    [string]$IdentityFile,
    [switch]$SyncUnoQ,
    [switch]$SkipUnoQ,
    [switch]$EnableSnapkickBridge,
    [ValidateRange(0, 86400)]
    [int]$AutoStopAfterSeconds = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$runLogDir = Join-Path $repoRoot "logs\game-run"
$serverScript = Join-Path $repoRoot "server.py"
$bridgeScript = Join-Path $repoRoot "snapkick_bridge.py"
$streamerScript = Join-Path $repoRoot "unoq\sentinel_pose_streamer.py"
$serverProcess = $null
$relayProcess = $null
$bridgeProcess = $null
$remoteStarted = $false
$scriptExitCode = 0
$unoQTransport = $null
$unoQPassword = $null
$plinkPath = $null
$pscpPath = $null
$resolvedIdentityFile = $null
$cancelHandler = $null

if (-not ("SentinelMeshCancelSignal" -as [type])) {
    Add-Type -TypeDefinition @'
using System;

public static class SentinelMeshCancelSignal
{
    public static volatile bool IsCancellationRequested;
    private static bool IsInstalled;

    public static void Install()
    {
        if (IsInstalled) return;
        Console.CancelKeyPress += Handle;
        IsInstalled = true;
    }

    public static void Remove()
    {
        if (!IsInstalled) return;
        Console.CancelKeyPress -= Handle;
        IsInstalled = false;
    }

    private static void Handle(object sender, ConsoleCancelEventArgs args)
    {
        args.Cancel = true;
        IsCancellationRequested = true;
    }
}
'@
}

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-IpAddress([string]$Value, [string]$Name) {
    $parsed = $null
    if (-not [System.Net.IPAddress]::TryParse($Value, [ref]$parsed)) {
        throw "$Name is not a valid IP address: $Value"
    }
}

function Get-RoutedLaptopIp([string]$RemoteIp) {
    $udp = [System.Net.Sockets.UdpClient]::new()
    try {
        $udp.Connect($RemoteIp, 22)
        return ([System.Net.IPEndPoint]$udp.Client.LocalEndPoint).Address.ToString()
    } finally {
        $udp.Dispose()
    }
}

function Get-PreferredLaptopIp {
    $route = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix "0.0.0.0/0" `
        -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1
    if ($route) {
        $address = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.InterfaceIndex `
            -ErrorAction SilentlyContinue | Where-Object {
                $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254.*"
            } | Select-Object -First 1
        if ($address) { return $address.IPAddress }
    }
    return "127.0.0.1"
}

function Get-SshBaseArgs {
    $sshArguments = @("-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=10")
    if ($resolvedIdentityFile) {
        $sshArguments += @("-i", $resolvedIdentityFile)
    }
    return $sshArguments
}

function Find-Executable([string]$Name, [string]$ProgramFilesRelativePath) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return $command.Source }

    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}) |
        Where-Object { $_ } | Select-Object -Unique
    foreach ($root in $roots) {
        $candidate = Join-Path $root $ProgramFilesRelativePath
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

function Initialize-UnoQTransport {
    if ($IdentityFile) {
        $script:resolvedIdentityFile = (Resolve-Path -LiteralPath $IdentityFile).Path
        $script:unoQTransport = "OpenSSH"
        return
    }

    $script:plinkPath = Find-Executable "plink.exe" "PuTTY\plink.exe"
    $script:pscpPath = Find-Executable "pscp.exe" "PuTTY\pscp.exe"
    if (-not $plinkPath -or -not $pscpPath) {
        throw "Password reuse requires PuTTY plink.exe and pscp.exe. Install PuTTY, or pass -IdentityFile for key-based OpenSSH."
    }

    $securePassword = Read-Host "UNO Q password for $UnoQUser@$UnoQIp" -AsSecureString
    $credential = [System.Management.Automation.PSCredential]::new($UnoQUser, $securePassword)
    $script:unoQPassword = $credential.GetNetworkCredential().Password
    $script:unoQTransport = "PuTTY"

    Write-Host "Checking UNO Q login (the password will be reused in memory for this run)..."
    # The first connection is intentionally interactive so PuTTY can ask the
    # user to verify and cache a previously unseen host key. The password itself
    # was already collected once above and is supplied to this process.
    & $plinkPath -ssh -pw $unoQPassword "$UnoQUser@$UnoQIp" "printf sentinelmesh-auth-ok"
    if ($LASTEXITCODE -ne 0) {
        throw "UNO Q authentication failed with exit code $LASTEXITCODE"
    }
}

function Invoke-UnoQ([string]$Command, [switch]$IgnoreFailure) {
    # PowerShell here-strings use CRLF on Windows. Passing those bytes through
    # ssh makes Bash parse tokens such as `set -e\r` and `do\r`, so normalize
    # once at the transport boundary for every remote command.
    $unixCommand = $Command.Replace("`r`n", "`n").Replace("`r", "`n")
    if ($unoQTransport -eq "PuTTY") {
        & $plinkPath -batch -ssh -pw $unoQPassword "$UnoQUser@$UnoQIp" $unixCommand
    } else {
        $sshArguments = @(Get-SshBaseArgs)
        $sshArguments += "$UnoQUser@$UnoQIp"
        $sshArguments += $unixCommand
        & ssh @sshArguments
    }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $IgnoreFailure) {
        throw "UNO Q SSH command failed with exit code $exitCode"
    }
    return $exitCode
}

function Copy-ToUnoQ([string]$LocalPath, [string]$RemotePath, [string]$Description) {
    if ($unoQTransport -eq "PuTTY") {
        & $pscpPath -batch -pw $unoQPassword $LocalPath "$UnoQUser@$UnoQIp`:$RemotePath"
    } else {
        $scpArguments = @(Get-SshBaseArgs)
        $scpArguments += @($LocalPath, "$UnoQUser@$UnoQIp`:$RemotePath")
        & scp @scpArguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "UNO Q $Description sync failed with exit code $LASTEXITCODE"
    }
}

function Stop-KnownLocalServices {
    $servicePortPids = @(
        Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess
        Get-NetUDPEndpoint -LocalPort 9999 -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess
        Get-NetUDPEndpoint -LocalPort 5005 -ErrorAction SilentlyContinue |
            Select-Object -ExpandProperty OwningProcess
    ) | Sort-Object -Unique
    $knownPids = [System.Collections.Generic.HashSet[int]]::new()

    # Port ownership is sufficient to identify stale game hosts even when
    # Win32_Process command lines require elevated CIM permissions.
    foreach ($pidValue in $servicePortPids) {
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($process -and $process.ProcessName -match "^(python|python3|pythonw)$") {
            $knownPids.Add([int]$pidValue) | Out-Null
        }
    }

    # The relay uses its own SnapKick venv, so its executable path is a safe
    # non-admin identifier even though it owns no listening socket.
    $relayPython = Join-Path $SnapKickRoot "laptop\.venv\Scripts\python.exe"
    Get-Process -Name python, python3, pythonw -ErrorAction SilentlyContinue | ForEach-Object {
        try {
            if ($_.Path -and $_.Path -ieq $relayPython) {
                $knownPids.Add([int]$_.Id) | Out-Null
            }
        } catch { }
    }

    # Command lines make manual relative-path launches discoverable. Failure
    # is intentionally non-fatal because the port/path checks above remain.
    try {
        Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object {
            $line = $_.CommandLine
            if (-not $line -or $_.Name -notmatch "^(python|python3|pythonw)(\.exe)?$") { return }
            if ($line -match "(?i)(?:laptop[\\/])?server\.py" -or
                $line -match "(?i)snapkick_bridge\.py" -or
                $line -match "(?i)snap-kick.*unoq[\\/]camera_relay\.py") {
                $knownPids.Add([int]$_.ProcessId) | Out-Null
            }
        }
    } catch {
        Write-Verbose "CIM command-line scan unavailable: $($_.Exception.Message)"
    }

    foreach ($pidValue in $knownPids) {
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        $label = if ($process) { $process.ProcessName } else { "process" }
        Write-Host "Stopping stale local $label PID $pidValue"
        try {
            Stop-Process -Id $pidValue -Force -ErrorAction Stop
        } catch {
            throw "Could not stop stale $label PID $pidValue. Close it manually or run this terminal with sufficient permissions. $($_.Exception.Message)"
        }
    }
    if ($knownPids.Count -gt 0) { Start-Sleep -Milliseconds 500 }
}

function Get-ProcessDescription([int]$ProcessId) {
    try {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop |
            Select-Object -First 1
        if ($owner) { return "$($owner.Name) PID $($owner.ProcessId): $($owner.CommandLine)" }
    } catch { }
    $fallback = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($fallback) { return "$($fallback.ProcessName) PID $ProcessId" }
    return "PID $ProcessId"
}

function Assert-PortAvailable([int]$Port) {
    $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($listener) {
        $description = Get-ProcessDescription $listener.OwningProcess
        throw "TCP port $Port is still occupied by $description. It was not stopped because it is not a recognized SentinelMesh service."
    }
}

function Assert-UdpPortAvailable([int]$Port) {
    $endpoint = Get-NetUDPEndpoint -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($endpoint) {
        $description = Get-ProcessDescription $endpoint.OwningProcess
        throw "UDP port $Port is still occupied by $description. It was not stopped because it is not a recognized SentinelMesh service."
    }
}

function Start-LoggedProcess(
    [string]$Name,
    [string]$FilePath,
    [string[]]$Arguments,
    [string]$WorkingDirectory
) {
    $stdout = Join-Path $runLogDir "$Name.out.log"
    $stderr = Join-Path $runLogDir "$Name.err.log"
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    Write-Host "$Name started as PID $($process.Id)"
    return $process
}

function Wait-EdgeStatus([string]$Field, [int]$TimeoutSeconds, [string]$Description) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $last = $null
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $last = Invoke-RestMethod -Uri "http://127.0.0.1:8080/edge/status" -TimeoutSec 2
            if ($Field -eq "server" -or $last.$Field -eq "live") { return $last }
        } catch {
            $last = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 400
    }
    throw "Timed out waiting for $Description. Last status: $last"
}

function Stop-Child([System.Diagnostics.Process]$Process, [string]$Name) {
    if ($null -eq $Process) { return }
    try {
        $Process.Refresh()
        if (-not $Process.HasExited) {
            Write-Host "Stopping $Name PID $($Process.Id)"
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
            $Process.WaitForExit(3000) | Out-Null
        }
    } catch {
        Write-Warning "Could not stop ${Name}: $($_.Exception.Message)"
    }
}

function Stop-UnoQStreamer {
    if ($SkipUnoQ -or -not $UnoQIp) { return }
    $command = @'
for pid in $(pgrep -f '[s]entinel_pose_streamer.py' || true); do
  kill -TERM "$pid" 2>/dev/null || true
done
sleep 1
for pid in $(pgrep -f '[s]entinel_pose_streamer.py' || true); do
  kill -KILL "$pid" 2>/dev/null || true
done
rm -f '{0}/pose_streamer.pid'
'@ -f $RemoteDir
    Invoke-UnoQ $command -IgnoreFailure | Out-Null
}

try {
    New-Item -ItemType Directory -Force -Path $runLogDir | Out-Null
    if (-not (Test-Path -LiteralPath $serverScript)) { throw "Missing $serverScript" }

    if (-not $SkipUnoQ) {
        if (-not $UnoQIp) {
            $UnoQIp = Read-Host "UNO Q IP [192.168.150.72]"
            if (-not $UnoQIp) { $UnoQIp = "192.168.150.72" }
        }
        Test-IpAddress $UnoQIp "UNO Q IP"
        if (-not $LaptopIp) { $LaptopIp = Get-RoutedLaptopIp $UnoQIp }
        Test-IpAddress $LaptopIp "Laptop IP"
        Initialize-UnoQTransport
    } elseif (-not $LaptopIp) {
        $LaptopIp = Get-PreferredLaptopIp
    }

    [SentinelMeshCancelSignal]::IsCancellationRequested = $false
    [SentinelMeshCancelSignal]::Install()
    $cancelHandler = $true

    Write-Step "Cleaning up recognized services from the previous run"
    Stop-KnownLocalServices
    Assert-PortAvailable 8080
    Assert-UdpPortAvailable 9999
    if ($EnableSnapkickBridge) { Assert-UdpPortAvailable 5005 }

    Write-Step "Starting SentinelMesh host"
    $serverPython = (Get-Command python -ErrorAction Stop).Source
    $serverProcess = Start-LoggedProcess "server" $serverPython @("-u", $serverScript) $repoRoot
    Wait-EdgeStatus "server" 12 "the laptop server" | Out-Null

    if ($EnableSnapkickBridge) {
        Write-Step "Starting optional pre-solved SnapKick bridge on UDP 5005"
        if (-not (Test-Path -LiteralPath $bridgeScript)) { throw "Missing $bridgeScript" }
        $bridgeProcess = Start-LoggedProcess "snapkick-bridge" $serverPython @(
            "-u", $bridgeScript, "--host", "127.0.0.1:8080", "--udp-port", "5005"
        ) $repoRoot
    }

    if ($CameraMode -eq "Laptop" -and -not $SkipUnoQ) {
        Write-Step "Starting laptop USB-camera relay (camera $CameraIndex)"
        $relayPython = Join-Path $SnapKickRoot "laptop\.venv\Scripts\python.exe"
        $relayScript = Join-Path $SnapKickRoot "unoq\camera_relay.py"
        if (-not (Test-Path -LiteralPath $relayPython)) { throw "Missing relay Python: $relayPython" }
        if (-not (Test-Path -LiteralPath $relayScript)) { throw "Missing relay script: $relayScript" }
        $oldMsmf = $env:OPENCV_VIDEOIO_PRIORITY_MSMF
        $env:OPENCV_VIDEOIO_PRIORITY_MSMF = "0"
        try {
            $relayProcess = Start-LoggedProcess "camera-relay" $relayPython @(
                "-u", $relayScript,
                "--camera", "$CameraIndex",
                "--url", "http://127.0.0.1:8080/edge/source/frame",
                "--width", "640", "--height", "480", "--fps", "30"
            ) $SnapKickRoot
        } finally {
            $env:OPENCV_VIDEOIO_PRIORITY_MSMF = $oldMsmf
        }
        Wait-EdgeStatus "sourceCamera" 15 "the laptop camera relay" | Out-Null
    }

    if (-not $SkipUnoQ) {
        if ($SyncUnoQ) {
            Write-Step "Syncing the current UNO Q wrapper"
            Invoke-UnoQ "mkdir -p '$RemoteDir'" | Out-Null
            Copy-ToUnoQ $streamerScript "$RemoteDir/sentinel_pose_streamer.py" "streamer"
            Copy-ToUnoQ $backendScript "$RemoteDir/mediapipe_onnx_backend.py" "backend"
        }

        $camera = if ($CameraMode -eq "Laptop") {
            "http://$LaptopIp`:8080/edge/source/camera.mjpg"
        } else {
            $RemoteCamera
        }
        Write-Step "Starting UNO Q pose inference at $UnoQUser@$UnoQIp"
        Write-Host "Laptop route selected for UNO Q: $LaptopIp"
        Write-Host "UNO Q camera source: $camera"
        $remoteStart = @'
set -e
mkdir -p '{0}/logs'
for pid in $(pgrep -f '[s]entinel_pose_streamer.py' || true); do
  if [ "$pid" != "$$" ]; then kill -TERM "$pid" 2>/dev/null || true; fi
done
sleep 1
PY='/home/arduino/snapkick-starter/unoq/.venv/bin/python'
if [ ! -x "$PY" ]; then PY=python3; fi
nohup "$PY" '{0}/sentinel_pose_streamer.py' \
  --laptop-ip '{1}' \
  --camera '{2}' \
  --model '/home/arduino/models/opencv-mediapipe/pose_estimation_mediapipe_2023mar.onnx' \
  --person-model '/home/arduino/models/opencv-mediapipe/person_detection_mediapipe_2023mar.onnx' \
  --width 640 --height 480 --camera-fps 30 \
  --target-fps 10 --preview-fps 5 \
  --detector-interval 4 --detector-stable-interval 6 --detector-recovery-interval 1 \
  --optical-flow \
  >'{0}/logs/pose-streamer.log' 2>&1 </dev/null &
pid=$!
echo "$pid" >'{0}/pose_streamer.pid'
sleep 2
kill -0 "$pid"
echo "UNO Q pose streamer started as PID $pid"
'@ -f $RemoteDir, $LaptopIp, $camera
        # Set before SSH so a partially successful remote launch is still
        # cleaned up if the connection drops before the command returns.
        $remoteStarted = $true
        Invoke-UnoQ $remoteStart | Out-Null
        Wait-EdgeStatus "pose" 30 "UNO Q pose packets" | Out-Null
    }

    Write-Step "Gesture Football is ready"
    Write-Host "TV       http://localhost:8080/tv.html" -ForegroundColor Green
    Write-Host "Phone    ws://$LaptopIp`:8080/ws" -ForegroundColor Green
    Write-Host "Status   http://localhost:8080/edge/status" -ForegroundColor Green
    Write-Host "Logs     $runLogDir" -ForegroundColor Green
    if (-not $SkipUnoQ) {
        Write-Host "UNO Q    ssh $UnoQUser@$UnoQIp 'tail -f $RemoteDir/logs/pose-streamer.log'" -ForegroundColor Green
    }
    Write-Host "`nPress Ctrl+C once to stop every service started by this supervisor." -ForegroundColor Yellow

    $readyAt = [DateTime]::UtcNow
    while (-not [SentinelMeshCancelSignal]::IsCancellationRequested) {
        Start-Sleep -Seconds 1
        $serverProcess.Refresh()
        if ($serverProcess.HasExited) { throw "Laptop server exited. See $runLogDir\server.err.log" }
        if ($relayProcess) {
            $relayProcess.Refresh()
            if ($relayProcess.HasExited) { throw "Camera relay exited. See $runLogDir\camera-relay.err.log" }
        }
        if ($bridgeProcess) {
            $bridgeProcess.Refresh()
            if ($bridgeProcess.HasExited) { throw "SnapKick bridge exited. See $runLogDir\snapkick-bridge.err.log" }
        }
        if ($AutoStopAfterSeconds -gt 0 -and
            ([DateTime]::UtcNow - $readyAt).TotalSeconds -ge $AutoStopAfterSeconds) {
            Write-Host "Automatic stop timer reached."
            break
        }
    }
    if ([SentinelMeshCancelSignal]::IsCancellationRequested) {
        Write-Host "Ctrl+C received; stopping the game."
    }
} catch {
    Write-Host "`nSETUP ERROR: $($_.Exception.Message)" -ForegroundColor Red
    $scriptExitCode = 1
} finally {
    Write-Step "Shutting down Gesture Football"
    Stop-Child $relayProcess "camera relay"
    Stop-Child $bridgeProcess "SnapKick bridge"
    Stop-Child $serverProcess "laptop server"
    if ($remoteStarted) {
        Write-Host "Stopping UNO Q pose streamer"
        Stop-UnoQStreamer
    }
    try {
        Stop-KnownLocalServices
    } catch {
        Write-Warning "Final local cleanup was incomplete: $($_.Exception.Message)"
        $scriptExitCode = 1
    }
    if ($cancelHandler) {
        [SentinelMeshCancelSignal]::Remove()
    }
    $script:unoQPassword = $null
    Write-Host "Cleanup complete. TCP 8080 and the UNO Q streamer are ready for the next run." -ForegroundColor Green
}

exit $scriptExitCode
