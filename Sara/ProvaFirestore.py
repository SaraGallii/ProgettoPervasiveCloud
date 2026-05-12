from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime, timedelta
import json
import math
import statistics
import google.cloud.firestore as firestore

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

# --- INIZIALIZZAZIONE FIRESTORE ---
# Assicurati che il file JSON della chiave sia nella stessa cartella del file .py
db = firestore.Client.from_service_account_json('progetto-pcloud-5-b8e46802d217.json')

def init_db():
    """Inizializza l'utente admin se non esiste su Firestore"""
    user_ref = db.collection('utenti').document('admin')
    if not user_ref.get().exists:
        user_ref.set({
            'username': 'admin',
            'password': '0123',
            'id_utente': 'ALL',
            'cellulare': ''
        })

init_db()

# --- GESTIONE RICEZIONE DATI (SALVATAGGIO IN COLLEZIONI 01, 02, ecc.) ---
@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400
    try:
        # L'ID utente (es. "01") diventa il nome della collezione/cartella
        id_cartella = str(data.get('user')) 
        if not id_cartella:
            return jsonify({"status": "error", "message": "User ID missing"}), 400

        raw_ts = data.get('timestamp')
        ts_seconds = float(raw_ts) / 1000
        ts_formattato = datetime.fromtimestamp(ts_seconds).strftime('%H:%M:%S')
        
        doc_data = {
            'user': id_cartella,
            'session': data.get('session'),
            'sensor': data.get('sensor'),
            'timestamp': ts_formattato,
            'valori': json.dumps(data.get('data')),
            'data_ricezione': datetime.now() 
        }
        
        # Salvataggio nella collezione specifica
        db.collection(id_cartella).add(doc_data)
        
        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Errore Firestore durante ricezione: {e}")
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
        
        users_ref = db.collection('utenti')
        query = users_ref.where('username', '==', username).where('password', '==', password).limit(1).stream()
        
        user_doc = None
        for doc in query:
            user_doc = doc.to_dict()

        if user_doc:
            session['user'] = user_doc['username']
            session['id_utente'] = user_doc.get('id_utente', '01')
            session['tipo'] = tipo_utente
            return redirect(url_for('dashboard'))
        else:
            error = True

    return render_template_string(HTML_LOGIN, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- ROTTA DASHBOARD ---
@app.route('/dashboard_admin')
def dashboard():
    if 'user' not in session:
        return redirect(url_for('login'))

    # Recupero lista utenti per la tendina
    docs_u = db.collection('utenti').stream()
    lista_utenti = sorted([u.id for u in docs_u if u.id != 'admin'])
    if not lista_utenti: lista_utenti = ["01"]

    # Scelta dell'utente da visualizzare
    if session.get('tipo') == 'admin':
        selected_user = request.args.get('u', lista_utenti[0])
    else:
        selected_user = session.get('id_utente')

    selected_sess = request.args.get('s', '01') 
    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    data_charts = {}

    for s in sensori:
        # Query specifica sulla "cartella" dell'utente
        query = db.collection(selected_user)\
            .where('sensor', '==', s)\
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
                    magnitudes.append(round(math.sqrt(ax**2 + ay**2 + az**2), 3))
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

    return render_template_string(HTML_DASHBOARD, utenti=lista_utenti, selected_u=selected_user, selected_s=selected_sess, data_charts=data_charts)

# --- ROTTA STATISTICHE ---
@app.route('/statistics_admin')
def statistics_page():
    if 'user' not in session:
        return redirect(url_for('login'))

    docs_u = db.collection('utenti').stream()
    lista_utenti = sorted([u.id for u in docs_u if u.id != 'admin'])
    if not lista_utenti: lista_utenti = ["01"]

    if session.get('tipo') == 'admin':
        selected_user = request.args.get('u', lista_utenti[0])
    else:
        selected_user = session.get('id_utente')

    selected_sess = request.args.get('s', '01')
    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    stats_results = {}
    seven_days_ago = datetime.now() - timedelta(days=7)

    for s in sensori:
        # Query sulla "cartella" dell'utente
        query = db.collection(selected_user)\
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
    
    return render_template_string(HTML_STATS, data=stats_results, utenti=lista_utenti, sessioni=["01", "02", "03"], selected_u=selected_user, selected_s=selected_sess)

# --- ROTTA REGISTRAZIONE ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' not in session or session.get('tipo') != 'admin':
        return "Accesso negato.", 403

    message = ""
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        id_utente = request.form.get('id_utente')
        cellulare = request.form.get('cellulare')

        try:
            user_ref = db.collection('utenti').document(id_utente)
            if user_ref.get().exists:
                message = f"Errore: L'ID {id_utente} esiste già."
            else:
                user_ref.set({'username': username, 'password': password, 'id_utente': id_utente, 'cellulare': cellulare})
                # Crea un piccolo documento per inizializzare la collezione dell'utente
                db.collection(id_utente).document('init').set({'creato': datetime.now()})
                message = f"Utente {username} (ID: {id_utente}) registrato!"
        except Exception as e:
            message = f"Errore: {e}"

    return render_template_string(HTML_REGISTER, msg=message)

# --- TEMPLATES HTML (Tutto integrato) ---

HTML_LOGIN = '''
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
        <div style="margin-top:20px; color:#d93025; text-align:center; font-weight:bold; font-size:0.85rem; padding:10px; background:#fbe9e7; border-radius:4px;">ATTENZIONE: CREDENZIALI SBAGLIATE</div>
        {% endif %}
    </form>
</body>
</html>
'''

HTML_DASHBOARD = '''
<html>
    <head>
        <title>Dashboard Empatica E4</title>
        <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin: 0; padding: 0; }
            .navbar { background: #1a73e8; color: white; padding: 0 25px; height: 60px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 10px rgba(0,0,0,0.1); position: sticky; top: 0; z-index: 1000; }
            .dropdown { position: relative; display: inline-block; }
            .dropbtn { background-color: rgba(255,255,255,0.15); color: white; padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; }
            .dropdown-content { display: none; position: absolute; right: 0; background-color: white; min-width: 200px; box-shadow: 0px 8px 16px rgba(0,0,0,0.2); z-index: 1; border-radius: 4px; }
            .dropdown-content a { color: #333; padding: 12px 16px; text-decoration: none; display: block; }
            .dropdown-content a:hover { background-color: #f1f1f1; }
            .dropdown:hover .dropdown-content { display: block; }
            .container { max-width: 1000px; margin: 20px auto; padding: 0 20px; }
            .controls { background: white; padding: 15px 25px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; gap: 20px; }
            .chart-card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); height: 450px; display: none; }
            .chart-card.active { display: flex; flex-direction: column; }
        </style>
    </head>
    <body>
        <nav class="navbar">
            <h1 style="font-size:1.3rem;">Empatica E4 Dashboard</h1>
            <div class="dropdown">
                <button class="dropbtn">Menu principale ▼</button>
                <div class="dropdown-content">
                    <a href="/dashboard_admin">Grafici</a>
                    <a href="/statistics_admin">Statistiche</a>
                    <a href="/register">Registra Utente</a>
                    <a href="/logout" style="color:red; border-top:1px solid #eee;">Esci</a>
                </div>
            </div>
        </nav>
        <div class="container">
            <div class="controls">
                <div><label>Utente:</label> <select id="userSelect" onchange="updateFilters()">
                    {% for u in utenti %}<option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>{% endfor %}
                </select></div>
                <div><label>Sensore:</label> <select id="sensorSelect" onchange="updateFilters()">
                    <option value="ACC">ACC</option><option value="BVP">BVP</option><option value="EDA">EDA</option>
                    <option value="HR">HR</option><option value="IBI">IBI</option><option value="TEMP">TEMP</option>
                </select></div>
                <div><label>Sessione:</label> <select id="sessionSelect" onchange="updateFilters()">
                    <option value="01" {% if selected_s == '01' %}selected{% endif %}>01</option>
                    <option value="02" {% if selected_s == '02' %}selected{% endif %}>02</option>
                    <option value="03" {% if selected_s == '03' %}selected{% endif %}>03</option>
                </select></div>
            </div>
            {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
            <div id="card-{{ s }}" class="chart-card">
                <h3 style="color:#1a73e8;">{{ s }} - Utente {{ selected_u }}</h3>
                <div style="flex-grow:1;"><canvas id="chart-{{ s }}"></canvas></div>
            </div>
            {% endfor %}
        </div>
        <script>
            const dataCharts = {{ data_charts|tojson }};
            const currentS = localStorage.getItem('activeSensor') || 'ACC';
            document.getElementById('sensorSelect').value = currentS;
            document.getElementById('card-' + currentS).classList.add('active');
            new Chart(document.getElementById('chart-' + currentS).getContext('2d'), {
                type: 'line',
                data: {
                    labels: dataCharts[currentS].labels,
                    datasets: [{ label: currentS, data: dataCharts[currentS].values, borderColor: '#1a73e8', tension: 0.3, fill: true, backgroundColor: 'rgba(26, 115, 232, 0.1)' }]
                },
                options: { responsive: true, maintainAspectRatio: false }
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
'''

HTML_STATS = '''
<html>
    <head>
        <title>Statistiche Settimanali</title>
        <style>
            body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; margin:0; }
            .navbar { background: #1a73e8; color: white; padding: 15px 25px; display: flex; justify-content: space-between; }
            .container { max-width: 1000px; margin: 20px auto; padding: 0 20px; }
            .card { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); }
            table { width: 100%; border-collapse: collapse; }
            th, td { padding: 12px; border-bottom: 1px solid #eee; text-align: left; }
            th { color: #1a73e8; background: #f8f9fa; }
        </style>
    </head>
    <body>
        <nav class="navbar"><h1>Statistiche</h1><a href="/dashboard_admin" style="color:white;">Torna ai Grafici</a></nav>
        <div class="container">
            <div class="card">
                <h3>Dati Utente {{ selected_u }} - Sessione {{ selected_s }}</h3>
                <table>
                    <thead><tr><th>Sensore</th><th>Media</th><th>Mediana</th><th>Moda</th><th>Min</th><th>Max</th></tr></thead>
                    <tbody>
                        {% for s in ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"] %}
                        {% set vals = data[s] %}
                        <tr>
                            <td><strong>{{ s }}</strong></td>
                            {% if vals %}
                            <td>{{ vals.mean }}</td><td>{{ vals.median }}</td><td>{{ vals.mode }}</td><td>{{ vals.min }}</td><td>{{ vals.max }}</td>
                            {% else %}
                            <td colspan="5" style="color:#999;">Nessun dato negli ultimi 7 giorni</td>
                            {% endif %}
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </body>
</html>
'''

HTML_REGISTER = '''
<html>
<body style="font-family:sans-serif; background:#f0f2f5; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;">
    <div style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:350px;">
        <h2 style="color:#1a73e8; text-align:center;">Nuovo Utente</h2>
        {% if msg %}<p style="color:#1a73e8; text-align:center;">{{ msg }}</p>{% endif %}
        <form method="post">
            <label>Username:</label><input type="text" name="username" required style="width:100%; margin-bottom:15px; padding:8px;">
            <label>Password:</label><input type="password" name="password" required style="width:100%; margin-bottom:15px; padding:8px;">
            <label>ID Utente (Cartella Firestore - es. 02):</label><input type="text" name="id_utente" required style="width:100%; margin-bottom:15px; padding:8px;">
            <label>Cellulare:</label><input type="text" name="cellulare" style="width:100%; margin-bottom:20px; padding:8px;">
            <input type="submit" value="Registra" style="width:100%; padding:10px; background:#1a73e8; color:white; border:none; cursor:pointer;">
        </form>
        <p style="text-align:center;"><a href="/dashboard_admin">Annulla</a></p>
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    # Rendi accessibile il server sulla rete locale porta 5000
    app.run(host='0.0.0.0', port=5000, debug=True)