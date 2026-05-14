from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime,timezone, timedelta
from dateutil import parser
import json
import math
import statistics
from google.cloud import firestore

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

# Inizializzazione Firestore
db = firestore.Client.from_service_account_json('progetto-pcloud-5-b8e46802d217.json')

# --- GESTIONE DATI ---
@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400
    try:
        raw_ts = data.get('timestamp')
        # 1. Creiamo l'oggetto data/ora corretto
        ts_datetime = datetime.fromtimestamp(float(raw_ts) / 1000)
        
        # 2. Prendiamo i valori (es: {"temp": 33.81})
        valori_data = data.get('data') 

        # Salvataggio su Firestore
        doc_ref = db.collection('dati_sensori').document()
        doc_ref.set({
            'user': data.get('user'),
            'session': data.get('session'),
            'sensor': data.get('sensor'),
            'timestamp': ts_datetime,      # Ora è un vero Timestamp
            'valori': valori_data,         # Salvato come MAP (non stringa!), meglio per i calcoli
            'data_ricezione': datetime.now(timezone(timedelta(hours=2)))
        })
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Errore DB: {e}")
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
        
        print(f"Tentativo di login per: {username}") # Debug
        
        try:
            user_doc = db.collection('utenti').document(username).get()
            
            if user_doc.exists:
                user_data = user_doc.to_dict()
                print(f"Utente trovato in DB. Controllo password...") # Debug
                
                if str(user_data.get('password')) == str(password):
                    session['user'] = username
                    session['tipo'] = request.form.get('tipo_utente')
                    print("Login successo! Reindirizzamento...") # Debug
                    return redirect(url_for('dashboard'))
                else:
                    print("Password errata.")
                    error = True
            else:
                print("Utente non esistente su Firestore.")
                error = True
        except Exception as e:
            print(f"Errore durante il login: {e}")
            error = True

    return render_template_string('''
    <html>
    <body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f0f2f5; margin:0;">
        <form method="post" style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:320px;">
            <h2 style="color:#1a73e8; text-align:center;">Empatica E4 Login</h2>
            <select name="tipo_utente" style="width:100%; margin-bottom:15px; padding:10px; border-radius:6px; border:1px solid #ddd;">
                <option value="admin">Admin</option>
                <option value="utente">Utente</option>
            </select>
            <input type="text" name="username" placeholder="Username" required style="width:100%; margin-bottom:15px; padding:10px; border-radius:6px; border:1px solid #ddd; box-sizing:border-box;">
            <input type="password" name="password" placeholder="Password" required style="width:100%; margin-bottom:20px; padding:10px; border-radius:6px; border:1px solid #ddd; box-sizing:border-box;">
            <input type="submit" value="Accedi" style="width:100%; padding:12px; background:#1a73e8; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:bold;">
            {% if error %}<p style="color:red; text-align:center; font-size:0.8rem; margin-top:10px;">Credenziali errate o errore server</p>{% endif %}
        </form>
    </body>
    </html>
    ''', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ROTTA DASHBOARD COMPLETA ---
@app.route('/dashboard_admin')
def dashboard():
    # 1. Controllo Sessione rapido
    if 'user' not in session:
        return redirect(url_for('login'))

    print(f"DEBUG: Accesso dashboard per l'utente {session['user']}")

    try:
        # 2. Recupero Utenti (Limitato a 50 per evitare blocchi se il DB è grande)
        # Cerchiamo di ottenere solo i nomi degli utenti unici
        try:
            docs_u = db.collection('dati_sensori').select(['user']).limit(100).stream()
            lista_utenti = sorted(list(set([d.to_dict().get('user') for d in docs_u if d.to_dict().get('user')])))
        except Exception as e:
            print(f"Errore recupero utenti: {e}")
            lista_utenti = []

        # Se il database è vuoto o la query fallisce
        if not lista_utenti:
            lista_utenti = ["Nessun dato"]

        # 3. Parametri URL
        selected_user = request.args.get('u', lista_utenti[0])
        selected_sess = request.args.get('s', '01') 
        
        sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
        data_charts = {}

        # 4. Recupero Dati per ogni sensore
        for s in sensori:
            try:
                query = db.collection('dati_sensori')\
                          .where('user', '==', selected_user)\
                          .where('sensor', '==', s)\
                          .where('session', '==', selected_sess)\
                          .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                          .limit(40) # Limitiamo i punti per caricare velocemente
                
                results = [d.to_dict() for d in query.stream()]
                results.reverse() # Ordine cronologico per Chart.js
                
                labels = []
                for r in results:
                    ts = r.get('timestamp')
                    if ts and hasattr(ts, 'strftime'):
                        # Se è un oggetto datetime di Firestore
                        labels.append(ts.strftime('%H:%M:%S'))
                    else:
                        # Fallback se è rimasta qualche vecchia stringa nel DB
                        labels.append(str(ts))
                values = []
                
                for r in results:
                    val_raw = r.get('valori', '{}')
                    # Pulizia stringa se Firestore ha salvato JSON come stringa
                    val = json.loads(val_raw.replace("'", '"')) if isinstance(val_raw, str) else val_raw
                    
                    if s == "ACC":
                        ax = float(val.get('ax', val.get('x', 0)))
                        ay = float(val.get('ay', val.get('y', 0)))
                        az = float(val.get('az', val.get('z', 0)))
                        values.append(round(math.sqrt(ax**2 + ay**2 + az**2), 2))
                    else:
                        # Prende il primo valore numerico nel dizionario
                        values.append(float(next(iter(val.values()))))
                
                data_charts[s] = {"labels": labels, "values": values}
            except Exception as e:
                print(f"Errore sensore {s}: {e}")
                data_charts[s] = {"labels": [], "values": []}

        # 5. Ritorno dell'HTML (Template Integrato)
        return render_template_string('''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Empatica E4 - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
        .card-controls { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 25px; display: flex; gap: 20px; align-items: center; }
        select { padding: 10px; border-radius: 5px; border: 1px solid #ddd; font-size: 14px; background: white; }
        .chart-container { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); height: 500px; }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 500; font-size: 0.9rem; }
        .nav-links a:hover { text-decoration: underline; }
        h3 { margin-top: 0; color: #1a73e8; }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
        <div class="nav-links">
            <a href="/dashboard_admin">Dashboard</a>
            <a href="/statistics_admin">Statistiche</a>
            <a href="/register">Nuovo Utente</a>
            <a href="/logout" style="color: #ffcccc;">Logout</a>
        </div>
    </div>

    <div class="container">
        <div class="card-controls">
            <div>
                <label><b>Utente:</b></label>
                <select id="userSelect" onchange="update()">
                    {% for u in utenti %}
                    <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                    {% endfor %}
                </select>
            </div>
            <div>
                <label><b>Sensore:</b></label>
                <select id="sensorSelect" onchange="changeSensor()">
                    <option value="ACC">Accelerometro (Magnitudo)</option>
                    <option value="BVP">BVP (Blood Volume Pulse)</option>
                    <option value="EDA">EDA (Elettrodermica)</option>
                    <option value="HR">Frequenza Cardiaca (HR)</option>
                    <option value="IBI">IBI (Inter-Beat Interval)</option>
                    <option value="TEMP">Temperatura</option>
                </select>
            </div>
            <div>
                <label><b>Sessione:</b></label>
                <select id="sessSelect" onchange="update()">
                    <option value="01" {% if selected_s == '01' %}selected{% endif %}>01</option>
                    <option value="02" {% if selected_s == '02' %}selected{% endif %}>02</option>
                    <option value="03" {% if selected_s == '03' %}selected{% endif %}>03</option>
                </select>
            </div>
        </div>

        <div class="chart-container">
            <h3 id="chartTitle">Caricamento grafico...</h3>
            <div style="height: 400px; position: relative;">
                <canvas id="mainChart"></canvas>
            </div>
        </div>
    </div>

    <script>
        const allData = {{ data_charts|tojson }};
        let currentChart = null;

        function render(sensorId) {
            const ctx = document.getElementById('mainChart').getContext('2d');
            const data = allData[sensorId];
            
            document.getElementById('chartTitle').innerText = sensorId + " - Dati in tempo reale";

            if (currentChart) currentChart.destroy();

            currentChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: sensorId,
                        data: data.values,
                        borderColor: '#1a73e8',
                        backgroundColor: 'rgba(26, 115, 232, 0.1)',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true,
                        pointRadius: 3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: { y: { beginAtZero: false, grid: { color: '#f0f0f0' } }, x: { grid: { display: false } } }
                }
            });
        }

        function update() {
            const u = document.getElementById('userSelect').value;
            const s = document.getElementById('sessSelect').value;
            window.location.href = `/dashboard_admin?u=${u}&s=${s}`;
        }

        function changeSensor() {
            const s = document.getElementById('sensorSelect').value;
            localStorage.setItem('lastSensor', s);
            render(s);
        }

        // Inizializzazione
        window.onload = () => {
            const last = localStorage.getItem('lastSensor') || 'HR';
            document.getElementById('sensorSelect').value = last;
            render(last);
        };

        // Refresh automatico ogni 20 secondi
        setTimeout(() => location.reload(), 20000);
    </script>
</body>
</html>
        ''', utenti=lista_utenti, selected_u=selected_user, selected_s=selected_sess, data_charts=data_charts)

    except Exception as e:
        print(f"ERRORE FATALE: {e}")
        return f"<h1>Errore di caricamento</h1><p>{e}</p><a href='/logout'>Torna al login</a>"

@app.route('/statistics_admin')
def statistics_page():
    if 'user' not in session:
        return redirect(url_for('login'))

    # 1. Recupero lista utenti ordinata numericamente
    docs_u = db.collection('dati_sensori').select(['user']).limit(100).stream()
    lista_utenti = sorted(list(set([d.to_dict().get('user') for d in docs_u if d.to_dict().get('user')])), key=lambda x: int(x) if x.isdigit() else 0)
    if not lista_utenti:
        lista_utenti = ["Nessun dato"]

    # 2. Recupero lista sessioni disponibili
    docs_s = db.collection('dati_sensori').select(['session']).limit(100).stream()
    lista_sessioni = sorted(list(set([d.to_dict().get('session') for d in docs_s if d.to_dict().get('session')])))
    if not lista_sessioni:
        lista_sessioni = ["01"]

    # Parametri selezionati (da URL o default)
    selected_user = request.args.get('u', lista_utenti[0])
    selected_sess = request.args.get('s', lista_sessioni[0])

    # 3. Trova la data più recente nel database per l'utente selezionato
    #    per calcolare "ultimi 7 giorni" come nel tuo SQLite
    latest_query = db.collection('dati_sensori')\
                     .where('user', '==', selected_user)\
                     .where('session', '==', selected_sess)\
                     .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                     .limit(1)
    
    latest_docs = list(latest_query.stream())
    if latest_docs:
        latest_ts = latest_docs[0].to_dict().get('timestamp')
        if latest_ts:
            # Data di 7 giorni prima della data più recente
            end_date = latest_ts
            start_date = latest_ts - timedelta(days=7)
        else:
            # Fallback: usa aprile 2021
            start_date = datetime(2021, 4, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))
            end_date = datetime(2021, 4, 8, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))
    else:
        # Fallback
        start_date = datetime(2021, 4, 1, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        end_date = datetime(2021, 4, 8, 0, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    stats_results = {}

    # 4. Calcolo statistiche per l'utente e la sessione selezionati (ultimi 7 giorni)
    for s in sensori:
        try:
            query = db.collection('dati_sensori')\
                      .where('user', '==', selected_user)\
                      .where('sensor', '==', s)\
                      .where('session', '==', selected_sess)\
                      .where('timestamp', '>=', start_date)\
                      .where('timestamp', '<=', end_date)
            
            results = [d.to_dict() for d in query.stream()]
            raw_values = []
            
            for r in results:
                try:
                    val_raw = r.get('valori', {})
                    
                    # Parsing valori (gestisce sia dict che stringa)
                    if isinstance(val_raw, str):
                        val = json.loads(val_raw.replace("'", '"'))
                    else:
                        val = val_raw
                    
                    if s == "ACC":
                        ax = float(val.get('ax', val.get('x', 0)))
                        ay = float(val.get('ay', val.get('y', 0)))
                        az = float(val.get('az', val.get('z', 0)))
                        raw_values.append(math.sqrt(ax**2 + ay**2 + az**2))
                    else:
                        # Prende il primo valore disponibile
                        if isinstance(val, dict) and val:
                            first_val = next(iter(val.values()))
                            raw_values.append(float(first_val))
                        else:
                            raw_values.append(float(val))
                except (ValueError, TypeError, KeyError, StopIteration) as e:
                    print(f"Errore parsing {s}: {e}")
                    continue

            if raw_values:
                stats_results[s] = {
                    "mean": round(statistics.mean(raw_values), 2),
                    "median": round(statistics.median(raw_values), 2),
                    "mode": round(statistics.mode(raw_values), 2) if len(raw_values) > 2 else round(statistics.mean(raw_values), 2),
                    "min": round(min(raw_values), 2),
                    "max": round(max(raw_values), 2),
                    "count": len(raw_values)
                }
            else:
                stats_results[s] = None
                
        except Exception as e:
            print(f"Errore sensore {s}: {e}")
            stats_results[s] = None

    # Formatta le date per la visualizzazione
    periodo_str = f"{start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')}"

    return render_template_string('''
    <html>
        <head>
            <title>Statistiche Empatica E4</title>
            <style>
                body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 0; }
                .navbar { background: #1a73e8; color: white; padding: 0 25px; height: 60px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
                .navbar h1 { font-size: 1.3rem; margin: 0; }
                .navbar a { color: white; text-decoration: none; font-weight: bold; background: rgba(255,255,255,0.2); padding: 8px 15px; border-radius: 6px; }
                .container { max-width: 1200px; margin: 20px auto; padding: 0 20px; }
                .controls { 
                    background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; 
                    box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; flex-wrap: wrap;
                    align-items: center;
                }
                .control-group { display: flex; align-items: center; gap: 8px; }
                .control-group label { font-weight: 600; color: #555; }
                select { padding: 8px 12px; border-radius: 6px; border: 1px solid #ddd; background: white; cursor: pointer; }
                .period-badge { background: #e8f0fe; color: #1a73e8; padding: 6px 12px; border-radius: 20px; font-size: 0.8rem; margin-left: auto; }
                .card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
                .card h3 { margin: 0 0 20px 0; color: #1a73e8; }
                table { width: 100%; border-collapse: collapse; margin-top: 10px; }
                th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
                th { background: #f8f9fa; color: #1a73e8; font-weight: bold; text-transform: uppercase; font-size: 0.85rem; }
                tr:hover { background-color: #fcfcfc; }
                .no-data { color: #999; font-style: italic; text-align: center; }
                .sensor-name { font-weight: 600; color: #333; }
                .stats-value { font-family: monospace; font-size: 1.05rem; }
            </style>
        </head>
        <body>
            <nav class="navbar">
                <h1>📊 Statistiche Settimanali - Empatica E4</h1>
                <a href="/dashboard_admin">📈 Torna ai Grafici</a>
            </nav>

            <div class="container">
                <div class="controls">
                    <div class="control-group">
                        <label>👤 Utente:</label>
                        <select id="userSelect" onchange="updateFilters()">
                            {% for u in utenti %}
                            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="control-group">
                        <label>🎮 Sessione:</label>
                        <select id="sessionSelect" onchange="updateFilters()">
                            {% for s in sessioni %}
                            <option value="{{ s }}" {% if s == selected_s %}selected{% endif %}>{{ s }}</option>
                            {% endfor %}
                        </select>
                    </div>
                    <div class="period-badge">
                        📅 Ultimi 7 giorni: {{ periodo }}
                    </div>
                </div>

                <div class="card">
                    <h3>Riepilogo: Utente {{ selected_u }} - Sessione {{ selected_s }}</h3>
                    <table>
                        <thead>
                            <tr>
                                <th>Sensore</th>
                                <th>Media</th>
                                <th>Mediana</th>
                                <th>Moda</th>
                                <th>Min</th>
                                <th>Max</th>
                                <th>Campioni</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
                            {% set vals = data[s] %}
                            <tr>
                                <td class="sensor-name">
                                    {% if s == "ACC" %}📳 Accelerometro
                                    {% elif s == "BVP" %}❤️ BVP
                                    {% elif s == "EDA" %}⚡ EDA
                                    {% elif s == "HR" %}💓 Frequenza Cardiaca
                                    {% elif s == "IBI" %}⏱️ IBI
                                    {% elif s == "TEMP" %}🌡️ Temperatura
                                    {% else %}{{ s }}
                                    {% endif %}
                                </td>
                                {% if vals %}
                                <td class="stats-value">{{ vals.mean }}</td>
                                <td class="stats-value">{{ vals.median }}</td>
                                <td class="stats-value">{{ vals.mode }}</td>
                                <td class="stats-value">{{ vals.min }}</td>
                                <td class="stats-value">{{ vals.max }}</td>
                                <td class="stats-value">{{ vals.count }}</td>
                                {% else %}
                                <td colspan="6" class="no-data">❌ Nessun dato negli ultimi 7 giorni</td>
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
                    window.location.href = "/statistics_admin?u=" + encodeURIComponent(u) + "&s=" + encodeURIComponent(sess);
                }
            </script>
        </body>
    </html>
    ''', data=stats_results, utenti=lista_utenti, sessioni=lista_sessioni, 
        selected_u=selected_user, selected_s=selected_sess, periodo=periodo_str)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' not in session or session.get('user') != 'admin':
        return "Accesso negato.", 403

    message = ""
    if request.method == 'POST':
        username = request.form.get('username')
        user_ref = db.collection('utenti').document(username)
        if not user_ref.get().exists:
            user_ref.set({
                'username': username,
                'password': request.form.get('password'),
                'id_utente': request.form.get('id_utente'),
                'cellulare': request.form.get('cellulare')
            })
            message = f"Utente {username} registrato con successo!"
        else:
            message = "Errore: Lo username esiste già."

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
            <div style="text-align:center; margin-top:15px;"><a href="/dashboard_admin" style="color:#666; font-size:0.85rem; text-decoration:none;">← Dashboard</a></div>
        </div>
    </body>
    </html>
    ''', msg=message)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)