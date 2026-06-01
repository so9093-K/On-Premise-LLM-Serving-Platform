from __future__ import annotations

import json


import re


import os


import subprocess


from pathlib import Path


import yaml


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "VERSION").exists() and (parent / "configs").exists():
            return parent
    raise RuntimeError("repository root not found")


ROOT = _repo_root()


__all__ = [name for name in globals() if not name.startswith("__")]
