# Progetto base (locale) — Empatica E4 / FatigueSet

Questa versione include solo i requisiti **già soddisfatti**:
- Client che legge i CSV e invia righe via HTTP POST a intervalli regolari.
- Inserimento dinamico di nuove sorgenti dati (nuove cartelle) mentre il client gira.
- Server che riceve e salva su SQLite.
- Login.
- Dashboard con grafico (1 sensore alla volta).
- Statistiche ultimi 7 giorni (calcolo + salvataggio in DB dedicato).

## Struttura cartelle attesa
Metti il dataset sotto `fatigueset/` (hardcoded in `client.py`) con struttura:

```
fatigueset/
  P01/
    01/
      wrist_hr.csv
      wrist_acc.csv
      ...
```

Il client invierà `user=01` (converte `P01 -> 01`) e `session=01`.

## Avvio

```bash
pip install -r requirements.txt
python server.py
python client.py
```

Apri nel browser:
- http://127.0.0.1:5000/

Credenziali demo:
- admin / 0123
- user1 / 1111
- user2 / 2222

## Database
- `Empatica_E4_wristband.db` (dati raw)
- `statistiche_settimanali.db` (metriche settimanali)

## Grafici
Per semplicità è incluso un file `static/chart.umd.min.js` che implementa una **API minima** compatibile con `new Chart(...)`.
Se vuoi Chart.js ufficiale, sostituisci quel file con la build UMD ufficiale.
