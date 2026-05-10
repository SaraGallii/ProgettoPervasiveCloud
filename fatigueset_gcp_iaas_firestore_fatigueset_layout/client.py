import csv
import requests
import time
import threading
import os

# In GCP imposta SERVER_URL con l'IP pubblico/hostname della VM:
#   export SERVER_URL=http://X.X.X.X/data
SERVER_URL = os.environ.get('SERVER_URL', 'http://127.0.0.1:5000/data')
DATASET_PATH = r"fatigueset"  # hardcoded

SENSOR_CONFIGS = {
    "wrist_acc.csv": {"name": "ACC", "int": 0.031},
    "wrist_bvp.csv": {"name": "BVP", "int": 0.015},
    "wrist_eda.csv": {"name": "EDA", "int": 0.25},
    "wrist_hr.csv": {"name": "HR", "int": 1.0},
    "wrist_ibi.csv": {"name": "IBI", "int": 0.8},
    "wrist_skin_temperature.csv": {"name": "TEMP", "int": 0.25},
}


def normalize_user_id(user_raw: str) -> str:
    # Converte P01 -> 01
    if not user_raw:
        return "unknown"
    u = str(user_raw).strip()
    if len(u) >= 2 and (u[0] in ('P', 'p')) and u[1:].isdigit():
        return u[1:]
    if u.isdigit():
        return u
    digits = ''.join([c for c in u if c.isdigit()])
    return digits if digits else u


class FatigueSetClient:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.started_sessions = set()

    def send_data(self, file_path: str, sensor_name: str, interval: float):
        parts = file_path.split(os.sep)
        # struttura .../P01/01/wrist_*.csv
        user_part = parts[-3] if len(parts) >= 3 else "unknown"
        sess_part = parts[-2] if len(parts) >= 2 else "unknown"
        user_id = normalize_user_id(user_part)  # => 01
        session_id = sess_part

        print(f"[*] AVVIO SENSOR: {sensor_name} | Utente: {user_id} | Sessione: {session_id} | File: {os.path.basename(file_path)}")

        try:
            with open(file_path, mode='r', newline='', encoding='utf-8') as f:
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
                    try:
                        requests.post(self.server_url, json=payload, timeout=2)
                    except Exception:
                        pass
                    time.sleep(interval)
        except FileNotFoundError:
            print(f"[!] File non trovato: {file_path}")
        except Exception as e:
            print(f"[!] Errore invio {sensor_name}: {e}")

    def monitora_directory(self, base_dir: str):
        print(f"--- Client IoT Monitor in ascolto su: {base_dir} ---")
        print(f"--- Inviando verso: {self.server_url} ---")
        while True:
            for root, dirs, files in os.walk(base_dir):
                if any(f in SENSOR_CONFIGS for f in files):
                    if root not in self.started_sessions:
                        print(f"
[NEW] Rilevata nuova sorgente dati: {root}")
                        self.started_sessions.add(root)
                        for file_name in files:
                            if file_name in SENSOR_CONFIGS:
                                conf = SENSOR_CONFIGS[file_name]
                                full_path = os.path.join(root, file_name)
                                t = threading.Thread(
                                    target=self.send_data,
                                    args=(full_path, conf["name"], conf["int"]),
                                    daemon=True
                                )
                                t.start()
            time.sleep(5)


if __name__ == "__main__":
    client = FatigueSetClient(SERVER_URL)
    try:
        client.monitora_directory(DATASET_PATH)
    except KeyboardInterrupt:
        print("
[STOP] Monitoraggio interrotto dall'utente.")
