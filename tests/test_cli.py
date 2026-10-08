import json
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from extract_paradox_hive import cli
from tests.helpers import FakeLevelDB, compress_to_payload, fake_leveldb_factory

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA = {
    b"ns/pointers": b'["ns/cfg"]',
    b"ns/cfg": compress_to_payload({"a": 1}).encode(),
}


def fake_module(data=DATA, db_class=None):
    return types.SimpleNamespace(LevelDB=db_class or fake_leveldb_factory(data))


class RunTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "world" / "db").mkdir(parents=True)
        self.out = self.root / "out.json"
        FakeLevelDB.instances.clear()

    def run_cli(self, argv, module=None):
        with mock.patch.dict(sys.modules, {"leveldb": module or fake_module()}):
            with redirect_stdout(StringIO()) as buf:
                code = cli.run(argv)
        return code, buf.getvalue()

    def test_extracts_directory_to_json(self):
        code, out = self.run_cli([str(self.root / "world"), str(self.out)])
        self.assertEqual(code, 0)
        self.assertIn("[Success]", out)
        self.assertEqual(json.loads(self.out.read_text()), {"ns": {"cfg": {"a": 1}}})

    def test_extracts_mcworld_archive(self):
        archive = self.root / "w.mcworld"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("db/CURRENT", "x")
        code, _ = self.run_cli([str(archive), str(self.out)])
        self.assertEqual(code, 0)
        self.assertTrue(self.out.exists())

    def test_database_is_closed_after_success(self):
        self.run_cli([str(self.root / "world"), str(self.out)])
        self.assertTrue(FakeLevelDB.instances)
        self.assertTrue(all(db.closed for db in FakeLevelDB.instances))

    def test_missing_arguments_print_usage_and_fail(self):
        for argv in ([], ["only-one"]):
            with self.subTest(argv=argv):
                code, out = self.run_cli(argv)
                self.assertEqual(code, 1)
                self.assertIn("Usage", out)

    def test_usage_does_not_require_leveldb(self):
        with mock.patch.dict(sys.modules, {"leveldb": None}):
            with redirect_stdout(StringIO()) as buf:
                code = cli.run([])
        self.assertEqual(code, 1)
        self.assertIn("Usage", buf.getvalue())

    def test_missing_leveldb_is_fatal(self):
        with mock.patch.dict(sys.modules, {"leveldb": None}):
            with redirect_stdout(StringIO()) as buf:
                code = cli.run([str(self.root / "world"), str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("[Fatal]", buf.getvalue())
        self.assertFalse(self.out.exists())

    def test_world_input_errors_return_failure(self):
        code, out = self.run_cli([str(self.root / "missing"), str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("[Error]", out)
        self.assertFalse(self.out.exists())

    def test_database_is_closed_even_on_non_exception_interrupts(self):
        class Exploding(FakeLevelDB):
            def __init__(self, path):
                super().__init__(path, DATA)

            def items(self):
                raise KeyboardInterrupt  # not an Exception: must still close

        with self.assertRaises(KeyboardInterrupt):
            self.run_cli([str(self.root / "world"), str(self.out)], fake_module(db_class=Exploding))
        self.assertTrue(all(db.closed for db in FakeLevelDB.instances))

    def test_extraction_exception_is_reported_as_fatal(self):
        with mock.patch.object(cli, "extract_hive", side_effect=RuntimeError("kaboom")):
            code, out = self.run_cli([str(self.root / "world"), str(self.out)])
        self.assertEqual(code, 1)
        self.assertIn("kaboom", out)


class MainTests(unittest.TestCase):
    def test_main_exits_with_run_code_on_failure(self):
        with mock.patch.object(cli, "run", return_value=1):
            with self.assertRaises(SystemExit) as ctx:
                cli.main()
        self.assertEqual(ctx.exception.code, 1)

    def test_main_returns_normally_on_success(self):
        with mock.patch.object(cli, "run", return_value=0):
            cli.main()

    def test_public_reexports_resolve(self):
        for name in cli.__all__:
            self.assertTrue(hasattr(cli, name), name)


class ModuleEntryPointTests(unittest.TestCase):
    def test_python_dash_m_runs_the_cli(self):
        proc = subprocess.run(
            [sys.executable, "-m", "extract_paradox_hive"],
            cwd=PROJECT_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Usage", proc.stdout)
        self.assertNotIn("Traceback", proc.stderr)


if __name__ == "__main__":
    unittest.main()
