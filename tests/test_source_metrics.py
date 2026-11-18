"""Tests for production Python source-size accounting."""
from pathlib import Path

from scripts.measure_source import measure_source


def test_measure_source_excludes_tests_docs_scripts_and_comment_padding(tmp_path: Path):
    (tmp_path / "main.py").write_text(
        "import os\n\n# comment\nvalue = 1  # inline comment\n",
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "example.py").write_text("documentation = True\n", encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "tool.py").write_text("print('tool')\n", encoding="utf-8")

    report = measure_source(tmp_path)

    assert report["file_count"] == 1
    assert report["physical_lines"] == 4
    assert report["code_lines"] == 2
    assert report["comment_only_lines"] == 1
    assert report["blank_lines"] == 1
    assert report["largest_modules"][0]["path"] == "main.py"


def test_measure_source_rejects_missing_root(tmp_path: Path):
    missing = tmp_path / "missing"
    try:
        measure_source(missing)
    except NotADirectoryError:
        pass
    else:
        raise AssertionError("A non-directory root must be rejected.")
