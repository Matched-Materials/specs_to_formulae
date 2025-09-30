#!/usr/bin/env python3
"""
formulation_doe_generatorV1.py
Generates broad DOE for PP TPO formulations including sustainable options. Can be run as a script or imported as a module.

Outputs a CSV with compositions summing to 100 wt%, plus meta (cost, CO2, bio-content).

New in this version:
- --cycle names the iteration directory (e.g., iter_001).
- --out-dir sets the base results folder (default: ../results/datasets/formulations).
- If -o/--out is NOT provided, the output becomes:
    <out-dir>/<cycle>/doe_<cycle>.csv
- Always creates directories needed for output paths.
- Writes a small metadata JSON next to the CSV to capture args & counts.
"""
import json, argparse, random, csv, os, math
from typing import List, Dict, Optional
import pandas as pd

def load_json(p):
    with open(p, "r") as f:
        return json.load(f)

try:
    from src.compatibility import evaluate_formulation
    COMP_RULES = load_json("data/processed/compatibility_rules.json")
except (ImportError, FileNotFoundError):
    evaluate_formulation = None
    COMP_RULES = None

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def compute_mix_metrics(row, lib):
    cost = 0.0
    ef = 0.0
    bio_mass = 0.0
    # flatten catalog
    catalog = {}
    for cat in lib:
        arr = lib.get(cat)
        if isinstance(arr, list):
            for it in arr:
                name = it.get("name")
                if name:
                    catalog[name] = it
    # Walk ingredients
    pairs = [
        ("elastomer_name","elastomer_wtpct"),
        ("filler_name","filler_wtpct"),
        ("talc_name","talc_wtpct"),
        ("compat_name","compat_wtpct"),
        ("intune_name","intune_wtpct"),
        ("stabilizer_name","stabilizer_wtpct"),
        ("baseA_name","baseA_wtpct"),
        ("baseB_name","baseB_wtpct")
    ]
    for name_key, wt_key in pairs:
        name = row.get(name_key)
        try:
            wt = float(row.get(wt_key, 0.0) or 0.0)
        except Exception:
            wt = 0.0
        meta = catalog.get(name or "", {})
        c = float(meta.get("cost_usd_per_kg", 0.0) or 0.0)
        e = float(meta.get("ef_kgCO2e_per_kg", 0.0) or 0.0)
        sust = meta.get("sustainability", {}) if isinstance(meta.get("sustainability"), dict) else {}
        bio_pct = float((sust.get("bio_content_pct", 0) or 0))
        cost += (wt/100.0) * c
        ef += (wt/100.0) * e
        bio_mass += (wt/100.0) * (bio_pct/100.0)
    return cost, ef, bio_mass

