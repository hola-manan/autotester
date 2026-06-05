import math
from auto_tester.core.checks import invariant, metamorphic

# Helper functions

def get_component(output, path):
    """Safely get a nested value from the output dict."""
    if not isinstance(output, dict):
        return None
    value = output
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value

def _reconstruct_ndvi_fabricated(run):
    """Re-implements the logic of _process_ndvi_data to find expected fabricated keys."""
    payload = run.case.payload
    ndvi_timeseries = payload.get("ndvi_timeseries")
    ndvi_raster_data = payload.get("ndvi_raster_data")

    sar_steps = [s for s in run.trace.steps if s.name == "Sentinel1Client.fetch_latest_rvi"]
    sar_data = sar_steps[0].result if sar_steps else None

    fabricated = {"ndvi", "rvi", "rsm"}

    if ndvi_timeseries:
        if len(ndvi_timeseries) > 0:
            latest = ndvi_timeseries[-1]
            ts_ndvi = latest.get("ndvi")
            if ts_ndvi is not None:
                fabricated.discard("ndvi")
                fabricated.discard("rvi")

    if ndvi_raster_data:
        raster_ndvi = ndvi_raster_data.get("ndvi_mean")
        if raster_ndvi is not None:
            fabricated.discard("ndvi")

    if sar_data and sar_data.get("rvi") is not None:
        fabricated.discard("rvi")

    aoi_steps = [s for s in run.trace.steps if s.name == "fetch_aoi_data"]
    aoi_data = aoi_steps[0].result if aoi_steps else {}
    rsm_info = aoi_data.get("radar_soil_moisture") or {}
    if rsm_info.get("value") is not None and rsm_info.get("source") != "fabricated":
        fabricated.discard("rsm")

    return list(fabricated)

def _reconstruct_fabricated_fields(run):
    """Reconstructs the expected list of fabricated fields based on pipeline logic."""
    aoi_steps = [s for s in run.trace.steps if s.name == "fetch_aoi_data"]
    aoi_data = aoi_steps[0].result if aoi_steps else {}
    aoi_fabricated = aoi_data.get("_fabricated_sources", [])

    ndvi_fabricated = _reconstruct_ndvi_fabricated(run)

    return sorted(list(set(aoi_fabricated + ndvi_fabricated)))

# Invariant checks

@invariant(id="fabrication_is_disclosed",
           description="Any value that is a fabricated default must be listed in data_quality.fabricated_fields.",
           severity="critical", category="correctness")
def _(run):
    if run.output is None:
        return None

    fabricated_fields = get_component(run.output, "data_quality.fabricated_fields")
    if fabricated_fields is None:
        return {"observed": "data_quality.fabricated_fields is missing from the output.", "evidence": {}}

    try:
        expected_fabricated = _reconstruct_fabricated_fields(run)
    except Exception as e:
        return {"observed": "Failed to reconstruct expected fabricated fields from trace.", "evidence": {"error": str(e)}}

    if set(fabricated_fields) != set(expected_fabricated):
        return {
            "observed": "The list of fabricated fields is incorrect.",
            "evidence": {
                "expected": expected_fabricated,
                "actual": fabricated_fields,
                "missing": sorted(list(set(expected_fabricated) - set(fabricated_fields))),
                "extra": sorted(list(set(fabricated_fields) - set(expected_fabricated)))
            }
        }
    return None

@invariant(id="aoi_info_matches_input",
           description="The aoi_info block should correctly reflect the input payload.",
           severity="high", category="correctness")
def _(run):
    if run.output is None:
        return None

    payload = run.case.payload
    aoi_info = get_component(run.output, "aoi_info")
    if not aoi_info:
        return {"observed": "aoi_info block is missing from the output.", "evidence": {}}

    expected = {
        "latitude": round(payload["latitude"], 4),
        "longitude": round(payload["longitude"], 4),
        "area_acres": payload["area_acres"],
        "crop": payload["crop_type"],
    }

    mismatches = {}
    for key, val in expected.items():
        observed = aoi_info.get(key)
        if observed != val:
            mismatches[key] = {"expected": val, "observed": observed}

    # Special handling for location_name, which can be derived
    if "location_name" in payload and payload["location_name"]:
        if aoi_info.get("location") != payload["location_name"]:
            mismatches["location"] = {"expected": payload["location_name"], "observed": aoi_info.get("location")}

    if mismatches:
        return {
            "observed": "aoi_info block does not match input payload.",
            "evidence": {"mismatches": mismatches}
        }
    return None

