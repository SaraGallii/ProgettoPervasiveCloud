import csv
import requests
import time
import threading
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

# Client per inviare i dati dei sensori al server Flask
class FatigueSetClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.started_files = set()

    # Legge il CSV riga per riga e invia i dati via HTTP POST
    def send_data(self, file_path, sensor_name, interval):
        """Legge il CSV riga per riga e invia i dati via HTTP POST."""
        parts = file_path.split(os.sep)
        user_id = parts[-3] if len(parts) >= 3 else "unknown_user"
        session_id = parts[-2] if len(parts) >= 2 else "unknown_session"

        logging.info(f"Avvio invio dati: user={user_id}, session={session_id}, sensor={sensor_name}")

        try:
            with open(file_path, mode='r', newline='') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    timestamp = row.pop('timestamp', None)
                    if timestamp is None or timestamp == '':
                        timestamp = time.time()

                    payload = {
                        "user": user_id,
                        "session": session_id,
                        "sensor": sensor_name,
                        "timestamp": timestamp,
                        "data": row
                    }

                    for attempt in range(1, 4):
                        try:
                            response = requests.post(self.server_url, json=payload, timeout=5)
                            if response.status_code != 200:
                                logging.warning(
                                    f"Server response {response.status_code} for {file_path}: {response.text}"
                                )
                            break
                        except requests.exceptions.RequestException as err:
                            logging.error(
                                f"Errore connessione al server (tentativo {attempt}/3) per {file_path}: {err}"
                            )
                            if attempt == 3:
                                return
                            time.sleep(2)

                    time.sleep(interval)
        except FileNotFoundError:
            logging.error(f"File non trovato: {file_path}")
        except csv.Error as e:
            logging.exception(f"Errore CSV nel file {file_path}: {e}")
        except Exception as e:
            logging.exception(f"Errore durante l'invio del file {file_path}: {e}")
    
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

        while True:
            for root, dirs, files in os.walk(base_dir):
                for file_name in files:
                    if file_name in sensor_configs:
                        full_path = os.path.join(root, file_name)
                        if full_path in self.started_files:
                            continue
                        self.started_files.add(full_path)
                        conf = sensor_configs[file_name]
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