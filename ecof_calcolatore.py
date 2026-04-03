"""
ECOF ITALIA — Calcolatore Saldo Ambientale
==========================================
Avvio: streamlit run ecof_calcolatore.py
Dipendenze: pip install streamlit pandas plotly
"""

import json
import math
import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

# ──────────────────────────────────────────────────────────────────────────────
# PERSISTENZA JSON
# ──────────────────────────────────────────────────────────────────────────────

def load_json(filename, default):
    """Legge filename se esiste e restituisce il contenuto; altrimenti restituisce default."""
    try:
        with open(filename, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(filename, data):
    """Scrive data su filename in formato JSON leggibile."""
    with open(filename, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


# ──────────────────────────────────────────────────────────────────────────────
# FATTORI EMISSIVI (fonte: Cartel1.xlsx fornito da Ecof Italia)
# Unità: kgCO₂eq per kg di materiale
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
        # t_verg = n.d.: digestione aerobica a Recall Latina, output non definibile
        "t_verg": None, "t_smalt": 1.50, "t_tratt": 0.10, "t_ric": 0.20,
        "tipo": "biologico", "resa": 3.30, "f_equiv": 2.51,
    },
    "Indifferenziato": {
        "t_verg": None, "t_smalt": 1.10, "t_tratt": 0.10, "t_ric": 0.0,
        "tipo": "abiotico", "resa": None, "f_equiv": None,
    },
}

# Capacità assorbimento CO₂ foreste (GFN): 0.95 tCO₂/ha/anno × 1.26 = 1.197 tCO₂/gha/anno
CO2_PER_GHA = 1.197

# Veicoli di default — verranno sovrascritti dai dati in session_state
# co2pkm_pieno = None significa dato non ancora rilevato → fallback lineare
VEICOLI_DEFAULT = [
    {"modello": "Fiat Ducato 35 L3H2", "co2pkm_vuoto": 0.18, "co2pkm_pieno": 0.30, "c_max": 1200},
    {"modello": "Iveco Daily 35S",      "co2pkm_vuoto": 0.21, "co2pkm_pieno": 0.37, "c_max": 1500},
]


# ──────────────────────────────────────────────────────────────────────────────
# FUNZIONI DI CALCOLO
# ──────────────────────────────────────────────────────────────────────────────

def co2_per_km_log(carico, co2_vuoto, co2_pieno, c_max):
    """
    Modello logaritmico: CO2/km in funzione del carico.
    Calibrato su due punti: f(0) = co2_vuoto, f(c_max) = co2_pieno.
    b = (co2_pieno - co2_vuoto) / ln(1 + c_max)
    CO2perKm(C) = co2_vuoto + b * ln(1 + C)
    """
    b = (co2_pieno - co2_vuoto) / math.log(1 + c_max)
    return co2_vuoto + b * math.log(1 + carico)


def co2_per_km_lin(carico, co2_vuoto):
    """
    Fallback lineare EEA.
    Rimosso non appena tutti i modelli hanno co2pkm_pieno rilevato.
    """
    return co2_vuoto + carico * 0.00008


def co2_per_km(carico, veicolo):
    """Router: logaritmico se co2pkm_pieno disponibile, altrimenti lineare."""
    if veicolo["co2pkm_pieno"] is not None:
        return co2_per_km_log(carico, veicolo["co2pkm_vuoto"], veicolo["co2pkm_pieno"], veicolo["c_max"])
    return co2_per_km_lin(carico, veicolo["co2pkm_vuoto"])


def calcola_movimento(materiale, q_kg, d_ecof_km, d_impianto_km, n_giro, carico_totale_kg, veicolo):
    """
    Calcola CO2 netta e saldo in gha per un singolo movimento.

    P1 = Q * (T_tratt + T_ric - T_smalt - T_verg)
    P2 = (D_cliente * CO2perKm(0)) / N_giro
    P3 = (CO2perKm(0) / N_giro) * D_impianto + (CO2perKm(C) - CO2perKm(0)) * D_impianto * Conf

    gha_netti = -gha_impronta + bc_liberata
    Convenzione: positivo = biocapacita, negativo = impronta ecologica
    """
    f = st.session_state.get("materiali", FATTORI)[materiale]
    conf         = q_kg / carico_totale_kg
    co2pkm_vuoto = co2_per_km(0, veicolo)
    co2pkm_car   = co2_per_km(carico_totale_kg, veicolo)
    t_verg = f["t_verg"] if f["t_verg"] is not None else 0.0

    p1 = q_kg * (f["t_tratt"] + f["t_ric"] - f["t_smalt"] - t_verg)
    p2 = (d_ecof_km * co2pkm_vuoto) / n_giro
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
        "modello_curva": "logaritmico" if veicolo["co2pkm_pieno"] is not None else "lineare (fallback)",
        "co2pkm_vuoto": co2pkm_vuoto,
        "co2pkm_carico": co2pkm_car,
        "conf": conf,
    }


