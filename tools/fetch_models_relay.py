"""Temporary laptop relay: fetch AI Hub models, then they get scp'd to the phone.

Uses a throwaway venv under %TEMP% — nothing stays in the project tree.
Requires: QAI_HUB_API_TOKEN env var.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TOKEN = os.environ.get("QAI_HUB_API_TOKEN")
if not TOKEN and len(sys.argv) > 1:
    TOKEN = sys.argv[1]
if not TOKEN:
    sys.exit("Set QAI_HUB_API_TOKEN or pass token as argv[1]")

TEMP = Path(os.environ["TEMP"]) / "gf_aihub_relay"
VENV = TEMP / "venv"
OUT = TEMP / "models"
OUT.mkdir(parents=True, exist_ok=True)

py = sys.executable
print("python:", py, flush=True)
print("out:", OUT, flush=True)

if not (VENV / "Scripts" / "python.exe").exists():
    subprocess.check_call([py, "-m", "venv", str(VENV)])

vpy = str(VENV / "Scripts" / "python.exe")
cli = str(VENV / "Scripts" / "qai-hub-models.exe")

subprocess.check_call([vpy, "-m", "pip", "install", "-q", "--upgrade", "pip"])
subprocess.check_call([vpy, "-m", "pip", "install", "-q", "qai_hub_models_cli"])

# Persist token for qai-hub if the CLI shells out to it
cfg = Path.home() / ".qai_hub" / "client.ini"
cfg.parent.mkdir(parents=True, exist_ok=True)
cfg.write_text(
    "[api]\napi_token = %s\napi_url = https://workbench.aihub.qualcomm.com\n"
    "web_url = https://workbench.aihub.qualcomm.com\n" % TOKEN,
    encoding="utf-8",
)
print("wrote", cfg, flush=True)

os.environ["QAI_HUB_API_TOKEN"] = TOKEN

def run(args):
    print("+", " ".join(args), flush=True)
    r = subprocess.run(args, capture_output=True, text=True)
    print(r.stdout)
    if r.stderr:
        print(r.stderr, file=sys.stderr)
    return r.returncode

print("=== CLI version / whisper info ===", flush=True)
run([cli, "--help"])
run([cli, "info", "whisper_tiny_en"])
run([cli, "models", "--llm", "-q"])

CHIP = "qualcomm-snapdragon-8-elite-for-galaxy"
fetches = [
    [cli, "fetch", "whisper_tiny_en", "-r", "tflite", "-p", "float", "-o", str(OUT)],
    [cli, "fetch", "whisper_tiny_en", "-r", "onnx", "-p", "float", "-o", str(OUT)],
    [cli, "fetch", "whisper_tiny_en", "-r", "qnn", "-p", "float", "-c", CHIP, "-o", str(OUT)],
    [cli, "fetch", "whisper_tiny_en", "-r", "qnn_context_binary", "-p", "w8a16", "-c", CHIP, "-o", str(OUT)],
]

print("=== fetching ===", flush=True)
for cmd in fetches:
    run(cmd)

print("=== OUT contents ===", flush=True)
for p in sorted(OUT.rglob("*")):
    if p.is_file():
        print(f"{p.stat().st_size:10d}  {p.relative_to(OUT)}")
print("DONE", OUT)
