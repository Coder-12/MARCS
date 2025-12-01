# tests/test_golden_factory.py
import os
import shutil
from tools.golden_factory import GoldenFactory

def test_generate_small(tmp_path):
    out = str(tmp_path)
    gf = GoldenFactory(version="vtest", out_dir=out, seed=123)
    counts = {"style": 1, "security": 1, "mix": 1, "empty": 1, "big": 1}
    paths = gf.generate(counts)
    assert len(paths) == sum(counts.values())
    # file exists
    for p in paths:
        assert os.path.exists(p)

    # README written
    assert os.path.exists(os.path.join(out, "vtest", "README.json"))
