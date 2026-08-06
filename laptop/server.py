#!/usr/bin/env python3
"""
GESTURE FOOTBALL — launcher shim (four-pillar mainline)
=======================================================
The implementation moved:

    server/   device server — transport, sessions, telemetry, discovery, FX worker
    engine/   match logic — phases, THE WALL, referee, campaign, Desk, SceneEngine

`python server.py` still works and runs both in one process (in-proc link),
exactly like the old monolith. For the two-process boundary:

    python -m server --link tcp     # terminal 1: device server
    python -m engine                # terminal 2: match engine (GF_* env goes here)

Docs: docs/run-guide.md · docs/server-architecture.md · docs/device-protocol.md
Pillars: laptop/SCENE_ENGINE.md · laptop/NEURAL_FX.md
"""

from server.app import run

if __name__ == "__main__":
    run()
