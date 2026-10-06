from contextlib import contextmanager
import os
from pathlib import Path
import stat

import pytest

from services.repository import RepositoryError, RepositoryView, analyze_repository


def snapshot(root):
    result = {}
    for item in sorted(root.iterdir()):
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            result[item.name] = ("link", os.readlink(item))
        elif stat.S_ISDIR(mode):
            result[item.name] = ("dir", stat.S_IMODE(mode), snapshot(item))
        elif stat.S_ISREG(mode):
            result[item.name] = ("file", stat.S_IMODE(mode), item.read_bytes())
        else:
            result[item.name] = ("special", stat.S_IFMT(mode))
    return result


@contextmanager
def unchanged(root):
    before = snapshot(root)
    try:
        yield RepositoryView(root)
    finally:
        assert snapshot(root) == before


def populate(root, files):
    root.mkdir(exist_ok=True)
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return root


def test_root_is_canonical_read_only_and_not_owned(tmp_path):
    source = populate(tmp_path / "source", {"a.py": "text"})
    alias = tmp_path / "alias"
    alias.symlink_to(source, target_is_directory=True)
    with unchanged(source):
        view = RepositoryView(alias)
        assert view.root == source.resolve()
        assert view.read_text("a.py").content == "text"
        with pytest.raises(AttributeError):
            view.root = tmp_path
        assert not hasattr(view, "dispose")
    for path in (tmp_path / "missing", source / "a.py"):
        with pytest.raises(RepositoryError, match="invalid_root"):
            RepositoryView(path)


@pytest.mark.parametrize("path", ["/etc/passwd", "../outside", "a/../../outside", "a//b", "./a.py", "C:/x", "a\\b", "", "a\x00b"])
def test_direct_read_invalid_paths_rejected_without_opening(tmp_path, monkeypatch, path):
    source = populate(tmp_path / "source", {"a.py": "keep"})
    with unchanged(source) as view:
        with monkeypatch.context() as guard:
            guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("invalid path must not be opened"))
            with pytest.raises(RepositoryError, match="invalid_path"):
                view.read_text(path)


@pytest.mark.parametrize("path", [".env", "config/SECRETS.prod", "cert/file.KEY", "cert/file.pem", ".git/config", "nested/.GIT/index"])
def test_sensitive_paths_are_not_opened_or_emitted(tmp_path, monkeypatch, path):
    source = populate(tmp_path / "source", {path: "private fixture", "a.py": "keep"})
    with unchanged(source) as view:
        with monkeypatch.context() as guard:
            guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("inventory/policy must not read contents"))
            inventory = view.inventory()
            with pytest.raises(RepositoryError, match="excluded_policy"):
                view.read_text(path)
        assert path not in [entry.path for entry in inventory.entries]
        assert inventory.excluded_policy_count >= 1
        assert "private fixture" not in inventory.model_dump_json()


@pytest.mark.parametrize("path", [".env.example", "my.pem.backup", "monkey.py", "git_notes.txt", ".gitignore", ".github/workflow.yml", "journals/journal.py"])
def test_ordinary_paths_and_journals_source_are_preserved(tmp_path, path):
    source = populate(tmp_path / "source", {path: "exact source"})
    with unchanged(source) as view:
        assert path in [entry.path for entry in view.inventory().entries]
        assert view.read_text(path).content == "exact source"


def test_internal_links_are_structural_only_and_external_targets_unchanged(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"a.py": "source"})
    external = populate(tmp_path / "external", {"outside.py": "external sentinel"})
    (source / "linked.py").symlink_to(external / "outside.py")
    (source / "linked-dir").symlink_to(external, target_is_directory=True)
    (source / "inside.py").symlink_to(source / "a.py")
    (source / "broken.py").symlink_to(tmp_path / "missing")
    outside_before = snapshot(external)
    with unchanged(source) as view:
        inventory = view.inventory()
        assert sum(entry.kind == "symlink" for entry in inventory.entries) == 4
        assert "external sentinel" not in inventory.model_dump_json()
        assert "outside.py" not in [entry.path for entry in inventory.entries]
        with monkeypatch.context() as guard:
            guard.setattr(Path, "open", lambda *a, **kw: pytest.fail("link must not be opened"))
            for path in ("linked.py", "linked-dir/outside.py", "inside.py", "broken.py"):
                with pytest.raises(RepositoryError, match="symlink"):
                    view.read_text(path)
    assert snapshot(external) == outside_before


