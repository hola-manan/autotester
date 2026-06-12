import datetime
from hypothesis import strategies as st

# Helper strategies for common types and ranges
_float_0_100 = st.floats(min_value=0.0, max_value=100.0, allow_nan=False, allow_infinity=False)
_float_ge_0 = st.floats(min_value=0.0, max_value=1000.0, allow_nan=False, allow_infinity=False) # General positive float, capped at 1000 for reasonable agricultural values
_date_str = st.dates(min_value=datetime.date(2000, 1, 1), max_value=datetime.date(2030, 12, 31)).map(lambda d: d.isoformat())
# Short text for names, allowing letters, numbers, punctuation, and symbols (ASCII only)
_short_text = st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=('L', 'N', 'P', 'S', 'Z'), max_codepoint=127))

# Strategy for optional values: can be None, or the actual strategy's output
_optional_value = lambda s: st.one_of(st.none(), s)

# Strategy for optional dictionaries: can be None, an empty dictionary, or a dictionary with content
_optional_dict = lambda s: st.one_of(st.none(), st.just({}), s)

def _soil_properties_strategy_content():
    """Generates the content for the 'properties' dictionary within 'aoi_data.soil'."""
    return st.fixed_dictionaries({
        "ph": _optional_value(st.floats(min_value=0.0, max_value=14.0, allow_nan=False, allow_infinity=False)),
        "ec": _optional_value(st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False)), # Electrical conductivity
        "organic_carbon_percent": _optional_value(_float_0_100),
        "texture": _optional_value(st.sampled_from(['loam', 'sandy loam', 'clay loam', 'sand', 'silt', 'clay', 'unknown'])),
        "water_holding_capacity": _optional_value(_float_0_100), # Percentage
        "infiltration_rate": _optional_value(_float_ge_0), # e.g., mm/hr
        # Add components for texture sum check
        "sand_percent": _optional_value(_float_0_100),
        "silt_percent": _optional_value(_float_0_100),
        "clay_percent": _optional_value(_float_0_100),
    })

def _weather_daily_strategy_content():
    """Generates a list of daily weather data dictionaries."""
    return st.lists(
        st.fixed_dictionaries({
            "date": _date_str,
            "temp_mean": st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False),
            "temp_max": st.floats(min_value=-40.0, max_value=60.0, allow_nan=False, allow_infinity=False),
            "rainfall": _float_ge_0, # mm
        }).filter(lambda d: d["temp_max"] >= d["temp_mean"]), # Ensure max temp is not less than mean
        max_size=10 # Cap list size
    )

def _nutrient_levels_strategy_content():
    """Generates a dictionary of current soil nutrient levels."""
    return st.fixed_dictionaries({
        "N": _optional_value(_float_ge_0), # kg/acre
        "P": _optional_value(_float_ge_0),
        "K": _optional_value(_float_ge_0),
        "S": _optional_value(_float_ge_0),
        "Zn": _optional_value(_float_ge_0),
    })

def _aoi_data_strategy_content():
    """Generates the content for the 'aoi_data' dictionary."""
    return st.fixed_dictionaries({
        "soil": _optional_dict(st.fixed_dictionaries({
            "properties": _optional_dict(_soil_properties_strategy_content()),
        })),
        "weather": _optional_dict(st.fixed_dictionaries({
            "daily": _optional_value(_weather_daily_strategy_content()), # daily can be None or an empty list
        })),
        "current_soil_nutrient_levels": _optional_dict(_nutrient_levels_strategy_content()),
    })

def _ndvi_timeseries_strategy_content():
    """Generates a list of NDVI timeseries data points."""
    return st.lists(
        st.fixed_dictionaries({
            "date": _date_str,
            "value": st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
        }),
        max_size=10 # Cap list size
    )

