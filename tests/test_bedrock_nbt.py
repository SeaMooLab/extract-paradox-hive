import struct
import unittest

from extract_paradox_hive.bedrock_nbt import BedrockNBT
from tests.helpers import (
    nbt_compound_payload,
    nbt_root,
    nbt_str,
    nbt_string_tag,
    nbt_tag,
)


class ExtractStringsTests(unittest.TestCase):
    def test_collects_top_level_strings(self):
        blob = nbt_root(nbt_string_tag("a", "1"), nbt_string_tag("b", "2"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"a": "1", "b": "2"})

    def test_empty_and_non_compound_blobs_yield_nothing(self):
        self.assertEqual(BedrockNBT.extract_strings(b""), {})
        self.assertEqual(BedrockNBT.extract_strings(b"\x08\x00\x00"), {})

    def test_skips_every_fixed_width_and_array_type(self):
        children = [
            nbt_tag(1, "byte", b"\x01"),
            nbt_tag(2, "short", struct.pack("<h", 1)),
            nbt_tag(3, "int", struct.pack("<i", 1)),
            nbt_tag(4, "long", struct.pack("<q", 1)),
            nbt_tag(5, "float", struct.pack("<f", 1.0)),
            nbt_tag(6, "double", struct.pack("<d", 1.0)),
            nbt_tag(7, "bytes", struct.pack("<i", 3) + b"abc"),
            nbt_tag(11, "ints", struct.pack("<i", 2) + b"\x00" * 8),
            nbt_tag(12, "longs", struct.pack("<i", 2) + b"\x00" * 16),
            nbt_string_tag("found", "yes"),
        ]
        self.assertEqual(BedrockNBT.extract_strings(nbt_root(*children)), {"found": "yes"})

    def test_reads_strings_in_nested_compounds(self):
        inner = nbt_tag(10, "inner", nbt_compound_payload(nbt_string_tag("deep", "x")))
        blob = nbt_root(inner, nbt_string_tag("after", "y"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"deep": "x", "after": "y"})

    def test_reads_strings_inside_compounds_in_lists(self):
        element = nbt_compound_payload(nbt_string_tag("k", "v"))
        lst = nbt_tag(9, "lst", bytes([10]) + struct.pack("<i", 1) + element)
        blob = nbt_root(lst, nbt_string_tag("after", "y"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"k": "v", "after": "y"})

    def test_unnamed_list_strings_are_consumed_not_recorded(self):
        payload = bytes([8]) + struct.pack("<i", 2) + nbt_str("p") + nbt_str("q")
        blob = nbt_root(nbt_tag(9, "names", payload), nbt_string_tag("after", "y"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"after": "y"})

    def test_later_duplicate_names_overwrite(self):
        blob = nbt_root(nbt_string_tag("a", "old"), nbt_string_tag("a", "new"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"a": "new"})

    def test_non_utf8_strings_are_dropped(self):
        bad = nbt_tag(8, "bad", struct.pack("<H", 2) + b"\xff\xfe")
        blob = nbt_root(bad, nbt_string_tag("ok", "1"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"ok": "1"})

    def test_empty_string_value_is_kept(self):
        self.assertEqual(BedrockNBT.extract_strings(nbt_root(nbt_string_tag("e", ""))), {"e": ""})

    def test_truncated_blob_returns_what_was_recovered(self):
        blob = nbt_root(nbt_string_tag("a", "1"), nbt_string_tag("b", "2"))
        result = BedrockNBT.extract_strings(blob[:-8])
        self.assertEqual(result.get("a"), "1")

    def test_truncated_list_header_warns_instead_of_raising(self):
        blob = nbt_root(nbt_string_tag("a", "1")) [:-1] + nbt_tag(9, "l", b"")
        self.assertEqual(BedrockNBT.extract_strings(blob).get("a"), "1")

    def test_negative_array_length_cannot_loop_forever(self):
        evil = nbt_tag(7, "evil", struct.pack("<i", -1000))
        blob = nbt_root(evil, nbt_string_tag("a", "1"))
        self.assertEqual(BedrockNBT.extract_strings(blob), {"a": "1"})


if __name__ == "__main__":
    unittest.main()
