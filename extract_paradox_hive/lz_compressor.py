"""LZW decompression for Bedrock data hive payloads."""

from __future__ import annotations

import json


class LZCompressor:
    """UTF-16 safe LZW decompressor translated for Python.

    Expands the compact JSON arrays of integer codes used to store repetitive
    strings and JSON documents back into their original text.

    Attributes:
        ALPHABET_SIZE: Number of single-character entries that seed the
            decompression dictionary. Codes at or above this value refer to
            entries learned while decoding.
    """

    ALPHABET_SIZE = 256

    @staticmethod
    def decompress(compressed_str: str) -> str:
        """Decompresses a JSON array of LZW codes into the original text.

        Args:
            compressed_str: A JSON-encoded list of integer LZW codes.

        Returns:
            The decompressed text. An empty string is returned when the input
            is empty, is not a JSON list, or is an empty list.

        Raises:
            ValueError: If the input is not valid JSON, if any code is null,
                or if a code references a dictionary entry that does not
                exist.
        """
        codes = LZCompressor._parse_codes(compressed_str)
        if not codes:
            return ""
        return LZCompressor._expand(codes)

    @staticmethod
    def _parse_codes(compressed_str: str) -> list:
        """Parses the JSON payload into a list of codes.

        Args:
            compressed_str: A JSON-encoded list of integer LZW codes.

        Returns:
            The decoded list, or an empty list when the input is empty or
            decodes to something other than a list.

        Raises:
            ValueError: If the input is not valid JSON.
        """
        if not compressed_str:
            return []
        try:
            codes = json.loads(compressed_str)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON payload: {e}") from e
        return codes if isinstance(codes, list) else []

    @staticmethod
    def _is_code(value: object) -> bool:
        """Tells whether a decoded JSON value can be used as an LZW code.

        Args:
            value: A value taken from the decoded JSON list.

        Returns:
            True for integers. Booleans are rejected even though ``bool``
            subclasses ``int``.
        """
        return isinstance(value, int) and not isinstance(value, bool)

    @staticmethod
    def _expand(codes: list) -> str:
        """Runs the LZW expansion over a non-empty list of codes.

        Args:
            codes: A non-empty list of LZW codes.

        Returns:
            The decompressed text.

        Raises:
            ValueError: If the first code is null or not an integer, any later
                code is null, or a code references a dictionary entry that
                does not exist.
        """
        if codes[0] is None:
            raise ValueError("Initial code is null")
        if not LZCompressor._is_code(codes[0]):
            raise ValueError(f"Initial code is not an integer: {codes[0]!r}")

        dictionary = {i: chr(i) for i in range(LZCompressor.ALPHABET_SIZE)}
        dict_size = LZCompressor.ALPHABET_SIZE

        previous = chr(codes[0])
        result = [previous]

        for code in codes[1:]:
            if code is None:
                raise ValueError(
                    "Null code encountered in stream (Likely unseeded Unicode char)"
                )

            if code in dictionary:
                entry = dictionary[code]
            elif code == dict_size:
                entry = previous + previous[0]
            else:
                raise ValueError(
                    f"Invalid dictionary index: {code} (dict_size: {dict_size})"
                )

            result.append(entry)
            dictionary[dict_size] = previous + entry[0]
            dict_size += 1
            previous = entry

        return "".join(result)
