"""Lightweight little-endian NBT reader for Bedrock property blobs."""

from __future__ import annotations

from typing import Dict, Optional

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

FIXED_PAYLOAD_SIZES = {
    TAG_BYTE: 1,
    TAG_SHORT: 2,
    TAG_INT: 4,
    TAG_LONG: 8,
    TAG_FLOAT: 4,
    TAG_DOUBLE: 8,
}

ARRAY_ELEMENT_SIZES = {
    TAG_BYTE_ARRAY: 1,
    TAG_INT_ARRAY: 4,
    TAG_LONG_ARRAY: 8,
}


class _NbtReader:
    """Single-pass cursor over an NBT byte buffer.

    The reader skips every payload it does not care about and records each
    ``TAG_String`` it passes, keyed by its tag name.

    Attributes:
        strings: Every ``TAG_String`` found so far, mapping tag name to value.
    """

    def __init__(self, data: bytes) -> None:
        """Initializes the reader.

        Args:
            data: Raw NBT bytes, starting at the root tag.
        """
        self._data = data
        self._offset = 0
        self.strings: Dict[str, str] = {}

    def parse(self) -> Dict[str, str]:
        """Walks the buffer from the root compound tag.

        Returns:
            Every ``TAG_String`` found, mapping tag name to value. Buffers that
            do not start with a compound tag yield an empty mapping.

        Raises:
            IndexError: If a list header is truncated.
        """
        if self._data and self._data[0] == TAG_COMPOUND:
            self._offset += 1
            self._read_string()
            self._read_payload(TAG_COMPOUND)
        return self.strings

    def _read_int(self, size: int, signed: bool) -> int:
        """Reads a little-endian integer and advances the cursor.

        Args:
            size: Width of the integer in bytes.
            signed: Whether the value is two's-complement signed.

        Returns:
            The decoded integer. Truncated input decodes as if zero-padded.
        """
        end = self._offset + size
        value = int.from_bytes(self._data[self._offset : end], "little", signed=signed)
        self._offset = end
        return value

    def _read_string(self) -> bytes:
        """Reads a length-prefixed string and advances the cursor.

        Returns:
            The raw string bytes, or ``b""`` if no length prefix remains.
        """
        if self._offset + 2 > len(self._data):
            return b""
        length = self._read_int(2, signed=False)
        value = self._data[self._offset : self._offset + length]
        self._offset += length
        return value

    def _read_payload(self, tag_type: int) -> Optional[bytes]:
        """Consumes one tag payload.

        Args:
            tag_type: The NBT tag type id whose payload starts at the cursor.

        Returns:
            The raw bytes for ``TAG_String`` payloads, otherwise ``None``.
        """
        if tag_type in FIXED_PAYLOAD_SIZES:
            self._offset += FIXED_PAYLOAD_SIZES[tag_type]
        elif tag_type in ARRAY_ELEMENT_SIZES:
            self._skip_array(ARRAY_ELEMENT_SIZES[tag_type])
        elif tag_type == TAG_STRING:
            return self._read_string()
        elif tag_type == TAG_LIST:
            self._read_list()
        elif tag_type == TAG_COMPOUND:
            self._read_compound()
        return None

    def _skip_array(self, element_size: int) -> None:
        """Skips a length-prefixed array payload.

        A negative length is treated as empty, so corrupt data can never move
        the cursor backwards and make the parser loop forever.

        Args:
            element_size: Width of each array element in bytes.
        """
        length = max(self._read_int(4, signed=True), 0)
        self._offset += length * element_size

    def _read_list(self) -> None:
        """Consumes a list payload, recording strings nested in its elements.

        Raises:
            IndexError: If the element type byte is missing.
        """
        element_type = self._data[self._offset]
        self._offset += 1
        count = self._read_int(4, signed=True)
        for _ in range(count):
            self._read_payload(element_type)

    def _read_compound(self) -> None:
        """Consumes a compound payload, recording every string child."""
        while self._offset < len(self._data):
            tag_type = self._data[self._offset]
            self._offset += 1
            if tag_type == TAG_END:
                break
            name = self._read_string()
            value = self._read_payload(tag_type)
            if tag_type == TAG_STRING:
                self._record_string(name, value)

    def _record_string(self, name: bytes, value: bytes) -> None:
        """Stores a string tag, silently dropping anything that is not UTF-8.

        Args:
            name: Raw tag name bytes.
            value: Raw tag value bytes.
        """
        try:
            self.strings[name.decode("utf-8")] = value.decode("utf-8")
        except UnicodeDecodeError:
            pass


class BedrockNBT:
    """Lightweight, dependency-free little-endian NBT parser.

    Built to rip ``TAG_String`` payloads out of Bedrock's monolithic property
    blobs.
    """

    @staticmethod
    def extract_strings(data: bytes) -> Dict[str, str]:
        """Extracts every ``TAG_String`` from an NBT blob.

        Parsing is best-effort: if the blob is malformed, a warning is printed
        and whatever was recovered before the failure is returned.

        Args:
            data: Raw NBT bytes whose root tag is a compound.

        Returns:
            Mapping of tag name to string value, flattened across nesting
            levels. Later tags overwrite earlier ones with the same name.
        """
        reader = _NbtReader(data)
        try:
            reader.parse()
        except Exception as e:
            print(f"[Warning] NBT Parsing hit an exception: {e}")
        return reader.strings
