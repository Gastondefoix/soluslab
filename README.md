# Soluslab

**Calcolatore Saldo Ambientale** — applicazione Streamlit che calcola
l'impatto netto in CO₂ e biocapacità delle operazioni di raccolta
e trasporto rifiuti.

## Cosa fa

Per ogni movimento di rifiuti, Soluslab calcola un bilancio carbonico
in tre componenti:

- **P1** — Risparmio emissivo del riciclo rispetto all'alternativa lineare
  (discarica / produzione vergine)
- **P2** — Emissioni del viaggio di ritorno a vuoto dall'operatore al cliente
- **P3** — Emissioni del tragitto cliente → impianto, con componenti
  fisse e variabili

Le emissioni dei veicoli seguono una **curva logaritmica** calibrata su
valori a vuoto/pieno carico, con fallback lineare EEA per veicoli
non calibrati.

La biocapacità è calcolata tramite i fattori di assorbimento forestale
GFN e i fattori di equivalenza per materiali biologici.

## Funzionalità

- Calcolo CO₂ e ettari globali (gha) per singolo movimento
- Gestione veicoli e materiali con persistenza JSON
- Grafici Plotly tema scuro
- Gestione speciale per rifiuti indifferenziati
- Deployabile su Streamlit Community Cloud

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

Publica.

## License

Public.
