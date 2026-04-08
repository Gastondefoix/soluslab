"""
Soluslab — Calcolatore Saldo Ambientale
==========================================
Avvio: streamlit run app.py
Dipendenze: pip install streamlit pandas plotly
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
        "tipo": "biotico", "resa": 2.68, "f_equiv": 1.26,
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
        "tipo": "biotico", "resa": 2.68, "f_equiv": 1.26,
    },
    "Toner": {
        "t_verg": 4.00, "t_smalt": 1.20, "t_tratt": 0.10, "t_ric": 1.50,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
    "Organico": {
        # t_verg = n.d.: aerobic digestion at Recall Latina, output undefined
        "t_verg": None, "t_smalt": 1.50, "t_tratt": 0.10, "t_ric": 0.20,
        "tipo": "biotico", "resa": 3.30, "f_equiv": 2.51,
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


def calcola_movimento(materiale, q_kg, d_itinerario_km, d_baricentro_impianto_km, n_itinerario, carico_totale_kg, veicolo):
    """
    Calculates net CO2 and balance in gha for a single movement.

    no_recycling:
      S  = Q * (T_tratt + T_smalt)
    normal:
      S1 = -Q * (T_smalt + T_verg)
      S2 =  Q * (T_tratt + T_ric)
    T1 = (D_itinerario * CO2perKm(0)) / N_itinerario
    T2 = (CO2perKm(C) - CO2perKm(0)) * D_baricentro_impianto * Conf

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
        s  = q_kg * (f["t_tratt"] + f["t_smalt"])
        s1 = None
        s2 = None
    else:
        s  = None
        s1 = -q_kg * (f["t_smalt"] + t_verg)
        s2 =  q_kg * (f["t_tratt"] + f["t_ric"])
    t1 = (d_itinerario_km * co2pkm_vuoto) / n_itinerario
    t2 = (co2pkm_car - co2pkm_vuoto) * d_baricentro_impianto_km * conf

    if no_recycling:
        co2_netta = s + t1 + t2
    else:
        co2_netta = s1 + s2 + t1 + t2
    gha_imp = (co2_netta / 1000) / CO2_PER_GHA

    bc_lib = 0.0
    if f["tipo"] == "biotico" and f["resa"] and f["f_equiv"]:
        bc_lib = (q_kg / 1000) / f["resa"] * f["f_equiv"]

    return {
        "s": s, "s1": s1, "s2": s2, "t1": t1, "t2": t2,
        "co2_netta": co2_netta,
        "gha_impronta": gha_imp,
        "bc_liberata": bc_lib,
        "gha_netti": -gha_imp + bc_lib,
        "modello_curva": "logaritmico" if veicolo["co2pkm_pieno"] is not None else "lineare (fallback)",
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
            f'BIOCAPACITA LIBERATA</div>'
            f'<div style="color:#52FFB8;font-size:2rem;font-weight:800">+{gha:.3f} gha</div></div>'
        )
    else:
        return (
            f'<div style="background:#2e0a0a;border:2px solid #FF4B4B;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#FF4B4B;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'IMPRONTA ECOLOGICA</div>'
            f'<div style="color:#FF4B4B;font-size:2rem;font-weight:800">{gha:.3f} gha</div></div>'
        )


