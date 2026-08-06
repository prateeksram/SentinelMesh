"""Probe for public Qualcomm Whisper assets (run on the laptop, tiny requests only)."""
import urllib.error
import urllib.request

S3 = "https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-models/models"
CANDIDATES = [
    "https://huggingface.co/qualcomm/Whisper-Tiny-En/resolve/main/release_assets.json",
    f"{S3}/whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-tflite-float.zip",
    f"{S3}/whisper_tiny_en/releases/v0.59.0/whisper_tiny_en-qnn_context_binary-float-qualcomm_snapdragon_8_elite_for_galaxy.zip",
    "https://huggingface.co/qualcomm/Whisper-Base-En/resolve/main/release_assets.json",
    "https://huggingface.co/qualcomm/Whisper-Small-V2/resolve/main/release_assets.json",
]


def head(url):
    req = urllib.request.Request(url, method="HEAD")
    try:
        r = urllib.request.urlopen(req, timeout=10)
        size = r.headers.get("Content-Length", "?")
        return f"{r.status} ({size} bytes)"
    except urllib.error.HTTPError as e:
        return str(e.code)
    except Exception as e:
        return str(e)


for u in CANDIDATES:
    tail = u.split("/")[-2] + "/" + u.split("/")[-1]
    print(f"{head(u):>18}  <- {tail}")
