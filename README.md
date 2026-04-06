# Soluslab

**Calcolatore Saldo Ambientale** — applicazione *Streamlit* che calcola
l'impatto netto in CO₂ e biocapacità delle operazioni di raccolta
e trasporto rifiuti.

🌐 **Demo:** [soluslab.streamlit.app](https://soluslab.streamlit.app)

## Utilizzo

Per ogni movimento di rifiuti, *Soluslab* calcola un bilancio carbonico
in tre componenti:

- **P1** — Risparmio emissivo del riciclo rispetto all'alternativa lineare
  (discarica / produzione vergine)
- **P2** — Emissioni del viaggio dall'operatore al cliente
- **P3** — Emissioni del tragitto cliente → impianto, con componenti
  fisse e variabili

Le emissioni dei veicoli seguono una *curva logaritmica* calibrata su
valori a vuoto e pieno carico o una funzione lineare *EEA* di fallback.

La biocapacità è calcolata tramite i fattori di assorbimento forestale
*GFN* e i fattori di resa ed equivalenza *gha* per materiali biologici.

## Funzionalità

- Calcolo CO₂ e ettari globali *gha* per singolo movimento
- Gestione veicoli e materiali con persistenza *JSON*
- Grafici *Plotly* di approfondimento

## Stack

- Python 3
- Streamlit
- Plotly
- JSON (configurazione veicoli e materiali)

## Avvio locale

\`\`\`bash
pip install -r requirements.txt
streamlit run app.py
\`\`\`

## File di configurazione

| File | Descrizione |
|---|---|
| `veicoli.json` | Parco veicoli con parametri curva emissioni |
| `materiali.json` | Materiali rifiuto con fattori emissivi e tassi di riciclo |

## Licenza

Publica

## License

Public
