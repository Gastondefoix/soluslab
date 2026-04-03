"""
ECOF ITALIA — Calcolatore Saldo Ambientale
==========================================
Avvio: streamlit run ecof_calcolatore.py
Dipendenze: pip install streamlit pandas plotly
"""

import math
import streamlit as st
import pandas as pd
import plotly.graph_objects as go

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
}

# Capacità assorbimento CO₂ foreste (GFN): 0.95 tCO₂/ha/anno × 1.26 = 1.197 tCO₂/gha/anno
CO2_PER_GHA = 1.197

# Veicoli di default — verranno sovrascritti dai dati in session_state
# co2pkm_pieno = None significa dato non ancora rilevato → fallback lineare
VEICOLI_DEFAULT = [
    {"modello": "Fiat Ducato 35 L3H2", "co2pkm_vuoto": 0.18, "co2pkm_pieno": None, "c_max": 1200},
    {"modello": "Iveco Daily 35S",      "co2pkm_vuoto": 0.21, "co2pkm_pieno": None, "c_max": 1500},
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
    P3 = (CO2perKm(C) - CO2perKm(0)) * D_impianto * Conf

    gha_netti = -gha_impronta + bc_liberata
    Convenzione: positivo = biocapacita, negativo = impronta ecologica
    """
    f = FATTORI[materiale]
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
            f'<div style="color:#52FFB8;font-size:2rem;font-weight:800">+{gha:.5f} gha</div></div>'
        )
    else:
        return (
            f'<div style="background:#2e0a0a;border:2px solid #FF4B4B;border-radius:10px;'
            f'padding:1rem 1.5rem;text-align:center;display:inline-block">'
            f'<div style="color:#FF4B4B;font-size:.7rem;font-weight:700;letter-spacing:.1em">'
            f'IMPRONTA ECOLOGICA</div>'
            f'<div style="color:#FF4B4B;font-size:2rem;font-weight:800">{gha:.5f} gha</div></div>'
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
        st.dataframe(df_v, hide_index=True, use_container_width=True)
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

        materiale = st.selectbox("Materiale", list(FATTORI.keys()))
        f = FATTORI[materiale]

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
        st.dataframe(df_f, hide_index=True, use_container_width=True)

        calcola = st.button("Calcola", type="primary", use_container_width=True)

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

        m1, m2, m3 = st.columns(3)
        m1.metric("CO2 netta totale",  f"{r['co2_netta']:+.2f} kg")
        m2.metric("Impronta in gha",   f"{r['gha_impronta']:.5f}")
        m3.metric("BC liberata (gha)", f"{r['bc_liberata']:.5f}")

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
        st.plotly_chart(fig, use_container_width=True)

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
                st.markdown(f"""
**BC liberata** = ({q_kg} / 1000) / {f['resa']} x {f['f_equiv']} = **{r['bc_liberata']:.5f} gha**

**Saldo netto** = -{r['gha_impronta']:.5f} + {r['bc_liberata']:.5f} = **{r['gha_netti']:+.5f} gha**
""")
            else:
                st.markdown(f"""
*(Materiale abiotico — nessuna biocapacita liberata)*

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
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    with tab2:
        st.caption("Veicoli inseriti nella sessione corrente.")
        if st.session_state.get("veicoli"):
            df_v = pd.DataFrame(st.session_state.veicoli)
            df_v["co2pkm_pieno"] = df_v["co2pkm_pieno"].apply(
                lambda x: x if x is not None else "— (da rilevare)"
            )
            df_v.columns = ["Modello", "CO2/km vuoto", "CO2/km pieno", "C_max (kg)"]
            st.dataframe(df_v, hide_index=True, use_container_width=True)
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
        st.session_state.veicoli = VEICOLI_DEFAULT.copy()

    st.markdown("# Calcolatore Saldo Ambientale")
    st.caption("Ecof Italia — strumento interno di calcolo CO2 e biocapacita per movimento rifiuti.")
    st.divider()

    sezione = st.radio(
        "Sezione", ["Calcolatore", "Veicoli", "Tabelle"],
        horizontal=True, label_visibility="collapsed",
    )
    st.markdown("---")

    if sezione == "Calcolatore":
        pagina_calcolatore()
    elif sezione == "Veicoli":
        sezione_veicoli()
    elif sezione == "Tabelle":
        pagina_tabelle()


if __name__ == "__main__":
    main()
