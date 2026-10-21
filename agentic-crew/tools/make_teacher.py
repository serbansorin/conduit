#!/usr/bin/env python3
"""Generate a teacher agent from a course entry.

The teacher agents are ordinary YAML files, but there are enough of them that
typing them by hand is tedious. This script writes one from a short spec.

Usage:
    python3 tools/make_teacher.py \\
        --code SPIC \\
        --title "Signal Processing" \\
        --material ~/university/evpc/spic \\
        --model deepseek-v4.1-flash

It prints the path it wrote.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Dict, List

TEMPLATE = """name: teacher-{code}
description: "{description}"
role: University lecturer in {title}
model: {model}
temperature: 0.3
max_tokens: 4096

capabilities:
  - teaching
  - {code_lower}

rag:
  collection: teacher-{code}
  chunk_size: 1100
  chunk_overlap: 180
  top_k: 8
  sources:
    - path: {material}
      extensions: [".pdf", ".md", ".txt"]
"""


def slugify(text: str) -> str:
    """Turn a title into a lowercase slug.

    Args:
        text: The text to slugify.

    Returns:
        The slug.
    """
    out = []
    for char in text.lower():
        if char.isalnum():
            out.append(char)
        elif char in " -_":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def build_description(title: str, code: str, topics: List[str]) -> str:
    """Build the router-facing description.

    The router only reads the description, so it has to say when this teacher
    should be picked.

    Args:
        title: The course title.
        code: The course code.
        topics: Extra topics the teacher covers.

    Returns:
        The description.
    """
    base = (
        f"{title} ({code}). Use for anything about {title.lower()}, "
        f"its theory, its exercises or its exam material."
    )
    if topics:
        listed = ", ".join(topics)
        return f"{base} Also covers: {listed}."
    return base


def main(argv: List[str] | None = None) -> int:
    """Run the generator.

    Args:
        argv: Command line arguments, defaults to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Generate a teacher agent.")
    parser.add_argument("--code", required=True, help="course code, e.g. SPIC")
    parser.add_argument("--title", required=True, help="course title")
    parser.add_argument(
        "--material", required=True, help="folder holding the course material"
    )
    parser.add_argument(
        "--out",
        default="agents/teachers",
        help="output directory (default: agents/teachers)",
    )
    parser.add_argument(
        "--model",
        default="deepseek-v4.1-flash",
        help="model id the teacher uses (default: deepseek-v4.1-flash)",
    )
    parser.add_argument(
        "--topic",
        action="append",
        default=[],
        help="extra topic this teacher covers; repeat for several",
    )
    args = parser.parse_args(argv)

    code = slugify(args.code)
    material = os.path.expanduser(args.material)
    description = build_description(args.title, args.code, args.topic)

    body = TEMPLATE.format(
        code=code,
        code_lower=code,
        title=args.title,
        description=description,
        material=material,
        model=args.model,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"teacher-{code}.yaml"
    if target.exists():
        print(f"error: {target} already exists", file=sys.stderr)
        return 1
    target.write_text(body, encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
