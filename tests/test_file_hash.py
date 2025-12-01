# tests/test_file_hash.py
import pytest
from utils.file_hash import file_dict_hashes, sha256_of_text

def test_file_hashes_reproducible():
    files = {
        "a.txt": "hello",
        "b.txt": "world",
    }
    per1, repo1 = file_dict_hashes(files)
    per2, repo2 = file_dict_hashes(files)
    assert per1 == per2
    assert repo1 == repo2
    assert all(len(h) == 64 for h in per1.values())
    # changing content changes hash
    files["b.txt"] = "world!"
    per3, repo3 = file_dict_hashes(files)
    assert per1 != per3
    assert repo1 != repo3

def test_sha256_known_value():
    assert sha256_of_text("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"