@invariant(id="soil_ph_in_valid_range",
           description="Soil pH must be within the valid physical range of 0-14.",
           severity="medium", category="correctness")
def _(run):
    if run.output is None:
        return None
    ph = get_component(run.output, "components.soil_management.ph")
    if ph is not None and not (0 <= ph <= 14):
        return {
            "observed": f"Soil pH is outside the valid 0-14 range.",
            "evidence": {"ph": ph}
        }
    return None

@invariant(id="soil_texture_sums_to_100",
           description="Soil texture percentages (sand, silt, clay) must sum to approximately 100%.",
           severity="medium", category="correctness")
def _(run):
    if run.output is None:
        return None
    soil = get_component(run.output, "components.soil_management")
    if not soil:
        return None

    sand = soil.get("sand_percent")
    silt = soil.get("silt_percent")
    clay = soil.get("clay_percent")

    if any(p is None for p in [sand, silt, clay]):
        return None

    total = sand + silt + clay
    if not math.isclose(total, 100.0, abs_tol=1.0):
        return {
            "observed": "Soil texture percentages do not sum to 100%.",
            "evidence": {"sand": sand, "silt": silt, "clay": clay, "total": total}
        }
    return None

@invariant(id="irrigation_values_are_non_negative",
           description="Irrigation water amounts (ET0, ETc, total) must be non-negative.",
           severity="high", category="correctness")
def _(run):
    if run.output is None:
        return None
    irrigation = get_component(run.output, "components.irrigation_schedule")
    if not irrigation:
        return None

    negative_values = {}
    for key in ["et0_mm_per_day", "etc_mm_per_day", "total_water_mm"]:
        value = irrigation.get(key)
        if value is not None and value < 0:
            negative_values[key] = value

    if negative_values:
        return {
            "observed": "Irrigation values cannot be negative.",
            "evidence": {"negative_values": negative_values}
        }
    return None

@invariant(id="etc_kc_consistency",
           description="ETc must be calculated as ET0 * Kc.",
           severity="medium", category="correctness")
def _(run):
    if run.output is None:
        return None
    irrigation = get_component(run.output, "components.irrigation_schedule")
    if not irrigation:
        return None

    et0 = irrigation.get("et0_mm_per_day")
    kc = irrigation.get("kc")
    etc = irrigation.get("etc_mm_per_day")

    if any(v is None for v in [et0, kc, etc]):
        return None

    expected_etc = et0 * kc
    if not math.isclose(etc, expected_etc, rel_tol=0.05):
        return {
            "observed": "Reported ETc is not consistent with ET0 * Kc.",
            "evidence": {"et0": et0, "kc": kc, "reported_etc": etc, "expected_etc": expected_etc}
        }
    return None

@invariant(id="nutrient_gap_calculation_is_correct",
           description="Nutrient gap must be max(0, target_upper - current).",
           severity="high", category="correctness")
def _(run):
    if run.output is None:
        return None
    nutrients = get_component(run.output, "components.fertilizer_management.nutrient_requirements")
    if not nutrients:
        return None

    errors = {}
    for name, data in nutrients.items():
        current = data.get("current_kg_per_acre")
        gap = data.get("gap_kg_per_acre")
        target_str = data.get("target_kg_per_acre")

        if any(x is None for x in [current, gap, target_str]) or not isinstance(target_str, str):
            continue

        try:
            target_upper = float(target_str.split('-')[1])
        except (ValueError, IndexError):
            errors[name] = f"Malformed target range: {target_str}"
            continue

        expected_gap = max(0, target_upper - current)
        if not math.isclose(gap, expected_gap, rel_tol=1e-2, abs_tol=0.01):
            errors[name] = {"observed_gap": gap, "expected_gap": expected_gap, "current": current, "target_upper": target_upper}

    if errors:
        return {
            "observed": "Nutrient gap calculation is incorrect for one or more nutrients.",
            "evidence": errors
        }
    return None

@invariant(id="pest_risk_summary_is_consistent",
           description="Pest/disease/weed summary counts must match the detailed lists.",
           severity="medium", category="contract")
