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

from dateutil import parser # Assicurati che sia importato in alto

@app.route('/statistics_admin')
def statistics_admin():
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    # 1. Recupero lista utenti unica
    docs_u = db.collection('dati_sensori').select(['user']).limit(1000).stream()
    lista_utenti = sorted(list(set([d.to_dict().get('user') for d in docs_u if d.to_dict().get('user')])))
    
    selected_user = request.args.get('u')
    if not selected_user and lista_utenti:
        selected_user = lista_utenti[0]

    report_finale = {} 

    if selected_user:
        # 2. Recupero dinamico delle sessioni dell'utente (per non saltarne nessuna)
        docs_s = db.collection('dati_sensori').where('user', '==', selected_user).select(['session']).stream()
        elenco_sessioni = sorted(list(set([d.to_dict().get('session') for d in docs_s if d.to_dict().get('session')])))

        for sess in elenco_sessioni:
            report_finale[sess] = []
            
            # Cerchiamo l'ultimo timestamp per definire la finestra di 7 giorni
            query_last = db.collection('dati_sensori')\
                           .where('user', '==', selected_user)\
                           .where('session', '==', sess)\
                           .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                           .limit(1).get()
            
            if not query_last:
                continue
                
            raw_ts = query_last[0].to_dict().get('timestamp')
            
            try:
                # Gestione robusta della data
                if isinstance(raw_ts, str):
                    data_fine = parser.parse(raw_ts, fuzzy=True)
                else:
                    data_fine = raw_ts

                if data_fine.tzinfo is None:
                    data_fine = data_fine.replace(tzinfo=pytz.UTC)
                
                data_fine = data_fine.replace(microsecond=0)
                data_inizio = data_fine - timedelta(days=7)
            except Exception as e:
                print(f"Errore data per utente {selected_user}: {e}")
                continue

            sensori_nomi = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
            
            for s in sensori_nomi:
                # Query per il singolo sensore nell'intervallo stabilito
                docs_sensor = db.collection('dati_sensori')\
                                 .where('user', '==', selected_user)\
                                 .where('session', '==', sess)\
                                 .where('sensor', '==', s)\
                                 .where('timestamp', '>=', data_inizio)\
                                 .where('timestamp', '<=', data_fine)\
                                 .stream()

                valori = []
                for d in docs_sensor:
                    v_raw = d.to_dict().get('valori', {})
                    
                    # Se i valori sono salvati come stringa JSON (capita con alcuni upload)
                    if isinstance(v_raw, str):
                        try: v_raw = json.loads(v_raw.replace("'", '"'))
                        except: continue

                    try:
                        if s == "ACC":
                            # Cerca ax, ay, az sia minuscoli che maiuscoli
                            ax = float(v_raw.get('ax') or v_raw.get('AX') or v_raw.get('x', 0))
                            ay = float(v_raw.get('ay') or v_raw.get('AY') or v_raw.get('y', 0))
                            az = float(v_raw.get('az') or v_raw.get('AZ') or v_raw.get('z', 0))
                            valori.append(math.sqrt(ax**2 + ay**2 + az**2))
                        else:
                            # Cerca la chiave del sensore (es. "HR" o "hr")
                            # Se non la trova, prende il primo valore disponibile nel dizionario
                            val_found = v_raw.get(s) or v_raw.get(s.lower())
                            if val_found is None and v_raw:
                                val_found = next(iter(v_raw.values()))
                            
                            if val_found is not None:
                                valori.append(float(val_found))
                    except:
                        continue

                if valori:
                    s_media = round(statistics.mean(valori), 2)
                    s_min = round(min(valori), 2)
                    s_max = round(max(valori), 2)

                    # Salvataggio/Aggiornamento statistiche
                    stat_id = f"{selected_user}_{sess}_{s}"
                    db.collection('statistiche_settimanali').document(stat_id).set({
                        'user': selected_user,
                        'session': sess,
                        'sensor': s,
                        'media': s_media,
                        'min': s_min,
                        'max': s_max,
                        'data_elaborazione': datetime.now(pytz.UTC),
                        'intervallo_dati': f"{data_inizio.strftime('%d/%m/%Y')} - {data_fine.strftime('%d/%m/%Y')}"
                    })

                    report_finale[sess].append({
                        'sensor': s, 
                        'media': s_media, 
                        'min': s_min, 
                        'max': s_max
                    })

    return render_template_string(HTML_STATS_SIMPLE, utenti=lista_utenti, report=report_finale, sel_u=selected_user)

