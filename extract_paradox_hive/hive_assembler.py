"""Reassembly of chunked, pointer-indexed properties into a structured hive."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set

from extract_paradox_hive.lz_compressor import LZCompressor


class HiveAssembler:
    """Interprets the conventions of Paradox's dynamic property store.

    The store is a flat string-to-string mapping. A namespace publishes a
    ``<namespace>/pointers`` entry holding a JSON list of active keys, and each
    active key holds a compressed payload that may be split across
    ``<key>/0``, ``<key>/1`` and so on. Anything not claimed by a namespace is
    reported as a global property.

    This class is pure: it never touches the database, so it can be exercised
    with plain dictionaries.

    Attributes:
        GLOBAL_PROPERTIES_KEY: Hive key under which unclaimed properties are
            reported.
        POINTERS_SEGMENT: Path segment that marks a namespace's pointer entry.
        MIN_KEY_LENGTH: Shortest key considered a real property name.
        BINARY_MARKER: Sequence that identifies binary chunk data posing as text.
        COMPRESSED_HEADER_PREFIX: First character of an optional payload header.
    """

    GLOBAL_PROPERTIES_KEY = "__GLOBAL_PROPERTIES__"
    POINTERS_SEGMENT = "/pointers"
    MIN_KEY_LENGTH = 3
    BINARY_MARKER = "\x00\x00"
    COMPRESSED_HEADER_PREFIX = "\x02"

    _LETTER = re.compile(r"[a-zA-Z]")

    def __init__(self, raw_properties: Dict[str, str]) -> None:
        """Initializes the assembler.

        Args:
            raw_properties: Flat mapping of property key to string value.
        """
        self._raw = raw_properties
        self._consumed: Set[str] = set()

    @staticmethod
    def is_plausible_key(key: str) -> bool:
        """Tells real property names apart from binary keys pretending to be text.

        Args:
            key: Candidate property key.

        Returns:
            True if the key is long enough and contains at least one letter.
        """
        return len(key) >= HiveAssembler.MIN_KEY_LENGTH and bool(
            HiveAssembler._LETTER.search(key)
        )

    @staticmethod
    def has_binary_markers(value: str) -> bool:
        """Detects payloads that are really binary chunk data.

        Args:
            value: Decoded property value.

        Returns:
            True if the value contains consecutive null characters.
        """
        return HiveAssembler.BINARY_MARKER in value

    @staticmethod
    def chunk_keys(raw_properties: Dict[str, str], base_key: str) -> List[str]:
        """Lists the contiguous numbered chunk keys under a base key.

        Args:
            raw_properties: Flat mapping of property key to string value.
            base_key: Key whose numbered chunks (``<base_key>/0``, ``<base_key>/1``, and so on)
                are wanted.

        Returns:
            Chunk keys in order, stopping at the first gap. Empty when the
            value is not chunked.
        """
        keys: List[str] = []
        while f"{base_key}/{len(keys)}" in raw_properties:
            keys.append(f"{base_key}/{len(keys)}")
        return keys

    @staticmethod
    def stitch_chunks(raw_properties: Dict[str, str], base_key: str) -> str:
        """Concatenates the chunks stored under a base key.

        Args:
            raw_properties: Flat mapping of property key to string value.
            base_key: Key whose numbered chunks should be joined.

        Returns:
            The joined chunk text, or an empty string when there are no chunks.
        """
        return "".join(
            raw_properties[key]
            for key in HiveAssembler.chunk_keys(raw_properties, base_key)
        )

    def assemble(self) -> Dict[str, Any]:
        """Builds the structured hive from the raw properties.

        Returns:
            Mapping of namespace to its decoded entries. Properties that no
            namespace claimed appear under ``GLOBAL_PROPERTIES_KEY``.
        """
        hive: Dict[str, Any] = {}
        for namespace in self._find_namespaces():
            hive[namespace] = self._assemble_namespace(namespace)

        orphans = self._collect_orphans()
        if orphans:
            hive[self.GLOBAL_PROPERTIES_KEY] = orphans
        return hive

    def _find_namespaces(self) -> List[str]:
        """Finds every namespace that publishes a pointer entry.

        Returns:
            Namespace names in sorted order, so output is deterministic.
        """
        return sorted(
            {
                key.split(self.POINTERS_SEGMENT)[0]
                for key in self._raw
                if self.POINTERS_SEGMENT in key
            }
        )

    def _assemble_namespace(self, namespace: str) -> Dict[str, Any]:
        """Decodes every entry a namespace points at.

        Args:
            namespace: Namespace whose pointer entry should be followed.

        Returns:
            Mapping of entry name (last path segment) to decoded value.
            Empty when the pointer entry is missing or unreadable.
        """
        entries: Dict[str, Any] = {}
        pointer_keys = self._read_pointer_keys(namespace)
        if pointer_keys is None:
            return entries

        for base_key in pointer_keys:
            stitched = self._claim_value(base_key)
            if not stitched:
                continue
            entry_name = base_key.split("/")[-1]
            entries[entry_name] = self._decode_entry(
                f"{namespace}/{entry_name}", stitched
            )
        return entries

    def _read_pointer_keys(self, namespace: str) -> Optional[List[str]]:
        """Loads and parses a namespace's list of active keys.

        Args:
            namespace: Namespace whose pointer entry should be read.

        Returns:
            The active keys, or None (after printing a warning) when the
            pointer entry is absent or is not a JSON list.
        """
        pointer_key = f"{namespace}{self.POINTERS_SEGMENT}"
        pointer_data = self._claim_value(pointer_key)
        if not pointer_data:
            print(f"[Warning] No pointer data found for namespace: {namespace}")
            return None

        # A chunked copy wins in _claim_value; still retire a whole-valued twin.
        self._consumed.add(pointer_key)

        try:
            pointer_keys = json.loads(pointer_data)
        except json.JSONDecodeError:
            pointer_keys = None
        if not isinstance(pointer_keys, list):
            print(f"[Warning] Failed to parse pointers for namespace: {namespace}")
            return None
        return pointer_keys

    def _claim_value(self, key: str) -> Optional[str]:
        """Fetches a value, whether chunked or stored whole, and marks it used.

        Chunked storage wins over a whole value stored under the same key.

        Args:
            key: Base key of the value.

        Returns:
            The stitched chunk text or the whole value, or a falsy result when
            nothing is stored.
        """
        chunk_keys = self.chunk_keys(self._raw, key)
        stitched = "".join(self._raw[chunk_key] for chunk_key in chunk_keys)
        if stitched:
            self._consumed.update(chunk_keys)
            return stitched

        whole = self._raw.get(key)
        if whole:
            self._consumed.add(key)
        return whole

    def _decode_entry(self, label: str, stitched: str) -> Any:
        """Decompresses and parses one entry's payload.

        Args:
            label: ``namespace/entry`` name used in warnings.
            stitched: Full stored text, including any header.

        Returns:
            The parsed JSON value, the raw decompressed text if it is not
            JSON, None if it decompresses to blank text, or an error record
            holding the raw payload if decompression fails.
        """
        payload = self._strip_header(stitched)
        try:
            return self._parse_decompressed(LZCompressor.decompress(payload))
        except ValueError as e:
            print(
                f"[Warning] Corrupted payload caught for '{label}' - Dumping raw data."
            )
            return {
                "__ERROR__": "Decompression Failed",
                "__REASON__": str(e),
                "__RAW_PAYLOAD__": payload,
                "__HEX__": payload.encode("utf-8", errors="replace").hex(),
            }

    def _strip_header(self, stitched: str) -> str:
        """Removes the optional ``\\x02...:`` header from a stored payload.

        Args:
            stitched: Full stored text.

        Returns:
            The payload with its header removed, or the input unchanged when
            there is no complete header.
        """
        if stitched.startswith(self.COMPRESSED_HEADER_PREFIX):
            header_end = stitched.find(":", 2)
            if header_end != -1:
                return stitched[header_end + 1 :]
        return stitched

    def _parse_decompressed(self, text: str) -> Any:
        """Interprets decompressed text.

        Args:
            text: Decompressed payload text.

        Returns:
            None for blank text, otherwise the parsed JSON or the text itself.
        """
        if not text.strip():
            return None
        return self._loads_or_raw(text)

    def _collect_orphans(self) -> Dict[str, Any]:
        """Gathers plausible properties that no namespace claimed.

        Returns:
            Mapping of key to parsed JSON, or to the raw text when the value is
            not JSON.
        """
        orphans: Dict[str, Any] = {}
        for key, value in self._raw.items():
            if key in self._consumed:
                continue
            if not self.is_plausible_key(key) or self.has_binary_markers(value):
                continue
            orphans[key] = self._loads_or_raw(value)
        return orphans

    @staticmethod
    def _loads_or_raw(text: str) -> Any:
        """Parses JSON, falling back to the original text.

        Args:
            text: Text that may or may not be JSON.

        Returns:
            The parsed JSON value, or ``text`` if it is not valid JSON.
        """
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
