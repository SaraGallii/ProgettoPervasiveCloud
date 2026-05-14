from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime, timezone, timedelta
from dateutil import parser
import json
import math
import statistics
from google.cloud import firestore

import os
import time
import threading

app = Flask(__name__)
app.secret_key = "p4ssw0rd" 

# Inizializzazione Firestore
db = firestore.Client.from_service_account_json('progetto-pcloud-5-b8e46802d217.json')

# ============================================================
# STATISTICHE "PRO": buffer in RAM + flush periodico su Firestore
# ============================================================

# Intervallo flush (secondi). Consigliato 2–5. Default 3.
STATS_FLUSH_INTERVAL = float(os.environ.get("STATS_FLUSH_INTERVAL", "3.0"))

_stats_lock = threading.Lock()
_stats_buffer = {}   # key=(user,session,sensor) -> {"user","session","sensor","n","mean","m2","min","max"}
_flusher_started = False

# ============================================================
# FINESTRA MOBILE (sliding window) in RAM
# ============================================================
# Durata finestra in secondi (default 60). Modificabile via env var.
STATS_WINDOW_SECONDS = int(os.environ.get("STATS_WINDOW_SECONDS", "60"))

# Stato finestra: key=(user,session,sensor) -> dict
# dq contiene tuple (t_epoch_sec, value)
_window_state = {}  # key -> {"dq":deque, "sum":float, "sumsq":float, "min":float, "max":float, "dirty":bool}


def _to_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


def estrai_valore_numerico(sensor, valori):
    """
    Converte 'valori' in un valore numerico unico su cui calcolare statistiche.
    - ACC: magnitudo sqrt(ax^2 + ay^2 + az^2) usando ax/ay/az oppure x/y/z
    - altri sensori: media di tutti i campi numerici presenti (robusto)
    """
    if valori is None:
        return None

    # Se arriva come stringa, prova a convertirla in dict
    if isinstance(valori, str):
        try:
            valori = json.loads(valori)
        except Exception:
            try:
                valori = json.loads(valori.replace("'", '"'))
            except Exception:
                return None

    if not isinstance(valori, dict) or not valori:
        return None

    s = str(sensor).upper().strip()

    if s == "ACC":
        ax = _to_float(valori.get('ax', valori.get('x', 0)), 0.0)
        ay = _to_float(valori.get('ay', valori.get('y', 0)), 0.0)
        az = _to_float(valori.get('az', valori.get('z', 0)), 0.0)
        return math.sqrt(ax * ax + ay * ay + az * az)

    nums = []
    for v in valori.values():
        fv = _to_float(v, None)
        if fv is not None:
            nums.append(fv)

    if not nums:
        return None

    return nums[0] if len(nums) == 1 else (sum(nums) / len(nums))

def _window_update(user, session_id, sensor, valori, t_epoch_sec):
    """
    Aggiorna la finestra mobile per la chiave (user,session,sensor).
    Mantiene dq con (timestamp_sec, valore) e aggregati sum/sumsq/min/max.
    """
    x = estrai_valore_numerico(sensor, valori)
    if x is None:
        return

    key = (str(user).strip(), str(session_id).strip(), str(sensor).strip())
    x = float(x)
    t = float(t_epoch_sec)
    cutoff = t - STATS_WINDOW_SECONDS

    with _stats_lock:
        st = _window_state.get(key)
        if st is None:
            st = {
                "dq": deque(),
                "sum": 0.0,
                "sumsq": 0.0,
                "min": x,
                "max": x,
                "dirty": False
            }
            _window_state[key] = st

        dq = st["dq"]
        dq.append((t, x))
        st["sum"] += x
        st["sumsq"] += x * x

        # update min/max veloci
        if len(dq) == 1:
            st["min"] = x
            st["max"] = x
        else:
            st["min"] = min(st["min"], x)
            st["max"] = max(st["max"], x)

        # Evict vecchi
        while dq and dq[0][0] < cutoff:
            _, old = dq.popleft()
            st["sum"] -= old
            st["sumsq"] -= old * old
            if old == st["min"] or old == st["max"]:
                st["dirty"] = True

        # Se min/max potenzialmente invalidi, ricalcola una volta (solo quando serve)
        if st["dirty"]:
            if dq:
                vals = [v for _, v in dq]
                st["min"] = min(vals)
                st["max"] = max(vals)
            st["dirty"] = False