def _(run):
    if run.output is None:
        return None
    summary = get_component(run.output, "components.pest_disease_weed.summary")
    pests_diseases = get_component(run.output, "components.pest_disease_weed.pests_diseases") or []
    weeds = get_component(run.output, "components.pest_disease_weed.weeds") or []
    all_risks = pests_diseases + weeds

    if not summary:
        return None

    observed_counts = {
        "high": sum(1 for item in all_risks if item.get("risk_level") == "high"),
        "moderate": sum(1 for item in all_risks if item.get("risk_level") == "moderate"),
        "low": sum(1 for item in all_risks if item.get("risk_level") == "low"),
    }

    expected_counts = {
        "high": summary.get("high_risk_count"),
        "moderate": summary.get("moderate_risk_count"),
        "low": summary.get("low_risk_count"),
    }

    mismatches = {}
    for level in ["high", "moderate", "low"]:
        if observed_counts[level] != expected_counts[level]:
            mismatches[f"{level}_risk_count"] = {"expected": expected_counts[level], "observed": observed_counts[level]}

    if mismatches:
        return {
            "observed": "Pest risk summary counts do not match the number of items in the detailed lists.",
            "evidence": mismatches
        }
    return None

@invariant(id="nutrient_status_is_consistent_with_range",
           description="Nutrient status (e.g., 'optimal') must be consistent with the current value vs. its target range.",
           severity="medium", category="correctness")
def _(run):
    if run.output is None:
        return None
    nutrients = get_component(run.output, "components.fertilizer_management.nutrient_requirements")
    if not nutrients:
        return None

    errors = {}
    for name, data in nutrients.items():
        current = data.get("current_kg_per_acre")
        status = data.get("status")
        target_str = data.get("target_kg_per_acre")

        if any(x is None for x in [current, status, target_str]) or not isinstance(target_str, str):
            continue

        try:
            target_low, target_high = map(float, target_str.split('-'))
        except (ValueError, IndexError):
            continue

        is_low = current < target_low
        is_optimal = target_low <= current <= target_high

        if is_low and status in ["optimal", "high", "excess"]:
            errors[name] = f"Status is '{status}' but current value {current} is below target range {target_str}."
        if is_optimal and status not in ["optimal", "moderate"]: # Allow moderate in optimal range
            errors[name] = f"Status is '{status}' but current value {current} is within target range {target_str}."

    if errors:
        return {
            "observed": "Nutrient status is inconsistent with its value relative to the target range.",
            "evidence": errors
        }
    return None

# Metamorphic checks

@metamorphic(id="location_change_affects_outputs",
            description="Changing the location (lat/lon) should change location-dependent outputs like weather, soil, and terrain.",
            transform=lambda p: {**p, "latitude": p["latitude"] + 0.5, "longitude": p["longitude"] + 0.5},
            severity="critical", category="correctness")
def _(base, variant):
    if base.output is None or variant.output is None:
        return None

    identical_fields = {}
    
    # Check terrain
    base_elev = get_component(base.output, "components.irrigation_schedule.terrain.elevation_m")
    var_elev = get_component(variant.output, "components.irrigation_schedule.terrain.elevation_m")
    if base_elev is not None and base_elev == var_elev:
        identical_fields["elevation_m"] = base_elev

    # Check weather-derived value
    base_et0 = get_component(base.output, "components.irrigation_schedule.et0_mm_per_day")
    var_et0 = get_component(variant.output, "components.irrigation_schedule.et0_mm_per_day")
    if base_et0 is not None and base_et0 == var_et0:
        identical_fields["et0_mm_per_day"] = base_et0

    # Check soil-derived value
    base_ph = get_component(base.output, "components.soil_management.ph")
    var_ph = get_component(variant.output, "components.soil_management.ph")
    if base_ph is not None and base_ph == var_ph:
        identical_fields["soil_ph"] = base_ph

    # If all checked fields are identical, it's a strong sign of a bug.
    if len(identical_fields) >= 3:
        return {
            "observed": "Changing location had no effect on key location-dependent outputs, suggesting hardcoded data.",
            "evidence": {
                "identical_fields": identical_fields,
                "base_location": {"lat": base.case.payload["latitude"], "lon": base.case.payload["longitude"]},
                "variant_location": {"lat": variant.case.payload["latitude"], "lon": variant.case.payload["longitude"]}
            }
        }
    return None

@metamorphic(id="fertilizer_current_levels_depend_on_location",
            description="Current soil nutrient levels should change with location, not be hardcoded constants.",
            transform=lambda p: {**p, "latitude": p["latitude"] - 0.5, "longitude": p["longitude"] - 0.5},
            severity="critical", category="fabrication")
