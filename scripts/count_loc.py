#!/usr/bin/env python3
"""
Count lines of code (LOC) in finance_* code areas only.

Includes only: finance_config, finance_modules, finance_kernel, finance_engines, finance_services.
Excludes: blank lines, comment-only lines. Excludes docs/, plans/, tests/, scripts/, etc.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Only these top-level directories are counted
INCLUDE_TOP_DIRS = {"finance_config", "finance_modules", "finance_kernel", "finance_engines", "finance_services"}

# Directories to skip within those trees
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".tox",
    "node_modules",
    "htmlcov",
    ".hypothesis",
    "venv",
    ".venv",
}
SKIP_GLOBS = ("*.md", "*.txt", "*.rst", "*.log")

# File extensions to count
CODE_EXTENSIONS = {".py", ".sql", ".yaml", ".yml"}


def is_comment_py(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    return False


def is_comment_sql(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("--"):
        return True
    return False


def is_comment_yaml(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if stripped.startswith("#"):
        return True
    return False


def count_lines(path: Path) -> int:
    ext = path.suffix.lower()
    if ext == ".py":
        is_comment = is_comment_py
    elif ext == ".sql":
        is_comment = is_comment_sql
    elif ext in (".yaml", ".yml"):
        is_comment = is_comment_yaml
    else:
        return 0

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0

    count = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        if is_comment(line):
            continue
        count += 1
    return count


def main() -> None:
    totals: dict[str, int] = {}
    by_dir: dict[str, int] = {}

    for path in sorted(ROOT.rglob("*")):
        if path.is_dir():
            if path.name in SKIP_DIRS:
                continue
            continue

        # Only under finance_config, finance_modules, finance_kernel, finance_engines, finance_services
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            continue
        if not rel.parts or rel.parts[0] not in INCLUDE_TOP_DIRS:
            continue

        if path.suffix.lower() not in CODE_EXTENSIONS:
            continue
        if any(path.match(g) for g in SKIP_GLOBS):
            continue
        if any(s in path.parts for s in SKIP_DIRS):
            continue

        n = count_lines(path)
        if n == 0:
            continue

        ext = path.suffix.lower()
        totals[ext] = totals.get(ext, 0) + n

        top = path.relative_to(ROOT).parts[0] if path.relative_to(ROOT).parts else "."
        by_dir[top] = by_dir.get(top, 0) + n

    print("Lines of code: finance_config, finance_modules, finance_kernel, finance_engines, finance_services")
    print("(excluding blank and comment-only lines)\n")
    print("By extension:")
    for ext in sorted(totals.keys()):
        print(f"  {ext:8} {totals[ext]:>8}")
    print(f"  {'TOTAL':8} {sum(totals.values()):>8}")

    print("\nBy top-level directory:")
    for d in sorted(by_dir.keys()):
        print(f"  {d:30} {by_dir[d]:>8}")
    print(f"  {'TOTAL':30} {sum(by_dir.values()):>8}")


if __name__ == "__main__":
    main()
