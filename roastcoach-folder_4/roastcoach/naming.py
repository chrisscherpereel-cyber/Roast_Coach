"""Human names for the measures the app talks about."""

METRIC_LABELS = {
    "totalRoastMinutes": "total roast time (min)",
    "firstCrackTime": "first crack (min)",
    "yellowPointTime": "yellowing (min)",
    "turningPointTime": "turning point (min)",
    "developmentTime": "development (min)",
    "development_percent": "development share (%)",
    "drying_percent": "drying share (%)",
    "maillard_percent": "Maillard share (%)",
    "drumDropTemperature": "drop temperature (°C)",
    "drumChargeTemperature": "charge temperature (°C)",
    "firstCrackTemp": "temperature at first crack (°C)",
    "ibtsTurningPointTemp": "temperature at turning point (°C)",
    "tempRiseAfterFirstCrack": "climb after first crack (°C)",
    "weightLossPercent": "weight loss (%)",
    "peakROR": "peak rate of rise (°C/min)",
    "rorAtFirstCrack": "rate of rise at first crack (°C/min)",
    "rorAtDrop": "rate of rise at drop (°C/min)",
    "avgRoRDrying": "average rate of rise, drying (°C/min)",
    "avgRoRMaillard": "average rate of rise, Maillard (°C/min)",
    "avgRoRDevelopment": "average rate of rise, development (°C/min)",
    "powerDrying": "power during drying",
    "powerMaillard": "power during Maillard",
    "powerDevelopment": "power during development",
    "fanDevelopment": "fan during development",
    "flagRoRFlick": "the late rate-of-rise flick",
    "flagRoRCrash": "the rate-of-rise crash",
}


def label_for(metric: str) -> str:
    """A measure's name as a person would say it."""
    return METRIC_LABELS.get(metric, str(metric or "").replace("_", " "))
