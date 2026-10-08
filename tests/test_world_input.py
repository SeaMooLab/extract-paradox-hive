import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from extract_paradox_hive import world_input
from extract_paradox_hive.world_input import (
    WorldInputError,
    find_db_folder,
    locate_database,
)


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def make_world(self, base: Path, nested="world/db") -> Path:
        db = base / nested
        db.mkdir(parents=True)
        (db / "CURRENT").write_text("x")
        return db

    def make_zip(self, name: str, with_db=True) -> Path:
        path = self.root / name
        with zipfile.ZipFile(path, "w") as z:
            if with_db:
                z.writestr("world/db/CURRENT", "x")
            else:
                z.writestr("world/level.dat", "x")
        return path

    def locate(self, path):
        with redirect_stdout(StringIO()):
            with locate_database(path) as db:
                return db


class FindDbFolderTests(TempDirCase):
    def test_finds_nested_db(self):
        db = self.make_world(self.root)
        self.assertEqual(find_db_folder(str(self.root)), str(db))

    def test_finds_db_at_the_start_path_itself(self):
        db = self.make_world(self.root, "db")
        self.assertEqual(find_db_folder(str(db)), str(db))

    def test_returns_none_when_absent(self):
        (self.root / "other").mkdir()
        self.assertIsNone(find_db_folder(str(self.root)))

    def test_ignores_files_named_db(self):
        (self.root / "db").write_text("file, not a folder")
        self.assertIsNone(find_db_folder(str(self.root)))


class LocateDatabaseTests(TempDirCase):
    def test_directory_is_searched_in_place(self):
        db = self.make_world(self.root)
        self.assertEqual(self.locate(self.root), str(db))

    def test_zip_and_mcworld_archives_are_unpacked(self):
        for name in ("w.zip", "w.mcworld"):
            with self.subTest(name=name):
                path = self.make_zip(name)
                with redirect_stdout(StringIO()):
                    with locate_database(path) as db:
                        self.assertTrue(os.path.isfile(os.path.join(db, "CURRENT")))
                        workspace = db

                self.assertFalse(os.path.exists(workspace))

    def test_workspace_removed_when_body_raises(self):
        path = self.make_zip("w.zip")
        seen = []
        with redirect_stdout(StringIO()):
            with self.assertRaises(RuntimeError):
                with locate_database(path) as db:
                    seen.append(db)
                    raise RuntimeError("boom")
        self.assertFalse(os.path.exists(seen[0]))

    def test_missing_path(self):
        with self.assertRaisesRegex(WorldInputError, "does not exist"):
            self.locate(self.root / "nope")

    def test_unsupported_file_type(self):
        f = self.root / "world.txt"
        f.write_text("x")
        with self.assertRaisesRegex(WorldInputError, "Unsupported input type"):
            self.locate(f)

    def test_directory_without_db(self):
        with self.assertRaisesRegex(WorldInputError, "Could not locate"):
            self.locate(self.root)

    def test_archive_without_db_is_cleaned_up(self):
        path = self.make_zip("w.zip", with_db=False)
        with mock.patch.object(
            world_input.shutil, "rmtree", wraps=world_input.shutil.rmtree
        ) as rm:
            with self.assertRaisesRegex(WorldInputError, "Could not locate"):
                self.locate(path)
        rm.assert_called_once()

    def test_corrupt_archive_is_cleaned_up_and_reported(self):
        bad = self.root / "bad.zip"
        bad.write_text("not a zip")
        with mock.patch.object(
            world_input.shutil, "rmtree", wraps=world_input.shutil.rmtree
        ) as rm:
            with self.assertRaisesRegex(WorldInputError, "Failed to extract archive"):
                self.locate(bad)
        rm.assert_called_once()


if __name__ == "__main__":
    unittest.main()
