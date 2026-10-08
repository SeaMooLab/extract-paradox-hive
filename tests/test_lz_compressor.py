import unittest

from extract_paradox_hive.lz_compressor import LZCompressor
from tests.helpers import lzw_compress
import json


class DecompressTests(unittest.TestCase):
    def roundtrip(self, text):
        self.assertEqual(LZCompressor.decompress(json.dumps(lzw_compress(text))), text)

    def test_round_trips_plain_text(self):
        self.roundtrip("hello world")

    def test_round_trips_repetitive_json(self):
        self.roundtrip('{"a":[1,1,1,1,1,1],"b":"abababababab"}')

    def test_handles_kwkwk_case(self):
        # "aaaaaa" forces a code equal to the dictionary size.
        self.roundtrip("aaaaaa")

    def test_single_code(self):
        self.assertEqual(LZCompressor.decompress("[65]"), "A")

    def test_empty_inputs_yield_empty_string(self):
        for value in ("", "[]", '{"a": 1}', '"text"', "5"):
            with self.subTest(value=value):
                self.assertEqual(LZCompressor.decompress(value), "")

    def test_invalid_json_raises_value_error(self):
        with self.assertRaises(ValueError):
            LZCompressor.decompress("not json")

    def test_null_first_code_raises(self):
        with self.assertRaisesRegex(ValueError, "Initial code is null"):
            LZCompressor.decompress("[null, 65]")

    def test_non_integer_first_code_raises_value_error(self):
        # Must be ValueError so the assembler can dump the raw payload
        # instead of crashing the whole extraction.
        for value in ('["a"]', "[1.5]", "[true]"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    LZCompressor.decompress(value)

    def test_null_code_mid_stream_raises(self):
        with self.assertRaisesRegex(ValueError, "Null code"):
            LZCompressor.decompress("[65, null]")

    def test_unknown_dictionary_index_raises(self):
        with self.assertRaisesRegex(ValueError, "Invalid dictionary index"):
            LZCompressor.decompress("[65, 999]")


if __name__ == "__main__":
    unittest.main()
