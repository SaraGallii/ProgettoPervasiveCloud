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
db = firestore.Client.from_service_account_json('credentials.json')

# --- GESTIONE DATI ---
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
        tipo_scelto = request.form.get('tipo_utente')
        
        print(f"Tentativo di login per: {username}")
        
        try:
            user_doc = db.collection('utenti').document(username).get()
            
            if user_doc.exists:
                user_data = user_doc.to_dict()
                print(f"Utente trovato in DB. Controllo password...")
                
                if str(user_data.get('password')) == str(password):
                    session['user'] = username
                    session['tipo'] = tipo_scelto
                    session['id_utente'] = user_data.get('id_utente') # Salviamo l'id_utente (stringa)
                    print("Login successo! Reindirizzamento...")
                    
                    if tipo_scelto == 'admin':
                        return redirect(url_for('dashboard'))
                    else:
                        return redirect(url_for('dashboard_utente'))
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

# --- ROTTA API LIVE_DATA SENZA INDICI ---
@app.route('/api/live_data')
def api_live_data():
    if 'user' not in session or session.get('tipo') != 'admin':
        return jsonify({"status": "error", "message": "Accesso negato"}), 403
        
    try:
        # 1. Recuperiamo gli id_utente registrati
        docs_utenti = db.collection('utenti').stream()
        id_validi = set()
        for d in docs_utenti:
            u_data = d.to_dict()
            id_u = u_data.get('id_utente')
            if id_u:
                id_validi.add(str(id_u).strip())
            id_validi.add(str(d.id).strip()) # Sicurezza sul nome del documento
            
        if not id_validi:
            return jsonify({"message": "Nessun utente registrato nel DB"}), 404

        # 2. Query semplice (Usa solo l'ordinamento, QUINDI NO INDICI COMPOSITI)
        # Recuperiamo gli ultimi 50 record arrivati in assoluto
        query = db.collection('dati_sensori')\
                  .order_by('data_ricezione', direction=firestore.Query.DESCENDING)\
                  .limit(50)
        
        # 3. Filtriamo in Python anziché farlo fare a Firestore
        ultimo_dato_valido = None
        for doc in query.stream():
            dati_doc = doc.to_dict()
            user_del_dato = str(dati_doc.get('user', '')).strip()
            
            # Se l'utente che ha inviato questo dato fa parte di quelli registrati, abbiamo fatto centro!
            if user_del_dato in id_validi:
                ultimo_dato_valido = dati_doc
                break # Ci fermiamo al primo (che è il più recente in assoluto)

        if ultimo_dato_valido:
            ts = ultimo_dato_valido.get('timestamp')
            if ts and hasattr(ts, 'timestamp'):
                ts_millisecondi = int(ts.timestamp() * 1000)
            elif isinstance(ts, (int, float)):
                ts_millisecondi = int(ts) if ts > 9999999999 else int(ts * 1000)
            else:
                ts_millisecondi = str(ts)
            
            return jsonify({
                "utente": ultimo_dato_valido.get('user', 'N/D'),
                "sessione": ultimo_dato_valido.get('session', 'N/D'),
                "sensore": ultimo_dato_valido.get('sensor', 'N/D'),
                "orario": ts_millisecondi,
                "valori": ultimo_dato_valido.get('valori', {})
            })
        else:
            return jsonify({"message": "Nessun dato recente per gli utenti registrati"}), 404
            
    except Exception as e:
        print(f"Errore Live Data Software-Filtered: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# --- ROTTA PAGINA LIVE DATA (INTERFACCIA GRAFICA) ---
@app.route('/live_admin')
def live_admin():
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))
        
    return render_template_string(HTML_LIVE_DATA)

