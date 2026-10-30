"""Pre-flight check of macOS machine state before timing runs."""

import subprocess


def _pmset(*args: str) -> str:
    return subprocess.run(["pmset", "-g", *args], capture_output=True, text=True).stdout


def machine_state() -> dict:
    general = _pmset()
    therm = _pmset("therm")
    return {
        "on_ac_power": "AC Power" in _pmset("batt"),
        "low_power_mode": any(
            l.split()[-1] == "1" for l in general.splitlines() if "lowpowermode" in l
        ),
        "thermal_limited": any(
            "Speed_Limit" in l and not l.strip().endswith("100") for l in therm.splitlines()
        ),
    }


def preflight_warnings() -> list[str]:
    s = machine_state()
    warnings = []
    if not s["on_ac_power"]:
        warnings.append("on battery: plug in for stable timings")
    if s["low_power_mode"]:
        warnings.append("Low Power Mode is on: turn it off")
    if s["thermal_limited"]:
        warnings.append("CPU is thermally limited: let the machine cool down")
    return warnings
