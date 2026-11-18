#!/usr/bin/env python3
"""Measure non-empty, non-comment Python source lines outside tests and tooling."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    "tests",
    "scripts",
    "build",
    "dist",
    "node_modules",
}


def measure_source(root: Path = DEFAULT_ROOT) -> dict[str, Any]:
    """Return reproducible source-size counts for runtime Python modules.

    A code line is a physical line that is neither blank nor a full-line
    comment. Inline comments and docstrings remain part of the measured source.
    Test, documentation, generated, and tooling directories are excluded.
    """
    root = Path(root).resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)

    files = sorted(
        path
        for path in root.rglob("*.py")
        if not any(part in EXCLUDED_DIRECTORIES for part in path.relative_to(root).parts[:-1])
        and not any(part.startswith(".") for part in path.relative_to(root).parts[:-1])
    )
    totals = {
        "file_count": 0,
        "physical_lines": 0,
        "code_lines": 0,
        "comment_only_lines": 0,
        "blank_lines": 0,
    }
    modules: list[dict[str, int | str]] = []

    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        code = sum(bool(line.strip()) and not line.lstrip().startswith("#") for line in lines)
        comments = sum(line.lstrip().startswith("#") for line in lines)
        blanks = sum(not line.strip() for line in lines)
        totals["file_count"] += 1
        totals["physical_lines"] += len(lines)
        totals["code_lines"] += code
        totals["comment_only_lines"] += comments
        totals["blank_lines"] += blanks
        modules.append({
            "path": path.relative_to(root).as_posix(),
            "code_lines": code,
            "physical_lines": len(lines),
        })

    modules.sort(key=lambda item: (int(item["code_lines"]), str(item["path"])), reverse=True)
    return {
        "scope": "runtime Python source; excludes tests, docs, scripts, generated and hidden directories",
        **totals,
        "largest_modules": modules[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    args = parser.parse_args()
    report = measure_source(args.root)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(
            "Katheryne production source: "
            f"{report['code_lines']:,} code lines across {report['file_count']} Python files"
        )
        print(
            f"Physical: {report['physical_lines']:,} | "
            f"comments: {report['comment_only_lines']:,} | "
            f"blank: {report['blank_lines']:,}"
        )
        print("Largest modules:")
        for module in report["largest_modules"]:
            print(f"  {module['code_lines']:>7,}  {module['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