def test_special_entries_are_skipped_and_direct_read_fails(tmp_path):
    if not hasattr(os, "mkfifo"):
        pytest.skip("OS has no named-pipe fixture support")
    source = populate(tmp_path / "source", {"a.py": "keep"})
    os.mkfifo(source / "pipe")
    with unchanged(source) as view:
        inventory = view.inventory()
        assert inventory.unsupported_entry_count == 1
        assert "unsupported_entry" in inventory.warnings
        assert [entry.path for entry in inventory.entries] == ["a.py"]
        with pytest.raises(RepositoryError, match="not_regular"):
            view.read_text("pipe")


def test_inventory_deterministic_prefix_and_limits_across_creation_orders(tmp_path):
    files = {"z.py": "z", "src/z.py": "z", "src/a.py": "a", "a.py": "a", "tests/test_a.py": "t"}
    first = populate(tmp_path / "first", files)
    second = populate(tmp_path / "second", dict(reversed(list(files.items()))))
    with unchanged(first) as a, unchanged(second) as b:
        assert a.inventory() == b.inventory()
        short_a, short_b = a.inventory(max_entries=3), b.inventory(max_entries=3)
        assert short_a == short_b and short_a.truncated
        assert short_a.entries_returned == 3
        assert [entry.path for entry in short_a.entries] == ["a.py", "src", "src/a.py"]
        assert "entry_limit" in short_a.warnings
        limited = a.inventory(max_nodes=2)
        assert limited.truncated and "node_limit" in limited.warnings
        assert a.inventory(max_depth=1).truncated


@pytest.mark.parametrize("directory", ["__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache", ".tox", ".nox", ".venv", "venv", "ENV", "node_modules", "build", "dist"])
def test_generated_directories_excluded_without_blanket_operational_rules(tmp_path, directory):
    source = populate(tmp_path / "source", {directory + "/generated.py": "bad", "logs/source.py": "keep", "artifacts/source.py": "keep", "backups/source.py": "keep"})
    with unchanged(source) as view:
        inventory = view.inventory()
        assert inventory.excluded_policy_count == 1
        assert all(not entry.path.startswith(directory + "/") for entry in inventory.entries)
        assert all(path in [entry.path for entry in inventory.entries] for path in ("logs/source.py", "artifacts/source.py", "backups/source.py"))


def test_inventory_unreadable_entry_warning(tmp_path, monkeypatch):
    source = populate(tmp_path / "source", {"closed/a.py": "keep", "a.py": "source"})
    original = os.scandir
    with unchanged(source) as view:
        with monkeypatch.context() as failure:
            def unreadable(path):
                if Path(path).name == "closed":
                    raise PermissionError("fixture failure")
                return original(path)
            failure.setattr(os, "scandir", unreadable)
            inventory = view.inventory()
        assert inventory.unreadable_entry_count == 1 and "unreadable_entry" in inventory.warnings


def test_bounded_reads_preserve_unicode_crlf_and_line_attribution(tmp_path):
    source = populate(tmp_path / "source", {"a.py": "one\r\nαβ\nthree\n", "empty.py": ""})
    with unchanged(source) as view:
        text = view.read_text("a.py", start_line=2, max_lines=1)
        assert text.content == "αβ\n" and (text.start_line, text.end_line) == (2, 2) and text.truncated
        partial = view.read_text("a.py", start_line=2, max_bytes=3)
        assert partial.content == "α" and (partial.start_line, partial.end_line) == (2, 2) and partial.truncated
        empty = view.read_text("empty.py")
        assert empty.content == "" and empty.start_line is empty.end_line is None and not empty.truncated
        assert view.read_text("a.py").content == "one\r\nαβ\nthree\n"
        assert "TRUNCATED" not in partial.content


