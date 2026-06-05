# Intent: jeevn agricultural advisory pipeline

Given a farm location (`latitude`, `longitude`), an area in acres, a crop type,
and optionally a sowing date and NDVI timeseries, produce a **correct,
internally consistent agricultural advisory report**. The report includes field
maps, an irrigation schedule, soil management, growth/yield projection, pest /
disease / weed risk, and a fertilizer schedule.

## Core correctness expectations

1. **Real vs. fabricated data must be disclosed.** Every external input comes
   from a live source (weather, soil, terrain, satellite) with a fabricated
   fallback default when the source is unavailable. **Any value that is a
   fabricated default — or derived from a hardcoded placeholder rather than the
   actual fetched data — MUST be listed in
   `report["data_quality"]["fabricated_fields"]`.** Silent, undisclosed
   fabrication is the most serious failure: a farmer would act on numbers that
   are made up without knowing it.

2. **Inputs must actually flow into the outputs.** A value that the pipeline
   fetches (e.g. solar radiation, soil pH, current soil nutrient levels) must
   influence the calculation it feeds. If a fetched input is ignored and a
   constant is used instead, that is a correctness bug and must be disclosed.

3. **Numbers must be in valid physical ranges and consistent:**
   - soil `ph` in 0–14; texture `sand_percent + silt_percent + clay_percent`
     ≈ 100; percentages in 0–100.
   - `et0_mm_per_day`, `etc_mm_per_day`, `total_water_mm` ≥ 0 and finite;
     `etc = et0 × kc`.
   - nutrient `gap = max(0, target − current)`; the reported `target` range and
     `status` must be consistent with `current` and `target`.
   - yield/loss percentages within 0–100; pest risk scores within 0–100.

4. **The report must follow from its inputs.** The summary, recommendations,
   and statuses must be consistent with the component values they describe
   (e.g. a "critical" status implies the underlying number crossed its
   threshold).

## Known-relevant detail
The fertilizer step computes nutrient gaps from "current soil levels". The
intent is that these come from the actual fetched/soil-test data for the AOI —
not a fixed constant reused for every location.