def generate_formulation_doe(
    n: int,
    seed: int,
    ingredient_pools: Optional[Dict[str, List]] = None,
    ingredient_library: str = "data/processed/ingredient_library.json",
    focus: str = "none",
    elastomer_family: str = "",
    use_intune: bool = False,
) -> pd.DataFrame:
    """Generates a DataFrame of formulation candidates based on specified modes."""

    def to_ingredient_entry(item_meta, is_compat=False):
        """Helper to create a dict for the compatibility evaluation function."""
        if not item_meta:
            return None
        
        # MFR: use midpoint of range, or None if not specified
        mfr_range = item_meta.get("mfr_range")
        mfr = None
        if isinstance(mfr_range, list) and len(mfr_range) == 2 and all(isinstance(x, (int, float)) for x in mfr_range):
            mfr = 0.5 * (mfr_range[0] + mfr_range[1])
        
        # Infer needs_drying from chem_family
        chem_family = item_meta.get("chem_family", "")
        needs_drying = "polyester" in (chem_family or "").lower()
        
        return {
            "chem_family": chem_family, "MFR": mfr, "Tm_C": item_meta.get("Tm_C"),
            "needs_drying": needs_drying, "is_compatibilizer": is_compat
        }

    # If pre-filtered pools are not provided, load the full library as a fallback.
    if ingredient_pools:
        lib = ingredient_pools
    else:
        lib = load_json(ingredient_library)

    # --- 1. Assemble Ingredient Pools ---
    bases = list(lib.get("base_resins", []))
    elastomers = list(lib.get("elastomers", []))
    compat = list(lib.get("compatibilizers", []))
    all_minerals = list(lib.get("mineral_fillers", []))  # Preserve for talc lookup
    minerals = all_minerals[:]  # Work with a copy that can be filtered
    fibers = list(lib.get("fibrous_fillers", []))
    bio_extenders = list(lib.get("bio_extenders", []))
    bio_fillers = list(lib.get("bio_fillers", []))
    stabilizers = list(lib.get("stabilizers", []))
    nucleators = list(lib.get("nucleators", []))

    # --- 2. Filter Pools Based on Focus Mode ---
    if focus == "recycled":
        bases = [b for b in bases if "rpp" in b.get("type", "").lower() or "rpp" in b.get("name", "").lower()]
        if not bases:
            print("Warning: No 'recycled' base resins found for --focus=recycled. Check library.")

    elif focus == "bio-based":
        def is_bio(component):
            """More robust check for bio-based materials."""
            sust = component.get("sustainability", {})
            is_bio_origin = isinstance(sust, dict) and sust.get("origin") == "bio-based"
            is_bio_name = "bio" in component.get("name", "").lower()
            is_bio_type = "bio" in component.get("type", "").lower()
            return is_bio_origin or is_bio_name or is_bio_type

        # Filter all relevant pools for bio-based focus
        bases = [c for c in bases if is_bio(c)]
        minerals = [c for c in minerals if is_bio(c)]
        fibers = [c for c in fibers if is_bio(c)]
        bio_extenders = [c for c in bio_extenders if is_bio(c)]
        bio_fillers = [c for c in bio_fillers if is_bio(c)]

        if not bases:
            print("Warning: No 'bio-based' base resins found for --focus=bio-based. Check library.")

    elif focus == "biopolyester":
        # This mode specifically creates blends of PLA/PHA/PHB toughened with PBAT.
        bases = [b for b in bases if b.get("type") in ["PLA", "PHA", "PHB"]]
        elastomers = [e for e in elastomers if e.get("type") == "PBAT"]
        
        # Also filter for compatible fillers, though the compatibility logic should handle this.
        # This is more of an explicit guardrail.
        minerals = [m for m in minerals if m.get("type") in ["Talc", "CaCO3"]]
        fibers = [f for f in fibers if f.get("type") == "BioFiber"]
        bio_fillers = [bf for bf in bio_fillers if bf.get("type") in ["Biochar", "Cellulose"]]
        
        if not bases:
            print("Warning: No PLA/PHA/PHB base resins found for --focus=biopolyester.")
        if not elastomers:
            print("Warning: No PBAT elastomer found for --focus=biopolyester.")

    # --- 3. Configure Formulation Strategy ---
    chosen_elastomers = elastomers
    if elastomer_family:
        fam = elastomer_family
        chosen_elastomers = [
            e for e in elastomers
            if (fam.lower() in e.get("name","").lower()) or (fam.upper() in str(e.get("type","")))
        ] or elastomers

    chosen_filler_sets = []
    if focus in ["bio-based", "biopolyester"]:
        bio_pool = fibers + bio_extenders + bio_fillers + minerals # pools are pre-filtered
        if bio_pool:
            chosen_filler_sets.append(("BioBased", bio_pool))
    else:  # "none" or "recycled" focus
        if minerals:
            chosen_filler_sets.append(("Mineral", minerals))
        if fibers:
            chosen_filler_sets.append(("Fiber", fibers))

    if not chosen_filler_sets:
        # Add a dummy entry to allow the loop to run once for filler-less formulations
        chosen_filler_sets.append(("", [None]))

    rows: List[Dict] = []
    rng = random.Random(seed)

    for e in chosen_elastomers:
        for filler_label, filler_pool in chosen_filler_sets:
            # Filter base resins to be compatible with the chosen elastomer
            elastomer_type = e.get("type")
            compatible_bases = bases
            if elastomer_type:
                compatible_bases = [
                    b for b in bases
                    if elastomer_type in b.get("compatibility", {}).get("elastomer_types", [])
                ]
            if not compatible_bases:
                print(f"Warning: No compatible base resins found for elastomer '{e.get('name')}' in the current pool (focus='{focus}'). Skipping this elastomer.")
                continue

            generated_count = 0
            max_attempts = n * 20  # Safety break to prevent infinite loops
            attempts = 0
            while generated_count < n and attempts < max_attempts:
                attempts += 1
                row: Dict = {}
                talc = None # Define talc here to ensure it exists for the compatibility check
                # base blend
                baseA = rng.choice(compatible_bases)
                baseB = rng.choice(compatible_bases)
                if baseB.get("name") == baseA.get("name") and len(compatible_bases) > 1:
                    baseB = rng.choice([b for b in compatible_bases if b.get("name") != baseA.get("name")])

                # elastomer
                # Allow elastomer content to go to zero to explore formulations without it.
                _, e_hi = e.get("range_wt_pct",[8,18])
                elast_wt = rng.uniform(0.0, float(e_hi))

                # filler
                filler = rng.choice(filler_pool) if filler_pool else None
                filler_wt = 0.0
                if filler:
                    f_lo, f_hi = filler.get("range_wt_pct",[0,15])
                    filler_wt = rng.uniform(float(f_lo), float(f_hi))

                # add small talc when using biofiber
                talc_wt = 0.0
                talc_name = ""
                if filler_label == "BioBased":
                    talc = next((m for m in all_minerals if "Talc" in m.get("name","")), None)
                    if talc:
                        talc_wt = rng.uniform(0.0, 8.0)
                        talc_name = talc["name"]

                # --- Select compatibilizer based on focus mode ---
                if focus in ["biopolyester", "bio-based"]:
                    # For polyester-based systems, use a polyester-compatible agent
                    comp = next((c for c in compat if "PLA-g-MAH" in c.get("name","")), None)
                else:
                    # For polyolefin-based systems, use a PP-based agent
                    comp = next((c for c in compat if "PP-g-MAH" in c.get("name","")), None)
                
                comp_wt = 0.0
                if comp:
                    comp_wt = (rng.uniform(1.5, 3.0) if filler_label == "BioBased" else rng.uniform(0.5, 2.0))

                # INTUNE optional
                intune = next((c for c in compat if "INTUNE" in c.get("name","")), None)
                intune_wt = 0.0
                if use_intune and intune:
                    intune_wt = rng.uniform(0.0, 3.0)
                    elast_wt *= rng.uniform(0.85, 0.95)

                # Stabilizer
                stab = stabilizers[0] if stabilizers else None
                stab_wt = rng.uniform(0.2, 0.5) if stab else 0.0

                # Nucleator fixed mid
                nuc = next((n for n in nucleators if ("HPN" in n.get("name","") or "Hyperform" in n.get("name",""))), None)
                nuc_ppm = 800 if nuc else 0

                # compute remainder for bases
                non_base = elast_wt + filler_wt + talc_wt + comp_wt + intune_wt + stab_wt
                remaining = max(0.0, 100.0 - non_base)
                baseA_wt = rng.uniform(0.0, 1.0) * remaining
                baseB_wt = remaining - baseA_wt

                # Build row
                row["elastomer_name"] = e.get("name","")
                row["elastomer_wtpct"] = round(elast_wt, 2)
                row["filler_name"] = filler.get("name","") if filler else ""
                row["filler_wtpct"] = round(filler_wt, 2)
                row["talc_name"] = talc_name
                row["talc_wtpct"] = round(talc_wt, 2)
                row["compat_name"] = comp.get("name","") if comp else ""
                row["compat_wtpct"] = round(comp_wt, 2)
                row["intune_name"] = intune.get("name","") if intune and intune_wt>0 else ""
                row["intune_wtpct"] = round(intune_wt, 2)
                row["stabilizer_name"] = stab.get("name","") if stab else ""
                row["stabilizer_wtpct"] = round(stab_wt, 2)
                row["nucleator_name"] = nuc.get("name","") if nuc else ""
                row["nucleator_ppm"] = int(nuc_ppm)
                row["baseA_name"] = baseA.get("name","")
                row["baseA_wtpct"] = round(baseA_wt, 2)
                row["baseB_name"] = baseB.get("name","")
                row["baseB_wtpct"] = round(baseB_wt, 2)

                # --- Compatibility Scoring ---
                if evaluate_formulation and COMP_RULES:
                    formulation_ingredients = []
                    if baseA_wt > 0 and baseA: formulation_ingredients.append(to_ingredient_entry(baseA))
                    if baseB_wt > 0 and baseB: formulation_ingredients.append(to_ingredient_entry(baseB))
                    if elast_wt > 0 and e: formulation_ingredients.append(to_ingredient_entry(e))
                    if filler_wt > 0 and filler: formulation_ingredients.append(to_ingredient_entry(filler))
                    if talc_wt > 0 and talc: formulation_ingredients.append(to_ingredient_entry(talc))
                    if comp_wt > 0 and comp: formulation_ingredients.append(to_ingredient_entry(comp, is_compat=True))
                    if intune_wt > 0 and intune: formulation_ingredients.append(to_ingredient_entry(intune, is_compat=True))

                    compat_score, ok, reasons = evaluate_formulation(formulation_ingredients, COMP_RULES)
                    if not ok:
                        # This formulation is blocked by a hard rule, so we skip it.
                        print(f"Info: Skipping blocked formulation. Reason: {'; '.join(reasons)}")
                        continue
                    row["compat_score"] = round(compat_score, 3)
                    row["compat_notes"] = "; ".join(reasons[:3]) if reasons else ""
                else:
                    row["compat_score"] = 1.0
                    row["compat_notes"] = "Compatibility module not loaded."

                # Metrics
                cost, ef, bio_mass = compute_mix_metrics(row, lib)
                row["est_cost_usd_per_kg"] = round(cost, 3)
                row["est_ef_kgCO2e_per_kg"] = round(ef, 3)
                row["est_biogenic_mass_frac"] = round(bio_mass, 3)

                generated_count += 1
                rows.append(row)
            if attempts >= max_attempts:
                print(f"Warning: Reached max attempts ({max_attempts}) for elastomer '{e.get('name')}' and filler '{filler_label}'. Generated {generated_count}/{n} candidates.")
    return pd.DataFrame(rows)

