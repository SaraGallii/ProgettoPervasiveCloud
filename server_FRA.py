from flask import Flask, request, jsonify
from datetime import datetime
from pymongo import MongoClient
import json

app = Flask(__name__)

# --- CONFIGURAZIONE NOSQL (MongoDB) ---
# Si assume un'istanza MongoDB attiva localmente
client = MongoClient('mongodb://localhost:27017/')
db = client['EmpaticaE4_NoSQL']
# Collezione unica per i dati dei sensori
collection = db['sensor_data']

@app.route('/data', methods=['POST'])
def receive_data():
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data received"}), 400

    try:
        # Estrazione metadati dal payload inviato dal client
        user_id = data.get('user')      # Es: "01", "02"
        session_id = data.get('session') # Es: "01", "02", "03" (i tuoi "passi")
        sensor_type = data.get('sensor') # Es: "ACC", "BVP", "EDA", "HR", "IBI", "TEMP"
        timestamp_raw = data.get('timestamp')
        sensor_values = data.get('data') # I dati estratti dal CSV[cite: 3]

        # Creazione del documento NoSQL
        document = {
            "user": user_id,
            "step": session_id,
            "sensor": sensor_type,
            "timestamp_device": timestamp_raw,
            "received_at": datetime.now().isoformat(),
            "measurements": sensor_values
        }

        # Salvataggio nel repository locale NoSQL
        result = collection.insert_one(document)

        print(f"[NoSQL] Salvato documento per Utente {user_id}, Passo {session_id}, Sensore {sensor_type}")
        
        return jsonify({
            "status": "success", 
            "id_documento": str(result.inserted_id)
        }), 200

    except Exception as e:
        print(f"Errore durante il salvataggio NoSQL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

# Rotta di test per visualizzare gli ultimi inserimenti
@app.route('/test_nosql', methods=['GET'])
def get_recent_data():
    recent_docs = list(collection.find().sort("_id", -1).limit(10))
    for doc in recent_docs:
        doc['_id'] = str(doc['_id'])
    return jsonify(recent_docs)

if __name__ == '__main__':
    print("--- Server NoSQL Ricezione Dati Avviato ---")
    # Il server gira sulla porta 5000 come richiesto dai client[cite: 3, 4]
    app.run(port=5000, debug=False)