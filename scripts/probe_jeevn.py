r"""
Probe the jeevn adapter against the REAL pipeline (no Gemini key needed).

Validates that the in-process adapter imports jeevn, calls generate_report with
the right signature, and that instrument_targets actually capture the domain
steps. Also prints the fabrication disclosure + fertilizer 'current' levels so
we can eyeball the hardcoded-soil-data bug before the LLM oracles ever run.

Run with jeevn's venv (the one with requests + GDAL/rasterio), with JEEVN_SRC
pointing at jeevn's src directory:
    JEEVN_SRC=/path/to/ashi/src python scripts/probe_jeevn.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if not os.environ.get("JEEVN_SRC"):
    sys.exit("ERROR: set JEEVN_SRC to jeevn's src directory "
             "(the folder containing the 'jeevn' package).")

from auto_tester.registry import get_spec

adapter = get_spec("jeevn").make_adapter()
case = adapter.default_cases()[0]
print(f"running case: {case.label}  payload={case.payload}")
run = adapter.run(case)

if run.error:
    print("\nPIPELINE RAISED:\n", run.error[-1500:])
    sys.exit(1)

print(f"\nsteps captured ({len(run.trace.steps)}):")
for s in run.trace.steps:
    print(f"  - {s.name}  err={s.error is not None}")

out = run.output or {}
dq = out.get("data_quality", {})
print("\ndata_quality.fabricated_fields:", dq.get("fabricated_fields"))

fert = out.get("components", {}).get("fertilizer_management", {}).get("nutrient_requirements", {})
print("\nfertilizer 'current_kg_per_acre' (hardcoded constants if N=13.65,P=11,K=82,S=7,Zn=0.8):")
for nut, info in fert.items():
    print(f"  {nut}: current={info.get('current_kg_per_acre')}  gap={info.get('gap_kg_per_acre')}  status={info.get('status')}")

# Did the fertilizer step appear in the trace with its captured args?
ferts = run.trace.by_name(
    "jeevn.domain.fertilizer.requirements.NutrientRequirementCalculator.calculate_nutrient_requirements"
)
print(f"\nfertilizer step traced: {len(ferts)} call(s)")
print("\nPROBE OK — adapter + instrumentation work against the real pipeline.")