# ──────────────────────────────────────────────────────────────────────────────
# UI — COMPONENTI
# ──────────────────────────────────────────────────────────────────────────────

def badge_saldo(gha):
    if gha >= 0:
        return (
            f'<div style="background:#0a2e1f;border:2px solid #52FFB8;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#52FFB8;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'BIOCAPACITA LIBERATA</div>'
            f'<div style="color:#52FFB8;font-size:2rem;font-weight:800">+{gha:.4f} gha</div></div>'
        )
    else:
        return (
            f'<div style="background:#2e0a0a;border:2px solid #FF4B4B;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#FF4B4B;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'IMPRONTA ECOLOGICA</div>'
            f'<div style="color:#FF4B4B;font-size:2rem;font-weight:800">{gha:.4f} gha</div></div>'
        )


def sezione_veicoli():
    st.markdown("### Gestione Veicoli")
    st.caption(
        "Inserisci i modelli di furgone usati da Ecof. "
        "CO2/km pieno puo essere lasciato vuoto finche non e disponibile il dato reale: "
        "verra usato il modello lineare come fallback."
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
        nuovo_vuoto = st.number_input("CO2/km vuoto", min_value=0.05, value=0.18, step=0.01, key="input_vuoto")
    with c3:
        nuovo_pieno_str = st.text_input("CO2/km pieno (opz.)", key="input_pieno", placeholder="es. 0.24")
    with c4:
        nuovo_cmax = st.number_input("C_max (kg)", min_value=100, value=1200, step=100, key="input_cmax")

    if st.button("Aggiungi veicolo"):
        if not nuovo_modello.strip():
            st.warning("Inserisci il nome del modello.")
        else:
            try:
                pieno = float(nuovo_pieno_str) if nuovo_pieno_str.strip() else None
            except ValueError:
                pieno = None
                st.warning("CO2/km pieno non valido — verra usato il fallback lineare.")

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
        pieno_default = str(v_mod["co2pkm_pieno"]) if v_mod["co2pkm_pieno"] is not None else ""

        ec1, ec2, ec3, ec4 = st.columns([2, 1, 1, 1])
        with ec1:
            edit_modello = st.text_input("Nome modello", value=v_mod["modello"], key="edit_modello")
        with ec2:
            edit_vuoto = st.number_input("CO2/km vuoto", min_value=0.05, value=v_mod["co2pkm_vuoto"], step=0.01, key="edit_vuoto")
        with ec3:
            edit_pieno_str = st.text_input("CO2/km pieno (opz.)", value=pieno_default, key="edit_pieno")
        with ec4:
            edit_cmax = st.number_input("C_max (kg)", min_value=100, value=v_mod["c_max"], step=100, key="edit_cmax")

        if st.button("Salva modifiche"):
            if not edit_modello.strip():
                st.warning("Il nome del modello non può essere vuoto.")
            else:
                altri_modelli = [v["modello"] for v in st.session_state.veicoli if v["modello"] != sel_mod]
                if edit_modello.strip() in altri_modelli:
                    st.warning(f"Esiste già un veicolo con il nome '{edit_modello.strip()}'.")
                else:
                    try:
                        pieno = float(edit_pieno_str) if edit_pieno_str.strip() else None
                    except ValueError:
                        pieno = None
                        st.warning("CO2/km pieno non valido — verra usato il fallback lineare.")
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
        "T_verg, resa e f_equiv possono essere lasciati vuoti (n.d.)."
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
        nuovo_tipo = st.selectbox("Tipo", ["abiotico", "biologico"], key="mat_input_tipo")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        nuovo_tverg_str = st.text_input(
            "Produzione vergine (kgCO₂/kg)", key="mat_input_tverg", placeholder="es. 2.50",
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
    if nuovo_tipo == "biologico":
        d1, d2 = st.columns(2)
        with d1:
            nuovo_resa_str = st.text_input(
                "Resa coltura (t/ha/anno)", key="mat_input_resa", placeholder="es. 2.68",
                help="Tonnellate di biomassa prodotte per ettaro all'anno — fonte GFN",
            )
        with d2:
            nuovo_fequiv_str = st.text_input(
                "Equivalenza territoriale (gha/ha)", key="mat_input_fequiv", placeholder="es. 1.26",
                help="Conversione da ettari fisici a ettari globali — fonte GFN",
            )
    else:
        nuovo_resa_str = ""
        nuovo_fequiv_str = ""

    if st.button("Aggiungi materiale"):
        if not nuovo_nome.strip():
            st.warning("Inserisci il nome del materiale.")
        elif nuovo_nome.strip() in st.session_state.materiali:
            st.warning(f"Esiste già un materiale con il nome '{nuovo_nome.strip()}'.")
        else:
            try:
                tverg = float(nuovo_tverg_str) if nuovo_tverg_str.strip() else None
            except ValueError:
                tverg = None
                st.warning("T_verg non valido — impostato a n.d.")
            if nuovo_tipo == "biologico":
                try:
                    resa = float(nuovo_resa_str) if nuovo_resa_str.strip() else None
                except ValueError:
                    resa = None
                    st.warning("Resa non valida — impostata a n.d.")
                try:
                    fequiv = float(nuovo_fequiv_str) if nuovo_fequiv_str.strip() else None
                except ValueError:
                    fequiv = None
                    st.warning("F_equiv non valido — impostato a n.d.")
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
        tipo_opts = ["abiotico", "biologico"]
        tipo_idx  = tipo_opts.index(m["tipo"]) if m["tipo"] in tipo_opts else 0
        tverg_default  = str(m["t_verg"])  if m["t_verg"]  is not None else ""
        resa_default   = str(m["resa"])    if m["resa"]    is not None else ""
        fequiv_default = str(m["f_equiv"]) if m["f_equiv"] is not None else ""

        ea1, ea2 = st.columns([2, 1])
        with ea1:
            edit_nome = st.text_input("Nome materiale", value=sel_mat, key="mat_edit_nome")
        with ea2:
            edit_tipo = st.selectbox("Tipo", tipo_opts, index=tipo_idx, key="mat_edit_tipo")
        ec1, ec2, ec3, ec4 = st.columns(4)
        with ec1:
            edit_tverg_str = st.text_input(
                "Produzione vergine (kgCO₂/kg)", value=tverg_default, key="mat_edit_tverg",
                help="CO₂ emessa per produrre 1kg di materia vergine",
            )
        with ec2:
            edit_tsmalt = st.number_input(
                "Smaltimento (kgCO₂/kg)", min_value=0.0, value=float(m["t_smalt"]), step=0.01,
                key="mat_edit_tsmalt", help="CO₂ emessa per smaltire 1kg senza riciclo",
            )
        with ec3:
            edit_ttratt = st.number_input(
                "Trattamento (kgCO₂/kg)", min_value=0.0, value=float(m["t_tratt"]), step=0.01,
                key="mat_edit_ttratt", help="CO₂ emessa per il trattamento pre-riciclo di 1kg",
            )
        with ec4:
            edit_tric = st.number_input(
                "Riciclo (kgCO₂/kg)", min_value=0.0, value=float(m["t_ric"]), step=0.01,
                key="mat_edit_tric", help="CO₂ emessa dal processo di riciclo di 1kg",
            )
        if edit_tipo == "biologico":
            ed1, ed2 = st.columns(2)
            with ed1:
                edit_resa_str = st.text_input(
                    "Resa coltura (t/ha/anno)", value=resa_default, key="mat_edit_resa",
                    help="Tonnellate di biomassa prodotte per ettaro all'anno — fonte GFN",
                )
            with ed2:
                edit_fequiv_str = st.text_input(
                    "Equivalenza territoriale (gha/ha)", value=fequiv_default, key="mat_edit_fequiv",
                    help="Conversione da ettari fisici a ettari globali — fonte GFN",
                )
        else:
            edit_resa_str = ""
            edit_fequiv_str = ""

        if st.button("Salva modifiche", key="mat_btn_salva"):
            if not edit_nome.strip():
                st.warning("Il nome del materiale non può essere vuoto.")
            else:
                altri_nomi = [n for n in st.session_state.materiali if n != sel_mat]
                if edit_nome.strip() in altri_nomi:
                    st.warning(f"Esiste già un materiale con il nome '{edit_nome.strip()}'.")
                else:
                    try:
                        tverg = float(edit_tverg_str) if edit_tverg_str.strip() else None
                    except ValueError:
                        tverg = None
                        st.warning("T_verg non valido — impostato a n.d.")
                    if edit_tipo == "biologico":
                        try:
                            resa = float(edit_resa_str) if edit_resa_str.strip() else None
                        except ValueError:
                            resa = None
                            st.warning("Resa non valida — impostata a n.d.")
                        try:
                            fequiv = float(edit_fequiv_str) if edit_fequiv_str.strip() else None
                        except ValueError:
                            fequiv = None
                            st.warning("F_equiv non valido — impostato a n.d.")
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
            d_ecof = st.number_input("D_cliente (km)", min_value=0.1, value=10.0, step=0.5,
                                     help="Distanza Ecof -> unita locale del cliente")
            d_imp  = st.number_input("D_impianto (km)", min_value=0.1, value=15.0, step=0.5,
                                     help="Distanza unita locale -> impianto di riciclo")
        with c2:
            n_giro = st.number_input("N_giro", min_value=1, value=3, step=1,
                                     help="Clienti che condividono il tragitto Ecof->area")
            carico = st.number_input(
                "C — Carico totale furgone (kg)",
                min_value=q_kg,
                max_value=float(veicolo["c_max"]),
                value=min(max(q_kg * 3, 300.0), float(veicolo["c_max"])),
                step=50.0,
                help=f"Peso totale nel furgone — max {veicolo['c_max']} kg per questo modello"
            )

        st.divider()

        st.markdown("**Fattori emissivi applicati** *(kgCO2/kg — fonte: Ecof Italia)*")
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

        r = calcola_movimento(materiale, q_kg, d_ecof, d_imp, n_giro, carico, veicolo)

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
            m1.metric("CO₂ netta",                  f"{r['co2_netta']:+.2f} kg")
            m2.metric("Saldo netto",                 f"{r['gha_netti']:+.4f} gha")
            m3, m4 = st.columns(2)
            m3.metric("Biocapacità da CO₂ evitata", f"+{bc_co2:.4f} gha")
            m4.metric("Biocapacità da terreno",      f"+{r['bc_liberata']:.4f} gha" if r["bc_liberata"] > 0 else "—")
        else:
            # Riciclo non virtuoso: CO₂ netta positiva → impronta, nessuna biocapacità da CO₂
            m1, m2, m3 = st.columns(3)
            m1.metric("CO₂ netta",             f"{r['co2_netta']:+.2f} kg")
            m2.metric("Impronta ecologica",    f"{r['gha_impronta']:.4f} gha")
            m3.metric("Biocapacità da terreno", f"+{r['bc_liberata']:.4f} gha" if r["bc_liberata"] > 0 else "—")

        st.divider()

        st.markdown("**Scomposizione CO2 per componente**")
        st.caption("P1 = bilancio riciclo  |  P2 = tragitto Ecof->cliente  |  P3 = peso su cliente->impianto")

        # Ordine P1 → P2 → P3 dall'alto verso il basso (Plotly orizzontale è bottom-up, quindi invertiamo)
        componenti = ["P3 — Peso cliente->impianto", "P2 — Tragitto Ecof->cliente", "P1 — Bilancio riciclo"]
        valori     = [r["co2_p3"], r["co2_p2"], r["co2_p1"]]
        colori     = ["#52FFB8" if v <= 0 else "#FF4B4B" for v in valori]
        etichette  = [f"{v:+.3f} kg" for v in valori]

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
            height=240,
            margin=dict(l=0, r=120, t=10, b=30),  # margine destro per le etichette
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

        st.divider()

        with st.expander("Dettaglio calcolo passo per passo"):
            t_verg_val = f["t_verg"] if f["t_verg"] is not None else 0.0

            if veicolo["co2pkm_pieno"] is not None:
                b = (veicolo["co2pkm_pieno"] - veicolo["co2pkm_vuoto"]) / math.log(1 + veicolo["c_max"])
                curva_str = (
                    f"b = ({veicolo['co2pkm_pieno']} - {veicolo['co2pkm_vuoto']}) "
                    f"/ ln(1 + {veicolo['c_max']}) = **{b:.6f}**\n\n"
                    f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + {b:.6f} x ln(1 + C)"
                )
            else:
                curva_str = f"CO2perKm(C) = {veicolo['co2pkm_vuoto']} + C x 0.00008  *(lineare — fallback)*"

            st.markdown(f"""
**Curva emissioni ({r['modello_curva']}):**
{curva_str}

| Variabile | Valore |
|-----------|--------|
| Q (kg conferiti) | {q_kg} kg |
| Conf = Q / C | {r['conf']:.4f} |
| CO2perKm(0) vuoto | {r['co2pkm_vuoto']:.5f} kg/km |
| CO2perKm(C) carico | {r['co2pkm_carico']:.5f} kg/km |

**P1** = {q_kg} x ({f['t_tratt']} + {f['t_ric']} - {f['t_smalt']} - {t_verg_val}) = **{r['co2_p1']:+.3f} kg CO2**

**P2** = ({d_ecof} x {r['co2pkm_vuoto']:.5f}) / {n_giro} = **{r['co2_p2']:+.3f} kg CO2**

**P3** = ({d_imp} x {r['co2pkm_vuoto']:.5f}) / {n_giro} + ({r['co2pkm_carico']:.5f} - {r['co2pkm_vuoto']:.5f}) x {d_imp} x {r['conf']:.4f} = **{r['co2_p3']:+.3f} kg CO2**

**CO2 netta** = P1 + P2 + P3 = **{r['co2_netta']:+.3f} kg CO2**

**Impronta in gha** = ({r['co2_netta']:.3f} / 1000) / {CO2_PER_GHA} = **{r['gha_impronta']:.5f} gha**
""")
            if f["tipo"] == "biologico" and f["resa"]:
                bc_co2_str = f"\n**Biocapacità da CO₂ evitata** = |{r['gha_impronta']:.5f}| = **{abs(r['gha_impronta']):.5f} gha**\n" if r["co2_netta"] < 0 else "\n*(CO₂ netta positiva — nessuna biocapacità da CO₂ evitata)*\n"
                st.markdown(f"""
**Biocapacità da terreno** = ({q_kg} / 1000) / {f['resa']} x {f['f_equiv']} = **{r['bc_liberata']:.5f} gha**
{bc_co2_str}
**Saldo netto** = -{r['gha_impronta']:.5f} + {r['bc_liberata']:.5f} = **{r['gha_netti']:+.5f} gha**
""")
            else:
                bc_co2_str = f"\n**Biocapacità da CO₂ evitata** = |{r['gha_impronta']:.5f}| = **{abs(r['gha_impronta']):.5f} gha**\n" if r["co2_netta"] < 0 else "\n*(CO₂ netta positiva — nessuna biocapacità da CO₂ evitata)*\n"
                st.markdown(f"""
*(Materiale abiotico — nessuna biocapacità da terreno)*
{bc_co2_str}
**Saldo netto** = -{r['gha_impronta']:.5f} = **{r['gha_netti']:+.5f} gha**
""")


def pagina_tabelle():
    st.markdown("### Tabelle di riferimento")
    tab1, tab2 = st.tabs(["Fattori emissivi", "Veicoli"])

    with tab1:
        st.caption("Fonte: Ecof Italia. Unita: kgCO2eq/kg.")
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
        page_title="Ecof Italia — Calcolatore",
        page_icon="🌿",
        layout="wide",
    )

    if "veicoli" not in st.session_state:
        st.session_state.veicoli = load_json("veicoli.json", VEICOLI_DEFAULT.copy())

    if "materiali" not in st.session_state:
        st.session_state.materiali = load_json(
            "materiali.json", {k: dict(v) for k, v in FATTORI.items()}
        )

    st.markdown("# Calcolatore Saldo Ambientale")
    st.caption("Ecof Italia — strumento interno di calcolo CO2 e biocapacita per movimento rifiuti.")
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
