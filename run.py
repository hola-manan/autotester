"""
Simplest way to use the tester: edit PROJECT below and hit Run in your IDE.

It tests that project folder and writes the report INTO it
(``<project>/.autotester/reports/findings.md``), then opens it.

No CLI needed. (If the project has its own venv with extra deps, the tester
automatically runs inside it.)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# ===========================================================================
# 1) Paste the folder you want to test:
PROJECT = r"/path/to/your-project"  # Windows: r"C:\path\to\your-project"

# 2) Optional — a focus file describing one feature to check (or leave None):
FOCUS = None  # e.g. r"/path/to/your-project/.autotester/focus.md"

# 3) Optional knobs:
NUM_INPUTS = 12       # how many test inputs to generate
JUDGE_LIMIT = 6       # how many runs the LLM judges (cost control)
MODE = "both"         # "input" | "code" | "both"
# ===========================================================================

from auto_tester.cli import main

argv = ["test", PROJECT, "--num", str(NUM_INPUTS),
        "--judge-limit", str(JUDGE_LIMIT), "--mode", MODE]
if FOCUS:
    argv += ["--focus", FOCUS]

raise SystemExit(main(argv))