def _window_prune_to_now(key, now_epoch_sec):
    """
    Pruna la finestra in base al tempo corrente (utile se il flusher gira e arrivano pochi dati).
    """
    cutoff = float(now_epoch_sec) - STATS_WINDOW_SECONDS
    st = _window_state.get(key)
    if not st:
        return

    dq = st["dq"]
    while dq and dq[0][0] < cutoff:
        _, old = dq.popleft()
        st["sum"] -= old
        st["sumsq"] -= old * old
        if old == st["min"] or old == st["max"]:
            st["dirty"] = True

    if st["dirty"]:
        if dq:
            vals = [v for _, v in dq]
            st["min"] = min(vals)
            st["max"] = max(vals)
        st["dirty"] = False


def _window_snapshot(key, now_epoch_sec):
    """
    Restituisce uno snapshot statistiche finestra: count/mean/min/max/std campionaria.
    Pruna prima in base a now.
    """
    with _stats_lock:
        if key not in _window_state:
            return None

        _window_prune_to_now(key, now_epoch_sec)
        st = _window_state.get(key)
        if not st:
            return None

        n = len(st["dq"])
        if n <= 0:
            return None

        s = st["sum"]
        ss = st["sumsq"]
        mean = s / n

        if n > 1:
            # var campionaria = (Σx^2 - (Σx)^2/n)/(n-1)
            var = (ss - (s * s) / n) / (n - 1)
            if var < 0:
                var = 0.0
            std = math.sqrt(var)
        else:
            std = 0.0

        return {
            "seconds": STATS_WINDOW_SECONDS,
            "count": n,
            "mean": mean,
            "min": st["min"],
            "max": st["max"],
            "std": std,
            "updated_at": datetime.now(timezone.utc)
        }

def _buffer_update(user, session_id, sensor, valori):
    """
    Aggiorna il buffer in RAM con Welford incrementale per la chiave (user, session, sensor).
    """
    x = estrai_valore_numerico(sensor, valori)
    if x is None:
        return

    key = (str(user).strip(), str(session_id).strip(), str(sensor).strip())
    x = float(x)

    with _stats_lock:
        st = _stats_buffer.get(key)
        if st is None:
            _stats_buffer[key] = {
                "user": key[0],
                "session": key[1],
                "sensor": key[2],
                "n": 1,
                "mean": x,
                "m2": 0.0,
                "min": x,
                "max": x
            }
            return

        n = st["n"]
        mean = st["mean"]
        m2 = st["m2"]

        n_new = n + 1
        delta = x - mean
        mean_new = mean + delta / n_new
        delta2 = x - mean_new
        m2_new = m2 + delta * delta2

        st["n"] = n_new
        st["mean"] = mean_new
        st["m2"] = m2_new
        st["min"] = min(st["min"], x)
        st["max"] = max(st["max"], x)


@firestore.transactional
def _merge_stats_tx(transaction, doc_ref, delta_stats, win_snapshot=None):
    """
    Merge su Firestore: unisce statistiche già presenti con il delta (buffer) via formule Welford.
    """
    snap = doc_ref.get(transaction=transaction)
    now = datetime.now(timezone.utc)

    n2 = int(delta_stats["n"])
    mean2 = float(delta_stats["mean"])
    m2_2 = float(delta_stats["m2"])
    min2 = float(delta_stats["min"])
    max2 = float(delta_stats["max"])

    if not snap.exists:
        n = n2
        mean = mean2
        m2 = m2_2
        min_v = min2
        max_v = max2
    else:
        st = snap.to_dict() or {}
        n1 = int(st.get("count", 0))
        mean1 = float(st.get("mean", 0.0))
        m2_1 = float(st.get("m2", 0.0))
        min1 = st.get("min", min2)
        max1 = st.get("max", max2)

        if n1 <= 0:
            n = n2
            mean = mean2
            m2 = m2_2
            min_v = min2
            max_v = max2
        else:
            n = n1 + n2
            delta = mean2 - mean1
            mean = mean1 + delta * (n2 / n)
            m2 = m2_1 + m2_2 + (delta * delta) * (n1 * n2 / n)

            min_v = min(float(min1), min2) if min1 is not None else min2
            max_v = max(float(max1), max2) if max1 is not None else max2

    # std campionaria
    if n > 1:
        var = m2 / (n - 1)
        std = math.sqrt(var) if var >= 0 else 0.0
    else:
        std = 0.0

    payload = {
        "user": delta_stats["user"],
        "session": delta_stats["session"],
        "sensor": delta_stats["sensor"],
        "count": n,
        "mean": mean,
        "min": min_v,
        "max": max_v,
        "m2": m2,
        "std": std,
        "updated_at": now
    }

    # Aggiunge finestra mobile nello stesso documento (nessuna write extra)
    if win_snapshot:
        payload["win"] = win_snapshot

    transaction.set(doc_ref, payload, merge=True)