# --- TEMPLATE SCHERMATA DATI IN TEMPO REALE ---
HTML_LIVE_DATA = '''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Empatica E4 - Live Data</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 500; font-size: 0.9rem; }
        .nav-links a:hover { text-decoration: underline; }
        
        .container { max-width: 900px; margin: 50px auto; padding: 0 20px; }
        .live-card { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }
        
        h1 { color: #1a73e8; font-size: 2.5rem; margin-top: 0; margin-bottom: 30px; border-bottom: 2px solid #e8f0fe; padding-bottom: 15px; }
        
        .row { display: flex; margin-bottom: 25px; font-size: 1.2rem; align-items: center; }
        .label { width: 180px; font-weight: bold; color: #555; }
        .value { font-weight: 500; color: #000; }
        .value.sensor { color: #d93025; font-weight: bold; } /* Colore rosso per il sensore come in foto */
        
        /* Modificato per ospitare i badge estetici senza sfondi grigi ereditati */
        .value.json { font-family: sans-serif; background: transparent; padding: 0; border: none; }
        
        .footer-status { margin-top: 40px; color: #888; font-size: 0.9rem; font-style: italic; display: flex; align-items: center; gap: 8px; }
        .dot { width: 8px; height: 8px; background-color: #34a853; border-radius: 50%; display: inline-block; animation: blink 1.5s infinite; }
        
        @keyframes blink { 0% { opacity: 0.3; } 50% { opacity: 1; } 100% { opacity: 0.3; } }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
        <div class="nav-links">
            <a href="/dashboard_admin">Dashboard</a>
            <a href="/live_admin" style="text-decoration: underline;">Dati in tempo reale</a>
            <a href="/register">Nuovo Utente</a>
            <a href="/logout" style="color: #ffcccc;">Logout</a>
        </div>
    </div>

    <div class="container">
        <div class="live-card">
            <h1>Empatica E4 wristband</h1>
            
            <div class="row">
                <div class="label">Utente:</div>
                <div class="value" id="lblUtente">--</div>
            </div>
            
            <div class="row">
                <div class="label">Sessione:</div>
                <div class="value" id="lblSessione">--</div>
            </div>
            
            <div class="row">
                <div class="label">Sensore:</div>
                <div class="value sensor" id="lblSensore">--</div>
            </div>
            
            <div class="row">
                <div class="label">Orario:</div>
                <div class="value" id="lblOrario">--</div>
            </div>
            
            <div class="row" style="align-items: flex-start;">
                <div class="label" style="margin-top: 8px;">Dati ricevuti:</div>
                <div class="value json" id="lblDati">{}</div>
            </div>
            
            <div class="footer-status">
                <span class="dot"></span> Aggiornamento automatico attivo...
            </div>
        </div>
    </div>

    <script>
        function formattaOrario(timestampMillisecondi) {
            if (!timestampMillisecondi || isNaN(timestampMillisecondi)) return "--:--:--";
            const data = new Date(Number(timestampMillisecondi));
            
            const ore = String(data.getHours()).padStart(2, '0');
            const minuti = String(data.getMinutes()).padStart(2, '0');
            const secondi = String(data.getSeconds()).padStart(2, '0');
            
            return `${ore}:${minuti}:${secondi}`;
        }

        function generaBadgeDati(valori) {
            if (!valori || typeof valori !== 'object' || Object.keys(valori).length === 0) return '{}';
            
            let htmlBadges = '<div style="display: flex; gap: 10px; flex-wrap: wrap;">';
            
            for (const [chiave, valore] of Object.entries(valori)) {
                htmlBadges += `
                    <span style="
                        background: #e8f0fe; 
                        color: #1a73e8; 
                        padding: 6px 14px; 
                        border-radius: 20px; 
                        font-weight: 600; 
                        font-size: 0.95rem;
                        border: 1px solid #c2dbff;
                        font-family: sans-serif;
                    ">
                        <strong style="color: #555; margin-right: 4px;">${chiave}:</strong>${valore}
                    </span>`;
            }
            
            htmlBadges += '</div>';
            return htmlBadges;
        }

        function caricaDatoRealTime() {
            fetch('/api/live_data')
                .then(response => response.json())
                .then(data => {
                    if (data.status !== "error" && !data.message) {
                        document.getElementById('lblUtente').innerText = data.utente;
                        document.getElementById('lblSessione').innerText = data.sessione;
                        document.getElementById('lblSensore').innerText = data.sensore;
                        
                        // 1. Stampa l'orario convertito in formato HH:MM:SS
                        document.getElementById('lblOrario').innerText = formattaOrario(data.orario);
                        
                        // 2. Stampa i dati formattati in badge eleganti anziché stringhe
                        document.getElementById('lblDati').innerHTML = generaBadgeDati(data.valori);
                    }
                })
                .catch(err => console.error("Errore fetch dati live:", err));
        }

        // Esegue il fetch subito all'avvio e poi ogni 1000 millisecondi (1 secondo)
        caricaDatoRealTime();
        setInterval(caricaDatoRealTime, 1000);
    </script>
</body>
</html>
'''