def _(base, variant):
    if base.output is None or variant.output is None:
        return None

    base_nutrients = get_component(base.output, "components.fertilizer_management.nutrient_requirements")
    var_nutrients = get_component(variant.output, "components.fertilizer_management.nutrient_requirements")

    if not base_nutrients or not var_nutrients:
        return None

    identical_currents = {}
    for nutrient in ["N", "P", "K", "S", "Zn"]:
        base_val = get_component(base_nutrients, f"{nutrient}.current_kg_per_acre")
        var_val = get_component(var_nutrients, f"{nutrient}.current_kg_per_acre")
        if base_val is not None and base_val == var_val:
            identical_currents[nutrient] = base_val
    
    # If all nutrient levels are identical, it's a bug.
    if len(identical_currents) == len(base_nutrients):
        return {
            "observed": "Current soil nutrient levels are identical for different locations, indicating they are hardcoded.",
            "evidence": {
                "hardcoded_values": identical_currents,
                "base_location": {"lat": base.case.payload["latitude"], "lon": base.case.payload["longitude"]},
                "variant_location": {"lat": variant.case.payload["latitude"], "lon": variant.case.payload["longitude"]}
            }
        }
    return None

@metamorphic(id="crop_change_affects_recommendations",
            description="Changing the crop type should change crop-specific outputs like yield potential and nutrient targets.",
            transform=lambda p: {**p, "crop_type": "wheat" if p.get("crop_type") != "wheat" else "apple"},
            severity="high", category="correctness")
def _(base, variant):
    if base.output is None or variant.output is None:
        return None

    identical_fields = {}

    base_yield = get_component(base.output, "components.growth_yield.yield_potential_kg_per_acre")
    var_yield = get_component(variant.output, "components.growth_yield.yield_potential_kg_per_acre")
    if base_yield is not None and base_yield == var_yield:
        identical_fields["yield_potential_kg_per_acre"] = base_yield

    base_n_target = get_component(base.output, "components.fertilizer_management.nutrient_requirements.N.target_kg_per_acre")
    var_n_target = get_component(variant.output, "components.fertilizer_management.nutrient_requirements.N.target_kg_per_acre")
    if base_n_target is not None and base_n_target == var_n_target:
        identical_fields["N_target_kg_per_acre"] = base_n_target

    if len(identical_fields) >= 2:
        return {
            "observed": "Changing the crop type did not affect key crop-specific outputs.",
            "evidence": {
                "identical_fields": identical_fields,
                "base_crop": base.case.payload["crop_type"],
                "variant_crop": variant.case.payload["crop_type"]
            }
        }
    return None

@metamorphic(id="area_scales_total_yield",
            description="Changing the area in acres should scale total yield linearly, but not per-acre values.",
            transform=lambda p: {**p, "area_acres": p["area_acres"] * 2},
            severity="medium", category="correctness")
def _(base, variant):
    if base.output is None or variant.output is None:
        return None

    base_area = base.case.payload["area_acres"]
    var_area = variant.case.payload["area_acres"]
    scale_factor = var_area / base_area

    errors = {}

    # Check total yield
    base_total_yield = get_component(base.output, "components.growth_yield.total_yield_kg")
    var_total_yield = get_component(variant.output, "components.growth_yield.total_yield_kg")
    if base_total_yield is not None and var_total_yield is not None:
        expected_var_yield = base_total_yield * scale_factor
        if not math.isclose(var_total_yield, expected_var_yield, rel_tol=1e-5):
            errors["total_yield_kg"] = {"expected": expected_var_yield, "observed": var_total_yield}

    # Check per-acre yield (should not change)
    base_per_acre_yield = get_component(base.output, "components.growth_yield.yield_per_acre_kg")
    var_per_acre_yield = get_component(variant.output, "components.growth_yield.yield_per_acre_kg")
    if base_per_acre_yield is not None and var_per_acre_yield is not None:
        if not math.isclose(base_per_acre_yield, var_per_acre_yield, rel_tol=1e-5):
            errors["yield_per_acre_kg"] = {"expected_to_be_constant": base_per_acre_yield, "observed": var_per_acre_yield}

    if errors:
        return {
            "observed": "Scaling area did not correctly scale yield outputs.",
            "evidence": {
                "errors": errors,
                "base_area": base_area,
                "variant_area": var_area
            }
        }
    return None