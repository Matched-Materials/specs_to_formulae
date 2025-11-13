#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rHDPE_gen_formulations.py — self‑contained generator + property predictor for PCR rHDPE

What it does
------------
• Randomly generates N recyclable HDPE formulations (PCR rHDPE as base)
• Chooses 0–3 fillers and 0–3 additives per formulation from an embedded library
• Predicts material properties using literature‑rooted heuristics and light physics:
  - Shore D hardness, haze, intrinsic viscosity proxy, tensile strength, modulus,
    elongation at break, Tg, Tm, degradation temp, FTIR peaks, crystallinity,
    impact strength, MFI, WVTR, OTR, shrinkage, plus cost/CO2e estimates.
• Adds supplier, functional class, and concise safety/processing recommendations.

Inputs
------
No external files required. (Effects are embedded.)

Usage
-----
python rHDPE_gen_formulationsV1.0.py --n 1000 --seed 13 --outfile ../../data/formulations/rHDPE_syntheticV1.0.csv

"""

from __future__ import annotations
import argparse, math, random, statistics as stats, os, csv
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import pandas as pd

# -------------------------------
# Baseline ranges & constants
# -------------------------------

RNG = random.Random()

BASE = {
    # Baseline ranges for Evercap-like HDPE
    "density_gcc": (0.950, 0.955),           # g/cc
    "crystallinity": (0.55, 0.65),           # fraction (unitless)
    "mfi_190_216": (8, 12),                  # g/10 min, 190°C/2.16 kg
    "E_GPa": (1.0, 1.1),                     # Young's modulus
    "tensile_MPa": (25, 30),
    "elong_break_pct": (1200, 1800),
    "Tg_C": (-125, -115),
    "Tm_C": (128, 132),
    "Tdeg_C": (350, 385),
    "OIT_min_200C": (10, 20),                # oxidation induction time @ 200°C (indicative)
    "haze_pct": (90, 100),                   # HDPE → typically opaque
    "shrink_pct": (1.5, 2.5),                # ASTM D955 (flow direction typical range)
}

# Densities for volume‑fraction conversions
RHO = {
    "rHDPE": 0.955,
    "Virgin HDPE": 0.955,
    # Fillers / additives (approximate bulk polymeric densities)
    "CaCO3": 2.70,
    "Talc": 2.75,
    "Silica": 2.20,
    "Carbon Black": 1.80,
    "Zeolite": 2.20,
    "Mica": 2.80,
    "POE/POE-Elastomer": 0.87,
    "OBC/Plastomer": 0.88,
    "EVA": 0.93,
    "PE-g-MAH": 0.95,
    "EBA-GMA": 0.95,
    "Primary AO": 1.02,
    "Secondary AO": 1.02,
    "HALS/UV": 1.05,
    "Metal Deactivator": 1.05,
    "Antistat": 0.95,
    "Slip (Erucamide)": 0.95,
    "PPA": 2.0,
}

# Additive library (brand, supplier, chemical description, functional class, typical wt% range, safety note)
# Note: These names are representative; you can extend easily.
ADDITIVES: Dict[str, Dict] = {
    "Irganox 1010": {"supplier":"BASF","chem":"Pentaerythritol tetrakis(3-(3,5-di-tert-butyl-4-hydroxyphenyl)propionate)","class":"primary_antioxidant","wt%":(0.05,0.2),"rho":"Primary AO","safety":"Phenolic AO; low volatility; PPE for powders."},
    "Irganox 1076": {"supplier":"BASF","chem":"Octadecyl 3-(3,5-di-tert-butyl-4-hydroxyphenyl)propionate","class":"primary_antioxidant","wt%":(0.05,0.2),"rho":"Primary AO","safety":"Avoid dust inhalation; food-contact grades available."},
    "Irgafos 168": {"supplier":"BASF","chem":"Tris(2,4-di-tert-butylphenyl) phosphite","class":"secondary_antioxidant","wt%":(0.05,0.25),"rho":"Secondary AO","safety":"Hydrolysis-sensitive; keep dry; PPE."},
    "DSTDP": {"supplier":"SI Group","chem":"Distearyl thiodipropionate","class":"secondary_antioxidant","wt%":(0.05,0.3),"rho":"Secondary AO","safety":"Thioester; typical with phenolic AO; PPE for powders."},
    "MD 1024": {"supplier":"BASF","chem":"N,N′-bis(3,5-di-tert-butyl-4-hydroxyhydrocinnamoyl)hydrazine","class":"metal_deactivator","wt%":(0.05,0.2),"rho":"Metal Deactivator","safety":"Avoid dust; useful for Cu-containing recyclate."},
    "Tinuvin 770": {"supplier":"BASF","chem":"Bis(2,2,6,6-tetramethyl-4-piperidyl) sebacate (HALS)","class":"uv_stabilizer","wt%":(0.1,0.5),"rho":"HALS/UV","safety":"Dust control; check food-contact status."},
    "Carbon Black": {"supplier":"Various","chem":"Amorphous carbon black pigment","class":"pigment","wt%":(0.05,1.0),"rho":"Carbon Black","safety":"Inhalation hazard; use respirator as per SDS."},
    "Silica (antiblock)": {"supplier":"Grace","chem":"Precipitated silica","class":"antiblock","wt%":(0.05,0.3),"rho":"Silica","safety":"Dust control; low toxicity."},
    "Talc": {"supplier":"Imerys","chem":"Hydrous magnesium silicate","class":"mineral_filler","wt%":(2,20),"rho":"Talc","safety":"Mineral dust; PPE; choose low-impurity grades."},
    "Calcium Carbonate": {"supplier":"Omya","chem":"CaCO3","class":"mineral_filler","wt%":(2,25),"rho":"CaCO3","safety":"Dust; food-contact grades available."},
    "Zeolite": {"supplier":"Tosoh","chem":"Microporous aluminosilicate","class":"odor_scavenger","wt%":(0.2,2.0),"rho":"Zeolite","safety":"Dust control; can absorb moisture/organics."},
    "Mica": {"supplier":"Kuncai","chem":"Phlogopite/Muscovite","class":"platelet_barrier","wt%":(1,20),"rho":"Mica","safety":"Dust; plate-like flakes."},
    "Fusabond E (PE-g-MAH)": {"supplier":"DuPont/Celanese","chem":"PE grafted maleic anhydride","class":"compatibilizer","wt%":(0.5,3.0),"rho":"PE-g-MAH","safety":"Reactive anhydride; skin/eye irritant."},
    "EBA-GMA Compatibilizer": {"supplier":"Arkema","chem":"EBA with glycidyl methacrylate","class":"compatibilizer","wt%":(0.5,3.0),"rho":"EBA-GMA","safety":"Reactive GMA; handle with PPE."},
    "POE Elastomer": {"supplier":"Dow","chem":"Ethylene-octene copolymer (POE)","class":"elastomer","wt%":(2,20),"rho":"POE/POE-Elastomer","safety":"Soft elastomer; may affect demold."},
    "OBC/Plastomer": {"supplier":"ExxonMobil","chem":"Olefin block copolymer","class":"elastomer","wt%":(2,20),"rho":"OBC/Plastomer","safety":"Soft plastomer; can increase stickiness."},
    "EVA": {"supplier":"Various","chem":"Ethylene-vinyl acetate (low VA %)","class":"modifier","wt%":(2,15),"rho":"EVA","safety":"Soft; may increase haze; mild acetic odor."},
    "Antistat": {"supplier":"Croda","chem":"Glycerol monostearate/ethoxylates","class":"antistat","wt%":(0.1,0.5),"rho":"Antistat","safety":"Food-contact types available; hygroscopic."},
    "Slip (Erucamide)": {"supplier":"Generic","chem":"Erucamide","class":"slip","wt%":(0.05,0.3),"rho":"Slip (Erucamide)","safety":"May bloom; slippery surfaces."},
    "PPA (Dynamar)": {"supplier":"Chemours","chem":"Fluoropolymer processing aid","class":"ppa","wt%":(0.05,0.2),"rho":"PPA","safety":"Avoid overheating; fume control."},
}

def load_costs_data(filepath: str) -> Optional[Dict]:
    """Loads material data from a costs CSV file."""
    costs_data = {}
    try:
        with open(filepath, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            for row in reader:
                name = row.get('Name', '').strip()
                if not name: continue
                try:
                    costs_data[name] = {
                        'cost': float(row['Estimated Cost ($/kg)']),
                        'co2': float(row['Estimated CO2e (kg/kg)']),
                        'supply_kt': float(row['Estimated Supply Volume (kt/yr)'])
                    }
                except (ValueError, KeyError, TypeError):
                    pass # Silently skip rows with incomplete or non-numeric data
    except FileNotFoundError:
        print(f"Warning: Costs file not found at {filepath}")
        return None
    return costs_data

NAME_MAPPING = {
    "rHDPE": "PCR rHDPE (blow/film grade)",
    "Virgin HDPE": "Polyethylene (HDPE)",
    "Talc": "Imerys Talc (e.g., Mistron®/Jetfine®)",
    "Calcium Carbonate": "Omya CaCO₃ (e.g., Omyacarb®)",
    "Silica (antiblock)": "Syloid® 244 FP",
    "Carbon Black": "Carbon Black (e.g., Raven®)",
    "POE Elastomer": "ENGAGE™ POE (e.g., 8180)",
    "OBC/Plastomer": "INFUSE™ OBC (e.g., 9100)",
    "Fusabond E (PE-g-MAH)": "Fusabond® E (PE‑g‑MAH)",
    "EBA-GMA Compatibilizer": "LOTADER® AX8840", # Closest GMA compatibilizer
    "Antistat": "Atmer® 163",
    "Slip (Erucamide)": "Erucamide",
    "PPA (Dynamar)": "Dynamar™ PPA (FX 5920A)",
    "Irganox 1010": "Irganox® 1010",
    "Irganox 1076": "Irganox® 1076",
    "Irgafos 168": "Irgafos® 168",
    "DSTDP": "NAUGARD® DSTDP",
    "MD 1024": "Irganox® MD 1024",
    "Tinuvin 770": "Tinuvin® 770 / 783",
    "Zeolite": "Zeolite",
    "Mica": "Mica",
    "EVA": "EVA",
}

# -------------------------------
# Helpers
# -------------------------------

def urand(lo, hi): return RNG.uniform(lo, hi)
def nrand(mu, sig): return max(0.0, RNG.gauss(mu, sig))

def clamp(x, lo, hi): return lo if x < lo else hi if x > hi else x

def pick_baseline(BASEkey):
    a,b = BASE[BASEkey]
    return urand(a,b)

def to_volfrac(wt_pct: float, material: str, ref_density: float=0.955)->float:
    """Convert wt% to volume fraction versus rHDPE matrix of density ref_density."""
    rho_i = RHO.get(material, ref_density)
    # volume = mass/density; for small additive w, approximate versus matrix volume basis
    w = wt_pct/100.0
    # compute exact mixture vol fraction for single additive against matrix
    # v_i = (w/rho_i) / (w/rho_i + (1-w)/ref_density)
    denom = (w/rho_i) + ((1.0 - w)/ref_density)
    return (w/rho_i)/denom

def rmix(values: List[float], weights: List[float])->float:
    sw = sum(weights) or 1e-9
    return sum(v*w for v,w in zip(values, weights))/sw

# -------------------------------
# Property models (literature‑rooted heuristics)
# -------------------------------

def predict_properties(formu: dict)->dict:
    """
    Compute properties from formulation.
    The model structure follows physically‑aware rules derived for rHDPE.
    """
    # Base selection
    density0 = pick_baseline("density_gcc")
    Xc0 = pick_baseline("crystallinity")
    mfi0 = pick_baseline("mfi_190_216")
    E0 = pick_baseline("E_GPa")
    sig0 = pick_baseline("tensile_MPa")
    eb0 = pick_baseline("elong_break_pct")
    Tg = pick_baseline("Tg_C")
    Tm0 = pick_baseline("Tm_C")
    Tdeg0 = pick_baseline("Tdeg_C")
    OIT0 = pick_baseline("OIT_min_200C")
    haze0 = pick_baseline("haze_pct")
    shrink0 = pick_baseline("shrink_pct")

    # Summaries by class
    wt = formu  # shorthand
    wt_elast = 0.0
    wt_filler = 0.0
    wt_platelets = 0.0  # mica/talc
    wt_caco3 = 0.0
    wt_silica = 0.0
    wt_cb = 0.0
    wt_comp = 0.0
    wt_primAO = 0.0
    wt_secAO = 0.0
    wt_uv = 0.0
    wt_md = 0.0

    # Iterate fillers/additives present (F1/F2/F3 + A1/A2/A3 names and wt%)
    names = []
    for tag in ("Filler_1","Filler_2","Filler_3","Additive_1","Additive_2","Additive_3"):
        nm = wt.get(tag) or ""
        names.append(nm)

    # Map class contributions
    def get_wt(tag):
        return float(wt.get(tag.replace("Filler","F").replace("Additive","A")+"_Content (%)", 0.0)) if "Content" not in tag else float(wt.get(tag,0.0))

    for i,tag in enumerate(("Filler_1","Filler_2","Filler_3")):
        nm = wt.get(tag) or ""
        w = float(wt.get(f"F{i+1}_Content (%)", 0.0))
        if not nm or w<=0: continue
        if "Talc" in nm or "Mica" in nm:
            wt_platelets += w; wt_filler += w
        elif "Calcium" in nm or "CaCO3" in nm:
            wt_caco3 += w; wt_filler += w
        elif "Silica" in nm:
            wt_silica += w; wt_filler += w
        elif "Carbon Black" in nm:
            wt_cb += w; wt_filler += w
        elif "Zeolite" in nm:
            wt_filler += w  # odor scavenger, treat as filler
        else:
            wt_filler += w

    for i,tag in enumerate(("Additive_1","Additive_2","Additive_3")):
        nm = wt.get(tag) or ""
        w = float(wt.get(f"A{i+1}_Content (%)", 0.0))
        if not nm or w<=0: continue
        if "Fusabond" in nm or "PE-g-MAH" in nm or "Compatibilizer" in nm:
            wt_comp += w
        if "POE" in nm or "Plastomer" in nm or "OBC" in nm or "Vistamaxx" in nm or "EVA" in nm:
            wt_elast += w
        if "Irganox 1010" in nm or "Irganox 1076" in nm:
            wt_primAO += w
        if "Irgafos 168" in nm or "DSTDP" in nm:
            wt_secAO += w
        if "Tinuvin" in nm:
            wt_uv += w
        if "MD 1024" in nm:
            wt_md += w

    # Convert to volume fractions for barrier & modulus models
    vf_platelets = to_volfrac(wt_platelets, "Mica") + to_volfrac(wt_platelets*0.0, "Talc")  # use mica density as proxy
    vf_caco3 = to_volfrac(wt_caco3, "CaCO3")
    vf_silica = to_volfrac(wt_silica, "Silica")
    vf_cb = to_volfrac(wt_cb, "Carbon Black")
    vf_elast = to_volfrac(wt_elast, "POE/POE-Elastomer")
    vf_filler = to_volfrac(wt_filler, "CaCO3")

    # --- Crystallinity (Xc)
    # Dilution by elastomer/compatibilizer; nucleation by platelets; mild by CaCO3
    Xc = Xc0 * (1 - 0.6*vf_elast) * (1 - 0.12*to_volfrac(wt_comp,"PE-g-MAH"))
    Xc *= (1 + 0.18*vf_platelets + 0.06*vf_caco3)  # nucleation/ordering
    Xc = clamp(Xc, 0.40, 0.75)

    # --- Density (rho)
    density = density0 + 0.02*(Xc - Xc0) - 0.01*vf_elast + 0.005*vf_filler
    density = clamp(density, 0.940, 0.965)

    # --- Modulus (E)
    # Particle reinforcement (rule-of-mixtures + strain amplification) and elastomer softening
    E = E0 * (1 + 2.8*vf_caco3 + 3.2*vf_platelets + 1.5*vf_silica + 0.8*vf_cb)
    E *= (1 - 2.0*vf_elast)
    E = clamp(E, 0.6, 2.5)

    # --- Tensile strength (σ)
    sig = sig0 * (1 + 0.8*(E/E0 - 1)) * (1 + 0.2*(Xc - Xc0)) * (1 - 0.25*vf_elast) * (1 - 0.10*vf_filler)
    sig = clamp(sig, 12, 40)

    # --- Elongation at break (%)
    eb = eb0 * (1 + 2.5*vf_elast) * (1 - 1.2*vf_platelets) * (1 - 0.6*vf_caco3)
    eb = clamp(eb, 50, 1800)

    # --- Shore D hardness
    shoreD = 35 + 55*Xc*(density/0.95) - 12*vf_elast - 5*vf_filler
    shoreD = clamp(shoreD, 45, 75)

    # --- Thermal
    TgC = clamp(Tg + nrand(0,1.0) - 3.0*vf_elast, -125, -100)
    Tm = clamp(Tm0 - 8.0*vf_elast + 1.5*vf_platelets, 123, 136)
    # OIT with AO synergy; secondary + primary > sum
    wP = wt_primAO/100.0; wS = wt_secAO/100.0
    OIT = OIT0 * (1 + 25*wP + 40*wS + 140*wP*wS)
    OIT = clamp(OIT, 5, 120)
    # Degradation temperature follows OIT lightly and filler shielding
    Tdeg = Tdeg0 + 0.05*(OIT - OIT0) + 4.0*vf_filler - 6.0*wS  # phosphite hydrolysis penalty
    Tdeg = clamp(Tdeg, 330, 410)

    # --- MFI (190°C/2.16kg)
    mfi = mfi0 * (1 - 0.2*vf_filler)
    mfi = clamp(mfi, 5.0, 15.0)

    # --- Impact strength (Charpy notched @23°C, kJ/m^2)
    # Sigmoidal increase with elastomer content; penalties for platelets; compatibilizer boosts
    I0 = rmix([4,8],[0.5,0.5])  # baseline center
    Imax = 25.0
    k = 35.0
    I = I0 + (Imax - I0) * (1/(1 + math.exp(-k*(vf_elast - 0.06))))  # strong rise around ~6 vol% elastomer
    I *= (1 - 0.8*vf_platelets) * (1 - 0.3*vf_caco3) * (1 + 1.2*(wt_comp/100.0))
    I = clamp(I, 2.0, 30.0)

    # --- Haze (%): HDPE parts typically opaque; small platelets can raise scatter; carbon black forces 100% (visual opacity)
    haze = haze0
    if wt_cb > 0: haze = 100.0
    haze = clamp(haze + 8.0*vf_platelets + 2.0*vf_caco3 - 10.0*vf_elast, 85, 100)

    # --- Barrier (WVTR, OTR) using simplified Nielsen model: D/D0 = (1 - φ)/(1 + A φ)
    # WVTR ~ proportional to D (vapor diffusivity); OTR similarly
    A_wvtr = 0.5*vf_caco3 + 1.5*vf_platelets + 0.6*vf_silica + 0.2*vf_cb
    A_otr  = 0.4*vf_caco3 + 2.5*vf_platelets + 0.8*vf_silica + 0.2*vf_cb
    phi = clamp(vf_filler, 0.0, 0.6)
    red_wvtr = (1 - phi) / (1 + A_wvtr + 1e-9)
    red_otr  = (1 - phi) / (1 + A_otr  + 1e-9)
    WVTR = clamp(0.35 * red_wvtr * (1 + 0.8*vf_elast), 0.05, 0.6)  # g/m²/day (1mm film eq., relative scaling)
    OTR  = clamp(250  * red_otr  * (1 + 1.0*vf_elast), 10, 300)    # cc/m²/day (1mm film eq., relative scaling)

    # --- Shrinkage (ASTM D955, %)
    shrink = 2.0 + 10.0*(Xc - 0.60) - 2.0*vf_filler - 1.0*vf_elast
    shrink = clamp(shrink, 0.8, 3.0)

    # --- Intrinsic viscosity proxy (dL/g) — not standard for PE; provide melt viscosity indicator scaled
    IV = clamp(0.5 + 0.3*(1/mfi if mfi>0 else 10) + 0.1*(Xc-0.6), 0.3, 1.8)

    # --- FTIR peaks (cm^-1) as strings
    peaks = ["2915","2848","1470"]  # CH2 stretching/bending
    if wP>0 or wS>0: peaks.append("1735")  # carbonyl traces from AO/phosphite oxidation
    if wt_cb>0: peaks.append("1590")
    FTIR1 = peaks[0]; FTIR2 = peaks[1]

    return {
        "Density (g/cc)": round(density,5),
        "Crystallinity (%)": round(100*Xc,2),
        "Young's Modulus (GPa)": round(E,4),
        "Tensile Strength (MPa)": round(sig,3),
        "Elongation at Break (%)": round(eb,1),
        "Shore hardness (D)": round(shoreD,1),
        "Glass Transition Temp (°C)": round(TgC,1),
        "Melting Temp (°C)": round(Tm,1),
        "Degradation Temp (°C)": round(Tdeg,1),
        "OIT @200°C (min)": round(OIT,1),
        "Haze (%)": round(haze,1),
        "Impact Strength (kJ/m²)": round(I,3),
        "Melt Flow Index (g/10 min)": round(mfi,4),
        "Intrinsic Viscosity (dL/g)": round(IV,4),
        "WVTR (g/m²/day)": round(WVTR,4),
        "OTR (cc/m²/day)": round(OTR,2),
        "Shrinkage (ASTM D955) (%)": round(shrink,3),
        "FTIR Peak 1 (cm⁻¹)": FTIR1,
        "FTIR Peak 2 (cm⁻¹)": FTIR2,
    }

# -------------------------------
# Formulation generator
# -------------------------------

def random_dose(name:str)->float:
    lo,hi = ADDITIVES[name]["wt%"]
    return round(urand(lo,hi), 3)

def gen_one(formula_id:str, costs_data:dict)->dict:
    # Base composition
    virgin = max(0.0, urand(0, 35))  # allow some virgin HDPE
    # Draw number of fillers & additives
    n_fill = RNG.choices([0,1,2],[0.5,0.4,0.1])[0] # Reduced filler complexity
    n_add  = RNG.choices([0,1,2],[0.2,0.5,0.3])[0]

    # Choose fillers from pool (excluding those not suitable for this application)
    filler_pool = ["Calcium Carbonate","Talc"]
    
    # Additive pool - restricted to antioxidants for this application
    add_pool = ["Irganox 1010", "Irganox 1076", "Irgafos 168"]

    chosen_fillers = RNG.sample(filler_pool, k=n_fill) if n_fill>0 else []
    
    # Always include Slip (Erucamide) at a fixed dose
    chosen_adds = ["Slip (Erucamide)"]
    a_doses = [0.15] # 1500 ppm

    # Add other random additives from the restricted pool
    if n_add > 0:
        additional_adds = RNG.sample(add_pool, k=n_add)
        chosen_adds.extend(additional_adds)
        a_doses.extend([random_dose(nm) for nm in additional_adds])

    # Assign doses for fillers
    f_doses = [random_dose(nm) for nm in chosen_fillers]
    
    wt_fillers = sum(f_doses)
    wt_adds = sum(a_doses)

    # Balance to 100% with rHDPE & virgin
    rHDPE_wt = max(0.0, 100.0 - wt_fillers - wt_adds - virgin)
    # Re-normalize if needed
    if rHDPE_wt < 0:
        # scale down fillers/additives
        scale = 100.0/(wt_fillers + wt_adds + virgin + 1e-9)
        f_doses = [round(x*scale,3) for x in f_doses]
        a_doses = [round(x*scale,3) for x in a_doses]
        wt_fillers = sum(f_doses); wt_adds = sum(a_doses)
        rHDPE_wt = max(0.0, 100.0 - wt_fillers - wt_adds - virgin)

    # Create a dictionary to hold additives and their doses
    additives_dict = dict(zip(chosen_adds, a_doses))

    row = {
        "Formula_ID": formula_id,
        "rHDPE (%)": round(rHDPE_wt,3),
        "Virgin HDPE (%)": round(virgin,3),
        # Placeholders for up to 3 fillers/additives
        "Filler_1": chosen_fillers[0] if len(chosen_fillers)>0 else "",
        "F1_Content (%)": round(f_doses[0],3) if len(f_doses)>0 else 0.0,
        "Filler_2": chosen_fillers[1] if len(chosen_fillers)>1 else "",
        "F2_Content (%)": round(f_doses[1],3) if len(f_doses)>1 else 0.0,
        "Filler_3": "", # Max 2 fillers
        "F3_Content (%)": 0.0,
        "Additive_1": chosen_adds[0] if len(chosen_adds)>0 else "",
        "A1_Content (%)": round(additives_dict.get(chosen_adds[0], 0.0),3) if len(chosen_adds)>0 else 0.0,
        "Additive_2": chosen_adds[1] if len(chosen_adds)>1 else "",
        "A2_Content (%)": round(additives_dict.get(chosen_adds[1], 0.0),3) if len(chosen_adds)>1 else 0.0,
        "Additive_3": chosen_adds[2] if len(chosen_adds)>2 else "",
        "A3_Content (%)": round(additives_dict.get(chosen_adds[2], 0.0),3) if len(chosen_adds)>2 else 0.0,
    }

    # Supplier / class / safety columns per component slot
    for i, nm in enumerate([row["Filler_1"], row["Filler_2"], row["Filler_3"]], start=1):
        if nm:
            meta = ADDITIVES.get(nm, None)
            supplier = (meta or {}).get("supplier","Various")
            fclass = (meta or {}).get("class","mineral_filler")
            safety = (meta or {}).get("safety","Mineral dust; PPE.")
        else:
            supplier=fclass=safety=""
        row[f"F{i}_Supplier"]=supplier; row[f"F{i}_Functional Class"]=fclass; row[f"F{i}_Safety"]=safety

    for i, nm in enumerate([row["Additive_1"], row["Additive_2"], row["Additive_3"]], start=1):
        if nm:
            meta = ADDITIVES.get(nm, None)
            supplier = (meta or {}).get("supplier","Various")
            aclass = (meta or {}).get("class","additive")
            safety = (meta or {}).get("safety","Handle with PPE.")
        else:
            supplier=aclass=safety=""
        row[f"A{i}_Supplier"]=supplier; row[f"A{i}_Functional Class"]=aclass; row[f"A{i}_Safety"]=safety

    # Predict properties
    props = predict_properties(row)
    row.update(props)

    # --- Cost, CO2, and Supply Calculation ---
    all_components = [("rHDPE", row["rHDPE (%)"]), ("Virgin HDPE", row["Virgin HDPE (%)"])]
    for i in range(1, 4):
        name, wt = row.get(f"Filler_{i}"), row.get(f"F{i}_Content (%)")
        if name and wt and wt > 0: all_components.append((name, wt))
    for i in range(1, 4):
        name, wt = row.get(f"Additive_{i}"), row.get(f"A{i}_Content (%)")
        if name and wt and wt > 0: all_components.append((name, wt))

    total_cost, total_co2, min_blend_supply_kt = 0.0, 0.0, float('inf')
    for comp_name, comp_pct in all_components:
        lookup_name = NAME_MAPPING.get(comp_name, comp_name)
        cost_info = costs_data.get(lookup_name)
        if cost_info:
            weight_fraction = comp_pct / 100.0
            total_cost += cost_info.get('cost', 0.0) * weight_fraction
            total_co2 += cost_info.get('co2', 0.0) * weight_fraction

            if 'supply_kt' in cost_info and cost_info['supply_kt'] > 0:
                possible_supply = cost_info['supply_kt'] / weight_fraction
                min_blend_supply_kt = min(min_blend_supply_kt, possible_supply)
            else:
                min_blend_supply_kt = 0
        else:
            if comp_pct > 0: print(f"Warning: No cost data for '{comp_name}' (lookup: '{lookup_name}')")
            min_blend_supply_kt = 0

    final_supply_kg = min_blend_supply_kt * 1_000_000 if min_blend_supply_kt < float('inf') else 0

    row["Cost ($/kg)"] = round(total_cost, 3)
    row["Cost ($/lb)"] = round(total_cost / 2.20462, 3)
    row["CO2 Equivalent (kg CO2/kg)"] = round(total_co2, 3)
    row["kg produced annually"] = f"{final_supply_kg:,.0f}" if final_supply_kg > 0 else ""

    # Processing recommendations
    recs = [
        "Compounding: 34–44 L/D twin‑screw, melt 180–210 °C, vacuum venting; 60–80 mesh melt filter; residence ≤3 min.",
        "rHDPE handling: screen for PVC; dry if odor/moisture present (70–80 °C, 1–2 h).",
        "Injection molding: melt 190–220 °C; mold 20–40 °C; moderate pack/hold; expect 1.2–2.8% shrink (flow).",
    ]
    if row["Filler_1"] or row["Filler_2"] or row["Filler_3"]:
        recs.append("Minerals/platelets: ensure dispersion; check die pressure; plate-like fillers improve barrier but reduce impact.")
    if row["Additive_1"] or row["Additive_2"] or row["Additive_3"]:
        if "PPA" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]):
            recs.append("PPA: add early; maintain melt ≥200 °C for coating; avoid PTFE decomposition fumes.")
        if "Fusabond" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]) or "Compatibilizer" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]):
            recs.append("Compatibilizer: watch melt flow drift; balance with AO package to limit chain scission.")
        if "POE" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]) or "Plastomer" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]):
            recs.append("Elastomer: boosts impact; may lower stiffness/soften; adjust pack/hold and cooling to avoid sink/stick.")
        if "Tinuvin" in (row["Additive_1"]+row["Additive_2"]+row["Additive_3"]):
            recs.append("UV (HALS): avoid overdosing; check color/interaction with carbon black.")
    row["Processing Recommendations"] = " ".join(recs)

    # Safety summary
    safes = []
    for i in range(1,4):
        s = row.get(f"F{i}_Safety","")
        if s: safes.append(f"{row.get(f'Filler_{i}','')}: {s}")
    for i in range(1,4):
        s = row.get(f"A{i}_Safety","")
        if s: safes.append(f"{row.get(f'Additive_{i}','')}: {s}")
    row["Safety Summary"] = " | ".join(safes) if safes else ""

    # Notes
    row["Notes"] = "Generated"

    return row

def gen_many(n:int, seed:Optional[int]=None, costs_data:dict={})->pd.DataFrame:
    if seed is not None:
        RNG.seed(seed)
    rows = []
    for i in range(1, n+1):
        fid = f"rHDPE_{i:04d}"
        rows.append(gen_one(fid, costs_data))
    # Column order — grouped by category for clarity
    formulation_cols = [
        "Formula_ID", "Notes",
        "rHDPE (%)", "Virgin HDPE (%)",
        "Filler_1", "F1_Content (%)", "F1_Supplier", "F1_Functional Class",
        "Filler_2", "F2_Content (%)", "F2_Supplier", "F2_Functional Class",
        "Filler_3", "F3_Content (%)", "F3_Supplier", "F3_Functional Class",
        "Additive_1", "A1_Content (%)", "A1_Supplier", "A1_Functional Class",
        "Additive_2", "A2_Content (%)", "A2_Supplier", "A2_Functional Class",
        "Additive_3", "A3_Content (%)", "A3_Supplier", "A3_Functional Class",
    ]
    properties_cols = [
        "Density (g/cc)", "Shore hardness (D)", "Melt Flow Index (g/10 min)", "Intrinsic Viscosity (dL/g)",
        "Tensile Strength (MPa)", "Young's Modulus (GPa)", "Elongation at Break (%)", "Impact Strength (kJ/m²)",
        "Haze (%)", "Crystallinity (%)", "Glass Transition Temp (°C)", "Melting Temp (°C)",
        "Degradation Temp (°C)", "OIT @200°C (min)", "WVTR (g/m²/day)", "OTR (cc/m²/day)",
        "Shrinkage (ASTM D955) (%)", "FTIR Peak 1 (cm⁻¹)", "FTIR Peak 2 (cm⁻¹)",
        "Cost ($/kg)", "Cost ($/lb)", "CO2 Equivalent (kg CO2/kg)", "kg produced annually",
    ]
    processing_cols = [
        "Processing Recommendations",
    ]
    safety_cols = [
        "F1_Safety", "F2_Safety", "F3_Safety",
        "A1_Safety", "A2_Safety", "A3_Safety",
        "Safety Summary",
    ]
    cols = formulation_cols + properties_cols + processing_cols + safety_cols
    df = pd.DataFrame(rows)
    # ensure all columns exist
    for c in cols:
        if c not in df.columns: df[c] = "" if c not in ("Cost ($/kg)","Cost ($/lb)","CO2 Equivalent (kg CO2/kg)","Shore hardness (D)") else 0.0
    return df[cols]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", "--n", type=int, default=200, help="Number of formulations to generate")
    ap.add_argument("--seed", type=int, default=42, help="Random seed")
    ap.add_argument("--out", type=str, default="rHDPE_synthetic_with_models.csv", help="Output CSV file")
    args = ap.parse_args()

    # Load cost data from the standard path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    costs_path = os.path.join(script_dir, '..', '..', 'data', 'reference_files', 'costs.csv')
    print(f"Loading costs data from: {costs_path}")
    costs_data = load_costs_data(costs_path)
    if not costs_data:
        print("Could not load costs data. Proceeding without cost calculations.")
        costs_data = {}

    df = gen_many(args.n, args.seed, costs_data)
    df.to_csv(args.out, index=False)
    print(f"Wrote {args.out} with {len(df)} rows.")

if __name__ == "__main__":
    main()