def sezione_veicoli():
    st.markdown("### Gestione Veicoli")
    st.caption(
        "Inserisci i modelli di furgone usati dall'operatore. "
        "CO2/km pieno può essere lasciato vuoto finché non è disponibile il dato reale: "
        "verrà usato il modello lineare come fallback."
    )

    if "veicoli" not in st.session_state:
        st.session_state.veicoli = VEICOLI_DEFAULT.copy()

    if st.session_state.veicoli:
        df_v = pd.DataFrame(st.session_state.veicoli)
        df_v["co2pkm_pieno"] = df_v["co2pkm_pieno"].apply(
            lambda x: x if x is not None else "— (da rilevare)"
        )
        df_v.columns = ["Modello", "CO2/km vuoto", "CO2/km pieno", "C_max (kg)"]
        st.dataframe(df_v, hide_index=True, width='stretch')
    else:
        st.info("Nessun veicolo inserito.")

    st.markdown("**Aggiungi veicolo**")
    c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
    with c1:
        nuovo_modello = st.text_input("Nome modello", key="input_modello", placeholder="es. Fiat Ducato 35 L3H2")
    with c2:
        nuovo_vuoto = st.number_input("CO2/km vuoto", min_value=0.01, value=0.18, step=0.01, format="%.2f", help="Emissioni CO₂ al km a furgone vuoto (kg/km)", key="input_vuoto")
    with c3:
        nuovo_pieno = st.number_input("CO2/km pieno (opz.)", min_value=0.01, value=0.24, step=0.01, format="%.2f", help="Emissioni CO₂ al km a pieno carico — usato per calibrare la curva logaritmica (kg/km)", key="input_pieno")
    with c4:
        nuovo_cmax = st.number_input("C_max (kg)", min_value=100, value=1200, step=100, help="Portata massima del veicolo in kg — corrisponde a f(C_max) nella curva logaritmica", key="input_cmax")

    if st.button("Aggiungi veicolo"):
        if not nuovo_modello.strip():
            st.warning("Inserisci il nome del modello.")
        else:
            pieno = nuovo_pieno

            st.session_state.veicoli.append({
                "modello": nuovo_modello.strip(),
                "co2pkm_vuoto": nuovo_vuoto,
                "co2pkm_pieno": pieno,
                "c_max": nuovo_cmax,
            })
            save_json("veicoli.json", st.session_state.veicoli)
            st.success(f"Veicolo '{nuovo_modello}' aggiunto.")
            st.rerun()

    if st.session_state.veicoli:
        modelli_esistenti = [v["modello"] for v in st.session_state.veicoli]
        col_del1, col_del2 = st.columns([2, 1])
        with col_del1:
            da_rimuovere = st.selectbox("Rimuovi veicolo", modelli_esistenti, key="sel_rimuovi")
        with col_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Rimuovi"):
                st.session_state.veicoli = [
                    v for v in st.session_state.veicoli if v["modello"] != da_rimuovere
                ]
                save_json("veicoli.json", st.session_state.veicoli)
                st.rerun()

    if st.session_state.veicoli:
        st.markdown("**Modifica veicolo esistente**")

        modelli_mod = [v["modello"] for v in st.session_state.veicoli]

        # Reset dei campi di editing quando cambia il veicolo selezionato
        if "ultimo_veicolo_modifica" not in st.session_state:
            st.session_state.ultimo_veicolo_modifica = modelli_mod[0]

        sel_mod = st.selectbox("Seleziona veicolo da modificare", modelli_mod, key="sel_modifica")

        if sel_mod != st.session_state.ultimo_veicolo_modifica:
            for k in ("edit_modello", "edit_vuoto", "edit_pieno", "edit_cmax"):
                st.session_state.pop(k, None)
            st.session_state.ultimo_veicolo_modifica = sel_mod

        v_mod = next(v for v in st.session_state.veicoli if v["modello"] == sel_mod)
        pieno_default = v_mod["co2pkm_pieno"] if v_mod["co2pkm_pieno"] is not None else 0.24

        ec1, ec2, ec3, ec4 = st.columns([2, 1, 1, 1])
        with ec1:
            edit_modello = st.text_input("Nome modello", value=v_mod["modello"], key="edit_modello")
        with ec2:
            edit_vuoto = st.number_input("CO2/km vuoto", min_value=0.01, value=v_mod["co2pkm_vuoto"], step=0.01, format="%.2f", help="Emissioni CO₂ al km a furgone vuoto (kg/km)", key="edit_vuoto")
        with ec3:
            edit_pieno = st.number_input("CO2/km pieno (opz.)", min_value=0.01, value=pieno_default, step=0.01, format="%.2f", help="Emissioni CO₂ al km a pieno carico — usato per calibrare la curva logaritmica (kg/km)", key="edit_pieno")
        with ec4:
            edit_cmax = st.number_input("C_max (kg)", min_value=100, value=v_mod["c_max"], step=100, help="Portata massima del veicolo in kg — corrisponde a f(C_max) nella curva logaritmica", key="edit_cmax")

        if st.button("Salva modifiche"):
            if not edit_modello.strip():
                st.warning("Il nome del modello non può essere vuoto.")
            else:
                altri_modelli = [v["modello"] for v in st.session_state.veicoli if v["modello"] != sel_mod]
                if edit_modello.strip() in altri_modelli:
                    st.warning(f"Esiste già un veicolo con il nome '{edit_modello.strip()}'.")
                else:
                    pieno = edit_pieno
                    idx = next(i for i, v in enumerate(st.session_state.veicoli) if v["modello"] == sel_mod)
                    st.session_state.veicoli[idx] = {
                        "modello": edit_modello.strip(),
                        "co2pkm_vuoto": edit_vuoto,
                        "co2pkm_pieno": pieno,
                        "c_max": edit_cmax,
                    }
                    save_json("veicoli.json", st.session_state.veicoli)
                    st.session_state.ultimo_veicolo_modifica = edit_modello.strip()
                    st.success(f"Veicolo '{edit_modello.strip()}' aggiornato.")
                    st.rerun()


