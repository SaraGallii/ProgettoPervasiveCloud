import sys
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from pymongo import MongoClient, errors

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
app = Flask(__name__)

# --- CONFIGURAZIONE NOSQL (MongoDB) ---
# Si assume un'istanza MongoDB attiva localmente
try:
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=5000)
    client.server_info()
except errors.ServerSelectionTimeoutError as e:
    logging.error(f"Impossibile connettersi a MongoDB: {e}")
    sys.exit(1)

db = client['EmpaticaE4_NoSQL']
# Collezione unica per i dati dei sensori
collection = db['sensor_data']

@app.route('/data', methods=['POST'])
def receive_data():
    if not request.is_json:
        return jsonify({"status": "error", "message": "Richiesta JSON non valida"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    user_id = data.get('user')
    session_id = data.get('session')
    sensor_type = data.get('sensor')
    timestamp_raw = data.get('timestamp')
    sensor_values = data.get('data')

    missing = [key for key in ('user', 'session', 'sensor', 'timestamp', 'data') if data.get(key) is None]
    if missing:
        return jsonify({
            "status": "error",
            "message": f"Campi mancanti: {', '.join(missing)}"
        }), 400

    if not isinstance(sensor_values, dict):
        return jsonify({"status": "error", "message": "Formato misure non valido"}), 400

    document = {
        "user": user_id,
        "step": session_id,
        "sensor": sensor_type,
        "timestamp_device": timestamp_raw,
        "received_at": datetime.utcnow().isoformat() + 'Z',
        "measurements": sensor_values
    }

    try:
        result = collection.insert_one(document)
        logging.info(
            f"[NoSQL] Salvato documento per Utente {user_id}, Passo {session_id}, Sensore {sensor_type}"
        )
        return jsonify({"status": "success", "id_documento": str(result.inserted_id)}), 200
    except Exception as e:
        logging.exception(f"Errore durante il salvataggio NoSQL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Rotta di test per visualizzare gli ultimi inserimenti
@app.route('/test_nosql', methods=['GET'])
def get_recent_data():
    recent_docs = list(collection.find().sort("_id", -1).limit(10))
    for doc in recent_docs:
        doc['_id'] = str(doc['_id'])
    return jsonify(recent_docs)

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    logging.info("--- Server NoSQL Ricezione Dati Avviato ---")
    app.run(host='0.0.0.0', port=5000, debug=False)