"""Gesture Football device server — transport, sessions, telemetry.

Knows nothing about football (A§9.4). Game semantics live in engine/.

Dev vs target: developed on an x86-64 machine, deployed to the Snapdragon
X Elite laptop (ARM64). Pure Python + aiohttp — no architecture-specific
dependencies allowed in this package.
"""
