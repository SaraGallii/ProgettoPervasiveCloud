from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import sqlite3
import json
import math
import statistics

app = Flask(__name__)
DB_PATH = 'Empatica_E4_wristband.db'
DB_STATS_PATH = 'statistiche_settimanali.db'

def init_db():
    # DB Principale
    conn = sqlite3.connect(DB_PATH)
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

    # NUOVO DB: STATISTICHE SETTIMANALI
    conn_stats = sqlite3.connect(DB_STATS_PATH)
    cursor_stats = conn_stats.cursor()
    cursor_stats.execute('''
        CREATE TABLE IF NOT EXISTS metriche_settimanali (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT, session TEXT, sensor TEXT,
            mean REAL, median REAL, mode REAL, max REAL, min REAL, 
            samples INTEGER, data_calcolo DATETIME
        )
    ''')
    conn_stats.commit()
    conn_stats.close()

init_db()

@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400
    try:
        raw_ts = data.get('timestamp')
        ts_seconds = float(raw_ts) / 1000
        ts_formattato = datetime.fromtimestamp(ts_seconds).strftime('%H:%M:%S')
        conn = sqlite3.connect(DB_PATH, timeout=20)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO dati_sensori (user, session, sensor, timestamp, valori, data_ricezione)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            data.get('user'), data.get('session'), data.get('sensor'),
            ts_formattato, json.dumps(data.get('data')), datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def index():
    return statistics_page()

@app.route('/statistics')
def statistics_page():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Connessione al nuovo DB per il salvataggio
    conn_stats = sqlite3.connect(DB_STATS_PATH)
    cursor_stats = conn_stats.cursor()
    
    # Lista utenti per il dropdown
    cursor.execute("SELECT DISTINCT user FROM dati_sensori ORDER BY user")
    user_list = [row[0] for row in cursor.fetchall()]
    if not user_list: user_list = ["No data"]

    selected_user = request.args.get('u', user_list[0])
    sensors = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    
    # 1. Trova tutte le sessioni disponibili per l'utente
    cursor.execute("SELECT DISTINCT session FROM dati_sensori WHERE user=? ORDER BY session", (selected_user,))
    session_list = [row[0] for row in cursor.fetchall()]

    # Struttura: session_stats[sessione][sensore] = {metriche}
    session_stats = {}

    for sess in session_list:
        session_stats[sess] = {}
        for s in sensors:
            cursor.execute("""
                SELECT valori FROM dati_sensori
                WHERE sensor=? AND user=? AND session=?
                AND data_ricezione >= date('now', '-7 days')
            """, (s, selected_user, sess))
            
            rows = cursor.fetchall()
            dataset = []
            
            for r in rows:
                try:
                    val = json.loads(r[0])
                    if s == "ACC":
                        ax, ay, az = float(val.get('ax', 0)), float(val.get('ay', 0)), float(val.get('az', 0))
                        dataset.append(round(math.sqrt(ax**2 + ay**2 + az**2), 2))
                    else:
                        dataset.append(round(float(list(val.values())[0]), 2))
                except: continue
            
            if dataset:
                try:
                    m_mean = round(statistics.mean(dataset), 2)
                    m_median = round(statistics.median(dataset), 2)
                    m_mode = round(statistics.mode(dataset), 2)
                    m_max = max(dataset)
                    m_min = min(dataset)
                    m_samples = len(dataset)

                    session_stats[sess][s] = {
                        "mean": m_mean, "median": m_median, "mode": m_mode,
                        "max": m_max, "min": m_min, "samples": m_samples
                    }

                    # SALVATAGGIO NEL NUOVO DB STATISTICHE
                    cursor_stats.execute("DELETE FROM metriche_settimanali WHERE user=? AND session=? AND sensor=?", (selected_user, sess, s))
                    cursor_stats.execute('''
                        INSERT INTO metriche_settimanali (user, session, sensor, mean, median, mode, max, min, samples, data_calcolo)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (selected_user, sess, s, m_mean, m_median, m_mode, m_max, m_min, m_samples, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
                    
                except statistics.StatisticsError:
                    session_stats[sess][s] = {"mean": "Err", "median": "Err", "mode": "Err", "max": "Err", "min": "Err", "samples": len(dataset)}
            else:
                session_stats[sess][s] = {"mean": "N/A", "median": "N/A", "mode": "N/A", "max": "N/A", "min": "N/A", "samples": 0}

    conn_stats.commit()
    conn_stats.close()
    conn.close()

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="it">
    <head>
        <meta charset="UTF-8">
        <title>Empatica E4 Statistics</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #f0f4f8; margin: 0; padding: 20px; color: #2d3748; }
            .container { max-width: 1100px; margin: auto; }
            .header { background: #2b6cb0; color: white; padding: 20px; border-radius: 12px; margin-bottom: 20px; }
            .session-block { background: white; padding: 20px; border-radius: 12px; margin-bottom: 30px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
            .session-title { border-left: 5px solid #2b6cb0; padding-left: 15px; margin-bottom: 20px; color: #2b6cb0; font-size: 1.5em; }
            .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 15px; }
            .stat-card { border: 1px solid #e2e8f0; padding: 15px; border-radius: 8px; background: #f8fafc; }
            .sensor-name { font-weight: bold; color: #2d3748; border-bottom: 2px solid #e2e8f0; margin-bottom: 10px; display: flex; justify-content: space-between; }
            .metric { display: flex; justify-content: space-between; font-size: 0.9em; margin: 5px 0; }
            .metric span:last-child { font-weight: bold; }
            select { padding: 10px; width: 250px; border-radius: 6px; margin-bottom: 20px; }
            .badge { font-size: 0.7em; background: #2b6cb0; color: white; padding: 2px 8px; border-radius: 10px; }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Empatica E4 wristband</h1>
                <p>Riepilogo Statistiche Settimanali</p>
            </div>

            <label>Utente:</label><br>
            <select onchange="location.href='/statistics?u='+this.value">
                {% for u in users %}
                <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                {% endfor %}
            </select>

            {% for session_id, sensors_data in all_stats.items() %}
            <div class="session-block">
                <div class="session-title">Sessione: {{ session_id }}</div>
                <div class="stats-grid">
                    {% for sensor, data in sensors_data.items() %}
                    <div class="stat-card">
                        <div class="sensor-name">{{ sensor }}</div>
                        <div class="metric"><span>Media</span> <span>{{ data.mean }}</span></div>
                        <div class="metric"><span>Mediana</span> <span>{{ data.median }}</span></div>
                        <div class="metric"><span>Moda</span> <span>{{ data.mode }}</span></div>
                        <div class="metric"><span>Max</span> <span>{{ data.max }}</span></div>
                        <div class="metric"><span>Min</span> <span>{{ data.min }}</span></div>
                    </div>
                    {% endfor %}
                </div>
            </div>
            {% endfor %}
        </div>
        <script>setTimeout(() => location.reload(), 30000);</script>
    </body>
    </html>
    ''', users=user_list, selected_u=selected_user, all_stats=session_stats)

if __name__ == '__main__':
    app.run(port=5000)