HTML_STATS_SIMPLE = '''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Admin - Statistiche Sensori</title>
    <style>
        body { font-family: 'Inter', -apple-system, sans-serif; background: #f0f2f5; color: #1c1e21; padding: 0; margin: 0; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
        
        .user-selector { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); margin-bottom: 30px; display: flex; align-items: center; gap: 15px; }
        select { padding: 10px 15px; border-radius: 8px; border: 1px solid #ddd; font-size: 16px; min-width: 200px; background: white; }
        
        .card { background: white; border-radius: 16px; padding: 25px; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); border-top: 5px solid #1a73e8; margin-bottom: 40px; }
        .card h2 { margin-top: 0; color: #1a73e8; font-size: 1.3rem; border-bottom: 1px solid #eee; padding-bottom: 15px; margin-bottom: 20px; }
        
        table { width: 100%; border-collapse: collapse; table-layout: fixed; }
        th, td { padding: 12px; border: 1px solid #edf2f7; text-align: center; font-size: 14px; }
        th { background: #f8fafc; color: #64748b; text-transform: uppercase; font-size: 12px; letter-spacing: 0.05em; }
        
        .row-label { background: #f8fafc; font-weight: bold; color: #1e293b; text-align: left; width: 120px; }
        .val-media { font-weight: bold; color: #1a73e8; font-size: 1.1rem; }
        .val-min { color: #10b981; }
        .val-max { color: #ef4444; }
        
        .btn-back { color: white; text-decoration: none; background: rgba(255,255,255,0.2); padding: 8px 15px; border-radius: 6px; font-size: 14px; }
        .empty-state { text-align: center; color: #94a3b8; padding: 40px; font-style: italic; background: white; border-radius: 12px; }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0; font-size: 1.5rem;">Statistiche Empatica E4</h2>
        <a href="/dashboard_admin" class="btn-back">← Dashboard</a>
    </div>

    <div class="container">
        <div class="user-selector">
            <form method="get" style="display: flex; align-items: center; gap: 15px;">
                <label for="u"><b>Utente:</b></label>
                <select name="u" id="u" onchange="this.form.submit()">
                    {% for u in utenti %}
                        <option value="{{ u }}" {% if u == sel_u %}selected{% endif %}>{{ u }}</option>
                    {% endfor %}
                </select>
            </form>
            <span style="margin-left: auto; color: #64748b; font-size: 0.85rem;">Range: 7 giorni dall'ultima rilevazione della sessione</span>
        </div>

        {% if report %}
            {% for sess, data_list in report.items() %}
            <div class="card">
                <h2>Sessione: {{ sess }}</h2>
                
                {% if data_list %}
                <table>
                    <thead>
                        <tr>
                            <th>Statistica</th>
                            {% for item in data_list %}
                                <th>{{ item.sensor }}</th>
                            {% endfor %}
                        </tr>
                    </thead>
                    <tbody>
                        <tr>
                            <td class="row-label">Media</td>
                            {% for item in data_list %}
                                <td class="val-media">{{ item.media }}</td>
                            {% endfor %}
                        </tr>
                        <tr>
                            <td class="row-label">Minimo</td>
                            {% for item in data_list %}
                                <td class="val-min">{{ item.min }}</td>
                            {% endfor %}
                        </tr>
                        <tr>
                            <td class="row-label">Massimo</td>
                            {% for item in data_list %}
                                <td class="val-max">{{ item.max }}</td>
                            {% endfor %}
                        </tr>
                    </tbody>
                </table>
                {% else %}
                <div class="empty-state">Nessun dato trovato per questa sessione.</div>
                {% endif %}
            </div>
            {% endfor %}
        {% else %}
            <div class="empty-state">Nessun utente selezionato o nessun dato presente.</div>
        {% endif %}
    </div>
</body>
</html>
'''

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