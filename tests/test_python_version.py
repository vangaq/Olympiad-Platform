"""Interpreter compatibility must fail before Flask or database initialization."""
import runpy
import unittest
from pathlib import Path
from unittest.mock import patch

from check_python import require_supported_python


class PythonVersionTest(unittest.TestCase):
    def test_rejects_versions_below_311_with_clear_message(self):
        for version in ((3, 8, 10), (3, 9, 0), (3, 10, 15)):
            with self.subTest(version=version):
                with self.assertRaises(SystemExit) as caught:
                    require_supported_python(version)
                message = str(caught.exception)
                self.assertIn("Python 3.11", message)
                self.assertIn(".".join(map(str, version)), message)
                self.assertIn("PyCharm", message)

    def test_accepts_311_and_newer_without_upper_limit(self):
        for version in ((3, 11, 0), (3, 13, 1), (3, 99, 0), (4, 0, 0)):
            with self.subTest(version=version):
                require_supported_python(version)

    def test_main_rejects_old_python_before_loading_application(self):
        with patch("sys.version_info", (3, 8, 10)):
            with self.assertRaisesRegex(SystemExit, "Python 3.11"):
                runpy.run_path(str(Path(__file__).resolve().parents[1] / "main.py"))

    def test_installer_rejects_old_python_before_importing_setuptools(self):
        with patch("sys.version_info", (3, 8, 10)):
            with self.assertRaisesRegex(SystemExit, "Python 3.11"):
                runpy.run_path(str(Path(__file__).resolve().parents[1] / "setup.py"))
