"""Reject old interpreters before pip starts resolving app dependencies."""
import runpy
from pathlib import Path

version_check = runpy.run_path(str(Path(__file__).with_name("check_python.py")))
version_check["require_supported_python"]()

from setuptools import setup

setup()