def test_long_line_and_scan_limit_are_bounded(tmp_path):
    source = populate(tmp_path / "source", {"huge.py": "x" * 100000 + "\nlast\n"})
    with unchanged(source) as view:
        text = view.read_text("huge.py", max_bytes=12, max_scan_bytes=32)
        assert text.content == "x" * 12 and text.end_line == 1 and text.truncated
        with pytest.raises(RepositoryError, match="scan_limit"):
            view.read_text("huge.py", start_line=2, max_scan_bytes=32)


def test_read_byte_budget_smaller_than_first_codepoint_is_explicit(tmp_path):
    source = populate(tmp_path / "source", {"a.py": "αβ"})
    with unchanged(source) as view:
        result = view.read_text("a.py", max_bytes=1, max_scan_bytes=1)
        assert result.content == "" and result.truncated
        assert result.start_line is result.end_line is None


@pytest.mark.parametrize("path,code", [("missing.py", "missing"), ("directory", "not_regular"), ("bad.py", "invalid_utf8")])
def test_read_failure_is_explicit(tmp_path, path, code):
    source = populate(tmp_path / "source", {"bad.py": b"bad\xff"})
    (source / "directory").mkdir()
    with unchanged(source) as view:
        with pytest.raises(RepositoryError, match=code):
            view.read_text(path)


@pytest.mark.parametrize("framework", ["fastapi", "django", "flask"])
def test_python_analysis_dependency_facts_are_attributed(tmp_path, framework):
    source = populate(tmp_path / "source", {"requirements.txt": f"{framework.upper()}>=1\npytest[extra]>=8\n", "src/a.py": "pass", "tests/test_a.py": "pass", "frontend.js": "ancillary", "settings.json": "{}"})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert result.profile.language == "python"
        assert result.profile.framework == framework and result.profile.test_framework == "pytest"
        assert result.profile.important_dirs == ["src", "tests"]
        assert result.test_files == ["tests/test_a.py"] and result.config_files == ["requirements.txt"]
        for field, value in (("language", "python"), ("framework", framework), ("test_framework", "pytest"), ("important_dirs", "src"), ("important_dirs", "tests")):
            assert any(item.field == field and item.value == value and item.path for item in result.evidence)
        assert result.files_considered == 5


def test_unsupported_and_tests_directory_do_not_fabricate_profile(tmp_path):
    source = populate(tmp_path / "source", {"web/app.js": "javascript", "README.md": "No Python"})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert result.profile.language is None and "python_not_detected" in result.warnings
    python_source = populate(tmp_path / "python", {"tests/test_example.py": "pass", "fastapi.py": "not dependency"})
    with unchanged(python_source) as view:
        result = analyze_repository(view)
        assert result.profile.language == "python"
        assert result.profile.framework is result.profile.test_framework is None


def test_ambiguous_frameworks_do_not_choose_arbitrarily(tmp_path):
    source = populate(tmp_path / "source", {"requirements.txt": "Flask\nDjango\nFastAPI\n"})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert result.profile.framework is None and "ambiguous_framework" in result.warnings
        assert {item.value for item in result.evidence if item.field == "framework"} == {"fastapi", "django", "flask"}


@pytest.mark.parametrize("config,content", [
    ("pyproject.toml", '[project]\ndependencies=["FastAPI>=1"]\n[project.optional-dependencies]\ntest=["pytest"]\n[tool.pytest.ini_options]\naddopts="-q"\n'),
    ("pyproject.toml", '[tool.poetry.dependencies]\npython="^3.12"\nfastapi="*"\n[tool.poetry.group.test.dependencies]\npytest="*"\n'),
    ("setup.cfg", '[options]\ninstall_requires=\n FastAPI>=1\n[options.extras_require]\ntest=pytest\n[tool:pytest]\naddopts=-q\n'),
])
def test_data_only_project_config_parsing(tmp_path, config, content):
    source = populate(tmp_path / "source", {config: content})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert result.profile.framework == "fastapi" and result.profile.test_framework == "pytest"


