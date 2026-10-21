#!/usr/bin/env python3
"""Generate the UPB teacher agents from the official UPB study plan.

The programme plan lives in the faculty's AQ portal, which publishes the
course list, the ECTS, the weekly hours and the teacher for every discipline
of every semester. This script reads it and writes one teacher agent per
course.

Usage:
    python3 tools/extract_upb_plan.py [plan_id] [--out agents/teachers]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLAN_URL = "https://fise-discipline.etti.upb.ro/plan/{plan_id}"
DEFAULT_PLAN_ID = 18

# The specialisations that belong to this programme, in either language.
PROGRAMME_MARKERS = ("Propulsia", "Electric Vehicle", "Controlul i Propulsia")

# The AQ portal publishes a teacher for only part of the plan. The rest come
# from the official ETTI exam schedules and the published master timetables,
# which name the lecturer for every group of the programme. Where the lecturer
# changed between academic years the year is noted.
SCHEDULE_TEACHERS = {
    "Modelarea și simularea vehiculelor electrice": (
        "Conf. Dr. Ing. Octaviana Datcu (2024-25), "
        "Prof. Dr. Ing. Alexandru Ancuța (2025-26)"
    ),
    "Sisteme de control automat": "Prof. Dr. Ing. Dan Alexandru Stoichescu",
    "Sisteme neliniare de control automat": (
        "Conf. Dr. Ing. Cătălin Lupu (2024-25), "
        "Prof. Dr. Ing. Alexandru Dinu (2025-26)"
    ),
    "Încărcătoare de baterii": "Conf. dr. ing. Ștefan-George Roșu",
    "Cerințe specifice stocării energiei": (
        "Conf. dr. ing. Lucian-Andrei Perișoară, Prof. dr. ing. Alexandru Vasile"
    ),
    "Sisteme de management al bateriilor și ciclul de viață al bateriilor": (
        "Conf. dr. ing. Lucian-Andrei Perișoară, Prof. dr. ing. Alexandru Vasile"
    ),
    "Controlul fără senzori al mașinilor electrice": "Ș.l. dr. ing. Mihai Stanciu",
    "Aplicații ale microprocesoarelor pentru sisteme de calcul în timp real": (
        "ș.l. dr. ing. Georgian Nicolae (2025-26), "
        "Prof. Dr. Ing. Corneliu Burileanu (2024-25)"
    ),
    "Prelucrarea statistică a semnalelor și teoria estimării": (
        "Prof. Dr. Ing. Alexandru Dinu (2025-26), "
        "Conf. Dr. Ing. Anamaria Rădoi (2024-25)"
    ),
    "Mașini electrice": (
        "Prof. Dr. Ing. Tiberiu Tudorache (2024-25), "
        "Conf. Dr. Ing. A. Șerban (2025-26)"
    ),
    "Metodologia cercetării": "Prof. Dr. Ing. Răzvan Crăciunescu",
    "Proiect: sisteme embedded": "Prof. Dr. Ing. Alexandru Ancuța",
    "Practici avansate de etică și deontologie universitară": (
        "Prof. Dr. Ing. A. Simionescu"
    ),
    "Modul de cercetare științifică I": "Conf. Dr. Ing. Octaviana Datcu",
    "Modul de cercetare științifică II": "Conf. Dr. Ing. Octaviana Datcu",
    "Modul de cercetare științifică III": "Conf. Dr. Ing. Octaviana Datcu",
    "Modul de cercetare științifică, practică de cercetare și elaborare de disertație": (
        "Conf. Dr. Ing. Octaviana Datcu"
    ),
    "Limba și cultura română": "Mihaela Pricope",
}

# The semesters the plan is published in, in order.
SEMESTERS = ("Anul 1 Sem. 1", "Anul 1 Sem. 2", "Anul 2 Sem. 1", "Anul 2 Sem. 2")

TEMPLATE = """name: teacher-upb-{slug}
description: {description}
role: {role}
model: {model}
temperature: 0.3
max_tokens: 4096

capabilities:
  - teaching
  - epic
  - upb
  - {slug}

rag:
  collection: teacher-upb-{slug}
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
    """Turn a title into an ASCII slug.

    Args:
        text: The title.

    Returns:
        The slug.
    """
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    out = []
    for char in ascii_text.lower():
        if char.isalnum():
            out.append(char)
        elif char in " -_/&(),.":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def normalise(text: str) -> str:
    """Fold a name to a comparable key.

    Args:
        text: The name.

    Returns:
        The folded name.
    """
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()


def strip_tags(raw: str) -> str:
    """Remove HTML tags and collapse whitespace.

    Args:
        raw: The HTML.

    Returns:
        The text.
    """
    import html

    text = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def parse_tables(page: str) -> List[List[List[str]]]:
    """Extract the plan tables from the page.

    Args:
        page: The page HTML.

    Returns:
        One list of rows per table.
    """
    tables: List[List[List[str]]] = []
    for raw_table in re.findall(r"<table.*?</table>", page, flags=re.S | re.I):
        rows: List[List[str]] = []
        for raw_row in re.findall(r"<tr.*?</tr>", raw_table, flags=re.S | re.I):
            cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", raw_row, flags=re.S | re.I)
            rows.append([strip_tags(cell) for cell in cells])
        if rows:
            tables.append(rows)
    return tables


