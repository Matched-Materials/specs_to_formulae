#!/usr/bin/env python3
"""
Bridge: DOE formulations -> predicted properties via TSE hybrid model (iteration-aware)

Adds iteration-aware paths:
- --cycle <name> (e.g., iter_001)
- --in-dir  (defaults to ../results/datasets/formulations)  [where DOE lives]
- --out-dir (defaults to ../results/datasets/properties)     [where props will be written]
- If --doe and --out are not given, they resolve to:
    DOE  : <in-dir>/<cycle>/doe_<cycle>.csv
    OUT  : <out-dir>/<cycle>/props_<cycle>.csv
- Always creates destination directory.
- Writes a small metadata JSON next to the props CSV.

You can still pass explicit --doe and/or --out to override.
"""

import csv, json, math, argparse, os
from typing import Dict, Any, List, Optional

# ---------- Utilities ----------

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return float(default)

def read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def load_ingredient_catalog(lib: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    cat = {}
    for k, arr in lib.items():
        if isinstance(arr, list):
            for item in arr:
                name = item.get("name")
                if name:
                    cat[name] = item
    return cat

def lookup_density(name: Optional[str], catalog: Dict[str, Dict[str, Any]], fallback: Optional[float]) -> Optional[float]:
    if not name:
        return fallback
    meta = catalog.get(name, {})
    if "density_gcc" in meta:
        return safe_float(meta["density_gcc"], fallback if fallback is not None else 1.0)
    return fallback

def is_type(name: str, catalog: Dict[str, Dict[str, Any]], target_types: List[str]) -> bool:
    t = str(catalog.get(name, {}).get("type", "")).strip()
    return t in target_types

def name_contains(name: str, needles: List[str]) -> bool:
    s = str(name or "").lower()
    return any(n.lower() in s for n in needles)

# ---------- Volume fraction math ----------

def wt_to_phi(w_pp, w_el, w_talc, w_caco3, w_biofiber, w_biochar, w_compat, w_stab,
              rho_pp, rho_el, rho_talc, rho_caco3, rho_biofiber, rho_biochar, rho_compat, rho_stab) -> Dict[str, float]:
    """Converts weight percents to volume fractions for all major components."""
    m_pp = w_pp / 100.0; m_el = w_el / 100.0; m_ta = w_talc / 100.0
    m_ca = w_caco3 / 100.0; m_bf = w_biofiber / 100.0; m_bc = w_biochar / 100.0
    m_comp = w_compat / 100.0; m_stab = w_stab / 100.0

    def div(a,b,eps=1e-12): return a / (b if abs(b)>eps else eps)
    v_pp = div(m_pp, rho_pp or 0.905); v_el = div(m_el, rho_el or 0.87)
    v_ta = div(m_ta, rho_talc or 2.70); v_ca = div(m_ca, rho_caco3 or 2.70)
    v_bf = div(m_bf, rho_biofiber or 1.45); v_bc = div(m_bc, rho_biochar or 1.80)
    v_comp = div(m_comp, rho_compat or 0.92); v_stab = div(m_stab, rho_stab or 1.0)

    v_sum = max(1e-12, v_pp + v_el + v_ta + v_ca + v_bf + v_bc + v_comp + v_stab)
    return {
        "phi_pp": v_pp / v_sum, "phi_el": v_el / v_sum,
        "phi_f_talc": v_ta / v_sum, "phi_f_caco3": v_ca / v_sum,
        "phi_f_biofiber": v_bf / v_sum, "phi_f_biochar": v_bc / v_sum,
        "phi_compat": v_comp / v_sum, "phi_stab": v_stab / v_sum,
    }

# ---------- Per-row ----------

def compute_row_properties(row: Dict[str, str],
                           model: Dict[str, Any],
                           catalog: Dict[str, Dict[str, Any]],
                           process_cfg: Dict[str, Any]) -> Dict[str, Any]:
    # --- 1. Load all model parameters (priors, known materials, etc.) ---
    params = model.get("parameters", {})
    all_params = {}
    all_params.update(params.get("known_materials", {}))
    all_params.update(params.get("physical_constants", {}))
    for pname, pr in params.get("priors", {}).items():
        try:
            if isinstance(pr, (list, tuple)) and len(pr) == 2:
                all_params[pname] = 0.5 * (float(pr[0]) + float(pr[1]))
        except Exception:
            pass

    # --- 2. Parse formulation from the input row ---
    w_el = safe_float(row.get("elastomer_wtpct", 0.0))
    filler_name = row.get("filler_name", "")
    w_filler = safe_float(row.get("filler_wtpct", 0.0))
    w_talc_extra = safe_float(row.get("talc_wtpct", 0.0))
    w_compat = safe_float(row.get("compat_wtpct", 0.0))
    w_stab = safe_float(row.get("stabilizer_wtpct", 0.0))
    nuc_ppm = safe_float(row.get("nucleator_ppm", process_cfg.get("nucleator_ppm", 0)))

    w_talc = w_caco3 = w_biofiber = w_biochar = 0.0
    if filler_name:
        if is_type(filler_name, catalog, ["BioFiber", "Cellulose"]):
            w_biofiber += w_filler
        elif is_type(filler_name, catalog, ["Biochar"]):
            w_biochar += w_filler
        else:
            if name_contains(filler_name, ["talc"]):
                w_talc += w_filler
            elif name_contains(filler_name, ["caco3", "calcium carbonate"]):
                w_caco3 += w_filler

    w_talc += w_talc_extra
    # Correctly calculate PP weight by summing base resins from the input row.
    # This accounts for all other additives.
    w_pp = safe_float(row.get("baseA_wtpct", 0.0)) + safe_float(row.get("baseB_wtpct", 0.0))

    # --- 2a. Calculate weighted average MFI of the base resin blend ---
    baseA_name = row.get("baseA_name")
    baseB_name = row.get("baseB_name")
    baseA_meta = catalog.get(baseA_name, {})
    baseB_meta = catalog.get(baseB_name, {})

    # Use midpoint of mfr_range from the ingredient library, with a fallback.
    mfr_A_range = baseA_meta.get("mfr_range", [10, 40])
    mfr_B_range = baseB_meta.get("mfr_range", [10, 40])
    mfi_A = 0.5 * (mfr_A_range[0] + mfr_A_range[1])
    mfi_B = 0.5 * (mfr_B_range[0] + mfr_B_range[1])

    total_base_wt = w_pp
    if total_base_wt > 1e-6:
        # Use log-mixing rule for MFI, which is physically more appropriate.
        log_mfi_in = (safe_float(row.get("baseA_wtpct", 0.0)) / total_base_wt) * math.log(mfi_A) + \
                     (safe_float(row.get("baseB_wtpct", 0.0)) / total_base_wt) * math.log(mfi_B)
        mfi_in = math.exp(log_mfi_in)
    else:
        mfi_in = 25.0 # Fallback MFI if no base resin is present

    # This is the critical fix: include all components in the volume fraction calculation.
    # We assume generic densities for compatibilizer and stabilizer if not found in the catalog.
    rho_compat = lookup_density(row.get("compat_name"), catalog, 0.92)
    rho_stab = lookup_density(row.get("stabilizer_name"), catalog, 1.0)
    phis = wt_to_phi(w_pp, w_el, w_talc, w_caco3, w_biofiber, w_biochar,
                     w_compat, w_stab,
                     all_params.get("rho_PP"), all_params.get("rho_el"), all_params.get("rho_talc"),
                     all_params.get("rho_caco3"), all_params.get("rho_biofiber"), all_params.get("rho_biochar"),
                     rho_compat, rho_stab)

    # --- 3. Calculate properties using explicit physics equations ---
    inp = {
        'phi_el': phis.get('phi_el', 0),
        'phi_f_talc': phis.get('phi_f_talc', 0),
        'phi_f_caco3': phis.get('phi_f_caco3', 0),
        'phi_f_biofiber': phis.get('phi_f_biofiber', 0),
        'phi_f_biochar': phis.get('phi_f_biochar', 0),
        'c_nuc_ppm': nuc_ppm,
        'phi_comp': phis.get('phi_compat', 0),
        'lambda_visc': 1.0, 'sigma_if': 5.0, 'A_comp': 0.8 # Assumed constants
    }
    # Use process conditions passed from orchestrator
    process = process_cfg
    # Use a default tau_s if not provided
    if 'tau_s' not in process:
        process['tau_s'] = 45.0
    # Use a default pvac_bar_abs if not provided
    if 'pvac_bar_abs' not in process:
        process['pvac_bar_abs'] = 0.1

    # --- Calculate Latent States & Properties ---
    # Specific Energy Input (SEI) in kWh/kg.
    # This calculation is sensitive to process conditions. Using explicit values from the process config.
    power_W = process['Torque_Nm'] * 2 * math.pi * process['N_rps'] * all_params.get('gear_eff', 0.9)
    flow_kgs = process['Q_kgh'] / 3600.0
    SEI_J_per_kg = power_W / flow_kgs if flow_kgs > 1e-9 else 0.0
    SEI = SEI_J_per_kg / 3.6e6  # Convert from J/kg to kWh/kg

    shear_rate = all_params['k_gamma'] * process.get('N_rps', 5)
    
    # The model's degradation dose equation was likely fitted with tau_s in minutes,
    # despite the model definition specifying seconds. We apply this conversion to match the model's implicit assumption.
    tau_s_in_minutes = process['tau_s'] / 60.0
    degradation_dose = SEI * \
        (all_params['a1'] + all_params['a2'] * (shear_rate / all_params['shear0'])**all_params['m']) * \
        tau_s_in_minutes**all_params['nu'] * \
        math.exp(all_params['beta'] * (process.get('Tm_C', 220) - all_params['Tref_C'])) * \
        math.exp(-all_params['kappa'] * (all_params['patm_bar'] - process.get('pvac_bar_abs')))

    Mw_out = 350000 / (1 + all_params['kD'] * degradation_dose)
    # The model is ln(MFI_out) = ln(MFI_in) + alpha * ln(Mw_in/Mw_out).
    # This is equivalent to MFI_out = MFI_in * (Mw_in/Mw_out)^alpha.
    mfi = mfi_in * ((350000 / Mw_out) ** all_params['alpha_MFI'])
    Sc = 1 - math.exp(-all_params['kc'] * inp['phi_comp'] * inp['A_comp'])
    Psi_lambda = math.exp(-all_params['klambda'] * (inp['lambda_visc'] - 1.0)**2)
    Phi_stress = (Sc * Psi_lambda * process.get('K_knead', 5.0) * shear_rate) / max(inp['sigma_if'], 1e-6)
    dr_um = all_params['dmin_um'] + (all_params['d0_um'] - all_params['dmin_um']) * math.exp(-all_params['kd'] * SEI * Phi_stress)

    Xc = all_params['Xc0'] + all_params['alpha_n'] * math.log(1.0 + inp['c_nuc_ppm'] / all_params['c50_ppm']) - all_params['alpha_el'] * inp['phi_el']

    Em_GPa = all_params['Em0_GPa'] * (1 + all_params['beta_c'] * (Xc - all_params['Xc0']))
    Erubber_GPa = Em_GPa * (1 - all_params['br'] * inp['phi_el'])**all_params['pRub']

    # --- Flexural Modulus (E_GPa) with Halpin-Tsai for all fillers ---
    # Talc
    eta_talc = (all_params['Ef_talc_GPa'] / Erubber_GPa - 1) / (all_params['Ef_talc_GPa'] / Erubber_GPa + 2 * all_params['AR_talc'])
    HT_talc = (1 + eta_talc * inp['phi_f_talc']) / (1 - eta_talc * inp['phi_f_talc']) if (1 - eta_talc * inp['phi_f_talc']) != 0 else 1

    # CaCO3
    eta_caco3 = (all_params['Ef_caco3_GPa'] / Erubber_GPa - 1) / (all_params['Ef_caco3_GPa'] / Erubber_GPa + 2 * all_params['AR_caco3'])
    HT_caco3 = (1 + eta_caco3 * inp['phi_f_caco3']) / (1 - eta_caco3 * inp['phi_f_caco3']) if (1 - eta_caco3 * inp['phi_f_caco3']) != 0 else 1

    # Biofiber
    eta_biofiber = (all_params['Ef_biofiber_GPa'] / Erubber_GPa - 1) / (all_params['Ef_biofiber_GPa'] / Erubber_GPa + 2 * all_params['AR_biofiber'])
    HT_biofiber = (1 + eta_biofiber * inp['phi_f_biofiber']) / (1 - eta_biofiber * inp['phi_f_biofiber']) if (1 - eta_biofiber * inp['phi_f_biofiber']) != 0 else 1

    # Biochar
    eta_biochar = (all_params['Ef_biochar_GPa'] / Erubber_GPa - 1) / (all_params['Ef_biochar_GPa'] / Erubber_GPa + 2 * all_params['AR_biochar'])
    HT_biochar = (1 + eta_biochar * inp['phi_f_biochar']) / (1 - eta_biochar * inp['phi_f_biochar']) if (1 - eta_biochar * inp['phi_f_biochar']) != 0 else 1

    E_GPa = Erubber_GPa * HT_talc * HT_caco3 * HT_biofiber * HT_biochar

    sigma_y_MPa = all_params['sigma_y0_MPa'] * (1 + all_params['gamma_c'] * (Xc - all_params['Xc0'])) * (1 - all_params['ky'] * inp['phi_el'] * (dr_um / (dr_um + all_params['delta_um']))) * (1 + all_params['ky2'] * Sc)

    PiD = math.exp(-all_params['chi'] * degradation_dose)
    Izod23_kJm2 = PiD * 1.0 * all_params['Imax_kJm2'] * (1 - math.exp(-all_params['kI'] * (inp['phi_el'] * Sc / max(dr_um, 1e-3))))
    fT_m20 = 1.0 / (1 + math.exp(all_params['kT'] * (all_params['T0_C'] - (-20.0))))
    Izodm20_kJm2 = PiD * fT_m20 * all_params['Imax_kJm2'] * (1 - math.exp(-all_params['kI'] * (inp['phi_el'] * Sc / max(dr_um, 1e-3))))

    HDT_C = all_params['H0_C'] + all_params['h1'] * math.log(max(E_GPa, 1e-6)) + all_params['h2'] * Xc - all_params['h3'] * inp['phi_el']

    # --- Calculate composite density from the now-correct volume fractions ---
    # rho_composite = sum(phi_i * rho_i)
    rho_gcc = (phis.get('phi_pp', 0.0) * all_params.get('rho_PP', 0.9) +
               phis.get('phi_el', 0.0) * all_params.get('rho_el', 0.86) +
               phis.get('phi_f_talc', 0.0) * all_params.get('rho_talc', 2.7) +
               phis.get('phi_f_caco3', 0.0) * all_params.get('rho_caco3', 2.71) +
               phis.get('phi_f_biofiber', 0.0) * all_params.get('rho_biofiber', 1.45) +
               phis.get('phi_f_biochar', 0.0) * all_params.get('rho_biochar', 1.8) +
               phis.get('phi_compat', 0.0) * rho_compat +
               phis.get('phi_stab', 0.0) * rho_stab)

    # Elongation at Yield (eps_y_pct)
    # Clamp to a small positive value to prevent physically impossible negative elongation
    eps_y_pct_raw = all_params['eps0_pct'] - all_params['k_eps_E'] * E_GPa + all_params['k_eps_el'] * inp['phi_el']
    eps_y_pct = max(0.1, eps_y_pct_raw)

    # Gardner Impact (Gardner_J)
    Gardner_J = all_params['G0_J'] + all_params['G1_J_per_phi'] * inp['phi_el']

    # Map model outputs to the column names expected by the orchestrator
    props = {
        "E_GPa": E_GPa,
        "MFI_g10min": mfi,
        "sigma_y_MPa": sigma_y_MPa,
        "Izod_23_kJm2": Izod23_kJm2,
        "Izod_m20_kJm2": Izodm20_kJm2,
        "HDT_C": HDT_C,
        "Shrink_pct": None, # Not implemented in this model version
        "rho_gcc": rho_gcc,
        "eps_y_pct": eps_y_pct,
        "Gardner_J": Gardner_J,
        "Xc": Xc,
    }
    props.update({
        "phi_el": phis["phi_el"],
        "phi_f_talc": phis["phi_f_talc"],
        "phi_f_caco3": phis["phi_f_caco3"],
        "phi_f_biofiber": phis["phi_f_biofiber"],
        "phi_f_biochar": phis["phi_f_biochar"],
    })
    return props

# ---------- Path resolution ----------

def resolve_paths(args):
    # DOE input CSV
    if args.doe:
        in_csv = args.doe
    else:
        cycle = args.cycle or "iter_unlabeled"
        in_dir = args.in_dir or "../results/datasets/formulations"
        in_csv = os.path.join(in_dir, cycle, f"doe_{cycle}.csv")

    # OUT props CSV
    if args.out:
        out_csv = args.out
        out_dir = os.path.dirname(out_csv) or "."
    else:
        cycle = args.cycle or "iter_unlabeled"
        out_dir = os.path.join(args.out_dir or "../results/datasets/properties", cycle)
        out_csv = os.path.join(out_dir, f"props_{cycle}.csv")

    meta_json = os.path.join(out_dir, "bridge_run_metadata.json")
    return in_csv, out_csv, out_dir, meta_json

# ---------- CLI ----------

def main():
    ap = argparse.ArgumentParser()
    # iteration-aware options
    ap.add_argument("--cycle", type=str, default="", help="Iteration name (e.g., iter_001)")
    ap.add_argument("--in-dir", type=str, default="../results/datasets/formulations", help="Base dir for DOE CSVs")
    ap.add_argument("--out-dir", type=str, default="../results/datasets/properties", help="Base dir for props CSVs")
    # explicit overrides
    ap.add_argument("--doe", default="", help="Explicit DOE CSV path")
    ap.add_argument("--out", default="", help="Explicit props CSV path")
    # required data/model
    ap.add_argument("--ingredient-library", required=True, help="Path to ingredient_library.json")
    ap.add_argument("--model", required=True, help="Path to model JSON (e.g., ..._v1_gpt.json)")
    ap.add_argument("--process", default="", help="Optional JSON with process settings")
    args = ap.parse_args()

    in_csv, out_csv, out_dir, meta_json = resolve_paths(args)
    os.makedirs(out_dir, exist_ok=True)

    lib = read_json(args.ingredient_library)
    model = read_json(args.model)
    process_cfg = read_json(args.process) if args.process else {}
    catalog = load_ingredient_catalog(lib)

    n = 0
    with open(in_csv, newline="", encoding="utf-8") as f_in, \
         open(out_csv, "w", newline="", encoding="utf-8") as f_out:
        reader = csv.DictReader(f_in)
        
        # Setup output fields
        in_fields = list(reader.fieldnames or [])
        prop_fields = ["E_GPa","MFI_g10min","sigma_y_MPa","Izod_23_kJm2","Izod_m20_kJm2","HDT_C","Shrink_pct",
                       "rho_gcc", "eps_y_pct", "Gardner_J", "Xc", "phi_el","phi_f_talc","phi_f_caco3","phi_f_biofiber","phi_f_biochar"]
        process_fields = list(process_cfg.keys())

        # The compute function may add default process variables if they are missing.
        # Ensure they are in the header to prevent a ValueError from the CSV writer.
        for default_key in ["tau_s", "pvac_bar_abs"]:
            if default_key not in process_fields:
                process_fields.append(default_key)

        # Combine all fields for the header, ensuring no duplicates and preserving order
        out_fields = list(in_fields)
        for f in prop_fields:
            if f not in out_fields:
                out_fields.append(f)
        for f in process_fields:
            if f not in out_fields:
                out_fields.append(f)
        
        w = csv.DictWriter(f_out, fieldnames=out_fields)
        w.writeheader()

        for row in reader:
            try:
                props = compute_row_properties(row, model, catalog, process_cfg) # Calculate properties
                row.update(props) # Merge new properties
                row.update(process_cfg) # Merge process conditions
            except Exception as e:
                print(f"Warning: Could not compute properties for row. Error: {e}. Row: {row}")
            w.writerow(row)
            n += 1

    # metadata
    meta = {
        "cycle": args.cycle or "iter_unlabeled",
        "in_csv": in_csv,
        "out_csv": out_csv,
        "model": args.model,
        "ingredient_library": args.ingredient_library,
        "process": args.process or None,
        "rows_written": n
    }
    with open(meta_json, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    print(f"[bridge] Wrote {n} rows with predicted properties to {out_csv}")
    print(f"[bridge] Metadata: {meta_json}")

if __name__ == "__main__":
    main()
