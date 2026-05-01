import csv
import os
import sys
import time
import threading
import requests

# === Config ===
SERVER_URL = "https://<YOUR_PROJECT_ID>.ey.r.appspot.com/data"

SENSOR_CONFIGS = {
    # filename : (sensor_name, interval_seconds)
    "wrist_acc.csv": ("ACC", 1/32),   # 32 Hz  [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
    "wrist_bvp.csv": ("BVP", 1/64),   # 64 Hz  [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
    "wrist_eda.csv": ("EDA", 1/4),    # 4 Hz   [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
    "wrist_hr.csv":  ("HR", 1.0),     # 1 Hz   [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
    "wrist_ibi.csv": ("IBI", 0.8),    # per evento; simulazione (come tuo script) [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
    "wrist_skin_temperature.csv": ("TEMP", 1/4) # 4 Hz [2](https://coopservice-my.sharepoint.com/personal/francesco_romano_coopservice_it/Documents/File%20di%20Microsoft%20Copilot%20Chat/to_deploy.txt)
}

SCAN_EVERY_SECONDS = 5


class FatigueSetClientManager:
    """
    Predisposto per futuro:
    - più sessioni contemporanee
    - più utenti contemporanei
    - inserimento dinamico di nuove cartelle durante l'esecuzione
    """
    def __init__(self, server_url: str):
        self.server_url = server_url
        self.started_roots = set()  # evita doppi avvii

    def _infer_user_session(self, file_path: str):
        parts = file_path.split(os.sep)
        # Atteso: .../{user}/{session}/wrist_xxx.csv
        user = parts[-3] if len(parts) >= 3 else "unknown_user"
        session = parts[-2] if len(parts) >= 2 else "unknown_session"
        return user, session

    def _send_file_stream(self, file_path: str, sensor_name: str, interval: float):
        user, session = self._infer_user_session(file_path)
        print(f"[START] user={user} session={session} sensor={sensor_name} file={file_path}")

        try:
            with open(file_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.pop("timestamp", None)
                    if ts is None:
                        # se manca timestamp, generiamo uno "now" in ms
                        ts = int(time.time() * 1000)

                    payload = {
                        "user": user,
                        "session": session,
                        "sensor": sensor_name,
                        "timestamp": ts,
                        "data": row
                    }

                    try:
                        requests.post(self.server_url, json=payload, timeout=3)
                    except Exception:
                        # se server temporaneamente non disponibile, ignoriamo
                        pass

                    time.sleep(interval)

        except FileNotFoundError:
            print(f"[ERR] file non trovato: {file_path}")
        except Exception as e:
            print(f"[ERR] errore stream {file_path}: {e}")

    def monitor_directory(self, base_dir: str):
        print(f"[MONITOR] in ascolto su: {base_dir}")

        while True:
            for root, _, files in os.walk(base_dir):
                # Se la cartella contiene almeno uno dei file sensore
                if any(fn in SENSOR_CONFIGS for fn in files):
                    if root not in self.started_roots:
                        self.started_roots.add(root)
                        print(f"\n[NEW SOURCE] {root}")

                        for fn in files:
                            if fn in SENSOR_CONFIGS:
                                sensor_name, interval = SENSOR_CONFIGS[fn]
                                full_path = os.path.join(root, fn)
                                t = threading.Thread(
                                    target=self._send_file_stream,
                                    args=(full_path, sensor_name, interval),
                                    daemon=True
                                )
                                t.start()

            time.sleep(SCAN_EVERY_SECONDS)


if __name__ == "__main__":
    # Usage:
    # python client_iot.py /path/to/fatigueset
    # se non passi argomenti, usa ./fatigueset
    base = sys.argv[1] if len(sys.argv) > 1 else "fatigueset"
    mgr = FatigueSetClientManager(SERVER_URL)
    try:
        mgr.monitor_directory(base)
    except KeyboardInterrupt:
        print("\n[STOP] interrotto dall'utente.")