#!/usr/bin/env python3
"""Command-line interface for the Minecraft Bedrock Data Hive extractor.

Wires the focused modules together: ``world_input`` finds the LevelDB,
``hive_extractor`` reads it, ``hive_assembler`` interprets it, and this module
writes the result. The classes that used to live here are re-exported so
existing imports keep working.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Sequence

from extract_paradox_hive.bedrock_nbt import BedrockNBT
from extract_paradox_hive.hive_extractor import (
    BedrockHiveExtractor,
    LevelDBUnavailableError,
    load_leveldb,
)
from extract_paradox_hive.lz_compressor import LZCompressor
from extract_paradox_hive.world_input import (
    WorldInputError,
    find_db_folder,
    locate_database,
)

__all__ = [
    "BedrockHiveExtractor",
    "BedrockNBT",
    "LZCompressor",
    "find_db_folder",
    "main",
    "run",
]

USAGE = (
    "\nMinecraft Bedrock Data Hive Extractor\n"
    "Usage: python -m extract_paradox_hive <input_world_or_zip> <output_file.json>\n"
)


def extract_hive(db_path: str) -> Dict[str, Any]:
    """Reads the data hive out of a LevelDB, always closing it afterwards.

    Args:
        db_path: Path to the world's ``db`` directory.

    Returns:
        Mapping of namespace to decoded entries.
    """
    with BedrockHiveExtractor(db_path) as extractor:
        return extractor.extract()


def write_hive(data: Dict[str, Any], output_path: Path) -> None:
    """Writes the hive to disk as indented UTF-8 JSON.

    Args:
        data: The extracted hive.
        output_path: Destination file.
    """
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def run(argv: Sequence[str]) -> int:
    """Runs the extractor for a set of command-line arguments.

    Args:
        argv: Arguments excluding the program name: the input world path
            followed by the output JSON path.

    Returns:
        Process exit code: 0 on success, 1 on any failure.
    """
    if len(argv) < 2:
        print(USAGE)
        return 1

    try:
        load_leveldb()
    except LevelDBUnavailableError as e:
        print(f"[Fatal] {e}")
        return 1

    input_path = Path(argv[0]).resolve()
    output_path = Path(argv[1]).resolve()

    try:
        with locate_database(input_path) as db_path:
            data = extract_hive(db_path)
            write_hive(data, output_path)
            print(f"\n[Success] Data Hive extracted and saved to: {output_path}")
    except WorldInputError as e:
        print(f"[Error] {e}")
        return 1
    except Exception as e:
        print(f"\n[Fatal Error] {e}")
        return 1
    return 0


def main() -> None:
    """Entry point: runs against ``sys.argv`` and exits non-zero on failure."""
    exit_code = run(sys.argv[1:])
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
