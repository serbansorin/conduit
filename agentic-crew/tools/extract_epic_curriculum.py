#!/usr/bin/env python3
"""Extract the E-PiCo+ curriculum from the official programme handbook.

The handbook is a PDF published by the consortium. This script turns it into
a structured YAML file — one entry per course with its ECTS, instructor,
hosting institution, semester and topics — and then writes a teacher agent
for each course.

Usage:
    python3 tools/extract_epic_curriculum.py <handbook.pdf> [--out agents/teachers]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Which partner institution hosts which module, per the handbook's tables.
INSTITUTIONS = {
    "ECN": "École Centrale de Nantes, France",
    "UNSTPB": "POLITEHNICA University of Bucharest, Romania",
    "CAU": "Christian-Albrechts-Universität zu Kiel, Germany",
    "UAQ": "Università degli Studi dell'Aquila, Italy",
    "CINVESTAV": "CINVESTAV, Mexico",
}

# Tables in the handbook are identified by their caption, which follows the
# table on the page: "Table 4a. Subjects offered by UNSTPB during Semester 2".
CAPTION_RE = re.compile(
    r"Table\s+\d+[a-z]?\.?\s*Subjects offered by\s+([A-Z]+)\s+during\s+[Ss]emester\s+(\d)",
    re.I,
)

# The semester each institution's table belongs to, per the handbook.
TABLE_MAP: List[Tuple[int, str, int]] = [
    (13, "ECN", 1),
    (13, "UNSTPB", 2),
    (14, "CAU", 2),
    (15, "UAQ", 2),
    (15, "CINVESTAV", 2),
    (15, "ECN", 3),
    (16, "UNSTPB", 3),
    (16, "CAU", 3),
    (17, "UAQ", 3),
    (17, "CINVESTAV", 3),
]

TEACHER_TEMPLATE = """name: teacher-{slug}
description: {description}
role: {role}
model: {model}
temperature: 0.3
max_tokens: 4096

capabilities:
  - teaching
  - epic
  - {slug}

rag:
  collection: teacher-{slug}
  chunk_size: 1100
  chunk_overlap: 180
  top_k: 8
  sources:
    - path: {material}
      extensions: [".pdf", ".md", ".txt", ".pptx"]
