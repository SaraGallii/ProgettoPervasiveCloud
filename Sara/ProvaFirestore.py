from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime, timedelta
import json
import math
import statistics
import google.cloud.firestore as firestore

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

# --- INIZIALIZZAZIONE FIRESTORE ---
# Se sei su una VM Google Cloud con permessi corretti, basta firestore.Client()
# Altrimenti usa: firestore.Client.from_service_account_json('path/to/key.json')
db = firestore.Client()

def init_db():
    """Inizializza l'utente admin se non esiste su Firestore"""
    user_ref = db.collection('utenti').document('admin')
    if not user_ref.get().exists:
        user_ref.set({
            'username': 'admin',
            'password': '0123',
            'id_utente': '01',
            'cellulare': ''
        })

init_db()

# --- GESTIONE DATI ---
@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400
    try:
        raw_ts = data.get('timestamp')
        ts_seconds = float(raw_ts) / 1000
        ts_formattato = datetime.fromtimestamp(ts_seconds).strftime('%H:%M:%S')
        
        # Salvataggio su Firestore
        doc_data = {
            'user': data.get('user'),
            'session': data.get('session'),
            'sensor': data.get('sensor'),
            'timestamp': ts_formattato,
            'valori': json.dumps(data.get('data')),
            'data_ricezione': datetime.now() # Salvato come oggetto timestamp nativo
        }
        db.collection('dati_sensori').add(doc_data)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Errore Firestore: {e}")
        return jsonify({"status": "error"}), 500

