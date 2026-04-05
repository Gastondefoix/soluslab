"""
Soluslab — Environmental Balance Calculator
==========================================
Run: streamlit run app.py
Dependencies: pip install streamlit pandas plotly
"""

import json
import math
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ──────────────────────────────────────────────────────────────────────────────
# JSON PERSISTENCE
# ──────────────────────────────────────────────────────────────────────────────

def load_json(filename, default):
    """Reads filename if it exists and returns the content; otherwise returns default."""
    try:
        with open(filename, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filename, data):
    """Writes data to filename in readable JSON format."""
    with open(filename, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# EMISSION FACTORS (source: internal data)
# Unit: kgCO₂eq per kg of material
# ──────────────────────────────────────────────────────────────────────────────
FATTORI = {
    "Plastica (mix)": {
        "t_verg": 2.50, "t_smalt": 1.00, "t_tratt": 0.10, "t_ric": 0.60,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
    "Carta/cartone": {
        "t_verg": 1.15, "t_smalt": 0.85, "t_tratt": 0.10, "t_ric": 0.45,
        "tipo": "biologico", "resa": 2.68, "f_equiv": 1.26,
    },
    "Vetro": {
        "t_verg": 0.90, "t_smalt": 0.15, "t_tratt": 0.05, "t_ric": 0.30,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
    "Metalli": {
        "t_verg": 3.50, "t_smalt": 0.20, "t_tratt": 0.10, "t_ric": 0.60,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
    "Legno": {
        "t_verg": 0.35, "t_smalt": 1.15, "t_tratt": 0.10, "t_ric": 0.20,
        "tipo": "biologico", "resa": 2.68, "f_equiv": 1.26,
    },
    "Toner": {
        "t_verg": 4.00, "t_smalt": 1.20, "t_tratt": 0.10, "t_ric": 1.50,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
    "Organico": {
        # t_verg = n.d.: aerobic digestion at Recall Latina, output undefined
        "t_verg": None, "t_smalt": 1.50, "t_tratt": 0.10, "t_ric": 0.20,
        "tipo": "biologico", "resa": 3.30, "f_equiv": 2.51,
    },
    "Indifferenziato": {
        "t_verg": None, "t_smalt": 1.10, "t_tratt": 0.10, "t_ric": 0.0,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
}

# Forest CO₂ absorption capacity (GFN): 0.95 tCO₂/ha/year × 1.26 = 1.197 tCO₂/gha/year
CO2_PER_GHA = 1.197

# Default vehicles — will be overridden by session_state data
# co2pkm_pieno = None means data not yet measured → linear fallback
VEICOLI_DEFAULT = [
    {"modello": "Fiat Ducato 35 L3H2", "co2pkm_vuoto": 0.18, "co2pkm_pieno": 0.30, "c_max": 1200},
    {"modello": "Iveco Daily 35S",      "co2pkm_vuoto": 0.21, "co2pkm_pieno": 0.37, "c_max": 1500},
]


# ──────────────────────────────────────────────────────────────────────────────
# CALCULATION FUNCTIONS
# ──────────────────────────────────────────────────────────────────────────────

def co2_per_km_log(carico, co2_vuoto, co2_pieno, c_max):
    """
    Logarithmic model: CO2/km as a function of load.
    Calibrated on two points: f(0) = co2_vuoto, f(c_max) = co2_pieno.
    b = (co2_pieno - co2_vuoto) / ln(1 + c_max)
    CO2perKm(C) = co2_vuoto + b * ln(1 + C)
    """
    b = (co2_pieno - co2_vuoto) / math.log(1 + c_max)
    return co2_vuoto + b * math.log(1 + carico)


def co2_per_km_lin(carico, co2_vuoto):
    """
    EEA linear fallback.
    Removed once all models have a measured co2pkm_pieno.
    """
    return co2_vuoto + carico * 0.00008


def co2_per_km(carico, veicolo):
    """Router: logarithmic if co2pkm_pieno is available, otherwise linear."""
    if veicolo["co2pkm_pieno"] is not None:
        return co2_per_km_log(carico, veicolo["co2pkm_vuoto"], veicolo["co2pkm_pieno"], veicolo["c_max"])
    return co2_per_km_lin(carico, veicolo["co2pkm_vuoto"])


def calcola_movimento(materiale, q_kg, d_operator_km, d_impianto_km, n_giro, carico_totale_kg, veicolo):
    """
    Calculates net CO2 and balance in gha for a single movement.

    P1 = Q * (T_tratt + T_ric - T_smalt - T_verg)
    P2 = (D_cliente * CO2perKm(0)) / N_giro
    P3 = (CO2perKm(0) / N_giro) * D_impianto + (CO2perKm(C) - CO2perKm(0)) * D_impianto * Conf

    gha_netti = -gha_impronta + bc_liberata
    Convention: positive = biocapacity, negative = ecological footprint
    """
    f = st.session_state.get("materiali", FATTORI)[materiale]
    conf         = q_kg / carico_totale_kg
    co2pkm_vuoto = co2_per_km(0, veicolo)
    co2pkm_car   = co2_per_km(carico_totale_kg, veicolo)
    t_verg = f["t_verg"] if f["t_verg"] is not None else 0.0

    no_recycling = f["t_ric"] == 0 and f["t_verg"] is None
    if no_recycling:
        p1 = q_kg * (f["t_tratt"] + f["t_smalt"])
    else:
        p1 = q_kg * (f["t_tratt"] + f["t_ric"] - f["t_smalt"] - t_verg)
    p2 = (d_operator_km * co2pkm_vuoto) / n_giro
    p3 = (d_impianto_km * co2pkm_vuoto) / n_giro + (co2pkm_car - co2pkm_vuoto) * d_impianto_km * conf

    co2_netta = p1 + p2 + p3
    gha_imp   = (co2_netta / 1000) / CO2_PER_GHA

    bc_lib = 0.0
    if f["tipo"] == "biologico" and f["resa"] and f["f_equiv"]:
        bc_lib = (q_kg / 1000) / f["resa"] * f["f_equiv"]

    return {
        "co2_p1": p1, "co2_p2": p2, "co2_p3": p3,
        "co2_netta": co2_netta,
        "gha_impronta": gha_imp,
        "bc_liberata": bc_lib,
        "gha_netti": -gha_imp + bc_lib,
        "modello_curva": "logarithmic" if veicolo["co2pkm_pieno"] is not None else "linear (fallback)",
        "co2pkm_vuoto": co2pkm_vuoto,
        "co2pkm_carico": co2pkm_car,
        "conf": conf,
        "no_recycling": no_recycling,
    }


# ──────────────────────────────────────────────────────────────────────────────
# UI — COMPONENTS
# ──────────────────────────────────────────────────────────────────────────────

def badge_saldo(gha):
    if gha >= 0:
        return (
            f'<div style="background:#0a2e1f;border:2px solid #52FFB8;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#52FFB8;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'BIOCAPACITY RELEASED</div>'
            f'<div style="color:#52FFB8;font-size:2rem;font-weight:800">+{gha:.4f} gha</div></div>'
        )
    else:
        return (
            f'<div style="background:#2e0a0a;border:2px solid #FF4B4B;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#FF4B4B;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'ECOLOGICAL FOOTPRINT</div>'
            f'<div style="color:#FF4B4B;font-size:2rem;font-weight:800">{gha:.4f} gha</div></div>'
        )


def sezione_veicoli():
    st.markdown("### Vehicle Management")
    st.caption(
        "Enter the van models used by the operator. "
        "CO2/km full load can be left blank until the actual measurement is available: "
        "the linear model will be used as fallback."
    )

    if "veicoli" not in st.session_state:
        st.session_state.veicoli = VEICOLI_DEFAULT.copy()

    if st.session_state.veicoli:
        df_v = pd.DataFrame(st.session_state.veicoli)
        df_v["co2pkm_pieno"] = df_v["co2pkm_pieno"].apply(
            lambda x: x if x is not None else "— (to be measured)"
        )
        df_v.columns = ["Model", "CO2/km empty", "CO2/km full", "C_max (kg)"]
        st.dataframe(df_v, hide_index=True, width='stretch')
    else:
        st.info("No vehicles entered.")

    st.markdown("**Add vehicle**")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        nuovo_modello = st.text_input("Model name", key="input_modello", placeholder="e.g. Fiat Ducato 35 L3H2")
    with c2:
        nuovo_vuoto = st.number_input("CO2/km empty", min_value=0.05, value=0.18, step=0.01, key="input_vuoto")
    with c3:
        nuovo_pieno_str = st.text_input("CO2/km full (opt.)", key="input_pieno", placeholder="e.g. 0.24")
    with c4:
        nuovo_cmax = st.number_input("C_max (kg)", min_value=100, value=1200, step=100, key="input_cmax")

    if st.button("Add vehicle"):
        if not nuovo_modello.strip():
            st.warning("Enter the model name.")
        else:
            try:
                pieno = float(nuovo_pieno_str) if nuovo_pieno_str.strip() else None
            except ValueError:
                pieno = None
                st.warning("Invalid CO2/km full load — linear fallback will be used.")

            st.session_state.veicoli.append({
                "modello": nuovo_modello.strip(),
                "co2pkm_vuoto": nuovo_vuoto,
                "co2pkm_pieno": pieno,
                "c_max": nuovo_cmax,
            })
            save_json("veicoli.json", st.session_state.veicoli)
            st.success(f"Vehicle '{nuovo_modello}' added.")
            st.rerun()

    if st.session_state.veicoli:
        modelli_esistenti = [v["modello"] for v in st.session_state.veicoli]
        col_del1, col_del2 = st.columns([2, 1])
        with col_del1:
            da_rimuovere = st.selectbox("Remove vehicle", modelli_esistenti, key="sel_rimuovi")
        with col_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Remove"):
                st.session_state.veicoli = [
                    v for v in st.session_state.veicoli if v["modello"] != da_rimuovere
                ]
                save_json("veicoli.json", st.session_state.veicoli)
                st.rerun()

    if st.session_state.veicoli:
        st.markdown("**Edit existing vehicle**")

        modelli_mod = [v["modello"] for v in st.session_state.veicoli]

        # Reset editing fields when the selected vehicle changes
        if "ultimo_veicolo_modifica" not in st.session_state:
            st.session_state.ultimo_veicolo_modifica = modelli_mod[0]

        sel_mod = st.selectbox("Select vehicle to edit", modelli_mod, key="sel_modifica")

        if sel_mod != st.session_state.ultimo_veicolo_modifica:
            for k in ("edit_modello", "edit_vuoto", "edit_pieno", "edit_cmax"):
                st.session_state.pop(k, None)
            st.session_state.ultimo_veicolo_modifica = sel_mod

        v_mod = next(v for v in st.session_state.veicoli if v["modello"] == sel_mod)
        pieno_default = str(v_mod["co2pkm_pieno"]) if v_mod["co2pkm_pieno"] is not None else ""

        ec1, ec2, ec3, ec4 = st.columns([2, 1, 1, 1])
        with ec1:
            edit_modello = st.text_input("Model name", value=v_mod["modello"], key="edit_modello")
        with ec2:
            edit_vuoto = st.number_input("CO2/km empty", min_value=0.05, value=v_mod["co2pkm_vuoto"], step=0.01, key="edit_vuoto")
        with ec3:
            edit_pieno_str = st.text_input("CO2/km full (opt.)", value=pieno_default, key="edit_pieno")
        with ec4:
            edit_cmax = st.number_input("C_max (kg)", min_value=100, value=v_mod["c_max"], step=100, key="edit_cmax")

        if st.button("Save changes"):
            if not edit_modello.strip():
                st.warning("Model name cannot be empty.")
            else:
                altri_modelli = [v["modello"] for v in st.session_state.veicoli if v["modello"] != sel_mod]
                if edit_modello.strip() in altri_modelli:
                    st.warning(f"A vehicle named '{edit_modello.strip()}' already exists.")
                else:
                    try:
                        pieno = float(edit_pieno_str) if edit_pieno_str.strip() else None
                    except ValueError:
                        pieno = None
                        st.warning("Invalid CO2/km full load — linear fallback will be used.")
                    idx = next(i for i, v in enumerate(st.session_state.veicoli) if v["modello"] == sel_mod)
                    st.session_state.veicoli[idx] = {
                        "modello": edit_modello.strip(),
                        "co2pkm_vuoto": edit_vuoto,
                        "co2pkm_pieno": pieno,
                        "c_max": edit_cmax,
                    }
                    save_json("veicoli.json", st.session_state.veicoli)
                    st.session_state.ultimo_veicolo_modifica = edit_modello.strip()
                    st.success(f"Vehicle '{edit_modello.strip()}' updated.")
                    st.rerun()


def sezione_materiali():
    st.markdown("### Material Management")
    st.caption(
        "Edit the emission factors used in the calculation. "
        "T_verg, resa and f_equiv can be left blank (n.d.)."
    )

    # ── Table ─────────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        rows = []
        for nome, f in st.session_state.materiali.items():
            rows.append({
                "Material":    nome,
                "Type":        f["tipo"],
                "T_verg":      f["t_verg"] if f["t_verg"] is not None else "n.d.",
                "T_smalt":     f["t_smalt"],
                "T_tratt":     f["t_tratt"],
                "T_ric":       f["t_ric"],
                "Yield (t/ha)": f["resa"] if f["resa"] is not None else "—",
                "F_equiv":     f["f_equiv"] if f["f_equiv"] is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    else:
        st.info("No materials entered.")

    # ── Add ───────────────────────────────────────────────────────────────────
    st.markdown("**Add material**")
    a1, a2 = st.columns([2, 1])
    with a1:
        nuovo_nome = st.text_input("Material name", key="mat_input_nome", placeholder="e.g. Rubber")
    with a2:
        nuovo_tipo = st.selectbox("Type", ["abiotico", "biologico"], key="mat_input_tipo")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        nuovo_tverg_str = st.text_input(
            "Virgin production (kgCO₂/kg)", key="mat_input_tverg", placeholder="e.g. 2.50",
            help="CO₂ emitted to produce 1kg of virgin material",
        )
    with c2:
        nuovo_tsmalt = st.number_input(
            "Disposal (kgCO₂/kg)", min_value=0.0, value=1.0, step=0.01, key="mat_input_tsmalt",
            help="CO₂ emitted to dispose of 1kg without recycling",
        )
    with c3:
        nuovo_ttratt = st.number_input(
            "Treatment (kgCO₂/kg)", min_value=0.0, value=0.10, step=0.01, key="mat_input_ttratt",
            help="CO₂ emitted for pre-recycling treatment of 1kg",
        )
    with c4:
        nuovo_tric = st.number_input(
            "Recycling (kgCO₂/kg)", min_value=0.0, value=0.60, step=0.01, key="mat_input_tric",
            help="CO₂ emitted by the recycling process for 1kg",
        )
    if nuovo_tipo == "biologico":
        d1, d2 = st.columns(2)
        with d1:
            nuovo_resa_str = st.text_input(
                "Crop yield (t/ha/year)", key="mat_input_resa", placeholder="e.g. 2.68",
                help="Tonnes of biomass produced per hectare per year — source GFN",
            )
        with d2:
            nuovo_fequiv_str = st.text_input(
                "Land equivalence (gha/ha)", key="mat_input_fequiv", placeholder="e.g. 1.26",
                help="Conversion from physical hectares to global hectares — source GFN",
            )
    else:
        nuovo_resa_str = ""
        nuovo_fequiv_str = ""

    if st.button("Add material"):
        if not nuovo_nome.strip():
            st.warning("Enter the material name.")
        elif nuovo_nome.strip() in st.session_state.materiali:
            st.warning(f"A material named '{nuovo_nome.strip()}' already exists.")
        else:
            try:
                tverg = float(nuovo_tverg_str) if nuovo_tverg_str.strip() else None
            except ValueError:
                tverg = None
                st.warning("Invalid T_verg — set to n.d.")
            if nuovo_tipo == "biologico":
                try:
                    resa = float(nuovo_resa_str) if nuovo_resa_str.strip() else None
                except ValueError:
                    resa = None
                    st.warning("Invalid resa — set to n.d.")
                try:
                    fequiv = float(nuovo_fequiv_str) if nuovo_fequiv_str.strip() else None
                except ValueError:
                    fequiv = None
                    st.warning("Invalid F_equiv — set to n.d.")
            else:
                resa = None
                fequiv = None
            st.session_state.materiali[nuovo_nome.strip()] = {
                "t_verg": tverg, "t_smalt": nuovo_tsmalt, "t_tratt": nuovo_ttratt,
                "t_ric": nuovo_tric, "tipo": nuovo_tipo, "resa": resa, "f_equiv": fequiv,
            }
            save_json("materiali.json", st.session_state.materiali)
            st.success(f"Material '{nuovo_nome.strip()}' added.")
            st.rerun()

    # ── Remove ────────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        nomi = list(st.session_state.materiali.keys())
        col_del1, col_del2 = st.columns([2, 1])
        with col_del1:
            da_rimuovere = st.selectbox("Remove material", nomi, key="mat_sel_rimuovi")
        with col_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Remove", key="mat_btn_rimuovi"):
                del st.session_state.materiali[da_rimuovere]
                save_json("materiali.json", st.session_state.materiali)
                st.rerun()

    # ── Edit ──────────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        st.markdown("**Edit existing material**")
        nomi_mod = list(st.session_state.materiali.keys())

        if "ultimo_mat_modifica" not in st.session_state:
            st.session_state.ultimo_mat_modifica = nomi_mod[0]

        sel_mat = st.selectbox("Select material to edit", nomi_mod, key="mat_sel_modifica")

        if sel_mat != st.session_state.ultimo_mat_modifica:
            for k in ("mat_edit_nome", "mat_edit_tipo", "mat_edit_tverg", "mat_edit_tsmalt",
                      "mat_edit_ttratt", "mat_edit_tric", "mat_edit_resa", "mat_edit_fequiv"):
                st.session_state.pop(k, None)
            st.session_state.ultimo_mat_modifica = sel_mat

        m = st.session_state.materiali[sel_mat]
        tipo_opts = ["abiotico", "biologico"]
        tipo_idx  = tipo_opts.index(m["tipo"]) if m["tipo"] in tipo_opts else 0
        tverg_default  = str(m["t_verg"])  if m["t_verg"]  is not None else ""
        resa_default   = str(m["resa"])    if m["resa"]    is not None else ""
        fequiv_default = str(m["f_equiv"]) if m["f_equiv"] is not None else ""

        ea1, ea2 = st.columns([2, 1])
        with ea1:
            edit_nome = st.text_input("Material name", value=sel_mat, key="mat_edit_nome")
        with ea2:
            edit_tipo = st.selectbox("Type", tipo_opts, index=tipo_idx, key="mat_edit_tipo")
        ec1, ec2, ec3, ec4 = st.columns(4)
        with ec1:
            edit_tverg_str = st.text_input(
                "Virgin production (kgCO₂/kg)", value=tverg_default, key="mat_edit_tverg",
                help="CO₂ emitted to produce 1kg of virgin material",
            )
        with ec2:
            edit_tsmalt = st.number_input(
                "Disposal (kgCO₂/kg)", min_value=0.0, value=float(m["t_smalt"]), step=0.01,
                key="mat_edit_tsmalt", help="CO₂ emitted to dispose of 1kg without recycling",
            )
        with ec3:
            edit_ttratt = st.number_input(
                "Treatment (kgCO₂/kg)", min_value=0.0, value=float(m["t_tratt"]), step=0.01,
                key="mat_edit_ttratt", help="CO₂ emitted for pre-recycling treatment of 1kg",
            )
        with ec4:
            edit_tric = st.number_input(
                "Recycling (kgCO₂/kg)", min_value=0.0, value=float(m["t_ric"]), step=0.01,
                key="mat_edit_tric", help="CO₂ emitted by the recycling process for 1kg",
            )
        if edit_tipo == "biologico":
            ed1, ed2 = st.columns(2)
            with ed1:
                edit_resa_str = st.text_input(
                    "Crop yield (t/ha/year)", value=resa_default, key="mat_edit_resa",
                    help="Tonnes of biomass produced per hectare per year — source GFN",
                )
            with ed2:
                edit_fequiv_str = st.text_input(
                    "Land equivalence (gha/ha)", value=fequiv_default, key="mat_edit_fequiv",
                    help="Conversion from physical hectares to global hectares — source GFN",
                )
        else:
            edit_resa_str = ""
            edit_fequiv_str = ""

        if st.button("Save changes", key="mat_btn_salva"):
            if not edit_nome.strip():
                st.warning("Material name cannot be empty.")
            else:
                altri_nomi = [n for n in st.session_state.materiali if n != sel_mat]
                if edit_nome.strip() in altri_nomi:
                    st.warning(f"A material named '{edit_nome.strip()}' already exists.")
                else:
                    try:
                        tverg = float(edit_tverg_str) if edit_tverg_str.strip() else None
                    except ValueError:
                        tverg = None
                        st.warning("Invalid T_verg — set to n.d.")
                    if edit_tipo == "biologico":
                        try:
                            resa = float(edit_resa_str) if edit_resa_str.strip() else None
                        except ValueError:
                            resa = None
                            st.warning("Invalid resa — set to n.d.")
                        try:
                            fequiv = float(edit_fequiv_str) if edit_fequiv_str.strip() else None
                        except ValueError:
                            fequiv = None
                            st.warning("Invalid F_equiv — set to n.d.")
                    else:
                        resa = None
                        fequiv = None
                    nuovo_mat = {
                        "t_verg": tverg, "t_smalt": edit_tsmalt, "t_tratt": edit_ttratt,
                        "t_ric": edit_tric, "tipo": edit_tipo, "resa": resa, "f_equiv": fequiv,
                    }
                    if edit_nome.strip() != sel_mat:
                        # Rename: rebuild dict to preserve insertion order
                        st.session_state.materiali = {
                            (edit_nome.strip() if k == sel_mat else k): (nuovo_mat if k == sel_mat else v)
                            for k, v in st.session_state.materiali.items()
                        }
                    else:
                        st.session_state.materiali[sel_mat] = nuovo_mat
                    save_json("materiali.json", st.session_state.materiali)
                    st.session_state.ultimo_mat_modifica = edit_nome.strip()
                    st.success(f"Material '{edit_nome.strip()}' updated.")
                    st.rerun()


def pagina_calcolatore():
    st.markdown("### Movement data")

    if not st.session_state.get("veicoli"):
        st.warning("No vehicles available. Add at least one in the Vehicles section.")
        return

    col_form, col_out = st.columns([1, 1], gap="large")

    with col_form:
        modelli = [v["modello"] for v in st.session_state.veicoli]
        modello_sel = st.selectbox("Select vehicle", modelli)
        veicolo = next(v for v in st.session_state.veicoli if v["modello"] == modello_sel)

        pieno_str = f"{veicolo['co2pkm_pieno']} kg/km" if veicolo["co2pkm_pieno"] else "— (linear fallback)"
        st.caption(
            f"CO2/km empty: **{veicolo['co2pkm_vuoto']}** · "
            f"CO2/km full: **{pieno_str}** · "
            f"C_max: **{veicolo['c_max']} kg** · "
            f"Curve: **{'logarithmic' if veicolo['co2pkm_pieno'] else 'linear (fallback)'}**"
        )

        st.divider()

        materiale = st.selectbox("Material", list(st.session_state["materiali"].keys()))
        f = st.session_state["materiali"][materiale]

        q_kg = st.number_input(
            "Q — Kg delivered by client",
            min_value=0.1, value=100.0, step=10.0,
            help="Weight of waste delivered in this pickup"
        )

        st.markdown("**Logistics**")
        c1, c2 = st.columns(2)
        with c1:
            d_operator = st.number_input("D_client (km)", min_value=0.1, value=10.0, step=0.5,
                                         help="Distance operator → client local unit")
            d_imp  = st.number_input("D_plant (km)", min_value=0.1, value=15.0, step=0.5,
                                     help="Distance local unit → recycling plant")
        with c2:
            n_giro = st.number_input("N_trip", min_value=1, value=3, step=1,
                                     help="Clients sharing the operator→area leg")
            carico = st.number_input(
                "C — Total van load (kg)",
                min_value=q_kg,
                max_value=float(veicolo["c_max"]),
                value=min(max(q_kg * 3, 300.0), float(veicolo["c_max"])),
                step=50.0,
                help=f"Total weight in the van — max {veicolo['c_max']} kg for this model"
            )

        st.divider()

        st.markdown("**Applied emission factors** *(kgCO2/kg — source: internal data)*")
        df_f = pd.DataFrame([{
            "T_verg":  f["t_verg"] if f["t_verg"] is not None else "n.d.",
            "T_smalt": f["t_smalt"], "T_tratt": f["t_tratt"],
            "T_ric":   f["t_ric"],   "Type":    f["tipo"],
        }])
        st.dataframe(df_f, hide_index=True, width='stretch')

        calcola = st.button("Calculate", type="primary", width='stretch')

    with col_out:
        st.markdown("### Results")

        if not calcola:
            st.info("Fill in the form and press **Calculate** to see results.")
            return

        r = calcola_movimento(materiale, q_kg, d_operator, d_imp, n_giro, carico, veicolo)

        st.markdown(badge_saldo(r["gha_netti"]), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        if veicolo["co2pkm_pieno"] is None:
            st.warning("CO2/km full load not available — using linear model (fallback).")
        else:
            st.success("Logarithmic curve applied.")

        # Biocapacity from avoided CO₂ — shown only when co2_netta < 0
        bc_co2 = abs(r["gha_impronta"]) if r["co2_netta"] < 0 else None

        if bc_co2 is not None:
            # Virtuous recycling: two rows of 2 metrics each
            m1, m2 = st.columns(2)
            m1.metric("Net CO₂",                      f"{r['co2_netta']:+.2f} kg")
            m2.metric("Net balance",                   f"{r['gha_netti']:+.4f} gha")
            m3, m4 = st.columns(2)
            m3.metric("Biocapacity from avoided CO₂", f"+{bc_co2:.4f} gha")
            m4.metric("Biocapacity from land",         f"+{r['bc_liberata']:.4f} gha" if r["bc_liberata"] > 0 else "—")
        else:
            # Non-virtuous recycling: positive net CO₂ → footprint, no biocapacity from CO₂
            m1, m2, m3 = st.columns(3)
            m1.metric("Net CO₂",               f"{r['co2_netta']:+.2f} kg")
            m2.metric("Ecological footprint",  f"{r['gha_impronta']:.4f} gha")
            m3.metric("Biocapacity from land",  f"+{r['bc_liberata']:.4f} gha" if r["bc_liberata"] > 0 else "—")

        st.divider()

        st.markdown("**CO2 breakdown by component**")
        st.caption("P1 — Emissions balance of recycling vs. disposal and virgin production  |  P2 — Emissions of the operator → client leg, split by number of clients on the route  |  P3 — Emissions of the client → plant leg, fixed share split by route plus variable share proportional to delivered weight")

        # Order P1 → P2 → P3 top to bottom (Plotly horizontal is bottom-up, so we reverse)
        componenti = ["P3", "P2", "P1"]
        valori     = [r["co2_p3"], r["co2_p2"], r["co2_p1"]]
        colori     = ["#52FFB8" if v <= 0 else "#FF4B4B" for v in valori]
        etichette  = [f"P3: {r['co2_p3']:+.3f} kg", f"P2: {r['co2_p2']:+.3f} kg", f"P1: {r['co2_p1']:+.3f} kg"]

        fig = go.Figure(go.Bar(
            x=valori,
            y=componenti,
            orientation="h",
            marker=dict(color=colori, line=dict(width=0), cornerradius=6),
            width=0.35,
            text=None,  # handle labels manually with annotations
        ))

        fig.add_vline(x=0, line_color="#555555", line_dash="dash")

        # Fixed-position annotations to the right of the chart — aligned and spaced
        for i, (label, colore) in enumerate(zip(etichette, colori)):
            fig.add_annotation(
                x=1.02,           # outside plot area, in paper coordinates (0-1)
                y=i,
                xref="paper",
                yref="y",
                text=label,
                showarrow=False,
                xanchor="left",
                font=dict(color=colore, size=12),
            )

        fig.update_layout(
            plot_bgcolor="#0E1117",
            paper_bgcolor="#0E1117",
            height=240,
            margin=dict(l=0, r=120, t=10, b=30),  # right margin for labels
            xaxis=dict(
                title="kgCO2",
                title_font=dict(color="#AAAAAA"),
                tickfont=dict(color="#AAAAAA"),
                gridcolor="#2E2E2E",
                zerolinecolor="#555555",
            ),
            yaxis=dict(
                tickfont=dict(color="#CCCCCC"),
                gridcolor="#2E2E2E",
                ticklabelposition="outside left",
                side="left",
            ),
            font=dict(color="#CCCCCC"),
        )
        st.plotly_chart(fig, width='stretch')

        if veicolo["co2pkm_pieno"] is not None:
            import numpy as np
            b_curve = (veicolo["co2pkm_pieno"] - veicolo["co2pkm_vuoto"]) / math.log(1 + veicolo["c_max"])
            xs = np.linspace(0, veicolo["c_max"], 300)
            ys = veicolo["co2pkm_vuoto"] + b_curve * np.log(1 + xs)

            y_vuoto  = veicolo["co2pkm_vuoto"]
            y_pieno  = veicolo["co2pkm_pieno"]
            y_giro   = r["co2pkm_carico"]
            x_giro   = carico

            fig_curve = go.Figure()

            fig_curve.add_trace(go.Scatter(
                x=xs, y=ys,
                mode="lines",
                line=dict(color="#52FFB8", width=2),
                showlegend=False,
            ))

            fig_curve.add_trace(go.Scatter(
                x=[0, veicolo["c_max"]],
                y=[y_vuoto, y_pieno],
                mode="markers+text",
                marker=dict(color="white", size=8),
                text=["Empty", "Full"],
                textposition=["top right", "top left"],
                textfont=dict(color="white", size=11),
                showlegend=False,
            ))

            fig_curve.add_trace(go.Scatter(
                x=[x_giro],
                y=[y_giro],
                mode="markers+text",
                marker=dict(
                    color="#FF4B4B", size=12,
                    line=dict(color="#FF4B4B", width=2),
                    symbol="circle",
                ),
                text=["This trip"],
                textposition="top right",
                textfont=dict(color="#FF4B4B", size=11),
                showlegend=False,
            ))

            fig_curve.add_shape(
                type="line",
                x0=x_giro, x1=x_giro, y0=min(ys), y1=y_giro,
                line=dict(color="#FF4B4B", width=1, dash="dash"),
            )
            fig_curve.add_shape(
                type="line",
                x0=0, x1=x_giro, y0=y_giro, y1=y_giro,
                line=dict(color="#FF4B4B", width=1, dash="dash"),
            )

            fig_curve.update_layout(
                plot_bgcolor="#0E1117",
                paper_bgcolor="#0E1117",
                height=250,
                margin=dict(l=0, r=20, t=10, b=30),
                xaxis=dict(
                    title="Load (kg)",
                    title_font=dict(color="#AAAAAA"),
                    tickfont=dict(color="#AAAAAA"),
                    showgrid=False,
                    zeroline=False,
                ),
                yaxis=dict(
                    title="CO₂/km (kg/km)",
                    title_font=dict(color="#AAAAAA"),
                    tickfont=dict(color="#AAAAAA"),
                    showgrid=False,
                    zeroline=False,
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_curve, width='stretch')

        st.divider()

        with st.expander("Step-by-step calculation detail"):
            t_verg_val = f["t_verg"] if f["t_verg"] is not None else 0.0

            if veicolo["co2pkm_pieno"] is not None:
                b = (veicolo["co2pkm_pieno"] - veicolo["co2pkm_vuoto"]) / math.log(1 + veicolo["c_max"])
                curva_str = (
                    f"b = ({veicolo['co2pkm_pieno']} - {veicolo['co2pkm_vuoto']}) "
                    f"/ ln(1 + {veicolo['c_max']}) = **{b:.6f}**\n\n"
                    f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + {b:.6f} x ln(1 + C)"
                )
            else:
                curva_str = f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + C x 0.00008  *(linear — fallback)*"

            st.markdown(f"""
**Emissions curve ({r['modello_curva']}):**
{curva_str}

| Variable | Value |
|-----------|--------|
| Q (kg delivered) | {q_kg} kg |
| Conf = Q / C | {r['conf']:.4f} |
| CO2perKm(0) empty | {r['co2pkm_vuoto']:.5f} kg/km |
| CO2perKm(C) loaded | {r['co2pkm_carico']:.5f} kg/km |
""")

            if r["no_recycling"]:
                st.markdown(f"""
*(Material without recycling pathway — calculation uses only treatment and disposal cost)*

**P1** = {q_kg} x ({f['t_tratt']} + {f['t_smalt']}) = **{r['co2_p1']:+.3f} kg CO2**

**P2** = ({d_operator} x {r['co2pkm_vuoto']:.5f}) / {n_giro} = **{r['co2_p2']:+.3f} kg CO2**

**P3** = ({d_imp} x {r['co2pkm_vuoto']:.5f}) / {n_giro} + ({r['co2pkm_carico']:.5f} - {r['co2pkm_vuoto']:.5f}) x {d_imp} x {r['conf']:.4f} = **{r['co2_p3']:+.3f} kg CO2**

**Net CO₂** = P1 + P2 + P3 = **{r['co2_netta']:+.3f} kg CO2**

**Ecological footprint** = ({r['co2_netta']:.3f} / 1000) / {CO2_PER_GHA} = **{r['gha_impronta']:.5f} gha**

**Net balance** = **{r['gha_netti']:+.5f} gha**
""")
            else:
                st.markdown(f"""
**P1** = {q_kg} x ({f['t_tratt']} + {f['t_ric']} - {f['t_smalt']} - {t_verg_val}) = **{r['co2_p1']:+.3f} kg CO2**

**P2** = ({d_operator} x {r['co2pkm_vuoto']:.5f}) / {n_giro} = **{r['co2_p2']:+.3f} kg CO2**

**P3** = ({d_imp} x {r['co2pkm_vuoto']:.5f}) / {n_giro} + ({r['co2pkm_carico']:.5f} - {r['co2pkm_vuoto']:.5f}) x {d_imp} x {r['conf']:.4f} = **{r['co2_p3']:+.3f} kg CO2**

**Net CO₂** = P1 + P2 + P3 = **{r['co2_netta']:+.3f} kg CO2**

**Footprint in gha** = ({r['co2_netta']:.3f} / 1000) / {CO2_PER_GHA} = **{r['gha_impronta']:.5f} gha**
""")
                if f["tipo"] == "biologico" and f["resa"]:
                    bc_co2_str = f"\n**Biocapacity from avoided CO₂** = |{r['gha_impronta']:.5f}| = **{abs(r['gha_impronta']):.5f} gha**\n" if r["co2_netta"] < 0 else "\n*(Net CO₂ positive — no biocapacity from avoided CO₂)*\n"
                    st.markdown(f"""
**Biocapacity from land** = ({q_kg} / 1000) / {f['resa']} x {f['f_equiv']} = **{r['bc_liberata']:.5f} gha**
{bc_co2_str}
**Net balance** = -{r['gha_impronta']:.5f} + {r['bc_liberata']:.5f} = **{r['gha_netti']:+.5f} gha**
""")
                else:
                    bc_co2_str = f"\n**Biocapacity from avoided CO₂** = |{r['gha_impronta']:.5f}| = **{abs(r['gha_impronta']):.5f} gha**\n" if r["co2_netta"] < 0 else "\n*(Net CO₂ positive — no biocapacity from avoided CO₂)*\n"
                    st.markdown(f"""
*(Abiotic material — no land biocapacity)*
{bc_co2_str}
**Net balance** = -{r['gha_impronta']:.5f} = **{r['gha_netti']:+.5f} gha**
""")


def pagina_tabelle():
    st.markdown("### Reference tables")
    tab1, tab2 = st.tabs(["Emission factors", "Vehicles"])

    with tab1:
        st.caption("Source: internal data. Unit: kgCO2eq/kg.")
        rows = []
        for mat, f in FATTORI.items():
            rows.append({
                "Material":    mat,
                "Type":        f["tipo"],
                "T_verg":      f["t_verg"] if f["t_verg"] is not None else "n.d.",
                "T_smalt":     f["t_smalt"],
                "T_tratt":     f["t_tratt"],
                "T_ric":       f["t_ric"],
                "Yield (t/ha)": f["resa"] if f["resa"] else "—",
                "F_equiv":     f["f_equiv"] if f["f_equiv"] else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

    with tab2:
        st.caption("Vehicles in the current session.")
        if st.session_state.get("veicoli"):
            df_v = pd.DataFrame(st.session_state.veicoli)
            df_v["co2pkm_pieno"] = df_v["co2pkm_pieno"].apply(
                lambda x: x if x is not None else "— (to be measured)"
            )
            df_v.columns = ["Model", "CO2/km empty", "CO2/km full", "C_max (kg)"]
            st.dataframe(df_v, hide_index=True, width='stretch')
        else:
            st.info("No vehicles entered.")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Soluslab — Calculator",
        page_icon="🌿",
        layout="wide",
    )

    if "veicoli" not in st.session_state:
        st.session_state.veicoli = load_json("veicoli.json", VEICOLI_DEFAULT.copy())

    if "materiali" not in st.session_state:
        st.session_state.materiali = load_json(
            "materiali.json", {k: dict(v) for k, v in FATTORI.items()}
        )

    st.markdown("# Environmental Balance Calculator")
    st.caption("Soluslab — internal CO2 and biocapacity calculation tool for waste movements.")
    st.divider()

    sezione = st.radio(
        "Section", ["Calculator", "Vehicles", "Materials", "Tables"],
        horizontal=True, label_visibility="collapsed",
    )
    st.markdown("---")

    if sezione == "Calculator":
        pagina_calcolatore()
    elif sezione == "Vehicles":
        sezione_veicoli()
    elif sezione == "Materials":
        sezione_materiali()
    elif sezione == "Tables":
        pagina_tabelle()


if __name__ == "__main__":
    main()
