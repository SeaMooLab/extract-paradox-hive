import unittest
from contextlib import redirect_stdout
from io import StringIO

from extract_paradox_hive.hive_assembler import HiveAssembler
from tests.helpers import compress_to_payload


def assemble(raw):
    with redirect_stdout(StringIO()) as out:
        hive = HiveAssembler(raw).assemble()
    return hive, out.getvalue()


class KeyHelperTests(unittest.TestCase):
    def test_plausible_key_needs_length_and_a_letter(self):
        self.assertTrue(HiveAssembler.is_plausible_key("abc"))
        self.assertTrue(HiveAssembler.is_plausible_key("1a2"))
        self.assertFalse(HiveAssembler.is_plausible_key("ab"))
        self.assertFalse(HiveAssembler.is_plausible_key("123"))
        self.assertFalse(HiveAssembler.is_plausible_key(""))

    def test_binary_markers(self):
        self.assertTrue(HiveAssembler.has_binary_markers("a\x00\x00b"))
        self.assertFalse(HiveAssembler.has_binary_markers("a\x00b"))

    def test_chunk_keys_stop_at_first_gap(self):
        raw = {"k/0": "a", "k/1": "b", "k/3": "d"}
        self.assertEqual(HiveAssembler.chunk_keys(raw, "k"), ["k/0", "k/1"])

    def test_stitch_chunks_joins_in_numeric_order(self):
        raw = {f"k/{i}": str(i) for i in range(12)}
        self.assertEqual(HiveAssembler.stitch_chunks(raw, "k"), "0123456789" "1011")

    def test_stitch_chunks_empty_when_unchunked(self):
        self.assertEqual(HiveAssembler.stitch_chunks({"k": "v"}, "k"), "")


class NamespaceTests(unittest.TestCase):
    def test_decodes_whole_valued_entries(self):
        raw = {
            "ns/pointers": '["ns/config"]',
            "ns/config": compress_to_payload({"on": True}),
        }
        hive, _ = assemble(raw)
        self.assertEqual(hive, {"ns": {"config": {"on": True}}})

    def test_decodes_chunked_pointers_and_chunked_data(self):
        pointers = '["ns/a", "ns/b"]'
        payload = compress_to_payload({"n": list(range(30))})
        mid = len(payload) // 2
        raw = {
            "ns/pointers/0": pointers[:6],
            "ns/pointers/1": pointers[6:],
            "ns/a/0": payload[:mid],
            "ns/a/1": payload[mid:],
            "ns/b": compress_to_payload("hi"),
        }
        hive, _ = assemble(raw)
        self.assertEqual(hive, {"ns": {"a": {"n": list(range(30))}, "b": "hi"}})

    def test_strips_optional_header(self):
        raw = {
            "ns/pointers": '["ns/a"]',
            "ns/a": compress_to_payload([1, 2], header="\x02v1:"),
        }
        hive, _ = assemble(raw)
        self.assertEqual(hive["ns"]["a"], [1, 2])

    def test_header_without_colon_is_left_alone(self):
        self.assertEqual(HiveAssembler({})._strip_header("\x02nocolon"), "\x02nocolon")

    def test_non_json_text_is_returned_raw(self):
        raw = {"ns/pointers": '["ns/a"]', "ns/a": "[104, 105]"}
        hive, _ = assemble(raw)
        self.assertEqual(hive["ns"]["a"], "hi")

    def test_blank_decompressed_text_becomes_none(self):
        raw = {"ns/pointers": '["ns/a"]', "ns/a": "[32]"}
        hive, _ = assemble(raw)
        self.assertIsNone(hive["ns"]["a"])

    def test_corrupt_payload_is_reported_not_raised(self):
        raw = {"ns/pointers": '["ns/a"]', "ns/a": "[65, 9999]"}
        hive, out = assemble(raw)
        entry = hive["ns"]["a"]
        self.assertEqual(entry["__ERROR__"], "Decompression Failed")
        self.assertIn("Invalid dictionary index", entry["__REASON__"])
        self.assertEqual(entry["__RAW_PAYLOAD__"], "[65, 9999]")
        self.assertEqual(entry["__HEX__"], b"[65, 9999]".hex())
        self.assertIn("ns/a", out)

    def test_non_integer_first_code_is_reported_not_raised(self):
        raw = {"ns/pointers": '["ns/a"]', "ns/a": '["x"]'}
        hive, _ = assemble(raw)
        self.assertEqual(hive["ns"]["a"]["__ERROR__"], "Decompression Failed")

    def test_pointer_to_missing_or_empty_entry_is_skipped(self):
        raw = {"ns/pointers": '["ns/gone", "ns/empty"]', "ns/empty": ""}
        hive, _ = assemble(raw)
        self.assertEqual(hive["ns"], {})

    def test_blank_pointer_entry_warns_and_yields_empty_namespace(self):
        hive, out = assemble({"ns/pointers": ""})
        self.assertEqual(hive["ns"], {})
        self.assertIn("No pointer data", out)

    def test_unparseable_or_non_list_pointers_warn(self):
        for bad in ("{oops", '{"a": 1}', "null"):
            with self.subTest(bad=bad):
                hive, out = assemble({"ns/pointers": bad})
                self.assertEqual(hive, {"ns": {}})
                self.assertIn("Failed to parse pointers", out)

    def test_namespaces_are_sorted(self):
        raw = {"zed/pointers": "[]", "alpha/pointers": "[]"}
        hive, _ = assemble(raw)
        self.assertEqual(list(hive), ["alpha", "zed"])

    def test_pointer_key_is_consumed_even_when_chunked_copy_wins(self):
        raw = {"ns/pointers": "[]", "ns/pointers/0": "[]"}
        hive, _ = assemble(raw)
        self.assertEqual(hive, {"ns": {}})


class OrphanTests(unittest.TestCase):
    def test_unclaimed_properties_are_reported_with_json_parsed(self):
        hive, _ = assemble(
            {"someFlag": "true", "label": "plain text", "obj": '{"a": 1}'}
        )
        self.assertEqual(
            hive["__GLOBAL_PROPERTIES__"],
            {"someFlag": True, "label": "plain text", "obj": {"a": 1}},
        )

    def test_claimed_properties_are_not_orphans(self):
        raw = {"ns/pointers": '["ns/a"]', "ns/a": compress_to_payload(1)}
        hive, _ = assemble(raw)
        self.assertNotIn(HiveAssembler.GLOBAL_PROPERTIES_KEY, hive)

    def test_implausible_and_binary_values_are_excluded(self):
        hive, _ = assemble({"ab": "x", "123": "x", "good": "a\x00\x00b"})
        self.assertNotIn(HiveAssembler.GLOBAL_PROPERTIES_KEY, hive)

    def test_no_orphan_section_when_nothing_is_left(self):
        hive, _ = assemble({})
        self.assertEqual(hive, {})


if __name__ == "__main__":
    unittest.main()
