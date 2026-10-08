import importlib
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


class PackagingTests(unittest.TestCase):
    def test_console_scripts_resolve_to_callables(self):
        scripts = PYPROJECT["project"]["scripts"]
        for name, target in scripts.items():
            module_name, _, attr = target.partition(":")
            if not module_name.startswith("extract_paradox_hive"):
                continue  # e.g. pytest:main, owned by a third party
            with self.subTest(script=name):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(getattr(module, attr)), target)

    def test_declared_readme_exists(self):
        self.assertTrue((ROOT / PYPROJECT["project"]["readme"]).is_file())

    def test_every_package_module_is_importable(self):
        package_dir = ROOT / "extract_paradox_hive"
        for path in sorted(package_dir.glob("*.py")):
            if path.name == "__main__.py":
                continue  # importing it is safe, but it is exercised via -m
            module = "extract_paradox_hive" + (
                "" if path.stem == "__init__" else f".{path.stem}"
            )
            with self.subTest(module=module):
                importlib.import_module(module)


if __name__ == "__main__":
    unittest.main()
