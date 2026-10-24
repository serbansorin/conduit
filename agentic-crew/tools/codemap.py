#!/usr/bin/env python3
"""Generate a structural map of a codebase for an agent's knowledge base.

The map is a single Markdown file that an agent can read as a skill. It
lists every directory, every source file, and every top-level definition
(class, function, method) with its signature. It is the answer to the
question "what already exists in this project?" — the question agents
get wrong most often.

Usage:
    python3 tools/codemap.py ~/imc-dev/www/getubetter/app.gap \\
        --out agents/maps/getubetter-angular.md \\
        --max-files 2000
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# File extensions we know how to outline.
EXTENSIONS = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
    ".php": "php",
    ".go": "go",
    ".java": "java",
    ".kt": "kotlin",
    ".rs": "rust",
    ".cs": "csharp",
    ".vue": "vue",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
}

# Directories that are never useful to map.
SKIP_DIRS = {
    "node_modules",
    ".git",
    ".svn",
    "vendor",
    "dist",
    "build",
    ".cache",
    "__pycache__",
    ".next",
    ".nuxt",
    "coverage",
    ".angular",
    ".ionic",
    "www",
    "platforms",
}

# Top-level definition patterns per language.
PATTERNS: Dict[str, List[Tuple[str, re.Pattern[str]]]] = {
    "typescript": [
        ("class", re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)", re.M)),
        ("interface", re.compile(r"^\s*(?:export\s+)?interface\s+(\w+)", re.M)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M)),
        ("const", re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M)),
    ],
    "javascript": [
        ("class", re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M)),
        ("function", re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M)),
        ("const", re.compile(r"^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M)),
    ],
    "python": [
        ("class", re.compile(r"^class\s+(\w+)", re.M)),
        ("function", re.compile(r"^def\s+(\w+)", re.M)),
        ("async function", re.compile(r"^async\s+def\s+(\w+)", re.M)),
    ],
    "php": [
        ("class", re.compile(r"^\s*(?:abstract\s+|final\s+)?class\s+(\w+)", re.M)),
        ("interface", re.compile(r"^\s*interface\s+(\w+)", re.M)),
        ("trait", re.compile(r"^\s*trait\s+(\w+)", re.M)),
        ("function", re.compile(r"^\s*(?:public|protected|private|static)?\s*function\s+(\w+)", re.M)),
    ],
    "go": [
        ("struct", re.compile(r"^type\s+(\w+)\s+struct", re.M)),
        ("interface", re.compile(r"^type\s+(\w+)\s+interface", re.M)),
        ("function", re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)", re.M)),
    ],
}


def should_skip_dir(name: str) -> bool:
    """Report whether a directory should be skipped.

    Args:
        name: The directory name.

    Returns:
        True when the directory is not useful to map.
    """
    return name in SKIP_DIRS or name.startswith(".")


def outline_file(path: Path, language: str) -> List[str]:
    """Return the top-level definitions of a file.

    Args:
        path: The file to outline.
        language: The language family.

    Returns:
        A list of "kind name" strings.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if len(text) > 500_000:
        return ["(file too large to outline)"]
    found: List[str] = []
    seen: set = set()
    for kind, pattern in PATTERNS.get(language, []):
        for match in pattern.finditer(text):
            name = match.group(1)
            key = (kind, name)
            if key in seen:
                continue
            seen.add(key)
            found.append(f"{kind} {name}")
    return found


def walk(root: Path, max_files: int) -> Tuple[List[Dict[str, object]], int, int]:
    """Walk the tree and collect file metadata.

    Args:
        root: The project root.
        max_files: Maximum files to include.

    Returns:
        A tuple of (entries, file_count, dir_count).
    """
    entries: List[Dict[str, object]] = []
    file_count = 0
    dir_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not should_skip_dir(d))
        dir_count += len(dirnames)
        for filename in sorted(filenames):
            if file_count >= max_files:
                return entries, file_count, dir_count
            full = Path(dirpath) / filename
            suffix = full.suffix.lower()
            if suffix not in EXTENSIONS:
                continue
            rel = full.relative_to(root)
            language = EXTENSIONS[suffix]
            outline = outline_file(full, language)
            entries.append(
                {
                    "path": str(rel),
                    "language": language,
                    "outline": outline,
                }
            )
            file_count += 1
    return entries, file_count, dir_count


def render(root: Path, entries: List[Dict[str, object]], file_count: int, dir_count: int) -> str:
    """Render the map as Markdown.

    Args:
        root: The project root.
        entries: The file entries.
        file_count: The number of files.
        dir_count: The number of directories.

    Returns:
        The Markdown.
    """
    lines: List[str] = []
    lines.append(f"# Code map: {root.name}")
    lines.append("")
    lines.append(f"Generated from `{root}`. {file_count} source files across "
                f"{dir_count} directories.")
    lines.append("")
    lines.append("## Directory structure")
    lines.append("")
    lines.append("```")
    # compact tree
    last_parts: List[str] = []
    for entry in entries:
        parts = Path(entry["path"]).parts
        # print directory headers when the parent changes
        parent = str(Path(entry["path"]).parent)
        if parent != "." and parent not in last_parts:
            lines.append(f"{parent}/")
            last_parts.append(parent)
        outline = entry["outline"]
        if outline:
            summary = ", ".join(outline[:6])
            if len(outline) > 6:
                summary += f", … (+{len(outline) - 6})"
            lines.append(f"  {Path(entry['path']).name}  — {summary}")
        else:
            lines.append(f"  {Path(entry['path']).name}")
    lines.append("```")
    lines.append("")
    lines.append("## Definitions by file")
    lines.append("")
    for entry in entries:
        outline = entry["outline"]
        if not outline:
            continue
        lines.append(f"### `{entry['path']}`")
        lines.append("")
        for item in outline:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


def main(argv: List[str] | None = None) -> int:
    """Run the generator.

    Args:
        argv: Command line arguments.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="project root to map")
    parser.add_argument("--out", default=None, help="output file (default: stdout)")
    parser.add_argument("--max-files", type=int, default=5000, help="max files")
    args = parser.parse_args(argv)

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    entries, file_count, dir_count = walk(root, args.max_files)
    output = render(root, entries, file_count, dir_count)

    if args.out:
        out_path = Path(os.path.expanduser(args.out))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output, encoding="utf-8")
        print(f"wrote {file_count} files to {out_path}")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
