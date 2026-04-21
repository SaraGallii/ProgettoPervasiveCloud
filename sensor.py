import csv
import requests
import time
import threading
import os

class FatigueSetClient:
    def __init__(self, server_url):
        self.server_url = server_url

    def send_data(self, file_path, sensor_name, interval):
        parts = file_path.split(os.sep)
        user_id = parts[-3] if len(parts) >= 3 else "unknown"
        session_id = parts[-2] if len(parts) >= 3 else "unknown"
        print(f"[*] Avvio: Utente {user_id} | Sessione {session_id} | Sensore {sensor_name}")
        
        try:
            with open(file_path, mode='r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.pop('timestamp')

                    payload = {
                        "user": user_id,
                        "session": session_id,
                        "sensor": sensor_name,
                        "timestamp": ts,
                        "data": row 
                    }

                    try:
                        requests.post(self.server_url, json=payload, timeout=2)
                    except Exception:
                        pass
                    
                    time.sleep(interval)
        except FileNotFoundError:
            pass 

def avvia_simulazione_globale(base_dir, server_url):
    client = FatigueSetClient(server_url)
    
    sensor_configs = {
        "wrist_acc.csv": {"name": "ACC", "int": 0.031},
        "wrist_bvp.csv": {"name": "BVP", "int": 0.015},
        "wrist_eda.csv": {"name": "EDA", "int": 0.25},
        "wrist_hr.csv":  {"name": "HR",  "int": 1.0},
        "wrist_ibi.csv": {"name": "IBI", "int": 0.8},
        "wrist_skin_temperature.csv": {"name": "TEMP", "int": 0.25}
    }

    threads = []

    for root, dirs, files in os.walk(base_dir):
        for file_name in files:
            if file_name in sensor_configs:
                conf = sensor_configs[file_name]
                full_path = os.path.join(root, file_name)
                
                # Creiamo un thread per ogni file trovato
                t = threading.Thread(
                    target=client.send_data, 
                    args=(full_path, conf["name"], conf["int"]),
                    daemon=True 
                )
                threads.append(t)
                t.start()

    print(f"[INFO] Lanciati {len(threads)} thread di monitoraggio.")
    
    for t in threads: #Aggiorna programma principale
        t.join()

if __name__ == "__main__":
    SERVER_URL = "http://127.0.0.1:5000/data"
    # Percorso assoluto
    # DATASET_PATH = r"C:\Users\galli\OneDrive\Desktop\fatigueset"  

    # Percorso relativo
    DATASET_PATH = r"fatigueset"
    
    avvia_simulazione_globale(DATASET_PATH, SERVER_URL)