def sezione_materiali():
    st.markdown("### Gestione Materiali")
    st.caption(
        "Modifica i fattori emissivi usati nel calcolo. "
        "Per i materiali biotici assicurati di inserire anche resa e fattore di equivalenza territoriale "
    )

    # ── Tabella ───────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        rows = []
        for nome, f in st.session_state.materiali.items():
            rows.append({
                "Materiale": nome,
                "Tipo":      f["tipo"],
                "T_verg":    f["t_verg"] if f["t_verg"] is not None else "n.d.",
                "T_smalt":   f["t_smalt"],
                "T_tratt":   f["t_tratt"],
                "T_ric":     f["t_ric"],
                "Resa (t/ha)": f["resa"] if f["resa"] is not None else "—",
                "F_equiv":   f["f_equiv"] if f["f_equiv"] is not None else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
    else:
        st.info("Nessun materiale inserito.")

    # ── Aggiungi ──────────────────────────────────────────────────────────────
    st.markdown("**Aggiungi materiale**")
    a1, a2 = st.columns([2, 1])
    with a1:
        nuovo_nome = st.text_input("Nome materiale", key="mat_input_nome", placeholder="es. Gomma")
    with a2:
        nuovo_tipo = st.selectbox("Tipo", ["abiotico", "biotico"], key="mat_input_tipo")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        nuovo_tverg_str = st.number_input(
            "Produzione vergine (kgCO₂/kg)",  min_value=0.0, value=1.0, step=0.01, key="mat_input_tverg",
            help="CO₂ emessa per produrre 1kg di materia vergine",
        )
    with c2:
        nuovo_tsmalt = st.number_input(
            "Smaltimento (kgCO₂/kg)", min_value=0.0, value=1.0, step=0.01, key="mat_input_tsmalt",
            help="CO₂ emessa per smaltire 1kg senza riciclo",
        )
    with c3:
        nuovo_ttratt = st.number_input(
            "Trattamento (kgCO₂/kg)", min_value=0.0, value=0.10, step=0.01, key="mat_input_ttratt",
            help="CO₂ emessa per il trattamento pre-riciclo di 1kg",
        )
    with c4:
        nuovo_tric = st.number_input(
            "Riciclo (kgCO₂/kg)", min_value=0.0, value=0.60, step=0.01, key="mat_input_tric",
            help="CO₂ emessa dal processo di riciclo di 1kg",
        )
    if nuovo_tipo == "biotico":
        d1, d2 = st.columns(2)
        with d1:
            nuovo_resa = st.number_input(
                "Resa coltura (t/ha/anno)", min_value=0.0, value=2.68, step=0.01, key="mat_input_resa",
                help="Tonnellate di biomassa prodotte per ettaro all'anno — fonte GFN",
            )
        with d2:
            nuovo_fequiv = st.number_input(
                "Equivalenza territoriale (gha/ha)", min_value=0.0, value=1.26, step=0.01, key="mat_input_fequiv",
                help="Conversione da ettari fisici a ettari globali — fonte GFN",
            )
    else:
        nuovo_resa = 0.0
        nuovo_fequiv = 0.0

    if st.button("Aggiungi materiale"):
        if not nuovo_nome.strip():
            st.warning("Inserisci il nome del materiale.")
        elif nuovo_nome.strip() in st.session_state.materiali:
            st.warning(f"Esiste già un materiale con il nome '{nuovo_nome.strip()}'.")
        else:
            tverg = nuovo_tverg_str if nuovo_tverg_str > 0 else None
            if nuovo_tipo == "biotico":
                resa = nuovo_resa if nuovo_resa > 0 else None
                fequiv = nuovo_fequiv if nuovo_fequiv > 0 else None
            else:
                resa = None
                fequiv = None
            st.session_state.materiali[nuovo_nome.strip()] = {
                "t_verg": tverg, "t_smalt": nuovo_tsmalt, "t_tratt": nuovo_ttratt,
                "t_ric": nuovo_tric, "tipo": nuovo_tipo, "resa": resa, "f_equiv": fequiv,
            }
            save_json("materiali.json", st.session_state.materiali)
            st.success(f"Materiale '{nuovo_nome.strip()}' aggiunto.")
            st.rerun()

    # ── Rimuovi ───────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        nomi = list(st.session_state.materiali.keys())
        col_del1, col_del2 = st.columns([2, 1])
        with col_del1:
            da_rimuovere = st.selectbox("Rimuovi materiale", nomi, key="mat_sel_rimuovi")
        with col_del2:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Rimuovi", key="mat_btn_rimuovi"):
                del st.session_state.materiali[da_rimuovere]
                save_json("materiali.json", st.session_state.materiali)
                st.rerun()

    # ── Modifica ──────────────────────────────────────────────────────────────
    if st.session_state.materiali:
        st.markdown("**Modifica materiale esistente**")
        nomi_mod = list(st.session_state.materiali.keys())

        if "ultimo_mat_modifica" not in st.session_state:
            st.session_state.ultimo_mat_modifica = nomi_mod[0]

        sel_mat = st.selectbox("Seleziona materiale da modificare", nomi_mod, key="mat_sel_modifica")

        if sel_mat != st.session_state.ultimo_mat_modifica:
            for k in ("mat_edit_nome", "mat_edit_tipo", "mat_edit_tverg", "mat_edit_tsmalt",
                      "mat_edit_ttratt", "mat_edit_tric", "mat_edit_resa", "mat_edit_fequiv"):
                st.session_state.pop(k, None)
            st.session_state.ultimo_mat_modifica = sel_mat

        m = st.session_state.materiali[sel_mat]
        tipo_opts = ["abiotico", "biotico"]
        tipo_idx  = tipo_opts.index(m["tipo"]) if m["tipo"] in tipo_opts else 0
        tverg_default  = m["t_verg"]  if m["t_verg"]  is not None else 0.0
        resa_default   = m["resa"]    if m["resa"]    is not None else 0.0
        fequiv_default = m["f_equiv"] if m["f_equiv"] is not None else 0.0

        ea1, ea2 = st.columns([2, 1])
        with ea1:
            edit_nome = st.text_input("Nome materiale", value=sel_mat, key="mat_edit_nome")
        with ea2:
            edit_tipo = st.selectbox("Tipo", tipo_opts, index=tipo_idx, key="mat_edit_tipo")
        ec1, ec2, ec3, ec4 = st.columns(4)
        with ec1:
            edit_tverg_str = st.number_input(
                "Produzione vergine (kgCO₂/kg)", min_value=0.0, value=tverg_default, step=0.01,
                key="mat_edit_tverg", help="CO₂ emessa per produrre 1kg di materia vergine",
            )
        with ec2:
            edit_tsmalt = st.number_input(
                "Smaltimento (kgCO₂/kg)", min_value=0.0, value=m["t_smalt"], step=0.01,
                key="mat_edit_tsmalt", help="CO₂ emessa per smaltire 1kg senza riciclo",
            )
        with ec3:
            edit_ttratt = st.number_input(
                "Trattamento (kgCO₂/kg)", min_value=0.0, value=m["t_tratt"], step=0.01,
                key="mat_edit_ttratt", help="CO₂ emessa per il trattamento pre-riciclo di 1kg",
            )
        with ec4:
            edit_tric = st.number_input(
                "Riciclo (kgCO₂/kg)", min_value=0.0, value=m["t_ric"], step=0.01,
                key="mat_edit_tric", help="CO₂ emessa dal processo di riciclo di 1kg",
            )
        if edit_tipo == "biotico":
            ed1, ed2 = st.columns(2)
            with ed1:
                edit_resa = st.number_input(
                    "Resa coltura (t/ha/anno)", min_value=0.0, value=resa_default, step=0.01, key="mat_edit_resa",
                    help="Tonnellate di biomassa prodotte per ettaro all'anno — fonte GFN",
                )
            with ed2:
                edit_fequiv = st.number_input(
                    "Equivalenza territoriale (gha/ha)", min_value=0.0, value=fequiv_default, step=0.01, key="mat_edit_fequiv",
                    help="Conversione da ettari fisici a ettari globali — fonte GFN",
                )
        else:
            edit_resa = 0.0
            edit_fequiv = 0.0

        if st.button("Salva modifiche", key="mat_btn_salva"):
            if not edit_nome.strip():
                st.warning("Il nome del materiale non può essere vuoto.")
            else:
                altri_nomi = [n for n in st.session_state.materiali if n != sel_mat]
                if edit_nome.strip() in altri_nomi:
                    st.warning(f"Esiste già un materiale con il nome '{edit_nome.strip()}'.")
                else:
                    tverg = edit_tverg_str if edit_tverg_str > 0 else None
                    if edit_tipo == "biotico":
                        resa = edit_resa if edit_resa > 0 else None
                        fequiv = edit_fequiv if edit_fequiv > 0 else None
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
                    st.success(f"Materiale '{edit_nome.strip()}' aggiornato.")
                    st.rerun()


def pagina_calcolatore():
    st.markdown("### Dati movimento")

    if not st.session_state.get("veicoli"):
        st.warning("Nessun veicolo disponibile. Aggiungine almeno uno nella sezione Veicoli.")
        return

    col_form, col_out = st.columns([1, 1], gap="large")

    with col_form:
        modelli = [v["modello"] for v in st.session_state.veicoli]
        modello_sel = st.selectbox("Seleziona veicolo", modelli)
        veicolo = next(v for v in st.session_state.veicoli if v["modello"] == modello_sel)

        pieno_str = f"{veicolo['co2pkm_pieno']} kg/km" if veicolo["co2pkm_pieno"] else "— (fallback lineare)"
        st.caption(
            f"CO2/km vuoto: **{veicolo['co2pkm_vuoto']}** · "
            f"CO2/km pieno: **{pieno_str}** · "
            f"C_max: **{veicolo['c_max']} kg** · "
            f"Curva: **{'logaritmica' if veicolo['co2pkm_pieno'] else 'lineare (fallback)'}**"
        )

        st.divider()

        materiale = st.selectbox("Materiale", list(st.session_state["materiali"].keys()))
        f = st.session_state["materiali"][materiale]

        q_kg = st.number_input(
            "Q — Kg conferiti dal cliente",
            min_value=0.1, value=100.0, step=10.0,
            help="Peso del rifiuto consegnato in questo ritiro"
        )

        st.markdown("**Logistica**")
        c1, c2 = st.columns(2)
        
        with c1:
            n_itinerario = st.number_input("N_itinerario", min_value=1, value=3, step=1,
                                           help="Numero di clienti nel giro")
            carico = st.number_input(
                "C — Carico totale furgone (kg)",
                min_value=q_kg,
                max_value=float(veicolo["c_max"]),
                value=min(max(q_kg * 3, 300.0), float(veicolo["c_max"])),
                step=1.0,
                help=f"Peso totale nel furgone — max {veicolo['c_max']} kg per questo modello"
            )
        with c2:
            d_baricentro_impianto = st.number_input(
                "D_baricentro_impianto (km)",
                min_value=1.0,
                value=50.0,
                step=1.0,
                help="Distanza dal baricentro dei clienti all'impianto di riciclo",
            )
            d_itinerario = st.number_input(
                "D_itinerario (km)",
                min_value=d_baricentro_impianto,
                value=max(100.0, d_baricentro_impianto),
                step=1.0,
                help="Distanza totale del giro circolare: Ecof → clienti → impianto → Ecof",
            )

        st.divider()

        st.markdown("**Fattori emissivi applicati** *(kgCO2/kg — fonte: dati interni)*")
        df_f = pd.DataFrame([{
            "T_verg":  f["t_verg"] if f["t_verg"] is not None else "n.d.",
            "T_smalt": f["t_smalt"], "T_tratt": f["t_tratt"],
            "T_ric":   f["t_ric"],   "Tipo":    f["tipo"],
        }])
        st.dataframe(df_f, hide_index=True, width='stretch')

        calcola = st.button("Calcola", type="primary", width='stretch')

    with col_out:
        st.markdown("### Risultati")

        if not calcola:
            st.info("Compila il form e premi **Calcola** per vedere i risultati.")
            return

        r = calcola_movimento(materiale, q_kg, d_itinerario, d_baricentro_impianto, n_itinerario, carico, veicolo)

        st.markdown(badge_saldo(r["gha_netti"]), unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)

        if veicolo["co2pkm_pieno"] is None:
            st.warning("CO2/km pieno non disponibile — usato modello lineare (fallback).")
        else:
            st.success("Curva logaritmica applicata.")

        # Biocapacità da CO₂ evitata — mostrata solo quando co2_netta < 0
        bc_co2 = abs(r["gha_impronta"]) if r["co2_netta"] < 0 else None

        if bc_co2 is not None:
            # Riciclo virtuoso: due righe da 2 metriche ciascuna
            m1, m2 = st.columns(2)
            m1.metric("CO₂ netta",                  f"{r['co2_netta']:+.3f} kg")
            m2.metric("Saldo netto",                 f"{r['gha_netti']:+.3f} gha")
            m3, m4 = st.columns(2)
            m3.metric("Biocapacità da CO₂ evitata", f"+{bc_co2:.3f} gha")
            m4.metric("Biocapacità da terreno",      f"+{r['bc_liberata']:.3f} gha" if r["bc_liberata"] > 0 else "—")
        else:
            # Riciclo non virtuoso: CO₂ netta positiva → impronta, nessuna biocapacità da CO₂
            m1, m2, m3 = st.columns(3)
            m1.metric("CO₂ netta",             f"{r['co2_netta']:+.3f} kg")
            m2.metric("Impronta ecologica",    f"{r['gha_impronta']:.3f} gha")
            m3.metric("Biocapacità da terreno", f"+{r['bc_liberata']:.3f} gha" if r["bc_liberata"] > 0 else "—")

        st.divider()

        st.markdown("**Scomposizione CO2 per componente**")
        st.caption("S1 — Emissioni evitate grazie al riciclo  |  S2 — Emissioni generate dal processo di riciclo  |  T1 — Emissioni del tragitto itinerario, ripartite per numero di clienti  |  T2 — Incremento delle emissioni dovuto al carico, proporzionale al peso conferito")

        if r["no_recycling"]:
            componenti = ["T2", "T1", "S"]
            valori     = [r["t2"], r["t1"], r["s"]]
            colori     = ["#FF4B4B", "#FF4B4B", "#FF4B4B"]
            etichette  = [f"T2: {r['t2']:+.3f} kg", f"T1: {r['t1']:+.3f} kg", f"S: {r['s']:+.3f} kg"]
        else:
            componenti = ["T2", "T1", "S2", "S1"]
            valori     = [r["t2"], r["t1"], r["s2"], r["s1"]]
            colori     = ["#FF4B4B", "#FF4B4B", "#FF4B4B", "#52FFB8"]
            etichette  = [f"T2: {r['t2']:+.3f} kg", f"T1: {r['t1']:+.3f} kg", f"S2: {r['s2']:+.3f} kg", f"S1: {r['s1']:+.3f} kg"]

        fig = go.Figure(go.Bar(
            x=valori,
            y=componenti,
            orientation="h",
            marker=dict(color=colori, line=dict(width=0), cornerradius=6),
            width=0.35,
            text=None,  # gestiamo le etichette manualmente con annotazioni
        ))

        fig.add_vline(x=0, line_color="#555555", line_dash="dash")

        # Annotazioni a posizione fissa a destra del grafico — allineate e distanziate
        for i, (label, colore) in enumerate(zip(etichette, colori)):
            fig.add_annotation(
                x=1.02,           # fuori dall'area plot, in coordinate paper (0-1)
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
            height=300,
            margin=dict(l=0, r=130, t=30, b=30),  # margine destro per le etichette
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
                line=dict(color="#4A90D9", width=2),
                showlegend=False,
            ))

            fig_curve.add_trace(go.Scatter(
                x=[0, veicolo["c_max"]],
                y=[y_vuoto, y_pieno],
                mode="markers+text",
                marker=dict(color="white", size=8),
                text=["Vuoto", "Pieno"],
                textposition=["top right", "top left"],
                textfont=dict(color="white", size=11),
                showlegend=False,
            ))

            fig_curve.add_trace(go.Scatter(
                x=[x_giro],
                y=[y_giro],
                mode="markers+text",
                marker=dict(
                    color="#FF4B4B", size=7,
                    line=dict(color="#FF4B4B", width=2),
                    symbol="circle",
                ),
                text=["Carico indicato"],
                textposition="bottom right",
                textfont=dict(color="#FF4B4B", size=11),
                showlegend=False,
            ))

            fig_curve.add_shape(
                type="line",
                x0=x_giro, x1=x_giro, y0=0, y1=y_giro,
                line=dict(color="#FF4B4B", width=1, dash="dot"),
            )
            fig_curve.add_shape(
                type="line",
                x0=0, x1=x_giro, y0=y_giro, y1=y_giro,
                line=dict(color="#FF4B4B", width=1, dash="dot"),
            )

            fig_curve.update_layout(
                plot_bgcolor="#0E1117",
                paper_bgcolor="#0E1117",
                hoverlabel=dict(bgcolor="#0E1117", font_color="#FFFFFF"),
                height=300,
                margin=dict(l=0, r=30, t=30, b=30),
                xaxis=dict(
                    title="Carico (kg)",
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
                    rangemode="tozero",
                ),
                showlegend=False,
            )
            st.plotly_chart(fig_curve, width='stretch')

        st.divider()

        with st.expander("Dettaglio calcolo passo per passo"):
            t_verg_val = f["t_verg"] if f["t_verg"] is not None else 0.0

            if veicolo["co2pkm_pieno"] is not None:
                b = (veicolo["co2pkm_pieno"] - veicolo["co2pkm_vuoto"]) / math.log(1 + veicolo["c_max"])
                curva_str = (
                    f"b = ({veicolo['co2pkm_pieno']} - {veicolo['co2pkm_vuoto']}) "
                    f"/ ln(1 + {veicolo['c_max']}) = **{b:.3f}**\n\n"
                    f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + {b:.3f} x ln(1 + C)"
                )
            else:
                curva_str = f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + C x 0.00008  *(lineare — fallback)*"

            st.markdown(f"""
**Curva emissioni ({r['modello_curva']}):**
{curva_str}

| Variabile | Valore |
|-----------|--------|
| Q (kg conferiti) | {q_kg} kg |
| Conf = Q / C | {r['conf']:.3f} |
| D_itinerario | {d_itinerario} km |
| D_baricentro_impianto | {d_baricentro_impianto} km |
| N_itinerario | {n_itinerario} |
| CO2perKm(0) vuoto | {r['co2pkm_vuoto']:.3f} kg/km |
| CO2perKm(C) carico | {r['co2pkm_carico']:.3f} kg/km |
""")

            if r["no_recycling"]:
                st.markdown(f"""
*(Materiale senza percorso di riciclo — il calcolo usa solo il costo di trattamento e smaltimento)*

**S** = {q_kg} x ({f['t_tratt']} + {f['t_smalt']}) = **{r['s']:+.3f} kg CO2**

**T1** = ({d_itinerario} x {r['co2pkm_vuoto']:.3f}) / {n_itinerario} = **{r['t1']:+.3f} kg CO2**

**T2** = ({r['co2pkm_carico']:.3f} - {r['co2pkm_vuoto']:.3f}) x {d_baricentro_impianto} x {r['conf']:.3f} = **{r['t2']:+.3f} kg CO2**

**CO2 netta** = S + T1 + T2 = **{r['co2_netta']:+.3f} kg CO2**

**Impronta ecologica** = ({r['co2_netta']:.3f} / 1000) / {CO2_PER_GHA} = **{r['gha_impronta']:.3f} gha**

**Saldo netto** = **{r['gha_netti']:+.5f} gha**
""")
            else:
                st.markdown(f"""
**S1** = -{q_kg} x ({f['t_smalt']} + {t_verg_val}) = **{r['s1']:+.3f} kg CO2**

**S2** = {q_kg} x ({f['t_tratt']} + {f['t_ric']}) = **{r['s2']:+.3f} kg CO2**

**T1** = ({d_itinerario} x {r['co2pkm_vuoto']:.3f}) / {n_itinerario} = **{r['t1']:+.3f} kg CO2**

**T2** = ({r['co2pkm_carico']:.3f} - {r['co2pkm_vuoto']:.3f}) x {d_baricentro_impianto} x {r['conf']:.3f} = **{r['t2']:+.3f} kg CO2**

**CO2 netta** = S1 + S2 + T1 + T2 = **{r['co2_netta']:+.3f} kg CO2**

**Impronta in gha** = ({r['co2_netta']:.3f} / 1000) / {CO2_PER_GHA} = **{r['gha_impronta']:.3f} gha**
""")
                if f["tipo"] == "biotico" and f["resa"]:
                    bc_co2_str = f"\n**Biocapacità da CO₂ evitata** = |{r['gha_impronta']:.3f}| = **{abs(r['gha_impronta']):.3f} gha**\n" if r["co2_netta"] < 0 else "\n*(CO₂ netta positiva — nessuna biocapacità da CO₂ evitata)*\n"
                    st.markdown(f"""
**Biocapacità da terreno** = ({q_kg} / 1000) / {f['resa']} x {f['f_equiv']} = **{r['bc_liberata']:.3f} gha**
{bc_co2_str}
**Saldo netto** = -{r['gha_impronta']:.3f} + {r['bc_liberata']:.3f} = **{r['gha_netti']:+.5f} gha**
""")
                else:
                    bc_co2_str = f"\n**Biocapacità da CO₂ evitata** = |{r['gha_impronta']:.3f}| = **{abs(r['gha_impronta']):.3f} gha**\n" if r["co2_netta"] < 0 else "\n*(CO₂ netta positiva — nessuna biocapacità da CO₂ evitata)*\n"
                    st.markdown(f"""
*(Materiale abiotico — nessuna biocapacità da terreno)*
{bc_co2_str}
**Saldo netto** = -{r['gha_impronta']:.3f} = **{r['gha_netti']:+.5f} gha**
""")


def pagina_tabelle():
    st.markdown("### Tabelle di riferimento")
    tab1, tab2 = st.tabs(["Fattori emissivi", "Veicoli"])

    with tab1:
        st.caption("Fonte: dati interni. Unita: kgCO2eq/kg.")
        rows = []
        for mat, f in FATTORI.items():
            rows.append({
                "Materiale":   mat,
                "Tipo":        f["tipo"],
                "T_verg":      f["t_verg"] if f["t_verg"] is not None else "n.d.",
                "T_smalt":     f["t_smalt"],
                "T_tratt":     f["t_tratt"],
                "T_ric":       f["t_ric"],
                "Resa (t/ha)": f["resa"] if f["resa"] else "—",
                "F_equiv":     f["f_equiv"] if f["f_equiv"] else "—",
            })
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')

    with tab2:
        st.caption("Veicoli inseriti nella sessione corrente.")
        if st.session_state.get("veicoli"):
            df_v = pd.DataFrame(st.session_state.veicoli)
            df_v["co2pkm_pieno"] = df_v["co2pkm_pieno"].apply(
                lambda x: x if x is not None else "— (da rilevare)"
            )
            df_v.columns = ["Modello", "CO2/km vuoto", "CO2/km pieno", "C_max (kg)"]
            st.dataframe(df_v, hide_index=True, width='stretch')
        else:
            st.info("Nessun veicolo inserito.")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    st.set_page_config(
        page_title="Soluslab",
        page_icon="🌐",
        layout="wide",
    )

    if "veicoli" not in st.session_state:
        st.session_state.veicoli = load_json("veicoli.json", VEICOLI_DEFAULT.copy())

    if "materiali" not in st.session_state:
        st.session_state.materiali = load_json(
            "materiali.json", {k: dict(v) for k, v in FATTORI.items()}
        )

    st.markdown("# Calcolatore Saldo Ambientale")
    st.caption("Soluslab — strumento di calcolo CO2 e biocapacita per movimento rifiuti.")
    st.divider()

    sezione = st.radio(
        "Sezione", ["Calcolatore", "Veicoli", "Materiali", "Tabelle"],
        horizontal=True, label_visibility="collapsed",
    )
    st.markdown("---")

    if sezione == "Calcolatore":
        pagina_calcolatore()
    elif sezione == "Veicoli":
        sezione_veicoli()
    elif sezione == "Materiali":
        sezione_materiali()
    elif sezione == "Tabelle":
        pagina_tabelle()


if __name__ == "__main__":
    main()
