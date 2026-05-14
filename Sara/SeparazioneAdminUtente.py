from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime, timezone, timedelta
from dateutil import parser
import json
import math
import statistics
from google.cloud import firestore

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

# Inizializzazione Firestore
db = firestore.Client.from_service_account_json('progetto-pcloud-5-b8e46802d217.json')

# --- GESTIONE DATI (Invariata) ---
@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400
    try:
        raw_ts = data.get('timestamp')
        ts_datetime = datetime.fromtimestamp(float(raw_ts) / 1000)
        valori_data = data.get('data') 

        doc_ref = db.collection('dati_sensori').document()
        doc_ref.set({
            'user': data.get('user'),
            'session': data.get('session'),
            'sensor': data.get('sensor'),
            'timestamp': ts_datetime,
            'valori': valori_data,
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
        tipo_scelto = request.form.get('tipo_utente') # 'admin' o 'utente'
        
        try:
            user_doc = db.collection('utenti').document(username).get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
                if str(user_data.get('password')) == str(password):
                    # Salviamo i dati necessari in sessione
                    session['user'] = username
                    session['tipo'] = tipo_scelto
                    # Prendiamo l'ID stringa dell'utente dal DB (es: "01", "02")
                    session['id_utente'] = user_data.get('id_utente')

                    if tipo_scelto == 'admin':
                        return redirect(url_for('dashboard_admin'))
                    else:
                        return redirect(url_for('dashboard_utente'))
                else:
                    error = True
            else:
                error = True
        except Exception as e:
            print(f"Errore login: {e}")
            error = True

    return render_template_string(HTML_LOGIN, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# --- DASHBOARD ADMIN ---
@app.route('/dashboard_admin')
def dashboard_admin():
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    try:
        docs_u = db.collection('dati_sensori').select(['user']).limit(100).stream()
        lista_utenti = sorted(list(set([d.to_dict().get('user') for d in docs_u if d.to_dict().get('user')])))
        if not lista_utenti: lista_utenti = ["Nessun dato"]

        selected_user = request.args.get('u', lista_utenti[0])
        selected_sess = request.args.get('s', '01') 
        
        data_charts = recupera_dati_grafici(selected_user, selected_sess)
        
        return render_template_string(HTML_DASHBOARD, utenti=lista_utenti, selected_u=selected_user, selected_s=selected_sess, data_charts=data_charts)
    except Exception as e:
        return f"<h1>Errore</h1><p>{e}</p>"

# --- DASHBOARD UTENTE (Solo i propri dati) ---
@app.route('/dashboard_utente')
def dashboard_utente():
    if 'user' not in session or session.get('tipo') != 'utente':
        return redirect(url_for('login'))

    # Qui forziamo l'ID utente preso dalla sessione (id_utente salvato in registrazione)
    mio_id = session.get('id_utente') 
    selected_sess = request.args.get('s', '01') 
    
    data_charts = recupera_dati_grafici(mio_id, selected_sess)
    
    # Passiamo una lista utenti contenente solo se stessi per non rompere il template
    return render_template_string(HTML_DASHBOARD, utenti=[mio_id], selected_u=mio_id, selected_s=selected_sess, data_charts=data_charts)

# --- FUNZIONE HELPER PER RECUPERO DATI ---
def recupera_dati_grafici(target_user, session_id):
    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    data_charts = {}
    for s in sensori:
        try:
            query = db.collection('dati_sensori')\
                      .where('user', '==', str(target_user))\
                      .where('sensor', '==', s)\
                      .where('session', '==', session_id)\
                      .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                      .limit(40)
            
            results = [d.to_dict() for d in query.stream()]
            results.reverse()
            
            labels = []
            values = []
            for r in results:
                ts = r.get('timestamp')
                labels.append(ts.strftime('%H:%M:%S') if hasattr(ts, 'strftime') else str(ts))
                
                val_raw = r.get('valori', {})
                val = json.loads(val_raw.replace("'", '"')) if isinstance(val_raw, str) else val_raw
                
                if s == "ACC":
                    ax = float(val.get('ax', val.get('x', 0)))
                    ay = float(val.get('ay', val.get('y', 0)))
                    az = float(val.get('az', val.get('z', 0)))
                    values.append(round(math.sqrt(ax**2 + ay**2 + az**2), 2))
                else:
                    values.append(float(next(iter(val.values()), 0)))
            
            data_charts[s] = {"labels": labels, "values": values}
        except Exception as e:
            print(f"Errore {s}: {e}")
            data_charts[s] = {"labels": [], "values": []}
    return data_charts

# --- REGISTRAZIONE (Solo Admin) ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' not in session or session.get('tipo') != 'admin':
        return "Accesso negato. Solo gli amministratori possono registrare utenti.", 403

    message = ""
    if request.method == 'POST':
        username = request.form.get('username')
        user_ref = db.collection('utenti').document(username)
        if not user_ref.get().exists:
            user_ref.set({
                'username': username,
                'password': request.form.get('password'),
                'id_utente': request.form.get('id_utente'), # Es: "02"
                'cellulare': request.form.get('cellulare')
            })
            message = f"Utente {username} registrato correttamente!"
        else:
            message = "Errore: Lo username esiste già."

    return render_template_string(HTML_REGISTER, msg=message)

# --- TEMPLATES HTML (Variabili stringa per pulizia) ---

HTML_LOGIN = '''
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
        {% if error %}<p style="color:red; text-align:center; font-size:0.8rem; margin-top:10px;">Credenziali errate</p>{% endif %}
    </form>
</body>
</html>
'''

HTML_DASHBOARD = '''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Empatica E4 - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; }
        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
        .card-controls { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 25px; display: flex; gap: 20px; align-items: center; }
        .chart-container { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); height: 500px; }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 500; }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0;">E4 {{ session['tipo']|upper }}</h2>
        <div class="nav-links">
            <a href="/{{ 'dashboard_admin' if session['tipo'] == 'admin' else 'dashboard_utente' }}">Dashboard</a>
            {% if session['tipo'] == 'admin' %}
            <a href="/register">Nuovo Utente</a>
            {% endif %}
            <a href="/logout" style="color: #ffcccc;">Logout</a>
        </div>
    </div>

    <div class="container">
        <div class="card-controls">
            {% if session['tipo'] == 'admin' %}
            <div>
                <label><b>Utente:</b></label>
                <select id="userSelect" onchange="update()">
                    {% for u in utenti %}
                    <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                    {% endfor %}
                </select>
            </div>
            {% else %}
            <div><label><b>Mio ID:</b> {{ session['id_utente'] }}</label></div>
            {% endif %}
            
            <div>
                <label><b>Sensore:</b></label>
                <select id="sensorSelect" onchange="changeSensor()">
                    <option value="ACC">Accelerometro</option>
                    <option value="BVP">BVP</option>
                    <option value="EDA">EDA</option>
                    <option value="HR">HR</option>
                    <option value="IBI">IBI</option>
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
            <h3 id="chartTitle">Caricamento...</h3>
            <canvas id="mainChart"></canvas>
        </div>
    </div>

    <script>
        const allData = {{ data_charts|tojson }};
        let currentChart = null;

        function render(sensorId) {
            const ctx = document.getElementById('mainChart').getContext('2d');
            const data = allData[sensorId];
            document.getElementById('chartTitle').innerText = sensorId;
            if (currentChart) currentChart.destroy();
            currentChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{ label: sensorId, data: data.values, borderColor: '#1a73e8', tension: 0.4 }]
                },
                options: { responsive: true, maintainAspectRatio: false }
            });
        }

        function update() {
            const s = document.getElementById('sessSelect').value;
            let url = "";
            if("{{ session['tipo'] }}" === "admin") {
                const u = document.getElementById('userSelect').value;
                url = `/dashboard_admin?u=${u}&s=${s}`;
            } else {
                url = `/dashboard_utente?s=${s}`;
            }
            window.location.href = url;
        }

        function changeSensor() {
            const s = document.getElementById('sensorSelect').value;
            localStorage.setItem('lastSensor', s);
            render(s);
        }

        window.onload = () => {
            const last = localStorage.getItem('lastSensor') || 'HR';
            document.getElementById('sensorSelect').value = last;
            render(last);
        };
        setTimeout(() => location.reload(), 30000);
    </script>
</body>
</html>
'''

HTML_REGISTER = '''
<html>
<body style="font-family:sans-serif; background:#f0f2f5; display:flex; justify-content:center; align-items:center; height:100vh; margin:0;">
    <div style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:350px;">
        <h2 style="color:#1a73e8; margin-top:0;">Registrazione Utente</h2>
        {% if msg %}<p style="color:blue;">{{ msg }}</p>{% endif %}
        <form method="post">
            <input type="text" name="username" placeholder="Username Login" required style="width:100%; margin-bottom:10px; padding:8px;">
            <input type="password" name="password" placeholder="Password" required style="width:100%; margin-bottom:10px; padding:8px;">
            <input type="text" name="id_utente" placeholder="ID Sensore (es: 02)" required style="width:100%; margin-bottom:10px; padding:8px;">
            <input type="text" name="cellulare" placeholder="Cellulare" style="width:100%; margin-bottom:15px; padding:8px;">
            <input type="submit" value="Registra" style="width:100%; padding:10px; background:#1a73e8; color:white; border:none; border-radius:6px; cursor:pointer;">
        </form>
        <a href="/dashboard_admin">Torna alla Dashboard</a>
    </div>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)