def payload_strategy():
    """Returns a Hypothesis strategy producing FULL input payload dicts."""
    return st.fixed_dictionaries({
        "lat": st.floats(min_value=-90.0, max_value=90.0, allow_nan=False, allow_infinity=False),
        "lon": st.floats(min_value=-180.0, max_value=180.0, allow_nan=False, allow_infinity=False),
        "area_acres": st.floats(min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False), # Area must be positive
        "crop_name": _short_text,
        "location_name": _short_text,
        "sowing_date": _optional_value(_date_str),
        "ndvi_timeseries": _optional_value(_ndvi_timeseries_strategy_content()),
        "aoi_data": _optional_dict(_aoi_data_strategy_content()),
    })

SEEDS = [
    # Seed 1: hardcoded_nutrient_current_levels - aoi_data with specific nutrient levels
    {
        "lat": 31.1,
        "lon": 77.17,
        "area_acres": 1.0,
        "crop_name": "wheat",
        "location_name": "TestFarm1",
        "aoi_data": {
            "current_soil_nutrient_levels": {
                "N": 10.0, "P": 5.0, "K": 20.0, "S": 2.0, "Zn": 0.5
            }
        }
    },
    # Seed 2: hardcoded_nutrient_current_levels - aoi_data with missing nutrient levels
    {
        "lat": 30.9,
        "lon": 75.85,
        "area_acres": 2.5,
        "crop_name": "corn",
        "location_name": "TestFarm2",
        "aoi_data": {
            "current_soil_nutrient_levels": {} # Empty nutrient levels dict
        }
    },
    # Seed 3: silent_fabrication_soil_properties - aoi_data with empty soil properties
    {
        "lat": 33.0,
        "lon": 78.0,
        "area_acres": 10.0,
        "crop_name": "soybean",
        "location_name": "SoilTestFarm",
        "aoi_data": {
            "soil": {
                "properties": {} # Empty properties dict
            }
        }
    },
    # Seed 4: silent_fabrication_irrigation_soil_params - full soil data, but irrigation params are not provided
    {
        "lat": 34.0,
        "lon": 79.0,
        "area_acres": 20.0,
        "crop_name": "cotton",
        "location_name": "IrrigationTestFarm",
        "aoi_data": {
            "soil": {
                "properties": {
                    "ph": 6.5,
                    "ec": 1.2,
                    "organic_carbon_percent": 1.0,
                    "texture": "sandy loam",
                    "water_holding_capacity": 25.0,
                    "infiltration_rate": 15.0,
                    "sand_percent": 60.0, "silt_percent": 20.0, "clay_percent": 20.0
                }
            }
        }
    },
    # Seed 5: silent_fabrication_weather_defaults - aoi_data with empty daily weather list
    {
        "lat": 35.0,
        "lon": 80.0,
        "area_acres": 5.0,
        "crop_name": "potato",
        "location_name": "WeatherTestFarm1",
        "aoi_data": {
            "weather": {
                "daily": [] # Empty daily weather list
            }
        }
    },
    # Seed 6: silent_fabrication_weather_defaults - aoi_data with missing 'daily' key in weather
    {
        "lat": 36.0,
        "lon": 81.0,
        "area_acres": 7.5,
        "crop_name": "tomato",
        "location_name": "WeatherTestFarm2",
        "aoi_data": {
            "weather": {} # Missing 'daily' key
        }
    },
    # Seed 7: silent_fabrication_days_since_sowing - sowing_date is explicitly None
    {
        "lat": 37.0,
        "lon": 82.0,
        "area_acres": 3.0,
        "crop_name": "cabbage",
        "location_name": "SowingDateTestFarm",
        "sowing_date": None
    },
    # Seed 8: Example payload with all optional fields as None to match minimal example structure
    {
        "lat": 31.1,
        "lon": 77.17,
        "area_acres": 1.0,
        "crop_name": "apple",
        "location_name": "Shimla",
        "sowing_date": None,
        "ndvi_timeseries": None,
        "aoi_data": None
    },
]