@pytest.mark.parametrize("name,content", [("pyproject.toml", '[project\ndependencies=["FastAPI", "pytest"]'), ("setup.cfg", 'no section\ninstall_requires=fastapi\npytest')])
def test_malformed_config_warns_without_framework_test_fabrication(tmp_path, name, content):
    source = populate(tmp_path / "source", {name: content, "a.py": "pass"})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert "malformed_config" in result.warnings
        assert result.profile.framework is result.profile.test_framework is None
        assert result.profile.language == "python"


def test_invalid_dependency_values_do_not_fabricate_framework_or_pytest(tmp_path):
    source = populate(tmp_path / "source", {"pyproject.toml":
        '[project]\ndependencies=[false, 7]\n[tool.poetry.dependencies]\nfastapi=false\npytest=7\n'})
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert result.profile.language == "python"
        assert result.profile.framework is result.profile.test_framework is None
        assert "invalid_dependency_data" in result.warnings
        assert not any(item.field in ("framework", "test_framework") for item in result.evidence)


def test_requirement_directives_are_not_followed_and_project_code_never_executes(tmp_path):
    trap = f"from pathlib import Path\nPath({str(tmp_path / 'EXECUTED')!r}).write_text('bad')\nraise AssertionError('do not execute')\n"
    source = populate(tmp_path / "source", {"setup.py": trap, "noxfile.py": trap,
        "requirements-dev.txt": "# fastapi\n-r ../outside.txt\n--index-url https://invalid.example\nhttps://invalid.example/pytest\nPYTEST>=8; python_version > '3'\n"})
    (tmp_path / "outside.txt").write_text("fastapi\n")
    with unchanged(source) as view:
        result = analyze_repository(view)
        assert not (tmp_path / "EXECUTED").exists()
        assert result.profile.framework is None and result.profile.test_framework == "pytest"


def test_readme_precedence_discoveries_and_important_directory_budget(tmp_path):
    source = populate(tmp_path / "source", {"README": "other", "Readme.md": "line\n" * 101,
        "docs/info.md": "not ingested", "app/a.py": "pass", "src/a.py": "pass", "lib/a.py": "pass", "other/a_test.py": "pass", "pytest.ini": "[pytest]\n", "tox.ini": "[tox]\n", "noxfile.py": "raise AssertionError"})
    with unchanged(source) as view:
        result = analyze_repository(view, max_important_dirs=2)
        assert result.readme_excerpt.path == "Readme.md" and result.readme_excerpt.end_line == 100
        assert result.readme_excerpt.truncated and result.truncated
        assert result.profile.important_dirs == ["src", "app"]
        assert result.test_files == ["other/a_test.py"]
        assert result.profile.test_framework == "pytest"
        assert result.config_files == ["noxfile.py", "pytest.ini", "tox.ini"]
        assert "multiple_readmes" in result.warnings and "important_dirs_limit" in result.warnings


def test_config_size_read_failures_and_inventory_bounds_are_explicit(tmp_path):
    source = populate(tmp_path / "source", {"pyproject.toml": "x" * 30, "requirements.txt": "fastapi\n", "README.md": b"bad\xff", "a.py": "pass"})
    with unchanged(source) as view:
        result = analyze_repository(view, max_config_bytes=16)
        assert "config_too_large" in result.warnings and "invalid_utf8" in result.warnings
        assert result.profile.framework == "fastapi" and result.readme_excerpt is None
        partial = analyze_repository(view, max_entries=1)
        assert partial.truncated and "entry_limit" in partial.warnings


@pytest.mark.parametrize("kwargs", [{"max_entries": 0}, {"max_nodes": -1}, {"max_depth": False}])
def test_invalid_inventory_limits_are_rejected(tmp_path, kwargs):
    with pytest.raises(RepositoryError, match="invalid_limit"):
        RepositoryView(tmp_path).inventory(**kwargs)
