# auto-tester

An AI QA agent that black-box tests multi-step pipelines the way *you* do: it
**runs** your code with varied inputs, watches every intermediate step, and
reports where actual behavior diverges from your **plain-English intent** —
catching the subtle correctness/accuracy bugs that AI-generated code slips past
its own passing unit tests.

It works **both ways**:

- **Input-based (black-box):** generate diverse/edge/adversarial inputs, run the
  pipeline, and judge the output + each captured step against intent.
- **Code-based (white-box):** an LLM reads the code, hypothesizes where it
  likely diverges from intent, and feeds those hypotheses back to the input
  generator to confirm them at runtime.

The LLM is **Google Gemini**.

## How it judges correctness (the oracle)

Outputs are non-deterministic (live data, LLM calls), so it never diffs against
a golden file. Instead it uses:

| Oracle | What it catches | Where |
|--------|-----------------|-------|
| **Invariants** | properties that must always hold (no dropped rows, no dup ids, totals reconcile, values in range) | `core/evaluator.py` + generated `checks_<project>.py` |
| **Metamorphic** | relations between runs (idempotency, scaling, reordering) | same |
| **Spot-check (LLM)** | a sampled step's output doesn't follow from its input / uses a placeholder | `llm_oracles.py` |
| **Final judge (LLM)** | whole output is wrong, contradictory, or fabricated | `llm_oracles.py` |
| **Crash → finding** | the pipeline raised on an input | `core/runner.py` |
| **Code-scan (LLM)** | suspicious code paths (low-confidence until a run confirms) | `code_scan.py` |

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env   # then paste your GEMINI_API_KEY into .env
```

## Run — the simple way

The only input is a project folder. Two equivalent options:

**A) `autotester` command (works from anywhere):**
```powershell
cd C:\path\to\your-project
autotester test
```
…or point it at a folder: `autotester test "C:\path\to\your-project"`.

It discovers how to run the project, tests it, and writes the report **into the
project**: `<project>\.autotester\reports\findings.md` (and auto-opens it).

**B) Edit-and-Run file (no terminal):** open [run.py](run.py), paste your
project path into `PROJECT`, and hit Run in your IDE.

Optional steering files inside the project (auto-discovered):
- `<project>\.autotester\intent.md` — authoritative spec (accuracy boost)
- `<project>\.autotester\focus.md` — one feature/concern to check
Scaffold them: `autotester template intent --out <project>` / `... focus ...`.

Flags (for either): `--mode input|code|both`, `--num N`, `--focus <file>`,
`--reonboard`, `--regenerate-checks`, `--judge-limit N`, `--no-open`.

### Making `autotester` global
A launcher lives in `%USERPROFILE%\.autotester-bin\` (added to your **user**
PATH). It runs `python -m auto_tester.cli` from this repo's `.venv`. To undo,
remove that folder from your PATH. The GEMINI key is read from this repo's
`.env` regardless of where you run from.

### Advanced (tester-managed projects)
`auto-tester run --project <name>` runs a built-in/registered project and stores
artifacts under this repo's `projects/<name>/`. Used for the `buggy` self-test
and the hand-wired `jeevn` demo.

## Testing the tester

The `buggy` project is a fixture pipeline with four deliberately injected bugs
(silent drop, off-by-one dedup, swapped fields, swallowed parse error). The
oracle suite must catch all four and raise nothing on the clean variant:

```powershell
.\.venv\Scripts\python.exe -m pytest -q          # regression tests (no key needed)
python scripts\selftest_fixture.py               # detailed self-test + sample report
```

## The jeevn (ashi) target

`jeevn` is a real geospatial advisory pipeline in a sibling repo. The adapter
runs it **in-process** so the domain steps are traceable, which means the tester
must run from an environment where jeevn (rasterio/GDAL) is importable — its own
venv. Install auto-tester there and point at jeevn's source:

```powershell
# from jeevn's venv (.venv — the one with requests + GDAL/rasterio installed):
$jp = "C:\Users\manan\OneDrive2\Desktop\ashi\.venv\Scripts\python.exe"
& $jp -m pip install google-genai python-dotenv          # tester's only extra deps
$env:PYTHONPATH = "C:\Users\manan\OneDrive2\Desktop\experiments\auto tester\src"
$env:JEEVN_SRC  = "C:\Users\manan\OneDrive2\Desktop\ashi\src"
$env:GEMINI_API_KEY = "<your key>"
& $jp -m auto_tester.cli run --project jeevn --mode both
```

A no-LLM sanity probe (validates the adapter + instrumentation, needs no key):

```powershell
& "C:\Users\manan\OneDrive2\Desktop\ashi\.venv\Scripts\python.exe" scripts\probe_jeevn.py
```

What it should surface (from the intent in `projects/jeevn/intent.md`): the
hardcoded nutrient `current_levels` presented as real soil data, the fetched
`solar_radiation` that ET0 silently ignores, and any value that's a fabricated
default but missing from `data_quality.fabricated_fields`.

## Adding a target

1. Write an adapter in `src/auto_tester/adapters/` (subclass `PipelineAdapter`,
   implement `invoke`, list `instrument_targets`).
2. Write `projects/<name>/intent.md`.
3. Register a `ProjectSpec` in `src/auto_tester/registry.py`.
