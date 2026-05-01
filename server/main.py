import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template_string
from google.cloud import firestore

app = Flask(__name__)

# main.py (server completo + dashboard + API JSON)

# Predisposizione futura: config centralizzata (es. ruoli, soglie, ecc.)
app.config["PROJECT_NAME"] = os.getenv("GOOGLE_CLOUD_PROJECT", "local")

db = firestore.Client()

# ---------------------------
# Helpers
# ---------------------------
def _parse_timestamp_to_ms(ts_raw):
    """
    FatigueSet tipicamente ha timestamp in millisecondi.
    Accettiamo anche secondi (o stringhe), e normalizziamo in ms.
    """
    try:
        ts = float(ts_raw)
    except Exception:
        return None

    # Se > 1e12 probabilmente sono ms (es. 1630411794250)
    # Se ~1e9 probabilmente sono seconds
    if ts > 1e12:
        return int(ts)
    return int(ts * 1000)

def _doc_path(user, session_id, sensor, timestamp_ms_str):
    """
    Struttura Firestore (valida) equivalente a sensors/{user}/{session}/{sensor}/{timestamp}.
    """
    return (
        db.collection("sensors").document(user)
          .collection("sessions").document(session_id)
          .collection("sensors").document(sensor)
          .collection("readings").document(timestamp_ms_str)
    )

@app.route("/health")
def health():
    return "ok", 200

# ===========================
# (PUNTO 2) Ingest API
# ===========================
@app.route("/data", methods=["POST"])
def ingest():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "Missing JSON body"}), 400

    # Payload atteso (come da tuoi client attuali)
    # {
    #   "user": "01", "session": "02", "sensor": "ACC",
    #   "timestamp": "1630411794250", "data": { ... }
    # }
    try:
        user = str(data["user"])
        session_id = str(data["session"])
        sensor = str(data["sensor"])
        ts_raw = data["timestamp"]
        values = data["data"]
    except KeyError as e:
        return jsonify({"status": "error", "message": f"Missing field: {e}"}), 400

    ts_ms = _parse_timestamp_to_ms(ts_raw)
    if ts_ms is None:
        return jsonify({"status": "error", "message": "Invalid timestamp"}), 400

    ts_ms_str = str(ts_ms)
    now = datetime.now(timezone.utc)

    # Documento reading
    ref = _doc_path(user, session_id, sensor, ts_ms_str)
    ref.set({
        "user": user,
        "session": session_id,
        "sensor": sensor,
        "timestamp_ms": ts_ms,
        "values": values,
        "ingested_at": now,
    })

    # Predisposizione futura:
    # - potrai aggiungere statistiche aggregate in documenti summary
    # - potrai inserire controllo soglie/anomalie e salvare in /anomalies
    return jsonify({"status": "ok"}), 200


# ===========================
# Dashboard WEB (legge davvero da Firestore)
# ===========================
DASHBOARD_HTML = """
<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8" />
  <title>FatigueSet – Firestore Live View</title>
  <meta http-equiv="refresh" content="10">
  <style>
    body { font-family: Segoe UI, Arial, sans-serif; background:#f6f7fb; margin:0; padding:20px; }
    .card { background:white; border-radius:12px; padding:16px; box-shadow:0 6px 18px rgba(0,0,0,.06); }
    h1 { margin:0 0 10px; color:#1a73e8; }
    .row { display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }
    input { padding:10px; border:1px solid #d0d7de; border-radius:8px; }
    .muted { color:#666; font-size:.9em; }
    pre { white-space: pre-wrap; word-wrap: break-word; background:#0b1020; color:#e6edf3; padding:12px; border-radius:10px; overflow:auto; }
    a { color:#1a73e8; text-decoration:none; }
  </style>
</head>
<body>
  <div class="card">
    <h1>✅ Dati realmente salvati su Firestore</h1>
    <div class="muted">
      Auto-refresh ogni 10s. Filtri opzionali: user, session, sensor. (Query su Firestore, non cache.)
    </div>

    <form class="row" method="get" action="/">
      <input name="user" placeholder="user (es. 01)" value="{{ user or '' }}">
      <input name="session" placeholder="session (es. 02)" value="{{ session or '' }}">
      <input name="sensor" placeholder="sensor (es. ACC)" value="{{ sensor or '' }}">
      <input name="limit" placeholder="limit (default 50)" value="{{ limit or '' }}">
      <button style="padding:10px 14px; border:0; border-radius:8px; background:#1a73e8; color:white; cursor:pointer;">
        Applica
      </button>
    </form>

    <div class="muted">
      API JSON: <a href="/api/latest">/api/latest</a>
    </div>

    <h3>Ultime letture</h3>
    <pre>{{ lines }}</pre>
  </div>
</body>
</html>
"""

@app.route("/", methods=["GET"])
def dashboard():
    user = request.args.get("user") or None
    session_id = request.args.get("session") or None
    sensor = request.args.get("sensor") or None
    limit = int(request.args.get("limit") or 50)

    # Query efficiente: collection group su "readings"
    # (funziona perché tutti i reading stanno in subcollection "readings")
    q = db.collection_group("readings").order_by("timestamp_ms", direction=firestore.Query.DESCENDING).limit(limit)

    if user:
        q = q.where("user", "==", user)
    if session_id:
        q = q.where("session", "==", session_id)
    if sensor:
        q = q.where("sensor", "==", sensor)

    docs = q.stream()

    lines = []
    for d in docs:
        x = d.to_dict()
        # timestamp visualizzabile
        dt = datetime.fromtimestamp(x["timestamp_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines.append(f'USER:{x["user"]} | SESSION:{x["session"]} | SENSOR:{x["sensor"]} | {dt} | values={x["values"]}')

    return render_template_string(
        DASHBOARD_HTML,
        user=user, session=session_id, sensor=sensor, limit=limit,
        lines="\n".join(lines) if lines else "(Nessun dato trovato)"
    )


# ===========================
# API JSON (predisposizione grafici futuri)
# ===========================
@app.route("/api/latest", methods=["GET"])
def api_latest():
    limit = int(request.args.get("limit") or 50)
    q = db.collection_group("readings").order_by("timestamp_ms", direction=firestore.Query.DESCENDING).limit(limit)
    docs = [d.to_dict() for d in q.stream()]
    return jsonify(docs), 200


@app.route("/health", methods=["GET"])
def health():
    return "ok", 200


if __name__ == "__main__":
    # In locale: python main.py
    app.run(host="0.0.0.0", port=8080, debug=False)