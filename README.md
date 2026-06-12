# auto-tester

An AI QA agent that tests **any Python project folder by running it**: it
generates varied inputs, executes your code (tracing every inner step), and
reports where actual behavior diverges from your **plain-English intent** —
catching the subtle correctness/accuracy bugs that AI-generated code slips past
its own passing unit tests.

It works **both ways**:

- **Input-based (black-box):** generate diverse/edge/adversarial inputs, run the
  pipeline, and judge the output + each captured step against intent.
- **Code-based (white-box):** an LLM reads the code and hypothesizes where it
  likely diverges from intent — but a suspicion only becomes a finding when an
  input-based run **confirms it at runtime**.

The LLM is **Google Gemini**. The deterministic spine (running, tracing,
checks, fuzzing) needs no key at all.

## How it judges correctness (the oracles)

Outputs are non-deterministic (live data, LLM calls), so it never diffs against
a golden file. Instead it uses:

| Oracle | What it catches | Where |
|--------|-----------------|-------|
| **Invariants** | properties that must always hold (no dropped rows, no dup ids, totals reconcile, values in range) | `core/evaluator.py` + generated `checks_<project>.py` |
| **Metamorphic** | relations between runs (idempotency, scaling, reordering) | same |
| **Hypothesis fuzz** | property violations anywhere in the legal input space, **shrunk to a minimal reproducing input** | `hypothesis_runner.py` + generated `strategies_<project>.py` |
| **Spot-check (LLM)** | a sampled step's output doesn't follow from its input / uses a placeholder | `llm_oracles.py` |
| **Final judge (LLM)** | whole output is wrong, contradictory, or fabricated — scored against a fixed **rubric** extracted from intent (`rubric.json`) | `llm_oracles.py` + `rubric.py` |
| **Crash → finding** | the pipeline raised on an input | `core/runner.py` |
| **Code-scan (LLM)** | suspicious code paths (steer inputs/fuzz seeds; runtime-confirmed) | `code_scan.py` |

Failures can never be lost mid-session: findings are checkpointed to disk after
every stage and after every LLM-judged run, and each LLM oracle call is
isolated — one flaky call costs only itself.

## Setup (Linux/macOS/Windows)

```bash
python -m venv .venv && . .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                            # then paste your GEMINI_API_KEY
```

No key? Everything still runs except the LLM stages, and the run is **loudly
marked PARTIAL** (banner + report header) — a clean partial report is not a
full pass.

If your key doesn't have access to the pro model (common with Vertex AI
Express keys), the tool warns once and falls back to the flash model instead
of failing those stages.

## Run — the simple way

The only input is a project folder:

```bash
autotester test /path/to/your-project
```

It discovers how to run the project (entrypoint, params, steps worth tracing),
tests it, and writes the report **into the project**:
`<project>/.autotester/reports/<timestamp>/findings.md`.

**No terminal:** open [run.py](run.py), paste your project path into `PROJECT`,
and hit Run in your IDE.

Flags: `--mode input|code|both`, `--num N`, `--fuzz N`, `--no-fuzz`,
`--focus <file>`, `--reonboard`, `--regenerate-checks`, `--judge-limit N`,
`--no-open`.

## Steering files your project can maintain (`<project>/.autotester/`)

All optional, all auto-discovered; scaffold with
`autotester template <kind> --out <project>`:

| File | Role |
|------|------|
| `intent.md` | **authoritative spec** — the rubric everything is judged against (biggest accuracy boost) |
| `focus.md` | one feature/concern to stress this run |
| `instrument.yaml` | the inner steps to trace (`- module:qualname` per line); **overrides** what the LLM discovered |

Project docs (`README.md`, `ARCHITECTURE.md`, `OVERVIEW.md`, …) are also read
during discovery — keeping them current improves onboarding accuracy.

Generated, reviewable artifacts land next to them: `profile.json` (how to run
you), `checks_<name>.py` (deterministic oracles), `strategies_<name>.py`
(Hypothesis input strategies + adversarial seeds), `rubric.json` (judging
criteria). All are plain files you can hand-correct; they are generated once
and reused (re-generate with `--regenerate-checks` / `--reonboard`).

## Testing the tester

The `buggy` project is a fixture pipeline with four deliberately injected bugs
(silent drop, off-by-one dedup, swapped fields, swallowed parse error). The
oracle suite must catch all four and raise nothing on the clean variant:

```bash
pip install -e ".[dev]"     # required once — tests import the package
pytest -q                   # regression tests (no key needed)
python scripts/selftest_fixture.py   # detailed self-test + sample report
```

The suite also covers the fuzz stage (must shrink a seeded bug to a minimal
repro and stay silent on the clean pipeline), session resilience (a flaky LLM
oracle can't lose earlier findings; keyless runs are loudly partial), and
profile/discovery validation errors.

## The jeevn (ashi) target

`jeevn` is a real geospatial advisory pipeline in a sibling repo, wired up as a
checked-in profile (`projects/jeevn/profile.json`) running through the same
GenericAdapter as any discovered project. The adapter runs it **in-process** so
the domain steps are traceable, which means the tester must run from an
environment where jeevn (rasterio/GDAL) is importable — its own venv:

```bash
# from jeevn's venv (the one with requests + GDAL/rasterio):
pip install -e /path/to/auto-tester
export JEEVN_SRC=/path/to/ashi/src       # the folder containing the 'jeevn' package
export GEMINI_API_KEY=<your key>
autotester run --project jeevn --mode both
```

If `JEEVN_SRC` is unset or wrong you get an immediate, explicit error — never a
silent run that scanned nothing.

A no-LLM sanity probe (validates the adapter + instrumentation, needs no key):

```bash
JEEVN_SRC=/path/to/ashi/src python scripts/probe_jeevn.py
```

What it should surface (from `projects/jeevn/intent.md`): the hardcoded
nutrient `current_levels` presented as real soil data, the fetched
`solar_radiation` that ET0 silently ignores, and any value that's a fabricated
default but missing from `data_quality.fabricated_fields`.

## Adding a target

Usually nothing: `autotester test <folder>` discovers it. For a target the
discovery can't handle, check in a `projects/<name>/profile.json` (see
`projects/jeevn/` — the `root` may reference `${ENV_VARS}`) plus an
`intent.md`; it runs through the GenericAdapter like everything else.
