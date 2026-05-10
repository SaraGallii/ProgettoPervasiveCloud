from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from datetime import datetime
import sqlite3
import json
import math
import statistics

APP_SECRET_KEY = "p4ssw0rd"  # solo locale/demo
DB_PATH = 'Empatica_E4_wristband.db'
DB_STATS_PATH = 'statistiche_settimanali.db'

SENSORS = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]

app = Flask(__name__)
app.secret_key = APP_SECRET_KEY


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS dati_sensori (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            session TEXT,
            sensor TEXT,
            timestamp TEXT,
            valori TEXT,
            data_ricezione DATETIME
        )
    ''')

    cur.execute('''
        CREATE TABLE IF NOT EXISTS utenti (
            username TEXT PRIMARY KEY,
            password TEXT
        )
    ''')

    # utenti di esempio
    cur.execute("INSERT OR IGNORE INTO utenti (username, password) VALUES ('admin', '0123')")
    cur.execute("INSERT OR IGNORE INTO utenti (username, password) VALUES ('user1', '1111')")
    cur.execute("INSERT OR IGNORE INTO utenti (username, password) VALUES ('user2', '2222')")

    conn.commit()
    conn.close()


def init_stats_db():
    conn = sqlite3.connect(DB_STATS_PATH)
    cur = conn.cursor()

    cur.execute('''
        CREATE TABLE IF NOT EXISTS metriche_settimanali (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            session TEXT,
            sensor TEXT,
            mean REAL,
            median REAL,
            mode REAL,
            max REAL,
            min REAL,
            samples INTEGER,
            data_calcolo DATETIME
        )
    ''')

    conn.commit()
    conn.close()


init_db()
init_stats_db()


def normalize_user_id(user_raw: str) -> str:
    """Converte P01 -> 01, p12 -> 12, altrimenti restituisce com'è."""
    if not user_raw:
        return "unknown"
    u = str(user_raw).strip()
    if len(u) >= 2 and (u[0] in ('P', 'p')) and u[1:].isdigit():
        return u[1:]
    if u.isdigit():
        return u
    digits = ''.join([c for c in u if c.isdigit()])
    return digits if digits else u


def parse_timestamp_to_hms(raw_ts) -> str:
    """Gestisce timestamp in ms o s. Restituisce HH:MM:SS."""
    try:
        ts = float(raw_ts)
    except Exception:
        return str(raw_ts)
    # euristica: se è troppo grande, sono ms
    if ts > 1e11:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts).strftime('%H:%M:%S')


def login_required(fn):
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return fn(*args, **kwargs)

    return wrapper


@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    try:
        user_id = normalize_user_id(data.get('user'))
        session_id = str(data.get('session') or "unknown")
        sensor = str(data.get('sensor') or "unknown")
        raw_ts = data.get('timestamp')
        ts_formattato = parse_timestamp_to_hms(raw_ts)
        valori = json.dumps(data.get('data') or {})

        conn = sqlite3.connect(DB_PATH, timeout=20)
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO dati_sensori (user, session, sensor, timestamp, valori, data_ricezione)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            user_id,
            session_id,
            sensor,
            ts_formattato,
            valori,
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
        conn.commit()
        conn.close()
        return jsonify({"status": "success"}), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/')
def index():
    # Home con bottoni (come richiesto)
    return render_template('index.html', logged_in=('user' in session), username=session.get('user'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT username FROM utenti WHERE username=? AND password=?", (username, password))
        row = cur.fetchone()
        conn.close()
        if row:
            session['user'] = username
            return redirect(url_for('index'))
        error = "Credenziali non valide"
    return render_template('login.html', error=error)


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))


@app.route('/dashboard')
@login_required
def dashboard():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # lista utenti reali (dati_sensori)
    cur.execute("SELECT DISTINCT user FROM dati_sensori ORDER BY user")
    users = [r[0] for r in cur.fetchall()]

    selected_user = request.args.get('u') or (users[0] if users else None)
    selected_sess = request.args.get('s') or '01'
    selected_sensor = request.args.get('sensor') or 'ACC'
    if selected_sensor not in SENSORS:
        selected_sensor = 'ACC'

    sessions = []
    if selected_user:
        cur.execute("SELECT DISTINCT session FROM dati_sensori WHERE user=? ORDER BY session", (selected_user,))
        sessions = [r[0] for r in cur.fetchall()]
        if sessions and selected_sess not in sessions:
            selected_sess = sessions[0]

    chart_labels, chart_values = [], []
    if selected_user:
        cur.execute('''
            SELECT timestamp, valori
            FROM dati_sensori
            WHERE user=? AND session=? AND sensor=?
            ORDER BY id DESC
            LIMIT 50
        ''', (selected_user, selected_sess, selected_sensor))
        rows = cur.fetchall()[::-1]
        chart_labels = [r[0] for r in rows]
        for _, valori_raw in rows:
            try:
                val = json.loads(valori_raw)
            except Exception:
                try:
                    val = json.loads(valori_raw.replace("'", '"'))
                except Exception:
                    val = {}

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

    conn.close()

    chart_data = {
        "labels": chart_labels,
        "values": chart_values,
        "sensor": selected_sensor,
        "user": selected_user,
        "session": selected_sess
    }

    return render_template(
        'dashboard.html',
        users=users,
        sessions=sessions if sessions else ['01', '02', '03'],
        sensors=SENSORS,
        selected_user=selected_user,
        selected_sess=selected_sess,
        selected_sensor=selected_sensor,
        chart_data=chart_data
    )


@app.route('/statistics')
@login_required
def statistics_page():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT DISTINCT user FROM dati_sensori ORDER BY user")
    users = [r[0] for r in cur.fetchall()]
    selected_user = request.args.get('u') or (users[0] if users else None)

    session_stats = {}
    sessions = []

    if selected_user:
        cur.execute("SELECT DISTINCT session FROM dati_sensori WHERE user=? ORDER BY session", (selected_user,))
        sessions = [r[0] for r in cur.fetchall()]

        conn_stats = sqlite3.connect(DB_STATS_PATH)
        cur_stats = conn_stats.cursor()

        for sess in sessions:
            session_stats[sess] = {}
            for sensor in SENSORS:
                cur.execute('''
                    SELECT valori FROM dati_sensori
                    WHERE user=? AND session=? AND sensor=?
                      AND data_ricezione >= date('now','-7 days')
                ''', (selected_user, sess, sensor))
                rows = cur.fetchall()
                dataset = []

                for (valori_raw,) in rows:
                    try:
                        val = json.loads(valori_raw)
                    except Exception:
                        try:
                            val = json.loads(valori_raw.replace("'", '"'))
                        except Exception:
                            continue

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
                    try:
                        m_mean = round(statistics.mean(dataset), 2)
                        m_median = round(statistics.median(dataset), 2)
                        try:
                            m_mode = round(statistics.mode(dataset), 2)
                        except statistics.StatisticsError:
                            m_mode = None
                        m_max = max(dataset)
                        m_min = min(dataset)
                        m_samples = len(dataset)

                        session_stats[sess][sensor] = {
                            "mean": m_mean,
                            "median": m_median,
                            "mode": m_mode if m_mode is not None else "N/A",
                            "max": m_max,
                            "min": m_min,
                            "samples": m_samples
                        }

                        # salva sempre (calcola+salva ad ogni visita)
                        cur_stats.execute(
                            "DELETE FROM metriche_settimanali WHERE user=? AND session=? AND sensor=?",
                            (selected_user, sess, sensor)
                        )
                        cur_stats.execute('''
                            INSERT INTO metriche_settimanali
                            (user, session, sensor, mean, median, mode, max, min, samples, data_calcolo)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            selected_user, sess, sensor,
                            m_mean, m_median,
                            (m_mode if m_mode is not None else None),
                            m_max, m_min,
                            m_samples,
                            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ))
                    except Exception:
                        session_stats[sess][sensor] = {
                            "mean": "Err",
                            "median": "Err",
                            "mode": "Err",
                            "max": "Err",
                            "min": "Err",
                            "samples": len(dataset)
                        }
                else:
                    session_stats[sess][sensor] = {
                        "mean": "N/A",
                        "median": "N/A",
                        "mode": "N/A",
                        "max": "N/A",
                        "min": "N/A",
                        "samples": 0
                    }

        conn_stats.commit()
        conn_stats.close()

    conn.close()

    return render_template(
        'statistics.html',
        users=users,
        selected_user=selected_user,
        sessions=sessions,
        sensors=SENSORS,
        all_stats=session_stats
    )


if __name__ == '__main__':
    app.run(port=5000, debug=False)
