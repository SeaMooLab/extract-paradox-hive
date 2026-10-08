import sys
import types
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from extract_paradox_hive.hive_extractor import (
    BedrockHiveExtractor,
    LevelDBUnavailableError,
    load_leveldb,
)
from tests.helpers import (
    FakeLevelDB,
    compress_to_payload,
    fake_leveldb_factory,
    nbt_root,
    nbt_string_tag,
)


def make_extractor(data):
    extractor = BedrockHiveExtractor("/fake/db")
    extractor.db = FakeLevelDB("/fake/db", data)
    return extractor


def quiet(fn, *args):
    with redirect_stdout(StringIO()) as out:
        result = fn(*args)
    return result, out.getvalue()


class LoadLevelDBTests(unittest.TestCase):
    def test_raises_dedicated_error_when_missing(self):
        with mock.patch.dict(sys.modules, {"leveldb": None}):
            with self.assertRaises(LevelDBUnavailableError) as ctx:
                load_leveldb()
        self.assertIn("pip install amulet-leveldb", str(ctx.exception))

    def test_error_is_an_import_error(self):
        self.assertTrue(issubclass(LevelDBUnavailableError, ImportError))

    def test_returns_class_when_present(self):
        module = types.SimpleNamespace(LevelDB=FakeLevelDB)
        with mock.patch.dict(sys.modules, {"leveldb": module}):
            self.assertIs(load_leveldb(), FakeLevelDB)


class LifecycleTests(unittest.TestCase):
    def test_context_manager_opens_and_closes(self):
        module = types.SimpleNamespace(LevelDB=fake_leveldb_factory({}))
        with mock.patch.dict(sys.modules, {"leveldb": module}):
            with BedrockHiveExtractor("/x") as extractor:
                handle = extractor.db
                self.assertIsNotNone(handle)
        self.assertTrue(handle.closed)
        self.assertIsNone(extractor.db)

    def test_closes_even_when_body_raises(self):
        module = types.SimpleNamespace(LevelDB=fake_leveldb_factory({}))
        with mock.patch.dict(sys.modules, {"leveldb": module}):
            with self.assertRaises(RuntimeError):
                with BedrockHiveExtractor("/x") as extractor:
                    handle = extractor.db
                    raise RuntimeError("boom")
        self.assertTrue(handle.closed)

    def test_close_is_idempotent_and_safe_when_never_opened(self):
        extractor = BedrockHiveExtractor("/x")
        extractor.close()
        extractor.close()


class ScanTests(unittest.TestCase):
    def test_merges_nbt_blob_strings_and_swept_entries(self):
        blob = nbt_root(nbt_string_tag("fromNbt", "1"))
        data = {b"DynamicProperties": blob, b"swept_key": b"value"}
        props, out = quiet(make_extractor(data).scan_for_dynamic_properties)
        self.assertEqual(props, {"fromNbt": "1", "swept_key": "value"})
        self.assertIn("DynamicProperties", out)

    def test_missing_nbt_blob_is_fine(self):
        props, _ = quiet(make_extractor({b"abc": b"x"}).scan_for_dynamic_properties)
        self.assertEqual(props, {"abc": "x"})

    def test_empty_nbt_blob_is_ignored(self):
        props, _ = quiet(
            make_extractor({b"DynamicProperties": b""}).scan_for_dynamic_properties
        )
        self.assertEqual(props, {})

    def test_sweep_filters_binary_keys_and_values(self):
        data = {
            b"\x00\x01\x02": b"x",  # no printable chars
            b"ab": b"x",  # too short
            b"binary_val": b"a\x00\x00b",  # binary marker
            b"bad_utf8": b"\xff\xfe",  # not text
            b"empty_val": b"",  # empty
            b"keep_me": b"ok",
        }
        props, _ = quiet(make_extractor(data).scan_for_dynamic_properties)
        self.assertEqual(props, {"keep_me": "ok"})

    def test_keys_are_reduced_to_printable_ascii(self):
        props, _ = quiet(
            make_extractor({b"\x00ns/key\xff": b"v"}).scan_for_dynamic_properties
        )
        self.assertEqual(props, {"ns/key": "v"})

    def test_dynamic_properties_key_is_not_swept_as_text(self):
        data = {b"DynamicProperties": b"plain text, not nbt"}
        props, _ = quiet(make_extractor(data).scan_for_dynamic_properties)
        self.assertEqual(props, {})

    def test_sweep_failure_keeps_partial_results_and_warns(self):
        class Exploding(FakeLevelDB):
            def items(self):
                yield b"first", b"1"
                raise RuntimeError("disk gone")

        extractor = BedrockHiveExtractor("/x")
        extractor.db = Exploding("/x")
        props, out = quiet(extractor.scan_for_dynamic_properties)
        self.assertEqual(props, {"first": "1"})
        self.assertIn("disk gone", out)


class IterEntriesTests(unittest.TestCase):
    def entries(self, db):
        extractor = BedrockHiveExtractor("/x")
        extractor.db = db
        return list(extractor._iter_entries())

    def test_prefers_items(self):
        self.assertEqual(self.entries(FakeLevelDB("/x", {b"k": b"v"})), [(b"k", b"v")])

    def test_falls_back_to_iterate(self):
        class Db:
            def iterate(self):
                return iter([(b"k", b"v")])

        self.assertEqual(self.entries(Db()), [(b"k", b"v")])

    def test_falls_back_to_key_iteration_with_get(self):
        class Db:
            def __iter__(self):
                return iter([b"k"])

            def get(self, key):
                return b"v"

        self.assertEqual(self.entries(Db()), [(b"k", b"v")])


class ExtractTests(unittest.TestCase):
    def test_extract_assembles_hive_from_database(self):
        data = {
            b"ns/pointers": b'["ns/cfg"]',
            b"ns/cfg": compress_to_payload({"a": 1}).encode(),
            b"loose": b"text",
        }
        hive, _ = quiet(make_extractor(data).extract)
        self.assertEqual(hive["ns"], {"cfg": {"a": 1}})
        self.assertEqual(hive["__GLOBAL_PROPERTIES__"], {"loose": "text"})

    def test_stitch_chunks_delegates_to_assembler(self):
        extractor = BedrockHiveExtractor("/x")
        self.assertEqual(extractor.stitch_chunks({"k/0": "a", "k/1": "b"}, "k"), "ab")


if __name__ == "__main__":
    unittest.main()
