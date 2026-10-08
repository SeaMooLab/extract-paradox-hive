"""LevelDB access layer for the Bedrock data hive extractor."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, Optional, Protocol, Tuple, cast

from extract_paradox_hive.bedrock_nbt import BedrockNBT
from extract_paradox_hive.hive_assembler import HiveAssembler


class LevelDBHandle(Protocol):
    """The slice of a LevelDB handle the extractor depends on.

    Real ``leveldb.LevelDB`` objects satisfy this, and so do the lightweight
    fakes used in tests. Iteration is deliberately not part of the contract:
    ``BedrockHiveExtractor`` probes for ``items``, ``iterate`` and plain
    iteration at runtime.
    """

    def get(self, key: bytes) -> bytes:
        """Returns the value for ``key``, raising ``KeyError`` if absent."""
        ...

    def close(self) -> None:
        """Releases the database."""
        ...


class LevelDBUnavailableError(ImportError):
    """Raised when the amulet-leveldb package cannot be imported."""


def load_leveldb() -> Any:
    """Imports the LevelDB class on demand.

    The import is deferred so that merely importing this package never kills
    the interpreter when the optional dependency is missing.

    Returns:
        The ``leveldb.LevelDB`` class.

    Raises:
        LevelDBUnavailableError: If amulet-leveldb is not installed.
    """
    try:
        from leveldb import LevelDB
    except ImportError as e:
        raise LevelDBUnavailableError(
            "amulet-leveldb is not installed. Run: pip install amulet-leveldb"
        ) from e
    return LevelDB


class BedrockHiveExtractor:
    """Reads dynamic properties out of a Bedrock world's LevelDB.

    This class owns database I/O only. Interpreting the raw properties is
    delegated to ``HiveAssembler``. Usable as a context manager so the
    database is always closed.

    Attributes:
        DYNAMIC_PROPERTIES_KEY: Key of the global NBT blob of dynamic properties.
        db_path: Filesystem path of the LevelDB directory.
        db: The open LevelDB handle, or None while closed.
    """

    DYNAMIC_PROPERTIES_KEY = b"DynamicProperties"

    def __init__(self, db_path: str):
        """Initializes the extractor without opening the database.

        Args:
            db_path: Path to the world's ``db`` directory.
        """
        self.db_path = db_path
        self.db: Optional[LevelDBHandle] = None

    def __enter__(self) -> BedrockHiveExtractor:
        """Opens the database.

        Returns:
            This extractor, ready for use.
        """
        self.open()
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Closes the database, letting any exception propagate."""
        self.close()

    def open(self) -> None:
        """Opens the LevelDB at ``db_path``.

        Raises:
            LevelDBUnavailableError: If amulet-leveldb is not installed.
        """
        self.db = load_leveldb()(self.db_path)

    def _require_db(self) -> LevelDBHandle:
        """Returns the open database handle.

        Returns:
            The handle opened by ``open()``.

        Raises:
            RuntimeError: If the database has not been opened, or was closed.
        """
        if self.db is None:
            raise RuntimeError("Database is not open. Call open() or use a with-block.")
        return self.db

    def close(self) -> None:
        """Closes the database if it is open. Safe to call repeatedly."""
        if self.db is not None:
            self.db.close()
            self.db = None

    def scan_for_dynamic_properties(self) -> Dict[str, str]:
        """Collects every string property stored in the database.

        The global NBT blob is read first, then a sweep of all remaining
        entries adds anything that looks like a text property. A failure
        part-way through the sweep prints a warning and keeps what was found.

        Returns:
            Flat mapping of property key to string value.

        Raises:
            RuntimeError: If the database is not open.
        """
        raw_properties = self._read_global_nbt_properties()

        try:
            for key_bytes, val_bytes in self._iter_entries():
                decoded = self._decode_property(key_bytes, val_bytes)
                if decoded is not None:
                    raw_properties[decoded[0]] = decoded[1]
        except Exception as e:
            print(f"[Warning] Legacy database sweep encountered an error: {e}")

        return raw_properties

    def stitch_chunks(self, raw_properties: dict, base_key: str) -> str:
        """Concatenates the numbered chunks stored under a base key.

        Args:
            raw_properties: Flat mapping of property key to string value.
            base_key: Key whose numbered chunks (``<base_key>/0``, ``<base_key>/1``, and so on)
                should be joined.

        Returns:
            The joined chunk text, or an empty string when there are no chunks.
        """
        return HiveAssembler.stitch_chunks(raw_properties, base_key)

    def extract(self) -> dict:
        """Scans the database and assembles the structured hive.

        Returns:
            Mapping of namespace to decoded entries, plus any unclaimed global
            properties.
        """
        print("[LevelDB] Scanning database...")
        raw_properties = self.scan_for_dynamic_properties()
        print(f"[LevelDB] Found {len(raw_properties)} raw string properties.")
        return HiveAssembler(raw_properties).assemble()

    def _read_global_nbt_properties(self) -> Dict[str, str]:
        """Reads the strings out of the global ``DynamicProperties`` NBT blob.

        Returns:
            Mapping of NBT tag name to string value, empty if the blob is
            absent or empty.

        Raises:
            RuntimeError: If the database is not open.
        """
        try:
            nbt_blob = self._require_db().get(self.DYNAMIC_PROPERTIES_KEY)
        except KeyError:
            return {}
        if not nbt_blob:
            return {}

        print("[LevelDB] Found global 'DynamicProperties' NBT blob. Decrypting NBT...")
        return BedrockNBT.extract_strings(nbt_blob)

    def _iter_entries(self) -> Iterator[Tuple[bytes, Optional[bytes]]]:
        """Iterates every key/value pair, tolerating differing LevelDB APIs.

        Prefers ``items()``, then ``iterate()``, then plain iteration over
        keys with a ``get()`` per key.

        Yields:
            ``(key, value)`` byte pairs.

        Raises:
            RuntimeError: If the database is not open.
        """
        db = self._require_db()
        for method_name in ("items", "iterate"):
            method = getattr(db, method_name, None)
            if method is not None:
                yield from method()
                return

        for key in cast(Iterable[bytes], db):
            yield key, db.get(key)

    def _decode_property(
        self, key_bytes: bytes, val_bytes: Optional[bytes]
    ) -> Optional[Tuple[str, str]]:
        """Turns a raw database entry into a text property, if it is one.

        Args:
            key_bytes: Raw entry key.
            val_bytes: Raw entry value.

        Returns:
            ``(key, value)`` with the key reduced to its printable ASCII
            characters, or None for the NBT blob, empty entries, binary keys,
            non-UTF-8 values and binary-looking values.
        """
        if not key_bytes or not val_bytes or key_bytes == self.DYNAMIC_PROPERTIES_KEY:
            return None

        clean_key = "".join(chr(b) for b in key_bytes if 32 <= b <= 126)
        if not HiveAssembler.is_plausible_key(clean_key):
            return None

        try:
            value = val_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if HiveAssembler.has_binary_markers(value):
            return None
        return clean_key, value
