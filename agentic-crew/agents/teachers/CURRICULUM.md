# The E-PiCo+ curriculum

The teacher agents in this folder cover the **E-PiCo+** master programme —
Electric Vehicle Propulsion and Control — run at the **ETTI** faculty
(Electronică, Telecomunicații și Tehnologia Informației) of POLITEHNICA
University of Bucharest, as the **UNSTPB** partner of a five-institution
Erasmus Mundus consortium.

This file is the reference. The teacher agents are generated from it.

## The programme

- **Name:** Electric Vehicle Propulsion and Control (EPIC / E-PiCo+)
- **Faculty:** ETTI, POLITEHNICA University of Bucharest
- **Language:** English
- **Length:** two years, 120 ECTS, four semesters
- **Coordinator (UPB):** Prof. Mădălin Frunzete
- **Partners:** ECN (Nantes, France), UNSTPB (Bucharest), CAU (Kiel, Germany),
  UAQ (L'Aquila, Italy), CINVESTAV (Mexico)

Semester 1 is common to everyone, at ECN. Semesters 2 and 3 are spent at a
partner institution, and semester 3 carries the specialisation. The thesis is
co-supervised across all five institutions.

## Semester 1 — ECN, common to everyone

| Course | ECTS | Instructor |
| --- | --- | --- |
| Control system | 4 | Guy Lebret, Franck Plestan |
| Research methodology | 4 | Mohamed Assaad Hamida |
| Embedded computing | 4 | Mickaël Hilairet |
| Statistical signal processing and estimation theory | 4 | Eric Le Carpentier |
| Fundamentals of electric and hydrogen vehicle system | 4 | Malek Ghanes |
| Electric and hydrogen vehicle modelling and simulation | 4 | Malek Ghanes |
| Project E-PiCo+ | 4 | — |
| French language | 2 | Silvia Ertl |

## Semester 2 — UNSTPB, POLITEHNICA Bucharest

**This is the semester at POLI Bucharest.**

| Course | ECTS | Instructor |
| --- | --- | --- |
| Power electronics converters | 3 | Adriana Florescu |
| Electrical machines | 4 | Tiberiu Tudorache |
| Renewable energy and storage systems | 3 | Adriana Florescu |
| Nonlinear control systems | 3 | Ciprian Lupu |
| Machine learning for autonomous systems | 4 | Anamaria Radoi |
| Romanian language and culture | 3 | Yolanda-Mirela Catelly |
| Research activity and practical work | 10 | Corneliu Burileanu |

## Semester 3 — UNSTPB, specialisation B: energy storage and energy management

| Course | ECTS | Instructor |
| --- | --- | --- |
| Battery chargers | 4 | Stefan George Rosu |
| Modern communication technologies for connected EVs | 4 | Daniel Oancea |
| Battery management systems for EVs | 4 | Alexandru Vasile |
| Retrofit, control of electrical machines | 4 | Bogdan Cristian Florea |
| Microprocessor applications for real time systems | 4 | Corneliu Burileanu |
| Scientific research and practical work | 10 | Corneliu Burileanu |

## The other partner institutions

| Institution | Semester 2 | Semester 3 specialisations |
| --- | --- | --- |
| **ECN** (Nantes) | — | A: energy management, converters, diagnosis, advanced control |
| **CAU** (Kiel) | design of converters, electric drives, renewable energy, nonlinear control, electives | power electronics and drives; embedded systems and advanced control |
| **UAQ** (L'Aquila) | power converters, machines and drives, renewable energy, nonlinear control, electives | AI techniques applied to EV mobility |
| **CINVESTAV** | design of converters, electric drives, renewable energy, nonlinear control, electives | computer vision, neural nets for EV control, AI for EV mobility, machine learning for EVs |

## Where the teacher agents come from

`tools/extract_epic_curriculum.py` parses the official E-PiCo+ programme
handbook and writes one agent per course. It reads the module tables, the
syllabus headings and the instructor lines, and it keys each course by
institution — the same title appears at several partners with a different
lecturer, and those must stay separate agents.

```bash
python3 tools/extract_epic_curriculum.py ~/university/epic/_programme/E-PiCo+-Handbook.pdf
```

Twelve courses have no instructor named in the handbook; their agents say
"the course teaching team", which is what the document says.

## Adding the material

Each teacher points at `~/university/epic/<agent-slug>/`. Put that course's
slides, notes and problem sets there, then:

```bash
agentic-crew ingest --agent teacher-unstpb-electrical-machines
```

The handbook itself lives in `~/university/epic/_programme/` and is not
committed to this repository.

## Reusing the generator for another programme

`tools/make_teacher.py` writes a single teacher from a course code, a title
and a material folder. It is the quick path when you are adding one course by
hand rather than a whole programme.
