from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime
import sqlite3
import json
import math

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

DB_PATH = 'Empatica_E4_wristband.db'

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dati_sensori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT, session TEXT, sensor TEXT, 
            timestamp TEXT, valori TEXT, data_ricezione DATETIME
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS utenti (
            username TEXT PRIMARY KEY,
            password TEXT
        )
    ''')
    cursor.execute("INSERT OR IGNORE INTO utenti (username, password) VALUES ('admin', '0123')")
    conn.commit()
    conn.close()

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
        print(f"Errore DB: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM utenti WHERE username=? AND password=?", (username, password))
        user = cursor.fetchone()
        conn.close()
        if user:
            session['user'] = username
            return redirect(url_for('dashboard'))
        return "Login fallito!"
    return '''
    <html><body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f0f2f5;">
    <form method="post" style="background:white; padding:2rem; border-radius:8px; box-shadow:0 4px 6px rgba(0,0,0,0.1);">
    <h2 style="color:#1a73e8; margin-top:0;">Login Empatica E4 wristband</h2>
    User: <input type="text" name="username" style="width:100%; margin-bottom:10px; padding:8px; border:1px solid #ddd; border-radius:4px;"><br>
    Password: <input type="password" name="password" style="width:100%; margin-bottom:20px; padding:8px; border:1px solid #ddd; border-radius:4px;"><br>
    <input type="submit" value="Accedi" style="width:100%; padding:10px; background:#1a73e8; color:white; border:none; border-radius:4px; cursor:pointer;">
    </form></body></html>'''

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT user FROM dati_sensori ORDER BY user")
    lista_utenti = [row[0] for row in cursor.fetchall()]
    if not lista_utenti: lista_utenti = ["Nessun dato"]

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    data_charts = {}
    selected_user = request.args.get('u', lista_utenti[0])

    for s in sensori:
        cursor.execute("""
            SELECT timestamp, valori 
            FROM dati_sensori 
            WHERE sensor=? AND user=? 
            ORDER BY timestamp DESC LIMIT 50
        """, (s, selected_user))
        
        rows = cursor.fetchall()[::-1]
        labels = [r[0] for r in rows]
        
        if s == "ACC":
            magnitudes = []
            for r in rows:
                try:
                    val = json.loads(r[1].replace("'", '"'))
                    ax = float(val.get('ax', val.get('x', 0)))
                    ay = float(val.get('ay', val.get('y', 0)))
                    az = float(val.get('az', val.get('z', 0)))
                    mag = math.sqrt(ax**2 + ay**2 + az**2)
                    magnitudes.append(round(mag, 3))
                except:
                    magnitudes.append(0)
            data_charts[s] = {"labels": labels, "values": magnitudes}
        else:
            values = []
            for r in rows:
                try:
                    val = json.loads(r[1].replace("'", '"'))
                    values.append(float(list(val.values())[0]))
                except:
                    values.append(0)
            data_charts[s] = {"labels": labels, "values": values}
    
    conn.close()

    return render_template_string('''
    <html>
        <head>
            <title>Empatica E4 wristband Dashboard</title>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }
                .container { max-width: 900px; margin: auto; }
                .header { background: white; padding: 15px 25px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
                .controls { background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; }
                select { padding: 8px; border-radius: 6px; border: 1px solid #1a73e8; color: #1a73e8; font-weight: bold; cursor: pointer; outline: none; }
                .chart-card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); height: 500px; display: none; flex-direction: column; }
                .chart-card.active { display: flex; }
                h1 { color: #1a73e8; font-size: 1.4em; margin: 0; }
                .logout { color: #d93025; text-decoration: none; font-weight: bold; border: 1px solid #d93025; padding: 5px 15px; border-radius: 5px; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Monitoraggio Empatica E4 </h1>
                    <a href="/logout" class="logout">Esci</a>
                </div>

                <div class="controls">
                    <div>
                        <label> Utente: </label>
                        <select id="userSelect" onchange="updateFilters()">
                            {% for u in utenti %}
                            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div>
                        <label> Sensore: </label>
                        <select id="sensorSelect" onchange="updateFilters()">
                            <option value="ACC">Accelerazione</option>
                            <option value="BVP">BVP (Pulsazioni)</option>
                            <option value="EDA">EDA (Sudorazione)</option>
                            <option value="HR">Heart Rate (BPM)</option>
                            <option value="IBI">IBI (Intervallo)</option>
                            <option value="TEMP">Temperatura</option>
                        </select>
                    </div>
                </div>

                {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
                <div id="card-{{ s }}" class="chart-card">
                    <h3 id="title-{{ s }}" style="font-size:0.9em; margin-top:0;">{{ s }} - UTENTE: {{ selected_u }}</h3>
                    <div id="plot-area-{{ s }}" style="flex-grow:1; position:relative;">
                        <canvas id="chart-{{ s }}"></canvas>
                    </div>
                </div>
                {% endfor %}
            </div>

            <script>
                const dataCharts = {{ data_charts|tojson }};
                const currentU = "{{ selected_u }}";
                const currentS = localStorage.getItem('activeSensor') || 'ACC';

                const sensorConfig = {
                    "ACC": { color: "#e67e22", pointColor: "#d35400", label: "Modulo Accelerazione" },
                    "BVP": { color: "#3498db", pointColor: "#2980b9", label: "BVP (Blood Volume Pulse)" },
                    "EDA": { color: "#2ecc71", pointColor: "#27ae60", label: "EDA (Electrodermal Activity)" },
                    "HR":  { color: "#e74c3c", pointColor: "#c0392b", label: "Heart Rate (BPM)" },
                    "IBI": { color: "#9b59b6", pointColor: "#8e44ad", label: "IBI (Inter-beat Interval)" },
                    "TEMP":{ color: "#f1c40f", pointColor: "#f39c12", label: "Temperatura (°C)" }
                };

                document.getElementById('sensorSelect').value = currentS;
                const activeCard = document.getElementById('card-' + currentS);
                if(activeCard) activeCard.classList.add('active');

                const activeTitle = document.getElementById('title-' + currentS);
                if(activeTitle) activeTitle.style.color = sensorConfig[currentS].color;

                const ctx = document.getElementById('chart-' + currentS).getContext('2d');
                const config = sensorConfig[currentS];

                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: dataCharts[currentS].labels,
                        datasets: [{
                            label: config.label,
                            data: dataCharts[currentS].values,
                            borderColor: config.color,
                            backgroundColor: config.color, // Usiamo lo stesso colore della linea per la riga della legenda
                            fill: false,
                            tension: 0.3,
                            pointRadius: 4,
                            pointBackgroundColor: config.pointColor,
                            pointBorderColor: "#fff",
                            pointBorderWidth: 1,
                            borderWidth: 2
                        }]
                    },
                    options: { 
                        responsive: true, 
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                labels: {
                                    // TRUCCO: Usiamo boxHeight molto piccolo e boxWidth grande per simulare una riga
                                    boxWidth: 35,
                                    boxHeight: 2, 
                                    padding: 20,
                                    font: { size: 12, weight: 'bold' }
                                }
                            }
                        },
                        scales: {
                            x: { title: { display: true, text: 'Tempo' } },
                            y: { beginAtZero: false }
                        }
                    }
                });

                function updateFilters() {
                    const u = document.getElementById('userSelect').value;
                    const s = document.getElementById('sensorSelect').value;
                    localStorage.setItem('activeSensor', s);
                    window.location.href = "/dashboard?u=" + u;
                }

                setTimeout(() => { window.location.reload(); }, 10000);
            </script>
        </body>
    </html>
    ''', utenti=lista_utenti, selected_u=selected_user, data_charts=data_charts)

if __name__ == '__main__':
    app.run(port=5000, debug=False)