"""
Adapter for the jeevn (ashi) agricultural advisory pipeline.

Runs in-process so the domain steps are visible: it imports jeevn and calls
``AgriculturalReportGenerator.generate_report`` directly, while
``instrument_targets`` monkeypatch-traces the key domain + data-source steps
(no edits to jeevn's source).

IMPORTANT: jeevn needs its own heavy environment (rasterio/GDAL). Run the
auto-tester from jeevn's venv, or set ``JEEVN_SRC`` to its ``src`` directory.
The data sources hit the network and fall back to fabricated defaults; that
fabrication (and whether it is disclosed) is exactly what we test.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, List

from ..core.adapter import PipelineAdapter
from ..core.models import Case

_DEFAULT_JEEVN_SRC = r"C:\Users\manan\OneDrive2\Desktop\ashi\src"
_PROJECT_DIR = Path(__file__).resolve().parents[3] / "projects" / "jeevn"


def _ensure_jeevn_on_path() -> None:
    src = os.environ.get("JEEVN_SRC", _DEFAULT_JEEVN_SRC)
    if src and src not in sys.path and Path(src).exists():
        sys.path.insert(0, src)


class JeevnAdapter(PipelineAdapter):
    name = "jeevn"

    # The names AS BOUND where they are actually called. Class methods are
    # patched on the class (shared object, so the binding in advisory_service
    # sees it); ``fetch_aoi_data`` is patched on advisory_service because it was
    # pulled in via ``from ... import fetch_aoi_data``.
    instrument_targets = (
        "jeevn.application.advisory_service:fetch_aoi_data",
        "jeevn.domain.irrigation.et0:IrrigationCalculator.calculate_et0_hargreaves_samani",
        "jeevn.domain.irrigation.et0:IrrigationCalculator.calculate_kc",
        "jeevn.domain.irrigation.et0:IrrigationCalculator.calculate_etc",
        "jeevn.domain.fertilizer.requirements:NutrientRequirementCalculator.calculate_nutrient_requirements",
        "jeevn.domain.soil.management:SoilManagementCalculator.analyze_soil",
        "jeevn.domain.pest_disease_weed.assessment:PestDiseaseWeedAssessor.assess_pest_disease_risk",
        "jeevn.domain.growth_yield.projection:YieldGrowthCalculator.calculate_yield_projection",
    )

    def __init__(self):
        try:
            self.intent = (_PROJECT_DIR / "intent.md").read_text(encoding="utf-8")
        except Exception:
            self.intent = "Generate a correct agricultural advisory report from a lat/lon + crop."

    def invoke(self, case: Case) -> Any:
        _ensure_jeevn_on_path()
        from jeevn.application.advisory_service import AgriculturalReportGenerator

        p = case.payload
        return AgriculturalReportGenerator.generate_report(
            lat=float(p["latitude"]),
            lon=float(p["longitude"]),
            area_acres=float(p.get("area_acres", 0.421)),
            crop_name=p.get("crop_type", "apple"),
            sowing_date=p.get("sowing_date"),
            ndvi_timeseries=p.get("ndvi_timeseries"),
            location_name=p.get("location_name", ""),
        )

    def default_cases(self) -> List[Case]:
        return [
            Case(
                label="punjab-apple-farm",
                origin="default",
                rationale="representative AOI in an agricultural region",
                payload={"name": "test", "latitude": 31.10, "longitude": 77.17,
                         "area_acres": 1.0, "crop_type": "apple", "location_name": "Shimla"},
            ),
            Case(
                label="wheat-default-area",
                origin="default",
                rationale="different crop, default area, plains",
                payload={"name": "test2", "latitude": 30.90, "longitude": 75.85,
                         "area_acres": 2.5, "crop_type": "wheat", "location_name": "Ludhiana"},
            ),
        ]
