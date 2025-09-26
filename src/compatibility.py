from itertools import combinations
import math

def mfr_ratio_score(mfr_a, mfr_b, max_ratio):
    if not mfr_a or not mfr_b: return 1.0
    r = max(mfr_a, mfr_b) / max(1e-9, min(mfr_a, mfr_b))
    if r >= max_ratio: return 0.0
    # Smooth falloff to 0 at r=max_ratio
    return max(0.0, 1.0 - math.log2(r)/math.log2(max_ratio))

def melt_overlap_score(Ta, Tb, good, min_):
    if Ta is None or Tb is None: return 1.0
    overlap = good - abs(Ta - Tb)
    if overlap <= 0: return 0.0 if abs(Ta - Tb) > min_ else 0.3
    return min(1.0, overlap/good)

def pair_key(fa, fb):
    return "##".join(sorted([fa, fb]))

def chemistry_score(fa, fb, rules, has_compat):
    pm = rules["pair_matrix"]
    # Normalize keys: both "A::B" and "B::A" go to a single sorted key
    k1 = f"{fa}::{fb}"
    k2 = f"{fb}::{fa}"
    entry = pm.get(k1) or pm.get(k2) or {}
    score = entry.get("score", 1.0 if fa == fb else 0.2)
    block = entry.get("block", False)
    # Optional compatibilizer boost
    if not block and has_compat:
        boost = rules.get("compatibilizer_suggestions", {}).get(k1) or \
                rules.get("compatibilizer_suggestions", {}).get(k2)
        if boost:
            score = max(score, min(1.0, boost.get("boost_to", score)))
    return score, block, entry.get("note")

def pair_process_score(a, b, rules):
    pr = rules["processing_rules"]
    s1 = mfr_ratio_score(a.get("MFR"), b.get("MFR"), pr["mfr_ratio_max"])
    s2 = melt_overlap_score(a.get("Tm_C"), b.get("Tm_C"),
                            pr["melt_T_overlap_good"], pr["melt_T_overlap_min"])
    s = min(s1, s2)
    if a.get("needs_drying") != b.get("needs_drying"):
        s = max(0.0, s - pr["drying_mismatch_penalty"])
    return s

def evaluate_formulation(ingredients, rules):
    """ingredients: list of dicts each with 'chem_family','MFR','Tm_C','needs_drying', and a boolean 'is_compatibilizer'."""
    has_compat = any(i.get("is_compatibilizer") for i in ingredients)
    th = rules["thresholds"]
    reasons = []
    worst = 1.0
    for A, B in combinations(ingredients, 2):
        fa, fb = A["chem_family"], B["chem_family"]
        c_score, blocked, note = chemistry_score(fa, fb, rules, has_compat)
        if blocked or c_score < th["doe_hard_block_threshold"]:
            reasons.append(f"BLOCK: {fa} ↔ {fb} ({note or 'chemistry'})")
            return 0.0, False, reasons
        p_score = pair_process_score(A, B, rules)
        pair_score = min(c_score, p_score)
        worst = min(worst, pair_score)
        # Optional: flag "needs compat" when chemistry below needs_compat_threshold
        if c_score < th["needs_compat_threshold"] and not has_compat:
            reasons.append(f"Penalty: {fa} ↔ {fb} benefits from compatibilizer")
    return worst, True, reasons
