from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from datetime import datetime, timedelta, timezone
import math
import statistics
import os

from google.cloud import firestore
from google.cloud.firestore_v1 import ArrayUnion

APP_SECRET_KEY = os.environ.get('APP_SECRET_KEY', 'p4ssw0rd')

# Firestore collections (equivalenti ai 2 DB locali)
COL_DATA = 'dati_sensori'
COL_STATS = 'metriche_settimanali'
COL_USERS = 'utenti'
COL_INDEX = 'index_users'  # indice per dropdown utenti/sessioni

SENSORS = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY

# Usa Application Default Credentials (ADC). Su Compute Engine, le credenziali
# arrivano dal Service Account associato alla VM.
db = firestore.Client()


def normalize_user_id(user_raw: str) -> str:
    'Converte P01 -> 01, p12 -> 12, altrimenti restituisce com'è.'
    if not user_raw:
        return 'unknown'
    u = str(user_raw).strip()
    if len(u) >= 2 and (u[0] in ('P', 'p')) and u[1:].isdigit():
        return u[1:]
    if u.isdigit():
        return u
    digits = ''.join([c for c in u if c.isdigit()])
    return digits if digits else u


def parse_timestamp(raw_ts):
    'Restituisce (HH:MM:SS, ts_ms). Accetta secondi o millisecondi.'
    try:
        ts = float(raw_ts)
    except Exception:
        now = datetime.now(tz=timezone.utc)
        return now.strftime('%H:%M:%S'), int(now.timestamp() * 1000)

    # euristica: se è troppo grande => ms
    if ts > 1e11:
        ts_ms = int(ts)
        ts_sec = ts / 1000.0
    else:
        ts_sec = ts
        ts_ms = int(ts * 1000)

    dt = datetime.fromtimestamp(ts_sec, tz=timezone.utc)
    return dt.strftime('%H:%M:%S'), ts_ms


def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)

    return wrapper


def ensure_demo_users():
    'Crea utenti demo se mancano (admin/user1/user2).'
    defaults = {
        'admin': '0123',
        'user1': '1111',
        'user2': '2222'
    }
    for username, pwd in defaults.items():
        ref = db.collection(COL_USERS).document(username)
        if not ref.get().exists:
            ref.set({
                'username': username,
                'password': pwd,
                'created_at': firestore.SERVER_TIMESTAMP
            })


ensure_demo_users()