"""


def quote(text: str) -> str:
    """Render a string as a double-quoted YAML scalar.

    Course names contain colons, so every interpolated string is quoted.

    Args:
        text: The value.

    Returns:
        The quoted scalar.
    """
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def slugify(text: str) -> str:
    """Turn a course title into a slug.

    Args:
        text: The title.

    Returns:
        The slug.
    """
    text = text.lower().replace("&", " and ")
    out = []
    for char in text:
        if char.isalnum():
            out.append(char)
        elif char in " -_/":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def module_rows(lines: List[str]) -> List[Dict[str, Any]]:
    """Extract the numbered module rows from a table.

    Args:
        lines: The page lines.

    Returns:
        One mapping per module.
    """
    rows: List[Dict[str, Any]] = []
    for line in lines:
        match = re.match(r"^(\d{1,2})\s+(.{3,90}?)\s+(\d(?:[.,]\d)?)$", line)
        if not match:
            continue
        title = match.group(2).strip()
        title = re.sub(r"\s*\*+\s*$", "", title)
        title = re.sub(r"\s*\*\*+\s*$", "", title)
        title = re.sub(r"\s*\(Option[^)]*\)\s*$", "", title, flags=re.I)
        title = title.strip(" ()")
        if not title or title.lower().startswith(("compulsory", "list of")):
            continue
        rows.append({"title": title, "ects": match.group(3).replace(",", ".")})
    return rows


def tables_by_caption(lines: List[str]) -> List[Tuple[Dict[str, str], List[Dict[str, Any]]]]:
    """Split a page's module rows into the tables they belong to.

    A table's caption follows the table, so the rows collected since the
    previous caption belong to the caption that closes them.

    Args:
        lines: The page lines.

    Returns:
        One (caption fields, rows) pair per table on the page.
    """
    tables: List[Tuple[Dict[str, str], List[Dict[str, Any]]]] = []
    pending: List[Dict[str, Any]] = []
    for line in lines:
        caption = CAPTION_RE.search(line)
        if caption:
            tables.append(
                (
                    {"institution": caption.group(1).upper(), "semester": caption.group(2)},
                    pending,
                )
            )
            pending = []
            continue
        if re.match(r"^\d{1,2}\s+.+\s+\d(?:[.,]\d)?$", line):
            pending.extend(module_rows([line]))
    return tables


STOPWORDS = {
    "and", "of", "for", "the", "in", "on", "a", "an", "to", "with",
    "systems", "system", "course", "courses", "i", "ii", "iii",
}


def tokens(title: str) -> set:
    """Reduce a title to its significant words.

    Two spellings of the same course ("Power electronics converters" and
    "POWER ELECTRONIC CONVERTERS") share no exact slug, but they share
    their content words.

    Args:
        title: The title.

    Returns:
        The significant words.
    """
    words = re.findall(r"[a-z0-9]+", title.lower())
    return {word for word in words if word not in STOPWORDS and len(word) > 2}


def similarity(left: str, right: str) -> float:
    """Score how likely two titles name the same course.

    Args:
        left: The first title.
        right: The second title.

    Returns:
        The Jaccard overlap of their significant words, 0 to 1.
    """
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def instructor_map(pages: List[List[str]]) -> Dict[Tuple[str, str], str]:
    """Map a course to its instructor.

    The same course title can appear at more than one partner institution,
    so the institution is part of the key. It is read from the syllabus
    header, which carries "Semester N" followed by the institution.

    Args:
        pages: The text of every page, split into lines.

    Returns:
        The mapping, keyed by (institution, course slug).
    """
    found: Dict[Tuple[str, str], str] = {}
    for lines in pages:
        current: Optional[str] = None
        institution = ""
        for index, line in enumerate(lines):
            if line.startswith("Credits") and index > 0:
                current = slugify(lines[index - 1])
                institution = ""
                # The institution sits a line or two below the credits.
                for ahead in lines[index : index + 4]:
                    if ahead.upper() in INSTITUTIONS:
                        institution = ahead.upper()
                        break
            elif line.startswith("Instructor") and current:
                instructor = " ".join(lines[index : index + 3])
                instructor = re.sub(r"\s+", " ", instructor)
                instructor = re.sub(r"^Instructor\s*", "", instructor)
                instructor = re.split(r"\bObjectives\b", instructor)[0]
                instructor = re.sub(r"\s*[-–]\s*[\w.\-]+@[\w.\-]+\s*$", "", instructor).strip()
                if instructor:
                    found.setdefault((institution, current), instructor[:80])
                current = None
    return found


def topics_for(lines: List[str]) -> List[str]:
    """Pull the bullet points under a course's Content section.

    Args:
        lines: The lines of one syllabus page.

    Returns:
        The topics.
    """
    topics: List[str] = []
    collecting = False
    for line in lines:
        if re.match(r"^Content\b", line, re.I):
            collecting = True
            continue
        if collecting:
            if re.match(r"^(Objectives|At the end|Students|On successful)", line):
                break
            cleaned = re.sub(r"^[•\-*]\s*", "", line).strip()
            if 12 <= len(cleaned) <= 90 and not cleaned.lower().startswith("total"):
                topics.append(cleaned)
        if len(topics) >= 12:
            break
    return topics


def main(argv: Optional[List[str]] = None) -> int:
    """Run the extraction.

    Args:
        argv: Command line arguments.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("handbook", help="path to the E-PiCo+ handbook PDF")
    parser.add_argument("--out", default="agents/teachers", help="output directory")
    parser.add_argument("--material", default="~/university/epic", help="material root")
    parser.add_argument(
        "--model", default="deepseek-v4.1-flash", help="model for the teachers"
    )
    args = parser.parse_args(argv)

    try:
        from pypdf import PdfReader
    except ImportError:
        print("error: pypdf is required (pip install pypdf)", file=sys.stderr)
        return 1

    reader = PdfReader(args.handbook)
    pages: List[List[str]] = [
        [line.strip() for line in (page.extract_text() or "").splitlines() if line.strip()]
        for page in reader.pages
    ]

    instructors = instructor_map(pages)

    courses: Dict[str, Dict[str, Any]] = {}
    for lines in pages:
        for caption, rows in tables_by_caption(lines):
            institution = caption["institution"]
            semester = int(caption["semester"])
            for row in rows:
                slug = slugify(row["title"])
                key = f"{institution.lower()}-{slug}"
                if not slug or key in courses:
                    continue
                courses[key] = {
                    "slug": slug,
                    "title": row["title"],
                    "ects": row["ects"],
                    "institution": institution,
                    "institution_name": INSTITUTIONS.get(institution, institution),
                    "semester": semester,
                    "instructor": instructors.get((institution, slug), ""),
                    "topics": [],
                }

    # Attach the syllabus topics from the annexes.
    for lines in pages:
        for index, line in enumerate(lines):
            if line.startswith("Credits") and index > 0:
                title = lines[index - 1].strip()
                key = slugify(title)
                if key in courses and not courses[key]["topics"]:
                    courses[key]["topics"] = topics_for(lines[index:])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for key, course in courses.items():
        instructor = course["instructor"] or "the course teaching team"
        if instructor == "the course teaching team":
            # The syllabus heading and the table row often differ in
            # spelling or word order ("Power electronics converters" versus
            # "POWER ELECTRONIC CONVERTERS"), so fall back to the closest
            # known instructor at the same institution.
            best, best_score = "", 0.0
            for (other_inst, other), name in instructors.items():
                if not name or other_inst != course["institution"]:
                    continue
                score = similarity(course["title"], other.replace("-", " "))
                if score > best_score:
                    best, best_score = name, score
            if best_score >= 0.4:
                instructor = best
                course["instructor"] = best
        description = (
            f"{course['title']} — {course['institution']}, semester "
            f"{course['semester']}, {course['ects']} ECTS. Teaches: "
            f"{instructor}. Use for anything about this course, its theory, "
            f"its exercises or its exam material."
        )
        body = TEACHER_TEMPLATE.format(
            slug=key,
            description=quote(description),
            role=quote(f"Lecturer in {course['title']} ({course['institution']})"),
            model=args.model,
            material=f"{args.material.rstrip('/')}/{key}",
        )
        (out_dir / f"teacher-{key}.yaml").write_text(body, encoding="utf-8")

    print(f"wrote {len(courses)} teacher agents to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