def resolve_output_paths(args) -> dict:
    """
    Decide where to write results based on --cycle, --out-dir, and optional -o.
    Returns dict with keys: out_csv, out_dir, meta_json
    """
    # If user gave explicit --out, use it and make parent dirs.
    if args.out:
        out_csv = args.out
        out_dir = os.path.dirname(out_csv) or "."
        meta_json = os.path.join(out_dir, "doe_run_metadata.json")
        return {"out_csv": out_csv, "out_dir": out_dir, "meta_json": meta_json}

    # Otherwise we require a cycle name to structure results
    cycle = args.cycle or "iter_unlabeled"
    base_dir = args.out_dir or "../results/datasets/formulations"
    out_dir = os.path.join(base_dir, cycle)
    out_csv = os.path.join(out_dir, f"doe_{cycle}.csv")
    meta_json = os.path.join(out_dir, "doe_run_metadata.json")
    return {"out_csv": out_csv, "out_dir": out_dir, "meta_json": meta_json}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ingredient_library", default="data/processed/ingredient_library.json",
                    help="Path to ingredient_library.json")
    ap.add_argument("-n", type=int, default=120,
                    help="Samples PER (elastomer family × filler set). Total rows multiply accordingly.")
    ap.add_argument("--elastomer_family", type=str, default="",
                    help="Filter elastomers by family name/type (e.g., POE, PBE, OBC).")
    ap.add_argument("--use-intune", dest="use_intune", action="store_true",
                    help="Allow INTUNE compatibilizer (reduces elastomer fraction slightly).")
    ap.add_argument("--seed", type=int, default=13, help="RNG seed.")
    ap.add_argument("--focus", type=str, default="none", choices=["none", "recycled", "bio-based", "biopolyester"],
                    help="Focus formulation strategy on recycled or bio-based content.")
    # Iteration-aware output handling
    ap.add_argument("--cycle", type=str, default="",
                    help="Iteration name used as subdirectory, e.g., iter_001.")
    ap.add_argument("--out-dir", type=str, default="results/datasets/formulations",
                    help="Base results directory for cycle subfolders.")
    ap.add_argument("-o", "--out", default="",
                    help="Explicit output CSV path (bypasses cycle/out-dir logic).")
    args = ap.parse_args()

    df = generate_formulation_doe(
        n=args.n,
        seed=args.seed,
        ingredient_library=args.ingredient_library,
        focus=args.focus,
        elastomer_family=args.elastomer_family,
        use_intune=args.use_intune,
    )
    if df.empty:
        print("No rows produced; check library content and flags.")
        return

    paths = resolve_output_paths(args)
    os.makedirs(paths["out_dir"], exist_ok=True)

    df.to_csv(paths["out_csv"], index=False, quoting=csv.QUOTE_NONNUMERIC)

    # Write a small metadata file alongside
    meta = {
        "cycle": args.cycle or "iter_unlabeled",
        "out_csv": paths["out_csv"],
        "out_dir": paths["out_dir"],
        "n_per_combo": args.n,
        "focus": args.focus,
        "use_intune": args.use_intune,
        "elastomer_family": args.elastomer_family,
        "ingredient_library": args.ingredient_library,
        "seed": args.seed,
        "total_rows_written": len(df)
    }
    with open(paths["meta_json"], "w", encoding="utf-8") as mf:
        json.dump(meta, mf, indent=2)

    print(f"Wrote {len(df)} candidates to {paths['out_csv']}")
    print(f"Wrote metadata to {paths['meta_json']}")

if __name__ == "__main__":
    main()
