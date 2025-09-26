from pathlib import Path
import json
from typing import Dict, Literal, Union

IZOD_TEMP = Literal["-20C", "23C"]

def _load_targets_file(path: Union[str, Path]) -> dict:
    with open(path, "r") as f:
        return json.load(f)

def get_targets(
    path: Union[str, Path] = "data/target_properties.json",
    izod_temp: IZOD_TEMP = "-20C",
) -> Dict[str, float]:
    """
    Returns canonical targets keyed to our internal property names used across the pipeline.
    """
    raw = _load_targets_file(path)

    # map raw file -> internal keys
    # Expect raw to have both Izod_23C and Izod_-20C or similar keys.
    izod_key = "Izod_-20C_kJm2" if izod_temp == "-20C" else "Izod_23C_kJm2"

    return {
        "MFI_g10min": float(raw["MFI_g10min"]),
        "sigma_y_MPa": float(raw["sigma_y_MPa"]),
        "E_GPa": float(raw["E_GPa"]),                 # stored as GPa in your CSVs
        "Izod_m20_kJm2": float(raw[izod_key]),        # ensures the -20C vs 23C mismatch never happens again
        "HDT_C": float(raw["DTUL_66psi_C"]),          # align naming across pipeline
    }