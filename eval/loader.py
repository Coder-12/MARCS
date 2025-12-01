import json
import os
from .schema import GoldenCase

BASE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "golden")


def _get_latest_version_dir():
    """
    Returns the path of the newest version folder inside data/golden/.
    Example: data/golden/v3 if v3 exists.
    """
    if not os.path.exists(BASE_DIR):
        return BASE_DIR

    subdirs = [
        d for d in os.listdir(BASE_DIR)
        if os.path.isdir(os.path.join(BASE_DIR, d)) and d.startswith("v")
    ]

    if not subdirs:
        return BASE_DIR   # fallback to root if no version folders exist

    # Sort versions like v1 < v2 < v10
    subdirs.sort(key=lambda x: int(x.lstrip("v")))

    return os.path.join(BASE_DIR, subdirs[-1])


def load_golden_cases(path=None):
    """
    Load all GoldenCase JSON files from the latest version directory.
    """
    if path is None:
        path = _get_latest_version_dir()

    cases = []
    for f in os.listdir(path):
        if f.endswith(".json"):
            full = os.path.join(path, f)
            with open(full) as fp:
                data = json.load(fp)
                cases.append(GoldenCase(**data))
    return cases