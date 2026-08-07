# Setup verification

One command to confirm a fresh clone is ready to run the game host:

```powershell
python setup_check\verify_setup.py
```

It checks the Python version, the packages from [`requirements.txt`](../requirements.txt), the server's referee geometry, and then boots the real aiohttp app on an **ephemeral** port to probe `/edge/status`, `/fx/status`, `/hw/status`, and the stadium TV page. Port 8080 is never touched, and nothing in the repository is modified.

- Exit code **0** — setup is good; start playing with `python server.py`.
- Exit code **1** — a check failed; the offending line and a fix hint are printed.

For the full test matrix (unit tests, end-to-end tests, Android, UNO Q), see [`docs/TESTING.md`](../docs/TESTING.md).