@app.route('/dashboard_admin')
def dashboard():
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    print(f"DEBUG: Accesso dashboard per l'utente {session['user']}")

    try:
        # MODIFICA: Recuperiamo la lista degli utenti direttamente dalla collezione 'utenti'
        try:
            docs_u = db.collection('utenti').stream()
            # Estraiamo l'id_utente di ogni documento (es: "01", "02", ecc.) 
            # che è quello usato nella collezione 'dati_sensori'
            lista_utenti = sorted(list(set([d.to_dict().get('id_utente') for d in docs_u if d.to_dict().get('id_utente')])))
        except Exception as e:
            print(f"Errore recupero utenti dal DB utenti: {e}")
            lista_utenti = []

        if not lista_utenti:
            lista_utenti = ["Nessun utente creato"]

        # Se l'admin non ha selezionato nulla dal menu a tendina, prendiamo il primo utente della lista
        selected_user = request.args.get('u', lista_utenti[0])
        selected_sess = request.args.get('s', '01') 
        
        data_charts = recupera_dati_grafici(selected_user, selected_sess)

        return render_template_string(HTML_DASHBOARD_ORIGINALE, utenti=lista_utenti, selected_u=selected_user, selected_s=selected_sess, data_charts=data_charts)

    except Exception as e:
        print(f"ERRORE FATALE: {e}")
        return f"<h1>Errore di caricamento</h1><p>{e}</p><a href='/logout'>Torna al login</a>"

# --- ROTTA DASHBOARD UTENTE ---
@app.route('/dashboard_utente')
def dashboard_utente():
    if 'user' not in session or session.get('tipo') != 'utente':
        return redirect(url_for('login'))

    mio_id = session.get('id_utente') # Prende l'id_utente stringa (es: "02")
    selected_sess = request.args.get('s', '01') 
    
    data_charts = recupera_dati_grafici(mio_id, selected_sess)

    # Passiamo in utenti solo una lista con il proprio ID per far funzionare il render senza errori
    return render_template_string(HTML_DASHBOARD_ORIGINALE, utenti=[mio_id], selected_u=mio_id, selected_s=selected_sess, data_charts=data_charts)

# --- FUNZIONE REFACTOR PER RECUPERO DATI ---
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
            for r in results:
                ts = r.get('timestamp')
                if ts and hasattr(ts, 'strftime'):
                    labels.append(ts.strftime('%H:%M:%S'))
                else:
                    labels.append(str(ts))
            
            values = []
            for r in results:
                val_raw = r.get('valori', '{}')
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
            print(f"Errore sensore {s}: {e}")
            data_charts[s] = {"labels": [], "values": []}
    return data_charts

# --- ROTTA REGISTRAZIONE ---
@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user' not in session or session.get('tipo') != 'admin':
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


# --- TEMPLATE HTML ORIGINALE PRESERVATO PARI PARI ---
HTML_DASHBOARD_ORIGINALE = '''
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
            {% if session['tipo'] == 'admin' %}
                <a href="/dashboard_admin">Dashboard</a>
                <a href="/live_admin">Dati in tempo reale</a>
                <a href="/statistics_admin">Statistiche</a>
                <a href="/register">Nuovo Utente</a>
            {% else %}
                <a href="/dashboard_utente">Dashboard</a>
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
            <div>
                <label><b>ID Utente:</b> {{ session['id_utente'] }}</label>
            </div>
            {% endif %}
            
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
            const s = document.getElementById('sessSelect').value;
            
            // Gestione URL dinamica basata sul ruolo per non rompere il refresh/update
            if ("{{ session['tipo'] }}" === "admin") {
                const u = document.getElementById('userSelect').value;
                window.location.href = `/dashboard_admin?u=${u}&s=${s}`;
            } else {
                window.location.href = `/dashboard_utente?s=${s}`;
            }
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

        setTimeout(() => location.reload(), 20000);
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)