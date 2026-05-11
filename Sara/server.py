from flask import Flask, request, jsonify
from datetime import datetime
import sqlite3
import os

app = Flask(__name__)

ultimo_dato = {}

def init_db():
    # Se il database esiste già all'avvio, allora viene cancellato e reinizializzato
    if os.path.exists('Empatica_E4_wristband.db'):
        os.remove('Empatica_E4_wristband.db')

    conn = sqlite3.connect('Empatica_E4_wristband.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dati_sensori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT, session TEXT, sensor TEXT, 
            timestamp TEXT, valori TEXT, data_ricezione DATETIME
        )
    ''')
    conn.commit()
    conn.close()

init_db() # Eseguila subito per creare il file .db

@app.route('/data', methods=['POST'])
def receive_data():
    global ultimo_dato
    data = request.json
    
    if data:
        # Salviamo il dato nella variabile globale
        ultimo_dato = data

        try:
            raw_ts = data.get('timestamp')
            ts_seconds = float(raw_ts) / 1000
            ts_formattato = datetime.fromtimestamp(ts_seconds).strftime('%H:%M:%d')
            conn = sqlite3.connect('Empatica_E4_wristband.db')
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO dati_sensori (user, session, sensor, timestamp, valori, data_ricezione)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                data.get('user'), data.get('session'), data.get('sensor'),
                ts_formattato, str(data.get('data')), datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"Errore Database: {e}")
        
        # Stampiamo nel terminale di VS Code per monitoraggio
        print(f"[POST] Ricevuto da Utente: {data.get('user')} | Sensore: {data.get('sensor')}")
        
        return jsonify({"status": "success"}), 200
    else:
        return jsonify({"status": "error", "message": "Nessun dato ricevuto"}), 400

@app.route('/data', methods=['GET'])
def show_data():
    global ultimo_dato
    
    # Se non è ancora arrivato nessun dato dal client
    if not ultimo_dato:
        return """
        <html>
            <body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                <h1>In attesa di dati...</h1>
                <p>Avvia lo script <b>sensor.py</b> per iniziare lo streaming.</p>
                <script>setTimeout(function(){ location.reload(); }, 2000);</script>
            </body>
        </html>
        """
    
    # Estraiamo i valori per la visualizzazione
    user = ultimo_dato.get('user')
    sensor = ultimo_dato.get('sensor')
    session = ultimo_dato.get('session')
    raw_ts = ultimo_dato.get('timestamp')
    values = ultimo_dato.get('data')

    try:
        # Trasformiamo il timestamp da millisecondi a secondi
        # 1630411794250 diventa 1630411794.250
        ts_in_secondi = float(raw_ts) / 1000
        
        # Lo convertiamo in un oggetto data/ora e lo formattiamo
        # %H:%M:%S mostra Ore:Minuti:Secondi
        readable_ts = datetime.fromtimestamp(ts_in_secondi).strftime('%H:%M:%S')
    except Exception as e:
        # Se c'è un errore, mostriamo il numero originale per non bloccare tutto
        readable_ts = raw_ts

    return f"""
    <html>
        <head>
            <title>Dashboard IoT - FatigueSet</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f0f2f5; display: flex; justify-content: center; padding: 40px; }}
                .card {{ background: white; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.1); width: 100%; max-width: 500px; }}
                h1 {{ color: #1a73e8; margin-top: 0; border-bottom: 2px solid #e8f0fe; padding-bottom: 10px; }}
                .info-row {{ display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid #fafafa; }}
                .label {{ font-weight: bold; color: #5f6368; }}
                .value {{ color: #202124; font-family: monospace; font-size: 1.1em; }}
                .highlight {{ color: #d93025; font-weight: bold; }}
            </style>
            <script>
                // Ricarica la pagina ogni secondo per vedere i dati cambiare
                setTimeout(function(){{ location.reload(); }}, 1000);
            </script>
        </head>
        <body>
            <div class="card">
                <h1>Empatica E4 wristband</h1>
                <div class="info-row"><span class="label">Utente:</span> <span class="value">{user}</span></div>
                <div class="info-row"><span class="label">Sessione:</span> <span class="value">{session}</span></div>
                <div class="info-row"><span class="label">Sensore:</span> <span class="value highlight">{sensor}</span></div>
                <div class="info-row"><span class="label">Orario:</span> <span class="value">{readable_ts}</span></div>
                <div class="info-row"><span class="label">Dati ricevuti:</span> <span class="value">{values}</span></div>
                <p style="color: #888; font-size: 0.8em; margin-top: 20px;">Aggiornamento automatico attivo...</p>
            </div>
        </body>
    </html>
    """

if __name__ == '__main__':
    print("--- Server Avviato ---")
    print("Dashboard disponibile su: http://127.0.0.1:5000/data")
    app.run(port=5000, debug=False)