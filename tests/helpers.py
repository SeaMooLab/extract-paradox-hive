"""Shared builders and fakes for the test suite."""

from __future__ import annotations

import json
import struct
from typing import Dict, Iterable, List, Tuple


def lzw_compress(text: str) -> List[int]:
    """Reference LZW compressor (ASCII-seeded) used to produce test payloads."""
    dictionary = {chr(i): i for i in range(256)}
    w, out = "", []
    for c in text:
        wc = w + c
        if wc in dictionary:
            w = wc
        else:
            out.append(dictionary[w])
            dictionary[wc] = len(dictionary)
            w = c
    if w:
        out.append(dictionary[w])
    return out


def compress_to_payload(value, header: str = "") -> str:
    """JSON-encodes ``value``, LZW-compresses it, and returns the stored text."""
    return header + json.dumps(lzw_compress(json.dumps(value)))


# --- NBT (little-endian) builders -------------------------------------------


def nbt_str(s: str) -> bytes:
    raw = s.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def nbt_tag(tag_type: int, name: str, payload: bytes) -> bytes:
    return bytes([tag_type]) + nbt_str(name) + payload


def nbt_string_tag(name: str, value: str) -> bytes:
    return nbt_tag(8, name, nbt_str(value))


def nbt_compound_payload(*children: bytes) -> bytes:
    return b"".join(children) + b"\x00"


def nbt_root(*children: bytes) -> bytes:
    return nbt_tag(10, "", nbt_compound_payload(*children))


# --- Fake LevelDB handles ---------------------------------------------------


class FakeLevelDB:
    """Dict-backed stand-in exposing ``items()`` like amulet-leveldb."""

    instances: List["FakeLevelDB"] = []

    def __init__(self, path, data: Dict[bytes, bytes] | None = None):
        self.path = path
        self.data = dict(data or {})
        self.closed = False
        FakeLevelDB.instances.append(self)

    def get(self, key):
        return self.data[key]

    def items(self):
        return iter(list(self.data.items()))

    def close(self):
        self.closed = True


def fake_leveldb_factory(data: Dict[bytes, bytes]):
    """Returns a LevelDB-class replacement preloaded with ``data``."""

    class _Preloaded(FakeLevelDB):
        def __init__(self, path):
            super().__init__(path, data)

    return _Preloaded