@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({'status': 'error', 'message': 'No data'}), 400

    try:
        user_id = normalize_user_id(data.get('user'))
        session_id = str(data.get('session') or 'unknown')
        sensor = str(data.get('sensor') or 'unknown')
        raw_ts = data.get('timestamp')
        ts_hms, ts_ms = parse_timestamp(raw_ts)
        payload_data = data.get('data') or {}

        # Dato raw
        db.collection(COL_DATA).add({
            'user': user_id,
            'session': session_id,
            'sensor': sensor,
            'timestamp_hms': ts_hms,
            'ts_ms': ts_ms,
            'data': payload_data,
            'received_at': firestore.SERVER_TIMESTAMP
        })

        # Indice utenti/sessioni (per dropdown)
        idx_ref = db.collection(COL_INDEX).document(user_id)
        idx_ref.set({
            'user': user_id,
            'sessions': ArrayUnion([session_id]),
            'updated_at': firestore.SERVER_TIMESTAMP
        }, merge=True)

        return jsonify({'status': 'success'}), 200
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/')
def index():
    return render_template('index.html', logged_in=('user' in session), username=session.get('user'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')

        doc = db.collection(COL_USERS).document(username).get()
        if doc.exists and doc.to_dict().get('password') == password:
            session['user'] = username
            return redirect(url_for('index'))
        error = 'Credenziali non valide'

    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    users = sorted([d.id for d in db.collection(COL_INDEX).stream()])
    selected_user = request.args.get('u') or (users[0] if users else None)

    sessions = ['01', '02', '03']
    if selected_user:
        doc = db.collection(COL_INDEX).document(selected_user).get()
        if doc.exists:
            s = doc.to_dict().get('sessions') or []
            sessions = sorted([str(x) for x in s]) if s else sessions

    selected_sess = request.args.get('s') or (sessions[0] if sessions else '01')
    selected_sensor = request.args.get('sensor') or 'ACC'
    if selected_sensor not in SENSORS:
        selected_sensor = 'ACC'

    chart_labels, chart_values = [], []

    if selected_user:
        q = (db.collection(COL_DATA)
             .where('user', '==', selected_user)
             .where('session', '==', selected_sess)
             .where('sensor', '==', selected_sensor)
             .order_by('ts_ms', direction=firestore.Query.DESCENDING)
             .limit(50))

        rows = list(q.stream())
        rows.reverse()

        for snap in rows:
            r = snap.to_dict()
            chart_labels.append(r.get('timestamp_hms'))
            val = r.get('data') or {}

            if selected_sensor == 'ACC':
                ax = float(val.get('ax', val.get('x', 0)) or 0)
                ay = float(val.get('ay', val.get('y', 0)) or 0)
                az = float(val.get('az', val.get('z', 0)) or 0)
                mag = math.sqrt(ax**2 + ay**2 + az**2)
                chart_values.append(round(mag, 3))
            else:
                v = None
                for vv in val.values():
                    try:
                        v = float(vv)
                        break
                    except Exception:
                        continue
                chart_values.append(v if v is not None else 0)

    chart_data = {
        'labels': chart_labels,
        'values': chart_values,
        'sensor': selected_sensor,
        'user': selected_user,
        'session': selected_sess
    }

    return render_template(
        'dashboard.html',
        users=users,
        sessions=sessions,
        sensors=SENSORS,
        selected_user=selected_user,
        selected_sess=selected_sess,
        selected_sensor=selected_sensor,
        chart_data=chart_data
    )


@app.route('/statistics')
@login_required
def statistics_page():
    users = sorted([d.id for d in db.collection(COL_INDEX).stream()])
    selected_user = request.args.get('u') or (users[0] if users else None)

    all_stats = {}
    sessions = []

    if selected_user:
        doc = db.collection(COL_INDEX).document(selected_user).get()
        if doc.exists:
            sessions = sorted([str(x) for x in (doc.to_dict().get('sessions') or [])])

        since = datetime.now(tz=timezone.utc) - timedelta(days=7)

        for sess in sessions:
            all_stats[sess] = {}
            for sensor in SENSORS:
                q = (db.collection(COL_DATA)
                     .where('user', '==', selected_user)
                     .where('session', '==', sess)
                     .where('sensor', '==', sensor)
                     .where('received_at', '>=', since))

                dataset = []
                for snap in q.stream():
                    r = snap.to_dict()
                    val = r.get('data') or {}
                    try:
                        if sensor == 'ACC':
                            ax = float(val.get('ax', val.get('x', 0)) or 0)
                            ay = float(val.get('ay', val.get('y', 0)) or 0)
                            az = float(val.get('az', val.get('z', 0)) or 0)
                            dataset.append(round(math.sqrt(ax**2 + ay**2 + az**2), 2))
                        else:
                            v = None
                            for vv in val.values():
                                try:
                                    v = float(vv)
                                    break
                                except Exception:
                                    continue
                            if v is not None:
                                dataset.append(round(v, 2))
                    except Exception:
                        continue

                if dataset:
                    m_mean = round(statistics.mean(dataset), 2)
                    m_median = round(statistics.median(dataset), 2)
                    try:
                        m_mode_val = round(statistics.mode(dataset), 2)
                        m_mode = m_mode_val
                    except statistics.StatisticsError:
                        m_mode = None
                    m_max = max(dataset)
                    m_min = min(dataset)
                    m_samples = len(dataset)

                    stats_obj = {
                        'mean': m_mean,
                        'median': m_median,
                        'mode': (m_mode if m_mode is not None else 'N/A'),
                        'max': m_max,
                        'min': m_min,
                        'samples': m_samples
                    }

                    # Salva sempre (calcola+salva ad ogni visita)
                    doc_id = f"{selected_user}_{sess}_{sensor}"
                    db.collection(COL_STATS).document(doc_id).set({
                        'user': selected_user,
                        'session': sess,
                        'sensor': sensor,
                        'mean': m_mean,
                        'median': m_median,
                        'mode': m_mode,
                        'max': m_max,
                        'min': m_min,
                        'samples': m_samples,
                        'computed_at': firestore.SERVER_TIMESTAMP
                    }, merge=True)

                    all_stats[sess][sensor] = stats_obj
                else:
                    all_stats[sess][sensor] = {
                        'mean': 'N/A',
                        'median': 'N/A',
                        'mode': 'N/A',
                        'max': 'N/A',
                        'min': 'N/A',
                        'samples': 0
                    }

    return render_template(
        'statistics.html',
        users=users,
        selected_user=selected_user,
        sessions=sessions,
        sensors=SENSORS,
        all_stats=all_stats
    )


if __name__ == '__main__':
    # debug diretto su VM (non produzione)
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', '5000')), debug=False)
