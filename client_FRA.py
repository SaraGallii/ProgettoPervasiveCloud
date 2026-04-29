import csv
import requests
import time
import threading
import os
import sys

# Client per inviare i dati dei sensori al server Flask
class FatigueSetClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.started_sessions = set()

    # Legge il CSV riga per riga e invia i dati via HTTP POST
    def send_data(self, file_path, sensor_name, interval):
        """Legge il CSV riga per riga e invia i dati via HTTP POST."""
        parts = file_path.split(os.sep)
        user_id = parts[-3] if len(parts) >= 3 else "unknown_user"
        session_id = parts[-2] if len(parts) >= 2 else "unknown_session"
        
        # Log di avvio del sensore
        try:
            with open(file_path, mode='r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.pop('timestamp', time.time()) 

                    payload = {
                        "user": user_id,
                        "session": session_id,
                        "sensor": sensor_name,
                        "timestamp": ts,
                        "data": row 
                    }

                    # Invio al server Flask
                    try:
                        requests.post(self.server_url, json=payload, timeout=2)
                    except Exception:
                        pass
                    
                    time.sleep(interval)
        except Exception as e:
            print(f"Errore durante l'invio: {e}")
    
    # Scansiona la cartella cercando file CSV e lancia thread per ogni sensore
    def monitora_directory(self, base_dir):
        """Scansiona la cartella cercando file CSV e lancia thread per ogni sensore."""
        sensor_configs = {
            "wrist_acc.csv": {"name": "ACC", "int": 0.031},
            "wrist_bvp.csv": {"name": "BVP", "int": 0.015},
            "wrist_eda.csv": {"name": "EDA", "int": 0.25},
            "wrist_hr.csv":  {"name": "HR",  "int": 1.0},
            "wrist_ibi.csv": {"name": "IBI", "int": 0.8},
            "wrist_skin_temperature.csv": {"name": "TEMP", "int": 0.25}
        }

        # Loop infinito per monitorare la directory: ogni 5 secondi scansiona di nuovo per nuovi file
        while True:
            for root, dirs, files in os.walk(base_dir):
                if any(f in sensor_configs for f in files):
                    if root not in self.started_sessions:
                        self.started_sessions.add(root)
                        for file_name in files:
                            if file_name in sensor_configs:
                                conf = sensor_configs[file_name]
                                full_path = os.path.join(root, file_name)
                                t = threading.Thread(
                                    target=self.send_data, 
                                    args=(full_path, conf["name"], conf["int"]),
                                    daemon=True 
                                )
                                t.start()
            time.sleep(5)

# Punto di ingresso del programma
if __name__ == "__main__":
    SERVER_URL = "http://127.0.0.1:5000/data"
    
    # Se viene passato un argomento da linea di comando, usalo come percorso del dataset, altrimenti usa il default
    if len(sys.argv) > 1:
        DATASET_PATH = sys.argv[1]
    else:
        DATASET_PATH = r"fatigueset" 

    # Avvia il client manager per monitorare la directory e inviare i dati al server
    client_manager = FatigueSetClient(SERVER_URL)
    
    # Avvio del monitoraggio della directory con gestione dell'interruzione da tastiera
    try:
        client_manager.monitora_directory(DATASET_PATH)
    except KeyboardInterrupt:
        print("\n[STOP] Monitoraggio interrotto.")