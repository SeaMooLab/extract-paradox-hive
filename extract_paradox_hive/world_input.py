"""Locating a Bedrock world's LevelDB inside a directory or archive."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

ARCHIVE_SUFFIXES = (".zip", ".mcworld")
DB_FOLDER_NAME = "db"
TEMP_DIR_PREFIX = "bedrock-extractor-"


class WorldInputError(Exception):
    """Raised when an input path cannot be resolved to a LevelDB folder."""


def find_db_folder(start_path: str) -> Optional[str]:
    """Finds the first folder named ``db`` at or below a directory.

    Args:
        start_path: Directory to search, top-down.

    Returns:
        Path of the first matching folder, or None if there is none.
    """
    for root, _dirs, _files in os.walk(start_path):
        if os.path.basename(root) == DB_FOLDER_NAME:
            return root
    return None


@contextmanager
def locate_database(input_path: Path) -> Iterator[str]:
    """Resolves an input path to a LevelDB folder for the duration of a block.

    Directories are searched in place. Archives are unpacked into a temporary
    workspace that is deleted when the block exits, even on error.

    Args:
        input_path: A world directory, ``.zip`` or ``.mcworld`` file.

    Yields:
        Path of the ``db`` folder.

    Raises:
        WorldInputError: If the path does not exist, is an unsupported type,
            cannot be unpacked, or contains no ``db`` folder.
    """
    if not input_path.exists():
        raise WorldInputError(f"Input path does not exist: {input_path}")

    tmp_dir: Optional[str] = None
    try:
        if input_path.is_dir():
            print("[Harness] Target is a directory. Searching for LevelDB...")
            db_path = find_db_folder(str(input_path))
        elif input_path.is_file() and input_path.suffix in ARCHIVE_SUFFIXES:
            print("[Harness] Target is an archive. Extracting...")
            tmp_dir = tempfile.mkdtemp(prefix=TEMP_DIR_PREFIX)
            _extract_archive(input_path, tmp_dir)
            db_path = find_db_folder(tmp_dir)
        else:
            raise WorldInputError("Unsupported input type. Must be a directory, .zip, or .mcworld")

        if not db_path:
            raise WorldInputError("Could not locate a 'db' folder in the provided input.")

        print(f"[Harness] Found LevelDB at: {db_path}")
        yield db_path
    finally:
        if tmp_dir:
            print("[Harness] Cleaning up temporary workspace...")
            shutil.rmtree(tmp_dir, ignore_errors=True)


def _extract_archive(archive_path: Path, destination: str) -> None:
    """Unpacks a zip-format archive.

    Args:
        archive_path: Archive to unpack.
        destination: Existing directory to unpack into.

    Raises:
        WorldInputError: If the archive cannot be read or unpacked.
    """
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            archive.extractall(destination)
    except Exception as e:
        raise WorldInputError(f"Failed to extract archive: {e}") from e
    print(f"[Harness] Extracted to temporary workspace: {destination}")