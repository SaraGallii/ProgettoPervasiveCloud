from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime
import sqlite3
import json

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
        # MODIFICA: Ordiniamo per timestamp DESC per prendere i 20 più recenti, 
        # poi invertiamo la lista con [::-1] per mostrarli cronologicamente da sinistra a destra
        cursor.execute("""
            SELECT timestamp, valori 
            FROM dati_sensori 
            WHERE sensor=? AND user=? 
            ORDER BY timestamp DESC LIMIT 20
        """, (s, selected_user))
        
        rows = cursor.fetchall()[::-1]
        
        labels = [r[0] for r in rows]
        values = []
        for r in rows:
            try:
                val = json.loads(r[1].replace("'", '"'))
                if s == "ACC": values.append(float(val.get('x', 0)))
                else: values.append(float(list(val.values())[0]))
            except: values.append(0)
        data_charts[s] = {"labels": labels, "values": values}
    
    conn.close()

    return render_template_string('''
    <html>
        <head>
            <title>Multi-User Empatica E4 wristband Dashboard</title>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 20px; }
                .container { max-width: 900px; margin: auto; }
                .header { background: white; padding: 15px 25px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }
                .controls { background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; }
                select { padding: 8px; border-radius: 6px; border: 1px solid #1a73e8; color: #1a73e8; font-weight: bold; cursor: pointer; outline: none; }
                .chart-card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); height: 450px; display: none; flex-direction: column; }
                .chart-card.active { display: flex; }
                h1 { color: #1a73e8; font-size: 1.4em; margin: 0; }
                .logout { color: #d93025; text-decoration: none; font-weight: bold; border: 1px solid #d93025; padding: 5px 15px; border-radius: 5px; transition: 0.3s; }
                .logout:hover { background: #fff1f0; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>Monitoraggio Empatica E4 wristband</h1>
                    <a href="/logout" class="logout">Esci</a>
                </div>

                <div class="controls">
                    <div>
                        <label style="color:#5f6368;"> Utente: </label>
                        <select id="userSelect" onchange="updateFilters()">
                            {% for u in utenti %}
                            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div>
                        <label style="color:#5f6368;"> Sensore: </label>
                        <select id="sensorSelect" onchange="updateFilters()">
                            <option value="ACC">Accelerometro</option>
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
                    <h3 style="color:#5f6368; font-size:0.9em; margin-top:0;">CRONOLOGIA LIVE: {{ s }} - UTENTE: <span class="displayUserText"></span></h3>
                    <div style="flex-grow:1; position:relative;"><canvas id="chart-{{ s }}"></canvas></div>
                </div>
                {% endfor %}
            </div>

            <script>
                const dataCharts = {{ data_charts|tojson }};
                
                const chartConfigs = {
                    "ACC": { color: "#1a73e8", label: "Accelerazione X" },
                    "BVP": { color: "#d93025", label: "Blood Volume Pulse" },
                    "EDA": { color: "#188038", label: "Attività Elettrodermica" },
                    "HR":  { color: "#e37400", label: "Battiti Cardiaci (BPM)" },
                    "IBI": { color: "#7b1fa2", label: "Intervallo tra battiti" },
                    "TEMP":{ color: "#12b5cb", label: "Temperatura Corporea" }
                };

                Object.keys(dataCharts).forEach(s => {
                    const ctx = document.getElementById('chart-' + s).getContext('2d');
                    new Chart(ctx, {
                        type: 'line',
                        data: {
                            labels: dataCharts[s].labels,
                            datasets: [{
                                label: chartConfigs[s].label,
                                data: dataCharts[s].values,
                                borderColor: chartConfigs[s].color,
                                backgroundColor: chartConfigs[s].color + '22',
                                pointBackgroundColor: chartConfigs[s].color,
                                pointBorderColor: "#fff",
                                pointRadius: 5,
                                pointHoverRadius: 7,
                                fill: true,
                                tension: 0.2, // Linea più tesa per evidenziare la sequenza temporale
                                borderWidth: 3
                            }]
                        },
                        options: { 
                            responsive: true, 
                            maintainAspectRatio: false,
                            plugins: { legend: { display: false } },
                            scales: {
                                y: { grid: { color: '#eee' } },
                                x: { 
                                    grid: { display: false },
                                    ticks: { maxRotation: 45, minRotation: 45 } // Timestamp inclinati per leggibilità
                                }
                            }
                        }
                    });
                });

                function updateFilters() {
                    const u = document.getElementById('userSelect').value;
                    const s = document.getElementById('sensorSelect').value;
                    localStorage.setItem('activeUser', u);
                    localStorage.setItem('activeSensor', s);
                    window.location.href = "/dashboard?u=" + u;
                }

                const urlParams = new URLSearchParams(window.location.search);
                const currentU = urlParams.get('u') || localStorage.getItem('activeUser') || (document.getElementById('userSelect').options[0] ? document.getElementById('userSelect').options[0].value : '');
                const currentS = localStorage.getItem('activeSensor') || 'ACC';

                if(document.getElementById('userSelect').querySelector('option[value="'+currentU+'"]')) {
                    document.getElementById('userSelect').value = currentU;
                }
                document.getElementById('sensorSelect').value = currentS;
                document.querySelectorAll('.displayUserText').forEach(el => el.innerText = currentU);
                
                const activeCard = document.getElementById('card-' + currentS);
                if(activeCard) activeCard.classList.add('active');

                setTimeout(() => { 
                    window.location.href = "/dashboard?u=" + currentU;
                }, 10000);
            </script>
        </body>
    </html>
    ''', utenti=lista_utenti, selected_u=selected_user, data_charts=data_charts)

if __name__ == '__main__':
    app.run(port=5000, debug=False)