"""20-byte fixed header — every UDP packet, version at byte 0 (A§8.1).

offset  size  field
  0      1    version              <- must stay at offset 0 forever
  1      1    msg_type
  2      1    flags
  3      1    channel
  4      4    session_id           big-endian
  8      4    seq                  big-endian
 12      8    device_timestamp_us  big-endian

No delimiter between header and body; both sides agree on 20 bytes out-of-band.
Today only the discovery beacon (DISCOVER/ANNOUNCE) travels as datagrams; the
WebSocket path stays JSON. This module is the single point of truth either way.
"""

from __future__ import annotations

import struct
from typing import NamedTuple

VERSION = 1
HEADER = struct.Struct(">BBBBIIQ")
SIZE = HEADER.size  # 20

# Datagrams are capped well under one MTU: one lost IP fragment kills the whole
# datagram, so we never allow fragmentation to begin with.
MAX_DATAGRAM = 1200


class Header(NamedTuple):
    version: int
    msg_type: int
    flags: int
    channel: int
    session_id: int
    seq: int
    ts_us: int


def pack(msg_type: int, *, flags: int = 0, channel: int = 0,
         session_id: int = 0, seq: int = 0, ts_us: int = 0) -> bytes:
    return HEADER.pack(VERSION, msg_type, flags, channel, session_id, seq, ts_us)


def unpack(data: bytes) -> Header | None:
    """Parse a header. Returns None (drop silently) on anything malformed —
    the sender's retry timer handles it (A§8.4)."""
    if len(data) < SIZE:
        return None
    h = Header(*HEADER.unpack_from(data))
    if h.version != VERSION:
        return None
    return h


def body(data: bytes) -> bytes:
    return data[SIZE:]