def _flush_stats_once():
    """
    Svuota il buffer in modo atomico e fa merge su Firestore.
    ==> Una scrittura per chiave ogni STATS_FLUSH_INTERVAL.
    """
    global _stats_buffer

    with _stats_lock:
        if not _stats_buffer:
            return
        to_flush = _stats_buffer
        _stats_buffer = {}

    now_epoch = time.time()

    for (u, sess, sens), delta_stats in to_flush.items():
        doc_id = f"{u}_{sess}_{sens}"
        doc_ref = db.collection("statistiche").document(doc_id)

        key = (u, sess, sens)
        win_snap = _window_snapshot(key, now_epoch)

        try:
            tx = db.transaction()
            _merge_stats_tx(tx, doc_ref, delta_stats, win_snapshot=win_snap)
        except Exception as e:
            print(f"[STATS] Errore flush {doc_id}: {e}")


def _stats_flusher_loop():
    while True:
        time.sleep(STATS_FLUSH_INTERVAL)
        _flush_stats_once()


def start_stats_flusher():
    """
    Avvia il thread flusher UNA SOLA VOLTA.
    Nota: con debug=True Flask avvia un reloader (2 processi). Evitiamo doppio thread.
    """
    global _flusher_started
    if _flusher_started:
        return

    # Evita double-start col reloader
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    _flusher_started = True
    t = threading.Thread(target=_stats_flusher_loop, daemon=True)
    t.start()
    print(f"[STATS] Flusher avviato. Interval={STATS_FLUSH_INTERVAL}s")

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

        # ✅ STATISTICHE PRO: aggiorna buffer in RAM (flush ogni STATS_FLUSH_INTERVAL secondi)
        try:
            _buffer_update(
                user=data.get('user'),
                session_id=data.get('session'),
                sensor=data.get('sensor'),
                valori=valori_data
            )
        except Exception as e:
            print(f"[STATS] Errore buffer_update: {e}")

        # ✅ FINESTRA MOBILE: aggiorna sliding window (ultimi STATS_WINDOW_SECONDS secondi)
        try:
            _window_update(
                user=data.get('user'),
                session_id=data.get('session'),
                sensor=data.get('sensor'),
                valori=valori_data,
                t_epoch_sec=ts_datetime.timestamp()
            )
        except Exception as e:
            print(f"[STATS] Errore window_update: {e}")

        return jsonify({"status": "success"}), 200
    except Exception as e:
        print(f"Errore DB: {e}")
        return jsonify({"status": "error"}), 500

# --- ROTTE AUTENTICAZIONE ---
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.before_request
def _ensure_stats_flusher():
    # Si avvia al primo request, una volta sola
    start_stats_flusher()

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

@app.route('/statistics_admin')
def statistics_admin():
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    # Recupera utenti dalla collezione 'utenti' (come fai in dashboard)
    try:
        docs_u = db.collection('utenti').stream()
        lista_utenti = sorted(list(set([d.to_dict().get('id_utente') for d in docs_u if d.to_dict().get('id_utente')])))
    except Exception as e:
        print(f"Errore recupero utenti: {e}")
        lista_utenti = []

    if not lista_utenti:
        lista_utenti = ["Nessun utente creato"]

    selected_user = request.args.get('u', lista_utenti[0])
    selected_sess = request.args.get('s', '01')

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    stats = {}

    for s in sensori:
        doc_id = f"{str(selected_user).strip()}_{str(selected_sess).strip()}_{s}"
        doc = db.collection("statistiche").document(doc_id).get()
        stats[s] = doc.to_dict() if doc.exists else None

    return render_template_string(
        HTML_STATISTICHE,
        utenti=lista_utenti,
        selected_u=selected_user,
        selected_s=selected_sess,
        stats=stats,
        interval=int(STATS_FLUSH_INTERVAL)
    )

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

