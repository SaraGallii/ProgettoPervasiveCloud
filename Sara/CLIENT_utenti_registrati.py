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


def scan_dataset(base_dir, sensor_configs):
    """
    Ritorna: dict user_id -> list of (full_path, sensor_name, interval)
    assumendo struttura: base_dir/<user_id>/<session_id>/<file>
    """
    user_files = {}

    for root, dirs, files in os.walk(base_dir):
        for file_name in files:
            if file_name in sensor_configs:
                conf = sensor_configs[file_name]
                full_path = os.path.join(root, file_name)

                parts = full_path.split(os.sep)
                user_id = parts[-3] if len(parts) >= 3 else "unknown"

                user_files.setdefault(user_id, []).append(
                    (full_path, conf["name"], conf["int"])
                )

    return user_files


def fetch_registered_ids(server_base):
    """
    Chiama il server e recupera gli utenti registrati.
    server_base: es. http://34.154.16.97:5000
    """
    try:
        r = requests.get(f"{server_base}/api/registered_users", timeout=3)
        data = r.json()
        ids = data.get("registered_ids", [])
        return set(str(x).strip() for x in ids if str(x).strip())
    except Exception:
        return set()


def avvia_simulazione_globale(base_dir, server_url, poll_seconds=5):
    client = FatigueSetClient(server_url)

    sensor_configs = {
        "wrist_acc.csv": {"name": "ACC", "int": 0.031},
        "wrist_bvp.csv": {"name": "BVP", "int": 0.015},
        "wrist_eda.csv": {"name": "EDA", "int": 0.25},
        "wrist_hr.csv":  {"name": "HR",  "int": 1.0},
        "wrist_ibi.csv": {"name": "IBI", "int": 0.8},
        "wrist_skin_temperature.csv": {"name": "TEMP", "int": 0.25}
    }

    # 1) Indicizza dataset per utente
    user_files = scan_dataset(base_dir, sensor_configs)
    print(f"[INFO] Dataset indicizzato. Utenti trovati nel dataset: {len(user_files)}")

    # 2) Calcola server_base (rimuove /data)
    #    es: http://34.154.16.97:5000/data -> http://34.154.16.97:5000
    server_base = server_url.rsplit("/", 1)[0]

    started_users = set()
    threads = []

    print("[INFO] In attesa utenti registrati... (polling)")

    while True:
        registered = fetch_registered_ids(server_base)

        # Avvia SOLO gli utenti registrati (e non ancora avviati)
        for user_id in registered:
            if user_id in started_users:
                continue

            if user_id not in user_files:
                # Utente registrato ma non presente nel dataset locale
                continue

            print(f"[INFO] Utente {user_id} registrato: avvio invio dati.")
            started_users.add(user_id)

            # Avvia i thread per tutti i sensori di quell'utente
            for (full_path, sensor_name, interval) in user_files[user_id]:
                t = threading.Thread(
                    target=client.send_data,
                    args=(full_path, sensor_name, interval),
                    daemon=True
                )
                threads.append(t)
                t.start()

        # Se vuoi chiudere quando hai avviato tutti gli utenti presenti nel dataset:
        # if started_users.issuperset(set(user_files.keys())):
        #     break

        time.sleep(poll_seconds)

    # (Di solito non ci arrivi se lasci il while True)
    for t in threads:
        t.join()

if __name__ == "__main__":
    SERVER_URL = "http://34.154.16.97:5000/data"
    DATASET_PATH = r"fatigueset"

    avvia_simulazione_globale(DATASET_PATH, SERVER_URL, poll_seconds=5)