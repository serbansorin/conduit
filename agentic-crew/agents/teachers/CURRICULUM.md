# The EPiC curriculum

The teacher agents in this folder cover **EPiC** — *Electric Vehicle Propulsion
and Control* (Romanian: *Controlul și Propulsia Vehiculelor Electrice*), the
master programme at the **ETTI** faculty of POLITEHNICA University of
Bucharest.

> **A correction to the brief.** The programme is administered by the Faculty
> of Electronics, Telecommunications and Informatics (**ETTI**), department
> **EAII** — not the Faculty of Automatic Control and Computers. The ETTI
> programme page lists ACS as the target group, which is probably where the
> mix-up came from. The abbreviation is **EVPC**; the programme brands itself
> **EPiC**.

- **Language:** English
- **Length:** two years, 120 ECTS, 30 per semester
- **Programme coordinator:** Prof. dr. ing. Mădălin Frunzete
- **Also known as:** an Erasmus Mundus joint master, *E-PiCo+*, with
  Centrale Nantes (ECN), Christian-Albrechts-Universität Kiel (CAU),
  Università dell'Aquila (UAQ) and CINVESTAV

---

## The UPB plan — 2026-2027

This is the plan a student at POLITEHNICA actually follows. It is published in
the faculty's AQ portal, which lists every discipline with its ECTS, its
weekly hours and its teacher.

### Year 1, semester 1 — 30 ECTS

| Course | Romanian | ECTS | Teacher |
| --- | --- | --- | --- |
| Electric Vehicle Modelling and Simulation | Modelarea și simularea vehiculelor electrice | 3 | Octaviana Datcu (2024-25), Alexandru Ancuța (2025-26) |
| Statistical Signal Processing and Estimation Theory | Prelucrarea statistică a semnalelor și teoria estimării | 4 | Alexandru Dinu (2025-26), Anamaria Rădoi (2024-25) |
| Control Systems | Sisteme de control automat | 3 | Dan Alexandru Stoichescu |
| Fundamentals of Electric Vehicle Systems | Bazele vehiculelor electrice | 3 | Lucian-Andrei Perișoară |
| Research Methodology | Metodologia cercetării | 3 | Răzvan Crăciunescu |
| Project: Embedded Systems | Proiect: sisteme embedded | 2 | Alexandru Ancuța |
| Advanced practices of university ethics and deontology | Practici avansate de etică și deontologie universitară | 2 | A. Simionescu |
| Module of scientific research I | Modul de cercetare științifică I | 10 | Octaviana Datcu |

### Year 1, semester 2 — 30 ECTS

| Course | Romanian | ECTS | Teacher |
| --- | --- | --- | --- |
| Power Electronic Converters | Convertoare electronice de putere | 3 | Adriana Florescu, Mihail Ștefan Teodorescu |
| Electrical Machines | Mașini electrice | 4 | Tiberiu Tudorache (2024-25), A. Șerban (2025-26) |
| Renewable Energy and Storage Systems | Energie regenerabilă și sisteme de stocare | 3 | Adriana Florescu, Mihail Ștefan Teodorescu |
| Nonlinear Control Systems | Sisteme neliniare de control automat | 3 | Cătălin Lupu (2024-25), Alexandru Dinu (2025-26) |
| Machine Learning for Autonomous Systems | Metode de învățare pentru sisteme autonome | 4 | Anamaria Rădoi |
| Module of scientific research II | Modul de cercetare științifică II | 10 | Octaviana Datcu |
| *Romanian Language and Culture* (option) | Limba și cultura română | 3 | Mihaela Pricope |

### Year 2, semester 1 — 30 ECTS