HTML_STATISTICHE = '''
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Empatica E4 - Statistiche</title>
  <style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
    .navbar { background: #1a73e8; color: white; padding: 15px 30px; display:flex; justify-content:space-between; align-items:center; box-shadow:0 2px 5px rgba(0,0,0,0.1); }
    .nav-links a { color:white; text-decoration:none; margin-left:20px; font-weight:500; font-size:0.9rem; }
    .nav-links a:hover { text-decoration: underline; }
    .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
    .card-controls { background:white; padding:20px; border-radius:10px; box-shadow:0 4px 6px rgba(0,0,0,0.05); margin-bottom:25px; display:flex; gap:20px; align-items:center; }
    select { padding:10px; border-radius:5px; border:1px solid #ddd; font-size:14px; background:white; }
    .grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:16px; }
    .card { background:white; padding:18px; border-radius:12px; box-shadow:0 10px 20px rgba(0,0,0,0.05); }
    .title { color:#1a73e8; font-weight:700; margin:0 0 10px; }
    .kpi { display:flex; flex-direction:column; gap:6px; font-size:0.95rem; }
    .kpi b { color:#555; width:120px; display:inline-block; }
    .muted { color:#888; font-style:italic; }
    .pill { display:inline-block; padding:6px 10px; border-radius:999px; background:#e8f0fe; color:#1a73e8; font-weight:600; font-size:0.85rem; }
  </style>
</head>
<body>

  <div class="navbar">
    <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
    <div class="nav-links">
      <a href="/dashboard_admin">Dashboard</a>
      <a href="/live_admin">Dati in tempo reale</a>
      <a href="/statistics_admin" style="text-decoration: underline;">Statistiche</a>
      <a href="/register">Nuovo Utente</a>
      <a href="/logout" style="color:#ffcccc;">Logout</a>
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
        <label><b>Sessione:</b></label>
        <select id="sessSelect" onchange="update()">
          <option value="01" {% if selected_s == '01' %}selected{% endif %}>01</option>
          <option value="02" {% if selected_s == '02' %}selected{% endif %}>02</option>
          <option value="03" {% if selected_s == '03' %}selected{% endif %}>03</option>
        </select>
      </div>

      <span class="pill">Flush stats ~ ogni {{ interval }}s</span>
      <span class="muted">Se non vedi valori, attendi qualche secondo (buffer RAM).</span>
    </div>

    <div class="grid">
      {% for sensor, st in stats.items() %}
        <div class="card">
          <h3 class="title">{{ sensor }}</h3>
          {% if st %}
            <div class="kpi">
              <div><b>Campioni:</b> {{ st.count }}</div>
              <div><b>Media:</b> {{ '%.3f'|format(st.mean) }}</div>
              <div><b>Min:</b> {{ '%.3f'|format(st.min) }}</div>
              <div><b>Max:</b> {{ '%.3f'|format(st.max) }}</div>
              <div><b>Dev Std:</b> {{ '%.3f'|format(st.std) }}</div>
              <div class="muted">Ultimo update: {{ st.updated_at }}</div>

              {% if st.win %}
              <hr style="border:none; border-top:1px solid #eee; margin:12px 0;">
              <div class="muted" style="margin-bottom:6px;">
                  Finestra mobile ultimi {{ st.win.seconds }}s
              </div>
              <div class="kpi">
                  <div><b>Campioni:</b> {{ st.win.count }}</div>
                  <div><b>Media:</b> {{ '%.3f'|format(st.win.mean) }}</div>
                  <div><b>Min:</b> {{ '%.3f'|format(st.win.min) }}</div>
                  <div><b>Max:</b> {{ '%.3f'|format(st.win.max) }}</div>
                  <div><b>Dev Std:</b> {{ '%.3f'|format(st.win.std) }}</div>
                  <div class="muted">Update finestra: {{ st.win.updated_at }}</div>
              </div>
            {% else %}
            <div class="muted" style="margin-top:10px;">Finestra mobile: in attesa dati/flush...</div>
            {% endif %}
            </div>
          {% else %}
            <div class="muted">Nessuna statistica disponibile (ancora in accumulo o nessun dato).</div>
          {% endif %}
        </div>
      {% endfor %}
    </div>
  </div>

<script>
  function update() {
    const u = document.getElementById('userSelect').value;
    const s = document.getElementById('sessSelect').value;
    window.location.href = `/statistics_admin?u=${u}&s=${s}`;
  }
  setTimeout(() => location.reload(), 5000); // refresh pagina ogni 5 sec
</script>

</body>
</html>
'''


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)