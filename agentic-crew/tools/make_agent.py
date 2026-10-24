#!/usr/bin/env python3
"""Generate a custom agent from a short spec.

Usage:
    python3 tools/make_agent.py \\
        --name my-agent \\
        --role "Senior backend engineer" \\
        --description "Use for anything in the api.backend service." \\
        --capability backend --capability laravel \\
        --skill ~/imc-dev/www/getubetter/api.backend/.opencode/skills/laravel-13 \\
        --source ~/imc-dev/www/getubetter/api.backend \\
        --model glm-5.3
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List

TEMPLATE = """name: {name}
description: {description}
role: {role}
model: {model}
temperature: {temperature}
max_tokens: {max_tokens}

capabilities:
{capabilities}

{skills_block}
rag:
  collection: {name}
  chunk_size: {chunk_size}
  chunk_overlap: {chunk_overlap}
  top_k: {top_k}
  sources:
{sources_block}
"""


def quote(text: str) -> str:
    """Render a string as a double-quoted YAML scalar.

    Args:
        text: The value.

    Returns:
        The quoted scalar.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def slugify(text: str) -> str:
    """Turn a name into a slug.

    Args:
        text: The name.

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


def main(argv: List[str] | None = None) -> int:
    """Run the generator.

    Args:
        argv: Command line arguments.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description="Generate a custom agent.")
    parser.add_argument("--name", required=True, help="unique agent name")
    parser.add_argument("--role", required=True, help="short role label")
    parser.add_argument("--description", required=True, help="when to pick this agent")
    parser.add_argument("--model", default="glm-5.3", help="model id")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--capability", action="append", default=[], help="capability tag; repeat")
    parser.add_argument("--skill", action="append", default=[], help="skill folder; repeat")
    parser.add_argument("--source", action="append", default=[], help="RAG source path; repeat")
    parser.add_argument("--chunk-size", type=int, default=900)
    parser.add_argument("--chunk-overlap", type=int, default=150)
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--out", default="agents", help="output directory")
    args = parser.parse_args(argv)

    name = slugify(args.name)
    if not name:
        print("error: --name must contain at least one letter or digit", file=sys.stderr)
        return 1

    capabilities = args.capability or ["general"]
    cap_block = "\n".join(f"  - {c}" for c in capabilities)

    skills_block = ""
    if args.skill:
        skills_block = "skills:\n"
        for path in args.skill:
            expanded = os.path.expanduser(path)
            skills_block += f"  - name: {Path(expanded).name}\n"
            skills_block += f"    path: {expanded}\n"
            skills_block += '    include: "**/*.md"\n'
            skills_block += "    max_chars: 12000\n"
        skills_block += "\n"

    sources_block = ""
    if args.source:
        for path in args.source:
            expanded = os.path.expanduser(path)
            sources_block += f"  - path: {expanded}\n"
            sources_block += '    include: "**/*.md"\n'
    else:
        sources_block = "  []\n"

    body = TEMPLATE.format(
        name=name,
        description=quote(args.description),
        role=args.role,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        capabilities=cap_block,
        skills_block=skills_block,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        top_k=args.top_k,
        sources_block=sources_block,
    )

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{name}.yaml"
    if target.exists():
        print(f"error: {target} already exists", file=sys.stderr)
        return 1
    target.write_text(body, encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