| Course | Romanian | ECTS | Teacher |
| --- | --- | --- | --- |
| Battery Chargers | Încărcătoare de baterii | 4 | Ștefan-George Roșu |
| Energy Storage Requirements | Cerințe specifice stocării energiei | 4 | Lucian-Andrei Perișoară, Alexandru Vasile |
| Battery Management Systems and Battery Life Cycle | Sisteme de management al bateriilor și ciclul de viață al bateriilor | 4 | Lucian-Andrei Perișoară, Alexandru Vasile |
| Sensorless Control of Electrical Machines | Controlul fără senzori al mașinilor electrice | 4 | Mihai Stanciu |
| Microprocessor Applications for Real Time Systems | Aplicații ale microprocesoarelor pentru sisteme de calcul în timp real | 4 | Georgian Nicolae (2025-26), Corneliu Burileanu (2024-25) |
| Module of scientific research III | Modul de cercetare științifică III | 10 | Octaviana Datcu |

### Year 2, semester 2 — 30 ECTS

| Course | Romanian | ECTS | Teacher |
| --- | --- | --- | --- |
| Scientific research, research practice and thesis development | Modul de cercetare științifică, practică de cercetare și elaborare de disertație | 30 | Octaviana Datcu |

### The optional block

The plan also carries a block of **facultative** courses: *Proiectarea și
managementul programelor educaționale*, *Psihopedagogia adolescenților,
tinerilor și adulților*, *Consiliere și orientare*, *Didactica domeniului…*,
*Educație interculturală* and *Practică pedagogică de specialitate*. These are
for students who also want the didactic qualification, not technical
electives, and they have no single lecturer. Teacher agents are generated for
them anyway so the whole plan is covered.

**There is no internal specialisation at UPB.** The programme is a single
English-taught track. The only menu is the semester-2 optional pair: *Limba și
cultura română* or *Instituții politice și administrative europene*.

---

## The Erasmus partner tracks

The specialisations live in the Erasmus Mundus framework. A student is hosted
by one partner for semesters 2 and 3, and each host runs its own track.

| Track | Host | Courses |
| --- | --- | --- |
| A | ECN, Nantes | advanced control of electric and hydrogen propulsion, control of power converters, observation and diagnosis, optimisation of charging and refuelling, a case study on vehicle topology, project |
| B | UNSTPB, Bucharest | battery chargers, modern communication technologies for connected EVs, battery management systems, retrofit and control of electrical machines, microprocessor applications, research module |
| C | CAU, Kiel | modelling and control of power electronics converters, battery chargers for EVs, power electronics seminar, laboratory, plus electives |
| D | UAQ, L'Aquila | systems identification and data analysis, embedded systems, advanced control systems, optimal control |
| E | CINVESTAV, Mexico | computer vision, neural network techniques for EV control, AI techniques for EV mobility, machine learning for EVs |

The teacher agents for these are named `teacher-ecn-*`, `teacher-cau-*`,
`teacher-uaq-*` and `teacher-cinvestav-*`, and are generated from the
consortium's E-PiCo+ student handbook.

---

## Where the teacher agents come from

Two generators, two official sources:

```bash
# The UPB plan, from the faculty's AQ portal.
python3 tools/extract_upb_plan.py

# The Erasmus partner tracks, from the E-PiCo+ programme handbook.
python3 tools/extract_epic_curriculum.py ~/university/epic/_programme/E-PiCo+-Handbook.pdf
```

`extract_upb_plan.py` reads the live plan, so it picks up whatever the
current academic year publishes. The AQ portal carries a teacher for only part
of the plan; the rest come from the official ETTI exam schedules and the
published master timetables, which name the lecturer for every group of the
programme. Where the lecturer changed between academic years the year is
noted.

## Adding the material

Each teacher points at `~/university/epic/upb-<course>/` for the UPB courses
and `~/university/epic/<agent>/` for the partner tracks. Put the slides,
notes and problem sets there, then:

```bash
agentic-crew ingest --agent teacher-upb-electrical-machines
```

The programme handbook itself lives in `~/university/epic/_programme/` and is
not committed to this repository.

## Reusing the generator for another programme

`tools/make_teacher.py` writes a single teacher from a course code, a title
and a material folder. It is the quick path when you are adding one course by
hand rather than a whole programme.