# --- ROTTE AUTENTICAZIONE ---
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = False
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        tipo_utente = request.form.get('tipo_utente')
        
        # Query su Firestore
        users_ref = db.collection('utenti')
        query = users_ref.where('username', '==', username).where('password', '==', password).limit(1).stream()
        
        user_doc = None
        for doc in query:
            user_doc = doc.to_dict()

        if user_doc:
            session['user'] = user_doc['username']
            session['id_utente'] = user_doc['id_utente']
            session['tipo'] = tipo_utente
            return redirect(url_for('dashboard'))
        else:
            error = True

    return render_template_string('''
    <html>
    <body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f0f2f5; margin:0;">
        <form method="post" style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:320px;">
            <h2 style="color:#1a73e8; margin-top:0; text-align:center; font-size:1.2rem;">Login Empatica E4</h2>
            <label style="font-size:0.9rem; color:#555;">Tipologia Utente:</label>
            <select name="tipo_utente" style="width:100%; margin-bottom:15px; padding:10px; border:1px solid #ddd; border-radius:6px; background:white;">
                <option value="admin">Admin</option>
                <option value="utente">Utente</option>
            </select>
            <label style="font-size:0.9rem; color:#555;">Username:</label>
            <input type="text" name="username" required style="width:100%; margin-bottom:15px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
            <label style="font-size:0.9rem; color:#555;">Password:</label>
            <input type="password" name="password" required style="width:100%; margin-bottom:25px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
            <input type="submit" value="Accedi" style="width:100%; padding:12px; background:#1a73e8; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:bold; font-size:1rem;">
            {% if error %}
            <div style="margin-top:20px; color:#d93025; text-align:center; font-weight:bold; font-size:0.85rem; padding:10px; background:#fbe9e7; border-radius:4px;">
                ATTENZIONE: CREDENZIALI SBAGLIATE
            </div>
            {% endif %}
        </form>
    </body>
    </html>
    ''', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ROTTA DASHBOARD ---
@app.route('/dashboard_admin')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Recupero utenti unici
    docs = db.collection('dati_sensori').stream()
    lista_utenti = sorted(list(set(doc.to_dict().get('user') for doc in docs if doc.to_dict().get('user'))))
    if not lista_utenti: lista_utenti = ["Nessun dato"]

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    data_charts = {}
    
    selected_user = request.args.get('u', lista_utenti[0])
    selected_sess = request.args.get('s', '01') 

    for s in sensori:
        # Query Firestore per sensore, utente e sessione
        query = db.collection('dati_sensori')\
            .where('sensor', '==', s)\
            .where('user', '==', selected_user)\
            .where('session', '==', selected_sess)\
            .order_by('data_ricezione', direction=firestore.Query.DESCENDING)\
            .limit(50).stream()
        
        rows = [doc.to_dict() for doc in query][::-1]
        labels = [r['timestamp'] for r in rows]
        
        if s == "ACC":
            magnitudes = []
            for r in rows:
                try:
                    val = json.loads(r['valori'].replace("'", '"'))
                    ax = float(val.get('ax', val.get('x', 0)))
                    ay = float(val.get('ay', val.get('y', 0)))
                    az = float(val.get('az', val.get('z', 0)))
                    mag = math.sqrt(ax**2 + ay**2 + az**2)
                    magnitudes.append(round(mag, 3))
                except: magnitudes.append(0)
            data_charts[s] = {"labels": labels, "values": magnitudes}
        else:
            values = []
            for r in rows:
                try:
                    val = json.loads(r['valori'].replace("'", '"'))
                    values.append(float(list(val.values())[0]))
                except: values.append(0)
            data_charts[s] = {"labels": labels, "values": values} 

    return render_template_string('''
    <html>
        <head>
            <title>Dashboard Empatica E4</title>
            <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 0; }
                .navbar { background: #1a73e8; color: white; padding: 0 25px; height: 60px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 1000; }
                .nav-title { margin: 0; font-size: 1.3rem; }
                .dropdown { position: relative; display: inline-block; }
                .dropbtn { background-color: rgba(255,255,255,0.15); color: white; padding: 8px 16px; font-size: 14px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
                .dropdown-content { display: none; position: absolute; right: 0; background-color: white; min-width: 200px; box-shadow: 0px 8px 16px rgba(0,0,0,0.2); z-index: 1; border-radius: 4px; overflow: hidden; }
                .dropdown-content a { color: #333; padding: 12px 16px; text-decoration: none; display: block; font-size: 14px; }
                .dropdown-content a:hover { background-color: #f1f1f1; }
                .dropdown:hover .dropdown-content { display: block; }
                .container { max-width: 1000px; margin: 20px auto; padding: 0 20px; }
                .controls { background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; flex-wrap: wrap; }
                .control-group { display: flex; align-items: center; gap: 8px; }
                select { padding: 6px; border-radius: 6px; border: 1px solid #ddd; }
                .chart-card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); height: 450px; display: none; }
                .chart-card.active { display: flex; flex-direction: column; }
                .logout-link { color: #d93025 !important; font-weight: bold; border-top: 1px solid #eee; }
            </style>
        </head>
        <body>
            <nav class="navbar">
                <h1 class="nav-title">Empatica E4 Dashboard</h1>
                <div class="dropdown">
                    <button class="dropbtn">Menu principale ▼</button>
                    <div class="dropdown-content">
                        <a href="/dashboard_admin">Grafici</a>
                        <a href="/statistics_admin">Statistiche</a>
                        <a href="/register">Registrazione Nuovo Utente</a>
                        <a href="/logout" class="logout-link">Esci (Logout)</a>
                    </div>
                </div>
            </nav>
            <div class="container">
                <div class="controls">
                    <div class="control-group">
                        <label>Utente:</label>
                        <select id="userSelect" onchange="updateFilters()">
                            {% for u in utenti %}
                            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="control-group">
                        <label>Sensore:</label>
                        <select id="sensorSelect" onchange="updateFilters()">
                            <option value="ACC">Accelerazione</option>
                            <option value="BVP">BVP</option>
                            <option value="EDA">EDA</option>
                            <option value="HR">HR (BPM)</option>
                            <option value="IBI">IBI</option>
                            <option value="TEMP">Temperatura</option>
                        </select>
                    </div>
                    <div class="control-group">
                        <label>Sessione:</label>
                        <select id="sessionSelect" onchange="updateFilters()">
                            <option value="01" {% if selected_s == '01' %}selected{% endif %}>01</option>
                            <option value="02" {% if selected_s == '02' %}selected{% endif %}>02</option>
                            <option value="03" {% if selected_s == '03' %}selected{% endif %}>03</option>
                        </select>
                    </div>
                </div>
                {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
                <div id="card-{{ s }}" class="chart-card">
                    <h3 style="margin:0 0 15px 0; color:#1a73e8;">{{ s }} - {{ selected_u }} (Sess. {{ selected_s }})</h3>
                    <div style="flex-grow:1; position:relative;"><canvas id="chart-{{ s }}"></canvas></div>
                </div>
                {% endfor %}
            </div>
            <script>
                const dataCharts = {{ data_charts|tojson }};
                const currentS = localStorage.getItem('activeSensor') || 'ACC';
                document.getElementById('sensorSelect').value = currentS;
                document.getElementById('card-' + currentS).classList.add('active');
                const ctx = document.getElementById('chart-' + currentS).getContext('2d');
                new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: dataCharts[currentS].labels,
                        datasets: [{
                            label: currentS,
                            data: dataCharts[currentS].values,
                            borderColor: '#1a73e8',
                            backgroundColor: 'rgba(26, 115, 232, 0.1)',
                            borderWidth: 2, tension: 0.3, fill: true, pointRadius: 2
                        }]
                    },
                    options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: false } } }
                });
                function updateFilters() {
                    const u = document.getElementById('userSelect').value;
                    const sensor = document.getElementById('sensorSelect').value;
                    const sess = document.getElementById('sessionSelect').value;
                    localStorage.setItem('activeSensor', sensor);
                    window.location.href = "/dashboard_admin?u=" + u + "&s=" + sess;
                }
                setTimeout(() => { window.location.reload(); }, 15000);
            </script>
        </body>
    </html>
    ''', utenti=lista_utenti, selected_u=selected_user, selected_s=selected_sess, data_charts=data_charts)


@app.route('/statistics_admin')
def statistics_page():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 1. Recupero lista utenti
    docs_u = db.collection('dati_sensori').stream()
    lista_utenti = sorted(list(set(doc.to_dict().get('user') for doc in docs_u if doc.to_dict().get('user'))))
    if not lista_utenti: lista_utenti = ["Nessun dato"]

    # 2. Recupero lista sessioni
    docs_s = db.collection('dati_sensori').stream()
    lista_sessioni = sorted(list(set(doc.to_dict().get('session') for doc in docs_s if doc.to_dict().get('session'))))
    if not lista_sessioni: lista_sessioni = ["01"]

    selected_user = request.args.get('u', lista_utenti[0])
    selected_sess = request.args.get('s', lista_sessioni[0])

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    stats_results = {}
    
    # Calcolo 7 giorni fa
    seven_days_ago = datetime.now() - timedelta(days=7)

    # 3. Calcolo statistiche
    for s in sensori:
        query = db.collection('dati_sensori')\
            .where('user', '==', selected_user)\
            .where('sensor', '==', s)\
            .where('session', '==', selected_sess)\
            .where('data_ricezione', '>=', seven_days_ago).stream()
        
        raw_values = []
        for doc in query:
            try:
                r = doc.to_dict()
                val = json.loads(r['valori'].replace("'", '"'))
                if s == "ACC":
                    ax, ay, az = float(val.get('ax', 0)), float(val.get('ay', 0)), float(val.get('az', 0))
                    raw_values.append(math.sqrt(ax**2 + ay**2 + az**2))
                else:
                    raw_values.append(float(list(val.values())[0]))
            except: continue

        if raw_values:
            stats_results[s] = {
                "mean": round(statistics.mean(raw_values), 2),
                "median": round(statistics.median(raw_values), 2),
                "mode": round(statistics.mode(raw_values), 2),
                "min": round(min(raw_values), 2),
                "max": round(max(raw_values), 2),
            }
        else:
            stats_results[s] = None
    
    return render_template_string('''
    <html>
        <head>
            <title>Statistiche Empatica E4</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 0; }
                .navbar { background: #1a73e8; color: white; padding: 0 25px; height: 60px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .container { max-width: 1000px; margin: 20px auto; padding: 0 20px; }
                .controls { background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; flex-wrap: wrap; }
                .control-group { display: flex; align-items: center; gap: 8px; }
                select { padding: 8px; border-radius: 6px; border: 1px solid #ddd; background: white; }
                .card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { padding: 15px; text-align: left; border-bottom: 1px solid #eee; }
                th { background: #f8f9fa; color: #1a73e8; font-weight: bold; text-transform: uppercase; font-size: 0.85rem; }
                .no-data { color: #999; font-style: italic; text-align: center; }
            </style>
        </head>
        <body>
            <nav class="navbar">
                <h1 style="font-size: 1.3rem; margin:0;">Statistiche Settimanali</h1>
                <a href="/dashboard_admin" style="color:white; text-decoration:none; font-weight:bold; background:rgba(255,255,255,0.2); padding:8px 15px; border-radius:6px;">Torna ai Grafici</a>
            </nav>
            <div class="container">
                <div class="controls">
                    <div class="control-group">
                        <label>Utente:</label>
                        <select id="userSelect" onchange="updateFilters()">
                            {% for u in utenti %}
                            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="control-group">
                        <label>Sessione:</label>
                        <select id="sessionSelect" onchange="updateFilters()">
                            {% for s in sessioni %}
                            <option value="{{ s }}" {% if s == selected_s %}selected{% endif %}>{{ s }}</option>
                            {% endfor %}
                        </select>
                    </div>
                </div>
                <div class="card">
                    <h3 style="margin:0 0 20px 0; color:#1a73e8;">Riepilogo: Utente {{ selected_u }} - Sessione {{ selected_s }}</h3>
                    <table>
                        <thead>
                            <tr><th>Sensore</th><th>Media</th><th>Mediana</th><th>Moda</th><th>Min</th><th>Max</th></tr>
                        </thead>
                        <tbody>
                            {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
                            {% set vals = data[s] %}
                            <tr>
                                <td><strong style="color:#333;">{{ s }}</strong></td>
                                {% if vals %}
                                <td>{{ vals.mean }}</td><td>{{ vals.median }}</td><td>{{ vals.mode }}</td><td>{{ vals.min }}</td><td>{{ vals.max }}</td>
                                {% else %}
                                <td colspan="5" class="no-data">Nessun dato negli ultimi 7 giorni</td>
                                {% endif %}
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            <script>
                function updateFilters() {
                    const u = document.getElementById('userSelect').value;
                    const sess = document.getElementById('sessionSelect').value;
                    window.location.href = "/statistics_admin?u=" + u + "&s=" + sess;
                }
            </script>
        </body>
    </html>
    ''', data=stats_results, utenti=lista_utenti, sessioni=lista_sessioni, selected_u=selected_user, selected_s=selected_sess)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' not in session or session.get('user') != 'admin':
        return "Accesso negato.", 403

    message = ""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        id_utente = request.form.get('id_utente')
        cellulare = request.form.get('cellulare')

        try:
            user_ref = db.collection('utenti').document(username)
            if user_ref.get().exists:
                message = "Errore: Lo username esiste già."
            else:
                user_ref.set({
                    'username': username,
                    'password': password,
                    'id_utente': id_utente,
                    'cellulare': cellulare
                })
                message = f"Utente {username} registrato con successo!"
        except Exception as e:
            message = f"Errore: {e}"

    return render_template_string('''
    <html>
    <body style="font-family:sans-serif; background:#f0f2f5; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;">
        <div style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:350px;">
            <h2 style="color:#1a73e8; margin-top:0; text-align:center;">Registrazione Utente</h2>
            {% if msg %}<div style="padding:10px; margin-bottom:15px; border-radius:6px; background:#e8f0fe; color:#1a73e8; font-size:0.9rem; text-align:center;">{{ msg }}</div>{% endif %}
            <form method="post">
                <label style="font-size:0.85rem; color:#555;">Username:</label>
                <input type="text" name="username" required style="width:100%; margin-bottom:15px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
                <label style="font-size:0.85rem; color:#555;">Password:</label>
                <input type="password" name="password" required style="width:100%; margin-bottom:15px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
                <label style="font-size:0.85rem; color:#555;">ID Utente :</label>
                <input type="text" name="id_utente" placeholder="Es: 02" required style="width:100%; margin-bottom:15px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
                <label style="font-size:0.85rem; color:#555;">Cellulare:</label>
                <input type="text" name="cellulare" style="width:100%; margin-bottom:20px; padding:10px; border:1px solid #ddd; border-radius:6px; box-sizing:border-box;">
                <input type="submit" value="Registra Utente" style="width:100%; padding:12px; background:#1a73e8; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:bold;">
            </form>
            <div style="text-align:center; margin-top:15px;"><a href="/dashboard_admin" style="color:#666; font-size:0.85rem; text-decoration:none;">← Torna alla Dashboard</a></div>
        </div>
    </body>
    </html>
    ''', msg=message)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)