def parse_teachers(page: str) -> Dict[str, str]:
    """Map a discipline name to its teacher.

    The plan page embeds the course catalogue as JSON, where every entry
    carries the discipline name and its teacher.

    Args:
        page: The page HTML.

    Returns:
        The mapping, keyed by the folded discipline name.
    """
    index = page.find('"course_teacher"')
    if index < 0:
        return {}
    start = page.rfind("=", 0, index)
    brace = page.find("{", start)
    depth = 0
    end = -1
    for position in range(brace, len(page)):
        if page[position] == "{":
            depth += 1
        elif page[position] == "}":
            depth -= 1
            if depth == 0:
                end = position + 1
                break
    if end < 0:
        return {}
    try:
        catalogue = json.loads(page[brace:end])
    except (ValueError, TypeError):
        return {}

    teachers: Dict[str, str] = {}
    for entry in catalogue.values():
        if not isinstance(entry, dict):
            continue
        specialisation = entry.get("specialization") or ""
        if not any(marker in specialisation for marker in PROGRAMME_MARKERS):
            continue
        name = entry.get("name") or ""
        teacher = (entry.get("course_teacher") or "").strip()
        if not name or not teacher:
            continue
        teachers[normalise(name)] = teacher
    return teachers


def courses_from_table(rows: List[List[str]]) -> List[Dict[str, Any]]:
    """Pull the discipline rows out of one plan table.

    Args:
        rows: The table rows.

    Returns:
        One mapping per discipline.
    """
    courses: List[Dict[str, Any]] = []
    section = ""
    for row in rows:
        if not row:
            continue
        first = row[0]
        if first.startswith("Discipline obligatorii") or first.startswith("Discipline opționale") or first.startswith("Discipline facultative"):
            section = first
            continue
        if first.startswith(("Statistici", "TOTAL", "Număr", "Nr. crt", "C")):
            continue
        # The row is: nr | code | name | type | ects | hours... | evaluation
        if len(row) < 5:
            continue
        try:
            int(first)
        except ValueError:
            continue
        name = row[2].strip()
        if not name:
            continue
        ects = row[4].strip()
        evaluation = row[-1].strip()
        courses.append(
            {
                "name": name,
                "ects": ects,
                "evaluation": evaluation,
                "section": section,
            }
        )
    return courses


def split_name(name: str) -> Tuple[str, str]:
    """Split "Romanian / English" into its two halves.

    Args:
        name: The combined name.

    Returns:
        The Romanian and the English name.
    """
    if " / " in name:
        romanian, _, english = name.partition(" / ")
        return romanian.strip(), english.strip()
    return name.strip(), ""


def main(argv: Optional[List[str]] = None) -> int:
    """Run the extraction.

    Args:
        argv: Command line arguments.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_id", nargs="?", default=DEFAULT_PLAN_ID)
    parser.add_argument("--out", default="agents/teachers")
    parser.add_argument("--material", default="~/university/epic")
    parser.add_argument(
        "--model", default="deepseek-v4.1-flash", help="model the teachers use"
    )
    args = parser.parse_args(argv)

    try:
        import urllib.request
    except ImportError:  # pragma: no cover
        print("error: urllib is unavailable", file=sys.stderr)
        return 1

    url = PLAN_URL.format(plan_id=args.plan_id)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            page = response.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - reported to the caller
        print(f"error: cannot read {url}: {exc}", file=sys.stderr)
        return 1

    teachers = parse_teachers(page)
    schedule_teachers = {normalise(name): value for name, value in SCHEDULE_TEACHERS.items()}
    tables = parse_tables(page)

    plan_tables = [rows for rows in tables if rows and any("ECTS" in " ".join(r) for r in rows[:2])]
    if not plan_tables:
        print("error: no plan tables found", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    written: List[str] = []
    for index, rows in enumerate(plan_tables):
        semester = SEMESTERS[index] if index < len(SEMESTERS) else f"Semester {index + 1}"
        for course in courses_from_table(rows):
            romanian, english = split_name(course["name"])
            slug = slugify(english or romanian)
            if not slug:
                continue
            teacher = schedule_teachers.get(normalise(romanian), "")
            if not teacher:
                teacher = teachers.get(normalise(romanian), "") or teachers.get(
                    normalise(course["name"]), ""
                )
            if not teacher:
                teacher = teachers.get(normalise(english), "")
            label = english or romanian
            description = (
                f"{label} ({romanian}) — POLITEHNICA University of Bucharest, "
                f"{semester}, {course['ects']} ECTS"
                f"{f', taught by {teacher}' if teacher else ''}. "
                f"Use for anything about this course, its theory, its exercises "
                f"or its exam material."
            )
            body = TEMPLATE.format(
                slug=slug,
                description=quote(description),
                role=quote(f"Lecturer in {label}"),
                model=args.model,
                material=f"{args.material.rstrip('/')}/upb-{slug}",
            )
            target = out_dir / f"teacher-upb-{slug}.yaml"
            target.write_text(body, encoding="utf-8")
            written.append(target.name)

    print(f"wrote {len(written)} UPB teacher agents to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
