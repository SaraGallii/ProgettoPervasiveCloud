import csv
import requests
import time
import threading
import os
import sys

class FatigueSetClient:
    def __init__(self, server_url):
        self.server_url = server_url
        self.started_sessions = set()  # Per evitare di riavviare thread su cartelle già in invio

    def send_data(self, file_path, sensor_name, interval):
        """Legge il CSV riga per riga e invia i dati via HTTP POST."""
        parts = file_path.split(os.sep)
        user_id = parts[-3] if len(parts) >= 3 else "unknown_user"
        session_id = parts[-2] if len(parts) >= 2 else "unknown_session"
        
        print(f"[*] AVVIO SENSOR: {sensor_name} | Utente: {user_id} | Sessione: {session_id}")
        
        try:
            with open(file_path, mode='r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Rimuoviamo il timestamp dal dizionario per metterlo nel campo dedicato
                    ts = row.pop('timestamp', time.time()) 

                    payload = {
                        "user": user_id,
                        "session": session_id,
                        "sensor": sensor_name,
                        "timestamp": ts,
                        "data": row 
                    }

                    try:
                        # Invio al server Flask
                        requests.post(self.server_url, json=payload, timeout=2)
                    except Exception as e:
                        # Silenzioso se il server è momentaneamente offline
                        pass
                    
                    # Simula la frequenza reale del sensore
                    time.sleep(interval)
        except FileNotFoundError:
            print(f"[!] Errore: File non trovato {file_path}")
        except Exception as e:
            print(f"[!] Errore durante l'invio: {e}")

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

        print(f"--- Client IoT Monitor in ascolto su: {base_dir} ---")
        
        while True:
            # os.walk naviga in tutte le sottocartelle
            for root, dirs, files in os.walk(base_dir):
                # Se la cartella contiene almeno uno dei file sensore
                if any(f in sensor_configs for f in files):
                    # Se non abbiamo ancora lanciato thread per questa specifica cartella
                    if root not in self.started_sessions:
                        print(f"\n[NEW] Rilevata nuova sorgente dati: {root}")
                        self.started_sessions.add(root)
                        
                        for file_name in files:
                            if file_name in sensor_configs:
                                conf = sensor_configs[file_name]
                                full_path = os.path.join(root, file_name)
                                
                                # Creazione del thread dedicato al singolo file/sensore
                                t = threading.Thread(
                                    target=self.send_data, 
                                    args=(full_path, conf["name"], conf["int"]),
                                    daemon=True 
                                )
                                t.start()
            
            # Attesa prima della prossima scansione per non sovraccaricare la CPU
            time.sleep(5)

if __name__ == "__main__":
    SERVER_URL = "http://127.0.0.1:5000/data"
    
    # LOGICA DINAMICA:
    # Se passi un percorso da terminale (es: python sensor.py C:\Dati\S1) usa quello.
    # Altrimenti usa il percorso di default della cartella generale.
    if len(sys.argv) > 1:
        DATASET_PATH = sys.argv[1]
    else:
        DATASET_PATH = r"C:\Users\galli\OneDrive\Desktop\fatigueset" 

    client_manager = FatigueSetClient(SERVER_URL)
    
    try:
        client_manager.monitora_directory(DATASET_PATH)
    except KeyboardInterrupt:
        print("\n[STOP] Monitoraggio interrotto dall'utente.")