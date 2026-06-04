from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from datetime import datetime, timezone, timedelta
from dateutil import parser
import json
import math
import statistics
from google.cloud import firestore
import os
import time
import threading
from collections import deque
import atexit
import signal
import sys
import requests

# Flask app setup
app = Flask(__name__)
app.secret_key = "p4ssw0rd" 
                            
# Firestore client setup (Si assume che il file di credenziali sia nella stessa cartella dello script)
db = firestore.Client.from_service_account_json('progetto-pcloud-5-b8e46802d217.json')
                                           
# Configurazione e stato per la gestione delle statistiche in memoria e del flush su Firestore
STATS_FLUSH_INTERVAL = 3.0

# Lock e buffer per la gestione thread-safe delle statistiche in memoria
_stats_lock = threading.Lock()
_stats_buffer = {}                                                                                         
_flusher_started = False
                                             
# Event e thread per il flusher periodico
_stop_event = threading.Event()
_flusher_thread = None
                                                         
# Configurazione per la finestra mobile di statistiche e alert
STATS_WINDOW_SECONDS = 20
                              
# Struttura in memoria per la finestra mobile
_window_state = {}                                                                                           

# Mappatura delle etichette di intensità
INTENSITY_LABELS_IT = {
    "low": "basso",
    "medium": "medio",
    "high": "alto",
}


def get_user_session_mapping_by_id(id_utente: str):
    """
    Recupera la mappatura tra intensità e session_id per l'utente dato. Cerca il documento Firestore con id_utente e ritorna i session_id per basso, medio e alto.
    """
    try:
        q = db.collection("utenti").where("id_utente", "==", str(id_utente).strip()).limit(1).stream()
        doc = next(q, None)
        if not doc:
            return None
        u = doc.to_dict() or {}
        low_s = str(u.get("low_session", "")).strip()
        med_s = str(u.get("medium_session", "")).strip()
        high_s = str(u.get("high_session", "")).strip()
        if not (low_s and med_s and high_s):
            return None
        return {"low": low_s, "medium": med_s, "high": high_s}
    except Exception as e:
        print(f"[MAP] Errore get_user_session_mapping_by_id({id_utente}): {e}")
        return None


def invert_session_mapping(map_intensity_to_session: dict):
    """
    Inverte la mappatura intensità->session_id in session_id->intensità, con pulizia di spazi e conversione a stringa.
    """
    if not map_intensity_to_session:
        return {}
    inv = {}
    for k, v in map_intensity_to_session.items():
        inv[str(v).strip()] = k
    return inv


def build_session_options_for_user(id_utente: str):
    """
    Crea le opzioni per il menu a tendina delle sessioni dell'utente, in ordine basso/medio/alto. Usa fallback 01/02/03 se non esiste la mappatura.
    """
    m = get_user_session_mapping_by_id(id_utente) or {"low": "01", "medium": "02", "high": "03"}
    return [
        (INTENSITY_LABELS_IT["low"], m["low"]),
        (INTENSITY_LABELS_IT["medium"], m["medium"]),
        (INTENSITY_LABELS_IT["high"], m["high"]),
    ]


def intensity_from_session(id_utente: str, session_id: str):
    """
    Determina low/medium/high per una session_id utilizzando la mappatura dell'utente.
    """
    m = get_user_session_mapping_by_id(id_utente) or {"low": "01", "medium": "02", "high": "03"}
    inv = invert_session_mapping(m)
    return inv.get(str(session_id).strip(), "low")


def session_label_from_session_id(id_utente: str, session_id: str) -> str:
    """
    Converte una session_id nella corrispondente etichetta italiana di intensità per l'utente.
    """
    intensity_key = intensity_from_session(id_utente, session_id)
    return INTENSITY_LABELS_IT.get(intensity_key, str(session_id).strip())


def _mark_session_started(user: str, session_id: str, now_epoch: float = None):
    """
    Registra il primo timestamp ricevuto per la sessione dell'utente, usato per il warmup degli alert.
    """
    if now_epoch is None:
        now_epoch = time.time()

    key = (str(user).strip(), str(session_id).strip())
    with _stats_lock:
        if key not in _session_first_seen:
            _session_first_seen[key] = float(now_epoch)


def _warmup_completed(user: str, session_id: str, now_epoch: float = None) -> bool:
    """
    Verifica se il warmup è già terminato per la sessione dell'utente.
    """
    if now_epoch is None:
        now_epoch = time.time()

    key = (str(user).strip(), str(session_id).strip())
    with _stats_lock:
        first_seen = _session_first_seen.get(key)

    if first_seen is None:
        return False

    return (float(now_epoch) - float(first_seen)) >= WARMUP_SECONDS


def _warmup_remaining_seconds(user: str, session_id: str, now_epoch: float = None) -> int:
    """
    Calcola i secondi rimanenti prima della fine del warmup per la sessione.
    """
    if now_epoch is None:
        now_epoch = time.time()

    key = (str(user).strip(), str(session_id).strip())
    with _stats_lock:
        first_seen = _session_first_seen.get(key)

    if first_seen is None:
        return WARMUP_SECONDS

    remaining = WARMUP_SECONDS - (float(now_epoch) - float(first_seen))
    return max(0, int(math.ceil(remaining)))


def build_mean_shift_context(user: str, session_id: str, sensor: str, global_stats: dict, win: dict):
    """
    Costruisce il contesto dettagliato dell'anomalia media finestra vs media globale.
    """
    if not sensor or not global_stats or not win:
        return None

    try:
        gmean = float(global_stats.get("mean"))
        wmean = float(win.get("mean"))
    except Exception:
        return None

    if gmean == 0.0:
        return None

    sensor_norm = str(sensor).upper().strip()
    thr_pct = MEAN_SHIFT_PCT.get(sensor_norm)
    if thr_pct is None:
        return None

    delta_pct = abs((wmean - gmean) / gmean) * 100.0
    direction = "sopra" if wmean > gmean else "sotto"
    delta_sign = "+" if wmean > gmean else "-"

    session_label = session_label_from_session_id(user, session_id)

    return {
        "user": str(user).strip(),
        "session_id": str(session_id).strip(),
        "session_label": session_label,
        "sensor": sensor_norm,
        "threshold_pct": float(thr_pct),
        "delta_pct": float(delta_pct),
        "direction": direction,
        "delta_sign": delta_sign,
        "window_mean": float(wmean),
        "global_mean": float(gmean),
        "window_count": int(win.get("count", 0)),
        "global_count": int(global_stats.get("count", 0)),
        "window_seconds": int(win.get("seconds", STATS_WINDOW_SECONDS))
    }


def format_telegram_anomaly_message(ctx: dict) -> str:
    """
    Formatta un messaggio Telegram con i dettagli dell'anomalia rilevata.
    """
    return (
        f"⚠️ Anomalia rilevata ⚠️\n"
        f"Utente: {ctx['user']}\n"
        f"Intensità: {ctx['session_label']}\n"
        f"Sensore: {ctx['sensor']}\n"
        f"Scostamento: {ctx['delta_sign']}{ctx['delta_pct']:.2f}% "
        f"(soglia ±{ctx['threshold_pct']:.2f}%)\n"
        f"Media finestra ({ctx['window_seconds']}s): {ctx['window_mean']:.3f}\n"
        f"Media globale: {ctx['global_mean']:.3f}\n"
        f"Campioni finestra/globale: {ctx['window_count']}/{ctx['global_count']}"
    )


def format_web_anomaly_message(ctx: dict) -> str:
    """
    Formatta un messaggio descrittivo da mostrare nella pagina delle statistiche.
    """
    return (
        f"La media degli ultimi {ctx['window_seconds']}s è {ctx['direction']} "
        f"la media globale del {ctx['delta_pct']:.2f}% "
        f"(soglia ±{ctx['threshold_pct']:.2f}%). "
        f"Media finestra: {ctx['window_mean']:.3f} • "
        f"Media globale: {ctx['global_mean']:.3f}."
    )

# Soglie di scostamento percentuale per alert media finestra vs media globale, specifiche per ogni sensore
MEAN_SHIFT_PCT = {
    "HR":   3.0,        
    "IBI":  4.0,        
    "TEMP": 0.3,          
    "EDA":  10.0,                                    
    "BVP":  10.0,                       
    "ACC":  15.0,                             
}


def evaluate_mean_shift_alert(sensor: str, global_stats: dict, win: dict):
    """
    Valuta se la media della finestra si discosta dalla media globale oltre la soglia per il sensore e restituisce soglia e messaggi di alert.
    """
    if not sensor:
        return None, []

    s = str(sensor).upper().strip()
    thr_pct = MEAN_SHIFT_PCT.get(s)
    if thr_pct is None:
        return None, []

    if not global_stats or not win:
        return thr_pct, []

    gmean = global_stats.get("mean", None)
    wmean = win.get("mean", None)

    try:
        gmean = float(gmean)
        wmean = float(wmean)
    except Exception:
        return thr_pct, []                
                                                                               
    if gmean == 0.0:
        return thr_pct, []

    pct = abs((wmean - gmean) / gmean) * 100.0

    if pct > thr_pct:
        direction = "sopra" if wmean > gmean else "sotto"
        msg = (
            f"Media finestra {direction} media globale: "
            f"{pct:.2f}% (win={wmean:.3f}, glob={gmean:.3f}, soglia={thr_pct:.2f}%)"
        )
        return thr_pct, [msg]

    return thr_pct, []

                                                
# Configurazione e stato per la gestione degli accessi a Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

# Cooldown in secondi per evitare di inviare ripetutamente lo stesso alert Telegram allo stesso utente/sessione/sensore                                       
TELEGRAM_COOLDOWN_SECONDS = 20                                     
TELEGRAM_POLL_INTERVAL = 2.0

# Base URL per le API Telegram
TELEGRAM_API_BASE = "https://api.telegram.org/bot"

# Stato per utilizzo Telegram
_telegram_started = False
_telegram_thread = None
_telegram_stop_event = threading.Event()
_telegram_last_update_id = 0

# Struttura in memoria per gestire il cooldown degli alert Telegram per chat_id/utente/sessione/sensore                                                                          
_telegram_cooldown = {}
_telegram_cooldown_lock = threading.Lock()

# Nome della collezione Firestore per le sottoscrizioni a Telegram, con documento per chat_id che contiene ruolo, username, id_utente e timestamp di aggiornamento
TELEGRAM_SUBS_COLLECTION = "telegram_subscriptions"

# Struttura in memoria per gestire i login guidati Telegram
_pending_telegram_logins = {}
_pending_telegram_logins_lock = threading.Lock()

# Durata del warmup in secondi per considerare una sessione "nuova" e non inviare alert Telegram finché non è trascorso questo tempo dal primo campione ricevuto per quella sessione                                    
WARMUP_SECONDS = 60

# Struttura in memoria per tenere traccia del primo timestamp visto per ogni sessione dell'utente, usata per il warmup degli alert Telegram                           
_session_first_seen = {}


def _tg_api_url(method: str) -> str:
    """
    Costruisce l'URL completo per chiamare un metodo API di Telegram.
    """
    return f"{TELEGRAM_API_BASE}{TELEGRAM_BOT_TOKEN}/{method}"


def telegram_send_message(chat_id: int, text: str):
    """
    Invia un messaggio Telegram in modalità best effort.
    """
    if not TELEGRAM_BOT_TOKEN:
        return
    try:
        requests.post(
            _tg_api_url("sendMessage"),
            json={"chat_id": chat_id, "text": text},
            timeout=5
        )
    except Exception as e:
        print(f"[TG] Errore invio messaggio a {chat_id}: {e}")


def telegram_save_subscription(chat_id: int, role: str, username: str, id_utente: str = None):
    """
    Salva o aggiorna la subscription Telegram in Firestore usando chat_id come documento.
    """
    doc_ref = db.collection(TELEGRAM_SUBS_COLLECTION).document(str(chat_id))
    payload = {
        "chat_id": int(chat_id),
        "role": role,                      
        "username": str(username),
        "updated_at": datetime.now(timezone.utc)
    }
    if id_utente is not None:
        payload["id_utente"] = str(id_utente).strip()
    doc_ref.set(payload, merge=True)


def telegram_delete_subscription(chat_id: int):
    """
    Elimina la subscription Telegram dal database.
    """
    try:
        db.collection(TELEGRAM_SUBS_COLLECTION).document(str(chat_id)).delete()
    except Exception as e:
        print(f"[TG] Errore delete subscription {chat_id}: {e}")


def telegram_get_admin_chat_ids():
    """
    Recupera i chat_id registrati come admin.
    """
    try:
        q = db.collection(TELEGRAM_SUBS_COLLECTION).where("role", "==", "admin").stream()
        return [int(d.to_dict().get("chat_id")) for d in q if d.to_dict().get("chat_id") is not None]
    except Exception as e:
        print(f"[TG] Errore get_admin_chat_ids: {e}")
        return []


def telegram_get_user_chat_ids(id_utente: str):
    """
    Recupera i chat_id degli utenti associati a un id_utente.
    """
    try:
        q = db.collection(TELEGRAM_SUBS_COLLECTION)\
              .where("role", "==", "utente")\
              .where("id_utente", "==", str(id_utente).strip())\
              .stream()
        return [int(d.to_dict().get("chat_id")) for d in q if d.to_dict().get("chat_id") is not None]
    except Exception as e:
        print(f"[TG] Errore get_user_chat_ids({id_utente}): {e}")
        return []


def _verify_credentials(username: str, password: str):
    """
    Verifica username e password su Firestore e restituisce esito e dati utente.
    """
    try:
        doc = db.collection("utenti").document(str(username)).get()
        if not doc.exists:
            return False, None
        u = doc.to_dict() or {}
        if str(u.get("password")) != str(password):
            return False, None
        return True, u
    except Exception as e:
        print(f"[TG] Errore verify_credentials: {e}")
        return False, None


def _set_pending_telegram_login(chat_id: int, role: str, step: str, username: str = None):
    """
    Memorizza lo stato del login guidato Telegram per una chat.
    """
    payload = {
        "role": str(role).strip(),
        "step": str(step).strip()
    }
    if username is not None:
        payload["username"] = str(username)

    with _pending_telegram_logins_lock:
        _pending_telegram_logins[int(chat_id)] = payload


def _get_pending_telegram_login(chat_id: int):
    """
    Recupera lo stato di login guidato pendente per una chat, se presente.
    """
    with _pending_telegram_logins_lock:
        return _pending_telegram_logins.get(int(chat_id))


def _clear_pending_telegram_login(chat_id: int):
    """
    Rimuove lo stato pendente di login guidato per una chat.
    """
    with _pending_telegram_logins_lock:
        _pending_telegram_logins.pop(int(chat_id), None)


def _handle_telegram_command(chat_id: int, text: str):
    """
    Gestisce i comandi e i messaggi Telegram, inclusi i passaggi del login guidato.
    """
    t = (text or "").strip()   
                               
    if t.startswith("/start"):
        _clear_pending_telegram_login(chat_id)
        telegram_send_message(
            chat_id,
            "Ciao! Sono Anomalie Pcloud.\n"
            "Comandi disponibili:\n"
            "/login_utente\n"
            "/login_admin\n"
            "/logout\n"
            "/annulla\n\n"
            "Per il login guidato ti chiederò prima username e poi password."
        )
        return

    if t.startswith("/logout"):
        _clear_pending_telegram_login(chat_id)
        telegram_delete_subscription(chat_id)
        telegram_send_message(chat_id, "Logout eseguito. Non riceverai più notifiche.")
        return

    if t.startswith("/annulla"):
        _clear_pending_telegram_login(chat_id)
        telegram_send_message(chat_id, "Operazione annullata.")
        return
  
    if t.startswith("/login_utente"):
        _set_pending_telegram_login(chat_id, role="utente", step="await_username")
        telegram_send_message(
            chat_id,
            "Login utente avviato.\n"
            "Inserisci username utente:"
        )
        return

    if t.startswith("/login_admin"):
        _set_pending_telegram_login(chat_id, role="admin", step="await_username")
        telegram_send_message(
            chat_id,
            "Login admin avviato.\n"
            "Inserisci username admin:"
        )
        return
                   
    pending = _get_pending_telegram_login(chat_id)
    if pending:
        role = str(pending.get("role", "")).strip()
        step = str(pending.get("step", "")).strip()

                                 
        if step == "await_username":
            username = t
            if not username:
                telegram_send_message(chat_id, "Username non valido. Riprova oppure usa /annulla.")
                return

            _set_pending_telegram_login(
                chat_id,
                role=role,
                step="await_password",
                username=username
            )

            telegram_send_message(
                chat_id,
                f"Username ricevuto: {username}\n"
                f"Inserisci password:"
            )
            return
                                 
        if step == "await_password":
            username = str(pending.get("username", "")).strip()
            password = t

            if not username:
                _clear_pending_telegram_login(chat_id)
                telegram_send_message(chat_id, "Errore interno login. Riprova con /login_utente o /login_admin.")
                return

            ok, u = _verify_credentials(username, password)
                                                     
            _clear_pending_telegram_login(chat_id)

            if not ok:
                telegram_send_message(chat_id, "Credenziali non valide. Login annullato.")
                return

            if role == "utente":
                id_utente = u.get("id_utente")
                if not id_utente:
                    telegram_send_message(chat_id, "Errore: id_utente mancante nel tuo profilo.")
                    return

                telegram_save_subscription(
                    chat_id,
                    role="utente",
                    username=username,
                    id_utente=str(id_utente)
                )
                telegram_send_message(
                    chat_id,
                    f"Login utente OK.\n"
                    f"Riceverai notifiche per id_utente={id_utente}."
                )
                return

            if role == "admin":
                telegram_save_subscription(
                    chat_id,
                    role="admin",
                    username=username,
                    id_utente=None
                )
                telegram_send_message(
                    chat_id,
                    "Login admin OK.\n"
                    "Riceverai notifiche per tutte le anomalie."
                )
                return

            telegram_send_message(chat_id, "Ruolo login non riconosciuto.")
            return
     
    telegram_send_message(
        chat_id,
        "Comando non riconosciuto.\n"
        "Usa /start per aiuto."
    )


def _telegram_poll_loop():
    """
    Esegue il polling continuo di Telegram getUpdates.
    """
    global _telegram_last_update_id

    if not TELEGRAM_BOT_TOKEN:
        print("[TG] TELEGRAM_BOT_TOKEN non impostato: polling disattivato.")
        return

    print("[TG] Polling Telegram avviato.")
    offset = _telegram_last_update_id + 1 if _telegram_last_update_id else None

    while not _telegram_stop_event.wait(TELEGRAM_POLL_INTERVAL):
        try:
            params = {"timeout": 10}
            if offset is not None:
                params["offset"] = offset

            r = requests.get(_tg_api_url("getUpdates"), params=params, timeout=15)
            data = r.json()

            if not data.get("ok"):
                continue

            updates = data.get("result", [])
            for upd in updates:
                upd_id = upd.get("update_id")
                if upd_id is None:
                    continue
                offset = upd_id + 1
                _telegram_last_update_id = upd_id

                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                chat = msg.get("chat") or {}
                chat_id = chat.get("id")
                text = msg.get("text", "")
                if chat_id is None:
                    continue

                _handle_telegram_command(int(chat_id), str(text))

        except Exception as e:
            print(f"[TG] Errore polling: {e}")


def start_telegram_polling():
    """
    Avvia il polling Telegram su un thread dedicato, evitando doppio avvio con il reloader Flask.
    """
    global _telegram_started, _telegram_thread
    if _telegram_started:
        return

                                           
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    _telegram_started = True
    _telegram_thread = threading.Thread(target=_telegram_poll_loop, daemon=True)
    _telegram_thread.start()


def stop_telegram_polling():
    """
    Ferma il polling Telegram segnalando l'evento di stop e aspettando la terminazione del thread.
    """
    try:
        _telegram_stop_event.set()
    except Exception:
        pass


def _cooldown_allow(chat_id: int, user: str, session_id: str, sensor: str) -> bool:
    """
    Verifica se il cooldown consente di inviare lo stesso alert alla stessa chat/utente/sessione/sensore.
    """
    key = (int(chat_id), str(user).strip(), str(session_id).strip(), str(sensor).strip())
    now = time.time()
    with _telegram_cooldown_lock:
        last = _telegram_cooldown.get(key, 0.0)
        if now - last < TELEGRAM_COOLDOWN_SECONDS:
            return False
        _telegram_cooldown[key] = now
        return True


def _to_float(x, default=None):
    """
    Converte in modo sicuro un valore in float, restituendo un default se non è possibile.
    """
    try:
        return float(x)
    except Exception:
        return default


def estrai_valore_numerico(sensor, valori):
    """
    Estrae un valore numerico dai dati del sensore con regole specifiche per ogni tipo di sensore.
    """
    if valori is None:
        return None
                                                         
    if isinstance(valori, str):
        try:
            valori = json.loads(valori)
        except Exception:
            try:
                valori = json.loads(valori.replace("'", '"'))
            except Exception:
                return None

    if not isinstance(valori, dict) or not valori:
        return None

    s = str(sensor).upper().strip()

    if s == "ACC":
        ax = _to_float(valori.get('ax', valori.get('x', 0)), 0.0)
        ay = _to_float(valori.get('ay', valori.get('y', 0)), 0.0)
        az = _to_float(valori.get('az', valori.get('z', 0)), 0.0)
        mag = math.sqrt(ax * ax + ay * ay + az * az)
                                                                                                
        return abs(mag - 1.0)

    if s == "BVP":                                                            
        vals = []
        for v in valori.values():
            fv = _to_float(v, None)
            if fv is not None:
                vals.append(fv)
        if not vals:
            return None
        return abs(vals[0]) if len(vals) == 1 else abs(sum(vals) / len(vals))

    nums = []
    for v in valori.values():
        fv = _to_float(v, None)
        if fv is not None:
            nums.append(fv)

    if not nums:
        return None

    return nums[0] if len(nums) == 1 else (sum(nums) / len(nums))

def _window_update(user, session_id, sensor, valori, t_epoch_sec):
    """
    Aggiorna lo stato della finestra mobile con il nuovo campione del sensore.
    """
    x = estrai_valore_numerico(sensor, valori)
    if x is None:
        return

    key = (str(user).strip(), str(session_id).strip(), str(sensor).strip())
    x = float(x)
    t = float(t_epoch_sec)
    cutoff = t - STATS_WINDOW_SECONDS

    with _stats_lock:
        st = _window_state.get(key)
        if st is None:
            st = {
                "dq": deque(),
                "sum": 0.0,
                "sumsq": 0.0,
                "min": x,
                "max": x,
                "dirty": False
            }
            _window_state[key] = st

        dq = st["dq"]
        dq.append((t, x))
        st["sum"] += x
        st["sumsq"] += x * x
                               
        if len(dq) == 1:
            st["min"] = x
            st["max"] = x
        else:
            st["min"] = min(st["min"], x)
            st["max"] = max(st["max"], x)
                      
        while dq and dq[0][0] < cutoff:
            _, old = dq.popleft()
            st["sum"] -= old
            st["sumsq"] -= old * old
            if old == st["min"] or old == st["max"]:
                st["dirty"] = True
                                                                                     
        if st["dirty"]:
            if dq:
                vals = [v for _, v in dq]
                st["min"] = min(vals)
                st["max"] = max(vals)
            st["dirty"] = False


def _window_prune_to_now(key, now_epoch_sec):
    """
    Rimuove i campioni scaduti dalla finestra mobile in base al tempo corrente.
    """
    cutoff = float(now_epoch_sec) - STATS_WINDOW_SECONDS
    st = _window_state.get(key)
    if not st:
        return

    dq = st["dq"]
    while dq and dq[0][0] < cutoff:
        _, old = dq.popleft()
        st["sum"] -= old
        st["sumsq"] -= old * old
        if old == st["min"] or old == st["max"]:
            st["dirty"] = True

    if st["dirty"]:
        if dq:
            vals = [v for _, v in dq]
            st["min"] = min(vals)
            st["max"] = max(vals)
        st["dirty"] = False


def _window_snapshot(key, now_epoch_sec):
    """
    Restituisce le statistiche correnti della finestra mobile dopo averla potata.
    """
    with _stats_lock:
        if key not in _window_state:
            return None

        _window_prune_to_now(key, now_epoch_sec)
        st = _window_state.get(key)
        if not st:
            return None

        n = len(st["dq"])
        if n <= 0:
            return None

        s = st["sum"]
        ss = st["sumsq"]
        mean = s / n

        if n > 1:
                                                       
            var = (ss - (s * s) / n) / (n - 1)
            if var < 0:
                var = 0.0
            std = math.sqrt(var)
        else:
            std = 0.0

        return {
            "seconds": STATS_WINDOW_SECONDS,
            "count": n,
            "mean": mean,
            "min": st["min"],
            "max": st["max"],
            "std": std,
            "updated_at": datetime.now(timezone.utc)
        }

def _window_snapshot_no_lock(key, now_epoch_sec):
    """
    Come _window_snapshot, ma assume che il lock sia già acquisito.
    """
    if key not in _window_state:
        return None

    _window_prune_to_now(key, now_epoch_sec)
    st = _window_state.get(key)
    if not st:
        return None

    n = len(st["dq"])
    if n <= 0:
        return None

    s = st["sum"]
    ss = st["sumsq"]
    mean = s / n

    if n > 1:
        var = (ss - (s * s) / n) / (n - 1)
        if var < 0:
            var = 0.0
        std = math.sqrt(var)
    else:
        std = 0.0

    return {
        "seconds": STATS_WINDOW_SECONDS,
        "count": n,
        "mean": mean,
        "min": st["min"],
        "max": st["max"],
        "std": std,
        "updated_at": datetime.now(timezone.utc)
    }


def _buffer_update(user, session_id, sensor, valori):
    """
    Aggiorna il buffer statistico Welford in memoria per la chiave utente/sessione/sensore.
    """
    x = estrai_valore_numerico(sensor, valori)
    if x is None:
        return

    key = (str(user).strip(), str(session_id).strip(), str(sensor).strip())
    x = float(x)

    with _stats_lock:
        st = _stats_buffer.get(key)
        if st is None:
            _stats_buffer[key] = {
                "user": key[0],
                "session": key[1],
                "sensor": key[2],
                "n": 1,
                "mean": x,
                "m2": 0.0,
                "min": x,
                "max": x
            }
            return

        n = st["n"]
        mean = st["mean"]
        m2 = st["m2"]

        n_new = n + 1
        delta = x - mean
        mean_new = mean + delta / n_new
        delta2 = x - mean_new
        m2_new = m2 + delta * delta2

        st["n"] = n_new
        st["mean"] = mean_new
        st["m2"] = m2_new
        st["min"] = min(st["min"], x)
        st["max"] = max(st["max"], x)


@firestore.transactional
def _merge_stats_tx(transaction, doc_ref, delta_stats, win_snapshot=None):
    """
    Unisce le statistiche buffer con quelle già presenti in Firestore usando la formula Welford.
    """
    snap = doc_ref.get(transaction=transaction)
    now = datetime.now(timezone.utc)

    n2 = int(delta_stats["n"])
    mean2 = float(delta_stats["mean"])
    m2_2 = float(delta_stats["m2"])
    min2 = float(delta_stats["min"])
    max2 = float(delta_stats["max"])

    if not snap.exists:
        n = n2
        mean = mean2
        m2 = m2_2
        min_v = min2
        max_v = max2
    else:
        st = snap.to_dict() or {}
        n1 = int(st.get("count", 0))
        mean1 = float(st.get("mean", 0.0))
        m2_1 = float(st.get("m2", 0.0))
        min1 = st.get("min", min2)
        max1 = st.get("max", max2)

        if n1 <= 0:
            n = n2
            mean = mean2
            m2 = m2_2
            min_v = min2
            max_v = max2
        else:
            n = n1 + n2
            delta = mean2 - mean1
            mean = mean1 + delta * (n2 / n)
            m2 = m2_1 + m2_2 + (delta * delta) * (n1 * n2 / n)

            min_v = min(float(min1), min2) if min1 is not None else min2
            max_v = max(float(max1), max2) if max1 is not None else max2
                     
    if n > 1:
        var = m2 / (n - 1)
        std = math.sqrt(var) if var >= 0 else 0.0
    else:
        std = 0.0

    payload = {
        "user": delta_stats["user"],
        "session": delta_stats["session"],
        "sensor": delta_stats["sensor"],
        "count": n,
        "mean": mean,
        "min": min_v,
        "max": max_v,
        "m2": m2,
        "std": std,
        "updated_at": now
    }
                                                                           
    if win_snapshot:
        payload["win"] = win_snapshot

    transaction.set(doc_ref, payload, merge=True)

    return payload


def _flush_stats_once():
    """
    Esegue un flush atomico del buffer RAM su Firestore e valuta eventuali anomalie.
    """
    global _stats_buffer

    with _stats_lock:
        if not _stats_buffer:
            return
                                                         
        now_epoch = time.time()
                           
        to_flush = _stats_buffer
        _stats_buffer = {}
                                                                     
        win_snaps = {}
        for key in to_flush.keys():
            win_snaps[key] = _window_snapshot_no_lock(key, now_epoch)
                        
    for (u, sess, sens), delta_stats in to_flush.items():
        doc_id = f"{u}_{sess}_{sens}"
        doc_ref = db.collection("statistiche").document(doc_id)

        key = (u, sess, sens)
        win_snap = win_snaps.get(key)

        try:
            tx = db.transaction()
            merged_payload = _merge_stats_tx(tx, doc_ref, delta_stats, win_snapshot=win_snap)

                                                                                  
            thr, alerts = evaluate_mean_shift_alert(sens, merged_payload, win_snap)

                                                                        
            if alerts and not _warmup_completed(u, sess, now_epoch):
                remaining = _warmup_remaining_seconds(u, sess, now_epoch)
                print(f"[WARMUP] Alert soppresso per {u}/{sess}/{sens}. Mancano {remaining}s.")
                alerts = []

            if alerts:
                ctx = build_mean_shift_context(u, sess, sens, merged_payload, win_snap)

                if ctx:
                    text = format_telegram_anomaly_message(ctx)
                else:
                                      
                    session_label = session_label_from_session_id(u, sess)
                    text = (
                        f"⚠️ Anomalia rilevata ⚠️\n"
                        f"Utente: {u}\n"
                        f"Intensità: {session_label}\n"
                        f"Sensore: {sens}\n"
                        f"{alerts[0]}"
                    )

                                               
                admin_chats = telegram_get_admin_chat_ids()
                for chat_id in admin_chats:
                    if _cooldown_allow(chat_id, u, sess, sens):
                        telegram_send_message(chat_id, text)

                                                                 
                user_chats = telegram_get_user_chat_ids(u)
                for chat_id in user_chats:
                    if _cooldown_allow(chat_id, u, sess, sens):
                        telegram_send_message(chat_id, text)

        except Exception as e:
            print(f"[STATS] Errore flush {doc_id}: {e}")


def _stats_flusher_loop():
    """
    Loop continuo che esegue il flush delle statistiche a intervalli regolari finché non viene segnalato lo stop.
    """                                                                            
    while not _stop_event.wait(STATS_FLUSH_INTERVAL):
        _flush_stats_once()


def start_stats_flusher():
    """
    Avvia il thread che esegue il flush periodico delle statistiche in memoria.
    """
    global _flusher_started
    if _flusher_started:
        return
                                     
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    global _flusher_thread

    _flusher_started = True
    _flusher_thread = threading.Thread(target=_stats_flusher_loop, daemon=True)
    _flusher_thread.start()
    print(f"[STATS] Flusher avviato. Interval={STATS_FLUSH_INTERVAL}s")

def _clear_ram_state():
    """
    Svuota le strutture in RAM senza eseguire flush su Firestore.
    """
    global _stats_buffer, _window_state, _session_first_seen

    with _stats_lock:
        _stats_buffer = {}
        _window_state = {}
        _session_first_seen = {}

    with _telegram_cooldown_lock:
        _telegram_cooldown.clear()

    with _pending_telegram_logins_lock:
        _pending_telegram_logins.clear()

    print("[STATS] Pulizia RAM completata: _stats_buffer, _window_state, _session_first_seen e _pending_telegram_logins svuotati.")

def _shutdown_cleanup(exit_code=0):
    """
    Ferma i thread attivi e pulisce lo stato di memoria durante lo shutdown.
    """
    try:
        _stop_event.set()
    except Exception:
        pass

    try:
        stop_telegram_polling()
    except Exception:
        pass

                 
    try:
        _clear_ram_state()
    except Exception as e:
        print(f"[STATS] Errore pulizia RAM: {e}")

                             
    try:
        global _flusher_thread
        if _flusher_thread and _flusher_thread.is_alive():
            _flusher_thread.join(timeout=2.0)
    except Exception:
        pass

    if exit_code is not None:
        raise SystemExit(exit_code)

def _signal_handler(signum, frame):
    """
    Gestisce i segnali di terminazione per eseguire il cleanup.
    """
    print(f"[STATS] Ricevuto segnale {signum}. Pulizia RAM in corso...")
    _shutdown_cleanup(exit_code=0)

def register_shutdown_hooks():
    """
    Registra hook di uscita e segnali per eseguire il cleanup all'arresto.
    """                                                                               
    if app.debug and os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        return

    atexit.register(lambda: _shutdown_cleanup(exit_code=None))

    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                signal.signal(sig, _signal_handler)
            except Exception as e:
                print(f"[STATS] Impossibile registrare handler per {sig}: {e}")

                       
@app.route('/data', methods=['POST'])
def receive_data():
    """
    Endpoint per ricevere i dati dei sensori. Si aspetta un JSON con user, session, sensor, timestamp e data.
    Salva i dati su Firestore e aggiorna le statistiche in memoria.
    """
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No data"}), 400

    try:
        raw_ts = data.get('timestamp')
        ts_datetime = datetime.fromtimestamp(float(raw_ts) / 1000)
        valori_data = data.get('data')

        doc_ref = db.collection('dati_sensori').document()
        doc_ref.set({
            'user': data.get('user'),
            'session': data.get('session'),
            'sensor': data.get('sensor'),
            'timestamp': ts_datetime,
            'valori': valori_data,
            'data_ricezione': datetime.now(timezone(timedelta(hours=2)))
        })
                                                          
        try:
            _mark_session_started(
                user=data.get('user'),
                session_id=data.get('session'),
                now_epoch=time.time()
            )
        except Exception as e:
            print(f"[WARMUP] Errore mark_session_started: {e}")
                                                   
        try:
            _buffer_update(
                user=data.get('user'),
                session_id=data.get('session'),
                sensor=data.get('sensor'),
                valori=valori_data
            )
        except Exception as e:
            print(f"[STATS] Errore buffer_update: {e}")
                           
        try:
            _window_update(
                user=data.get('user'),
                session_id=data.get('session'),
                sensor=data.get('sensor'),
                valori=valori_data,
                t_epoch_sec=time.time()
            )
        except Exception as e:
            print(f"[STATS] Errore window_update: {e}")

        return jsonify({"status": "success"}), 200

    except Exception as e:
        print(f"Errore DB: {e}")
        return jsonify({"status": "error"}), 500

                              
@app.route('/')
def index():
    """
    Pagina principale che reindirizza al login se non autenticato, altrimenti alla dashboard.
    """
    return redirect(url_for('login'))


@app.before_request
def _ensure_stats_flusher():
    """
    Garantisce che lo stats flusher sia avviato prima di gestire qualsiasi richiesta.
    """
    start_stats_flusher()
    start_telegram_polling()


@app.route('/login', methods=['GET', 'POST'])
def login():
    """
    Endpoint per il login degli utenti.
    """
    error = False
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        tipo_scelto = request.form.get('tipo_utente')
        
        print(f"Tentativo di login per: {username}")
        
        try:
            user_doc = db.collection('utenti').document(username).get()
            
            if user_doc.exists:
                user_data = user_doc.to_dict()
                print(f"Utente trovato in DB. Controllo password...")
                
                if str(user_data.get('password')) == str(password):
                    session['user'] = username
                    session['tipo'] = tipo_scelto
                    session['id_utente'] = user_data.get('id_utente')                                 
                    print("Login successo! Reindirizzamento...")
                    
                    if tipo_scelto == 'admin':
                        return redirect(url_for('dashboard'))
                    else:
                        return redirect(url_for('dashboard_utente'))
                else:
                    print("Password errata.")
                    error = True
            else:
                print("Utente non esistente su Firestore.")
                error = True
        except Exception as e:
            print(f"Errore durante il login: {e}")
            error = True

    return render_template_string('''
    <html>
    <body style="font-family:sans-serif; display:flex; justify-content:center; align-items:center; height:100vh; background:#f0f2f5; margin:0;">
        <form method="post" style="background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:320px;">
            <h2 style="color:#1a73e8; text-align:center;">Empatica E4 Login</h2>
            <select name="tipo_utente" style="width:100%; margin-bottom:15px; padding:10px; border-radius:6px; border:1px solid #ddd;">
                <option value="admin">Admin</option>
                <option value="utente">Utente</option>
            </select>
            <input type="text" name="username" placeholder="Username" required style="width:100%; margin-bottom:15px; padding:10px; border-radius:6px; border:1px solid #ddd; box-sizing:border-box;">
            <input type="password" name="password" placeholder="Password" required style="width:100%; margin-bottom:20px; padding:10px; border-radius:6px; border:1px solid #ddd; box-sizing:border-box;">
            <input type="submit" value="Accedi" style="width:100%; padding:12px; background:#1a73e8; color:white; border:none; border-radius:6px; cursor:pointer; font-weight:bold;">
            {% if error %}<p style="color:red; text-align:center; font-size:0.8rem; margin-top:10px;">Credenziali errate o errore server</p>{% endif %}
        </form>
    </body>
    </html>
    ''', error=error)


@app.route('/logout')
def logout():
    """
    Endpoint per il logout degli utenti, che pulisce la sessione e reindirizza al login.
    """
    session.clear()
    return redirect(url_for('login'))


@app.route("/api/registered_users")
def api_registered_users():
    """
    Restituisce la lista degli id_utente registrati in formato JSON.
    """
    try:
        docs = db.collection("utenti").stream()
        ids = set()

        for d in docs:
            u = d.to_dict() or {}
                              
            if u.get("id_utente"):
                ids.add(str(u["id_utente"]).strip())

                                                     
            ids.add(str(d.id).strip())

        ids.discard("")                          
        return jsonify({"registered_ids": sorted(ids)}), 200

    except Exception as e:
        return jsonify({"registered_ids": [], "error": str(e)}), 500

                                          
@app.route('/api/live_data')
def api_live_data():
    """
    Restituisce l'ultimo dato ricevuto per gli utenti registrati, filtrando i dati sensori in ordine di ricezione.
    """
    if 'user' not in session or session.get('tipo') != 'admin':
        return jsonify({"status": "error", "message": "Accesso negato"}), 403
        
    try:
        docs_utenti = db.collection('utenti').stream()
        id_validi = set()
        for d in docs_utenti:
            u_data = d.to_dict()
            id_u = u_data.get('id_utente')
            if id_u:
                id_validi.add(str(id_u).strip())
            id_validi.add(str(d.id).strip())                                   
            
        if not id_validi:
            return jsonify({"message": "Nessun utente registrato nel DB"}), 404
                                                                               
                            
        query = db.collection('dati_sensori')\
                  .order_by('data_ricezione', direction=firestore.Query.DESCENDING)\
                  .limit(50)

        ultimo_dato_valido = None
        for doc in query.stream():
            dati_doc = doc.to_dict()
            user_del_dato = str(dati_doc.get('user', '')).strip()
            
                                                                                                         
            if user_del_dato in id_validi:
                ultimo_dato_valido = dati_doc
                break                                                          

        if ultimo_dato_valido:
            ts = ultimo_dato_valido.get('timestamp')
            if ts and hasattr(ts, 'timestamp'):
                ts_millisecondi = int(ts.timestamp() * 1000)
            elif isinstance(ts, (int, float)):
                ts_millisecondi = int(ts) if ts > 9999999999 else int(ts * 1000)
            else:
                ts_millisecondi = str(ts)
            
            return jsonify({
                "utente": ultimo_dato_valido.get('user', 'N/D'),
                "sessione": ultimo_dato_valido.get('session', 'N/D'),
                "sensore": ultimo_dato_valido.get('sensor', 'N/D'),
                "orario": ts_millisecondi,
                "valori": ultimo_dato_valido.get('valori', {})
            })
        else:
            return jsonify({"message": "Nessun dato recente per gli utenti registrati"}), 404
            
    except Exception as e:
        print(f"Errore Live Data Software-Filtered: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

                                                      
@app.route('/live_admin')
def live_admin():
    """
    Pagina per visualizzare i dati in tempo reale filtrati per gli utenti registrati, accessibile solo agli admin.
    """
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))
        
    return render_template_string(HTML_LIVE_DATA)


# Il template HTML per la pagina dei dati in tempo reale.
HTML_LIVE_DATA = '''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Empatica E4 - Live Data</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 500; font-size: 0.9rem; }
        .nav-links a:hover { text-decoration: underline; }

        .container { max-width: 900px; margin: 50px auto; padding: 0 20px; }
        .live-card { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.05); }

        h1 { color: #1a73e8; font-size: 2.5rem; margin-top: 0; margin-bottom: 30px; border-bottom: 2px solid #e8f0fe; padding-bottom: 15px; }

        .row { display: flex; margin-bottom: 25px; font-size: 1.2rem; align-items: center; }
        .label { width: 180px; font-weight: bold; color: #555; }
        .value { font-weight: 500; color: #000; }
        .value.sensor { color: #d93025; font-weight: bold; }

        .value.json { font-family: sans-serif; background: transparent; padding: 0; border: none; }

        .footer-status { margin-top: 40px; color: #888; font-size: 0.9rem; font-style: italic; display: flex; align-items: center; gap: 8px; }
        .dot { width: 8px; height: 8px; background-color: #34a853; border-radius: 50%; display: inline-block; animation: blink 1.5s infinite; }

        @keyframes blink { 0% { opacity: 0.3; } 50% { opacity: 1; } 100% { opacity: 0.3; } }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
        <div class="nav-links">
            <a href="/dashboard_admin">Dashboard</a>
            <a href="/live_admin" style="text-decoration: underline;">Dati in tempo reale</a>
            <a href="/statistics_admin">Statistiche</a>
            <a href="/register">Nuovo Utente</a>
            <a href="/logout" style="color: #ffcccc;">Logout</a>
        </div>
    </div>

    <div class="container">
        <div class="live-card">
            <h1>Empatica E4 wristband</h1>

            <div class="row">
                <div class="label">Utente:</div>
                <div class="value" id="lblUtente">--</div>
            </div>

            <div class="row">
                <div class="label">Sessione:</div>
                <div class="value" id="lblSessione">--</div>
            </div>

            <div class="row">
                <div class="label">Sensore:</div>
                <div class="value sensor" id="lblSensore">--</div>
            </div>

            <div class="row">
                <div class="label">Orario:</div>
                <div class="value" id="lblOrario">--</div>
            </div>

            <div class="row" style="align-items: flex-start;">
                <div class="label" style="margin-top: 8px;">Dati ricevuti:</div>
                <div class="value json" id="lblDati">{}</div>
            </div>

            <div class="footer-status">
                <span class="dot"></span> Aggiornamento automatico attivo...
            </div>
        </div>
    </div>

    <script>
        function formattaOrario(timestampMillisecondi) {
            if (!timestampMillisecondi || isNaN(timestampMillisecondi)) return "--:--:--";
            const data = new Date(Number(timestampMillisecondi));

            const ore = String(data.getHours()).padStart(2, '0');
            const minuti = String(data.getMinutes()).padStart(2, '0');
            const secondi = String(data.getSeconds()).padStart(2, '0');

            return `${ore}:${minuti}:${secondi}`;
        }

        function generaBadgeDati(valori) {
            if (!valori || typeof valori !== 'object' || Object.keys(valori).length === 0) return '{}';

            let htmlBadges = '<div style="display: flex; gap: 10px; flex-wrap: wrap;">';

            for (const [chiave, valore] of Object.entries(valori)) {
                htmlBadges += `
                    <span style="
                        background: #e8f0fe;
                        color: #1a73e8;
                        padding: 6px 14px;
                        border-radius: 20px;
                        font-weight: 600;
                        font-size: 0.95rem;
                        border: 1px solid #c2dbff;
                        font-family: sans-serif;
                    ">
                        <strong style="color: #555; margin-right: 4px;">${chiave}:</strong>${valore}
                    </span>`;
            }

            htmlBadges += '</div>';
            return htmlBadges;
        }

        function caricaDatoRealTime() {
            fetch('/api/live_data')
                .then(response => response.json())
                .then(data => {
                    if (data.status !== "error" && !data.message) {
                        document.getElementById('lblUtente').innerText = data.utente;
                        document.getElementById('lblSessione').innerText = data.sessione;
                        document.getElementById('lblSensore').innerText = data.sensore;
                        document.getElementById('lblOrario').innerText = formattaOrario(data.orario);
                        document.getElementById('lblDati').innerHTML = generaBadgeDati(data.valori);
                    }
                })
                .catch(err => console.error("Errore fetch dati live:", err));
        }

        caricaDatoRealTime();
        setInterval(caricaDatoRealTime, 1000);
    </script>
</body>
</html>
'''

@app.route('/dashboard_admin')
def dashboard():
    """
    Dashboard principale per gli admin, che mostra la lista degli utenti e permette di selezionare sessioni e visualizzare i grafici.
    """
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    print(f"DEBUG: Accesso dashboard per l'utente {session['user']}")

    try:
                                                                                            
        try:
            docs_u = db.collection('utenti').stream()
                                                                             
                                                                
            lista_utenti = sorted(list(set([d.to_dict().get('id_utente') for d in docs_u if d.to_dict().get('id_utente')])))
        except Exception as e:
            print(f"Errore recupero utenti dal DB utenti: {e}")
            lista_utenti = []

        if not lista_utenti:
            lista_utenti = ["Nessun utente creato"]

                                                                                                       
        selected_user = request.args.get('u', lista_utenti[0])

        session_options = build_session_options_for_user(selected_user)
        default_sess = session_options[0][1]                         
        selected_sess = request.args.get('s', default_sess)
        
        data_charts = recupera_dati_grafici(selected_user, selected_sess)

        return render_template_string(
            HTML_DASHBOARD_ORIGINALE,
            utenti=lista_utenti,
            selected_u=selected_user,
            selected_s=selected_sess,
            data_charts=data_charts,
            session_options=session_options
        )

    except Exception as e:
        print(f"ERRORE FATALE: {e}")
        return f"<h1>Errore di caricamento</h1><p>{e}</p><a href='/logout'>Torna al login</a>"


def _build_statistics_payload(selected_user, selected_sess):
    """
    Compone il payload statistico per una coppia utente/sessione, includendo anche eventuali alert.
    """
    selected_session_label = session_label_from_session_id(selected_user, selected_sess)

    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]

    stats = {}
    mean_shift_thr_view = {}
    mean_shift_alerts_view = {}

    for s in sensori:
        doc_id = f"{str(selected_user).strip()}_{str(selected_sess).strip()}_{s}"
        doc = db.collection("statistiche").document(doc_id).get()
        st = doc.to_dict() if doc.exists else None
        stats[s] = st

        win = st.get("win") if st else None

        mean_thr, mean_alerts = evaluate_mean_shift_alert(s, st, win)
        mean_shift_thr_view[s] = mean_thr

        if mean_alerts and st and win:
            ctx = build_mean_shift_context(selected_user, selected_sess, s, st, win)
            if ctx:
                mean_shift_alerts_view[s] = [format_web_anomaly_message(ctx)]
            else:
                mean_shift_alerts_view[s] = mean_alerts
        else:
            mean_shift_alerts_view[s] = []

    return {
        "selected_session_label": selected_session_label,
        "stats": stats,
        "mean_shift_thr_view": mean_shift_thr_view,
        "mean_shift_alerts_view": mean_shift_alerts_view
    }


@app.route('/statistics_admin')
def statistics_admin():
    """
    Pagina per visualizzare le statistiche aggregate per gli admin, con alert di anomalie rilevate.
    """
    if 'user' not in session or session.get('tipo') != 'admin':
        return redirect(url_for('login'))

    try:
        docs_u = db.collection('utenti').stream()
        lista_utenti = sorted(list(set([
            d.to_dict().get('id_utente')
            for d in docs_u
            if d.to_dict().get('id_utente')
        ])))
    except Exception as e:
        print(f"Errore recupero utenti: {e}")
        lista_utenti = []

    if not lista_utenti:
        lista_utenti = ["Nessun utente creato"]

    selected_user = request.args.get('u', lista_utenti[0])

    session_options = build_session_options_for_user(selected_user)
    default_sess = session_options[0][1]
    selected_sess = request.args.get('s', default_sess)

    payload = _build_statistics_payload(selected_user, selected_sess)

    return render_template_string(
        HTML_STATISTICHE,
        utenti=lista_utenti,
        selected_u=selected_user,
        selected_s=selected_sess,
        selected_session_label=payload["selected_session_label"],
        stats=payload["stats"],
        interval=int(STATS_FLUSH_INTERVAL),
        session_options=session_options,
        mean_shift_thr_view=payload["mean_shift_thr_view"],
        mean_shift_alerts_view=payload["mean_shift_alerts_view"],
        warmup_seconds=WARMUP_SECONDS
    )


@app.route('/statistics_utente')
def statistics_utente():
    """
    Pagina per visualizzare le statistiche aggregate per gli utenti, con alert di anomalie rilevate.
    """
    if 'user' not in session or session.get('tipo') != 'utente':
        return redirect(url_for('login'))

    mio_id = session.get('id_utente')

    session_options = build_session_options_for_user(mio_id)
    default_sess = session_options[0][1]
    selected_sess = request.args.get('s', default_sess)

    payload = _build_statistics_payload(mio_id, selected_sess)

    return render_template_string(
        HTML_STATISTICHE,
        utenti=[mio_id],                                        
        selected_u=mio_id,
        selected_s=selected_sess,
        selected_session_label=payload["selected_session_label"],
        stats=payload["stats"],
        interval=int(STATS_FLUSH_INTERVAL),
        session_options=session_options,
        mean_shift_thr_view=payload["mean_shift_thr_view"],
        mean_shift_alerts_view=payload["mean_shift_alerts_view"],
        warmup_seconds=WARMUP_SECONDS
    )

                                
@app.route('/dashboard_utente')
def dashboard_utente():
    """
    Dashboard per gli utenti, che mostra solo le proprie sessioni e i grafici relativi.
    """
    if 'user' not in session or session.get('tipo') != 'utente':
        return redirect(url_for('login'))

    mio_id = session.get('id_utente')

    session_options = build_session_options_for_user(mio_id)
    default_sess = session_options[0][1]
    selected_sess = request.args.get('s', default_sess)
    
    data_charts = recupera_dati_grafici(mio_id, selected_sess)
                                                                                                   
    return render_template_string(HTML_DASHBOARD_ORIGINALE, utenti=[mio_id], selected_u=mio_id, selected_s=selected_sess, data_charts=data_charts, session_options=session_options)

                                             
def recupera_dati_grafici(target_user, session_id):
    """
    Recupera gli ultimi 40 dati per ogni sensore di una specifica sessione e utente, formattandoli per i grafici.
    """
    sensori = ["ACC", "BVP", "EDA", "HR", "IBI", "TEMP"]
    data_charts = {}
    for s in sensori:
        try:
            query = db.collection('dati_sensori')\
                      .where('user', '==', str(target_user))\
                      .where('sensor', '==', s)\
                      .where('session', '==', session_id)\
                      .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                      .limit(40)
            
            results = [d.to_dict() for d in query.stream()]
            results.reverse()
            
            labels = []
            for r in results:
                ts = r.get('timestamp')
                if ts and hasattr(ts, 'strftime'):
                    labels.append(ts.strftime('%H:%M:%S'))
                else:
                    labels.append(str(ts))
            
            values = []
            for r in results:
                val_raw = r.get('valori', '{}')
                val = json.loads(val_raw.replace("'", '"')) if isinstance(val_raw, str) else val_raw
                
                if s == "ACC":
                    ax = float(val.get('ax', val.get('x', 0)))
                    ay = float(val.get('ay', val.get('y', 0)))
                    az = float(val.get('az', val.get('z', 0)))
                    mag = math.sqrt(ax**2 + ay**2 + az**2)
                                                                                             
                    values.append(round(abs(mag - 1.0), 3))
                else:
                    raw_value = float(next(iter(val.values()), 0))
                                                                                                    
                    if s == "BVP":
                        values.append(abs(raw_value))
                    else:
                        values.append(raw_value)

            
            data_charts[s] = {"labels": labels, "values": values}
        except Exception as e:
            print(f"Errore sensore {s}: {e}")
            data_charts[s] = {"labels": [], "values": []}
    return data_charts

                             
@app.route('/register', methods=['GET', 'POST'])
def register():
    """
    Endpoint per la registrazione di un nuovo utente, accessibile solo agli admin.
    Permette di inserire username, password, id_utente, cellulare e le sessioni associate a basso/medio/alto, con validazione per evitare permutazioni errate.
    """
    if 'user' not in session or session.get('tipo') != 'admin':
        return "Accesso negato.", 403

    message = ""
    if request.method == 'POST':
        username = request.form.get('username')
        user_ref = db.collection('utenti').document(username)

        if not user_ref.get().exists:
            low_s = str(request.form.get("low_session", "")).strip()
            med_s = str(request.form.get("medium_session", "")).strip()
            high_s = str(request.form.get("high_session", "")).strip()

            chosen = [low_s, med_s, high_s]

                                                     
            if any(x not in ("01", "02", "03") for x in chosen) or len(set(chosen)) != 3:
                message = "Errore: seleziona una permutazione valida (01/02/03) per basso/medio/alto."
            else:
                user_ref.set({
                    'username': username,
                    'password': request.form.get('password'),
                    'id_utente': request.form.get('id_utente'),
                    'cellulare': request.form.get('cellulare'),
                    'low_session': low_s,
                    'medium_session': med_s,
                    'high_session': high_s
                })
                message = f"Utente {username} registrato con successo!"
        else:
            message = "Errore: Lo username esiste già."

    return render_template_string('''
    <html>
    <head>
        <title>Empatica E4 - Registrazione Utente</title>
        <style>
            body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
            .navbar { background: #1a73e8; color: white; padding: 15px 30px; display:flex; justify-content:space-between; align-items:center; box-shadow:0 2px 5px rgba(0,0,0,0.1); }
            .nav-links a { color:white; text-decoration:none; margin-left:20px; font-weight:500; font-size:0.9rem; }
            .nav-links a:hover { text-decoration: underline; }

            .page-wrap { min-height: calc(100vh - 70px); display:flex; justify-content:center; align-items:center; padding: 30px 0; }
            .card { background:white; padding:2rem; border-radius:12px; box-shadow:0 10px 25px rgba(0,0,0,0.1); width:350px; }

            h2 { color:#1a73e8; margin-top:0; text-align:center; }

            label { font-size:0.85rem; color:#555; }
            input, select {
                width:100%;
                margin-bottom:15px;
                padding:10px;
                border:1px solid #ddd;
                border-radius:6px;
                box-sizing:border-box;
                background:white;
            }

            .submit-btn {
                width:100%;
                padding:12px;
                background:#1a73e8;
                color:white;
                border:none;
                border-radius:6px;
                cursor:pointer;
                font-weight:bold;
            }

            .msg {
                padding:10px;
                margin-bottom:15px;
                border-radius:6px;
                background:#e8f0fe;
                color:#1a73e8;
                font-size:0.9rem;
                text-align:center;
            }

            .back-link {
                text-align:center;
                margin-top:15px;
            }

            .back-link a {
                color:#666;
                font-size:0.85rem;
                text-decoration:none;
            }
        </style>
    </head>
    <body>
        <div class="navbar">
            <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
            <div class="nav-links">
                <a href="/dashboard_admin">Dashboard</a>
                <a href="/live_admin">Dati in tempo reale</a>
                <a href="/statistics_admin">Statistiche</a>
                <a href="/register" style="text-decoration: underline;">Nuovo Utente</a>
                <a href="/logout" style="color:#ffcccc;">Logout</a>
            </div>
        </div>

        <div class="page-wrap">
            <div class="card">
                <h2>Registrazione Utente</h2>

                {% if msg %}
                    <div class="msg">{{ msg }}</div>
                {% endif %}

                <form method="post">
                    <label>Username:</label>
                    <input type="text" name="username" required>

                    <label>Password:</label>
                    <input type="password" name="password" required>

                    <label>ID Utente:</label>
                    <input type="text" name="id_utente" placeholder="Es: 02" required>

                    <label>Cellulare:</label>
                    <input type="text" name="cellulare">

                    <label>Sessione BASSO (low):</label>
                    <select name="low_session" required>
                        <option value="01">01</option>
                        <option value="02">02</option>
                        <option value="03">03</option>
                    </select>

                    <label>Sessione MEDIO (medium):</label>
                    <select name="medium_session" required>
                        <option value="01">01</option>
                        <option value="02">02</option>
                        <option value="03">03</option>
                    </select>

                    <label>Sessione ALTO (high):</label>
                    <select name="high_session" required>
                        <option value="01">01</option>
                        <option value="02">02</option>
                        <option value="03">03</option>
                    </select>

                    <input type="submit" value="Registra Utente" class="submit-btn">
                </form>

                <div class="back-link">
                    <a href="/dashboard_admin">← Dashboard</a>
                </div>
            </div>
        </div>
    </body>
    </html>
    ''', msg=message)



# Template HTML per la dashboard, con selezione dinamica di utente, sessione e sensore, e grafico aggiornabile.
HTML_DASHBOARD_ORIGINALE = '''
<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <title>Empatica E4 - Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
        .navbar { background: #1a73e8; color: white; padding: 15px 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }
        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
        .card-controls { background: white; padding: 20px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.05); margin-bottom: 25px; display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }
        select { padding: 10px; border-radius: 5px; border: 1px solid #ddd; font-size: 14px; background: white; }
        .chart-container { background: white; padding: 25px; border-radius: 12px; box-shadow: 0 10px 20px rgba(0,0,0,0.05); height: 500px; }
        .nav-links a { color: white; text-decoration: none; margin-left: 20px; font-weight: 500; font-size: 0.9rem; }
        .nav-links a:hover { text-decoration: underline; }
        h3 { margin-top: 0; color: #1a73e8; }
    </style>
</head>
<body>
    <div class="navbar">
        <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
        <div class="nav-links">
            {% if session['tipo'] == 'admin' %}
                <a href="/dashboard_admin">Dashboard</a>
                <a href="/live_admin">Dati in tempo reale</a>
                <a href="/statistics_admin">Statistiche</a>
                <a href="/register">Nuovo Utente</a>
            {% else %}
                <a href="/dashboard_utente">Dashboard</a>
                <a href="/statistics_utente">Statistiche</a>
            {% endif %}
            <a href="/logout" style="color: #ffcccc;">Logout</a>
        </div>
    </div>

    <div class="container">
        <div class="card-controls">
            {% if session['tipo'] == 'admin' %}
            <div>
                <label><b>Utente:</b></label>
                <select id="userSelect" onchange="update()">
                    {% for u in utenti %}
                    <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
                    {% endfor %}
                </select>
            </div>
            {% else %}
            <div>
                <label><b>ID Utente:</b> {{ session['id_utente'] }}</label>
            </div>
            {% endif %}

            <div>
                <label><b>Sensore:</b></label>
                <select id="sensorSelect" onchange="changeSensor()">
                    <option value="ACC">Accelerometro (Magnitudo)</option>
                    <option value="BVP">BVP (Blood Volume Pulse)</option>
                    <option value="EDA">EDA (Elettrodermica)</option>
                    <option value="HR">Frequenza Cardiaca (HR)</option>
                    <option value="IBI">IBI (Inter-Beat Interval)</option>
                    <option value="TEMP">Temperatura</option>
                </select>
            </div>

            <div>
                <label><b>Sessione:</b></label>
                <select id="sessSelect" onchange="update()">
                {% for label, sessid in session_options %}
                    <option value="{{ sessid }}" {% if sessid == selected_s %}selected{% endif %}>{{ label }}</option>
                {% endfor %}
                </select>
            </div>
        </div>

        <div class="chart-container">
            <h3 id="chartTitle">Caricamento grafico...</h3>
            <div style="height: 400px; position: relative;">
                <canvas id="mainChart"></canvas>
            </div>
        </div>
    </div>

    <script>
        const allData = {{ data_charts|tojson }};
        let currentChart = null;

        function render(sensorId) {
            const ctx = document.getElementById('mainChart').getContext('2d');
            const data = allData[sensorId];

            document.getElementById('chartTitle').innerText = sensorId + " - Dati in tempo reale";

            if (currentChart) currentChart.destroy();

            currentChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: data.labels,
                    datasets: [{
                        label: sensorId,
                        data: data.values,
                        borderColor: '#1a73e8',
                        backgroundColor: 'rgba(26, 115, 232, 0.1)',
                        borderWidth: 3,
                        tension: 0.4,
                        fill: true,
                        pointRadius: 3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: { beginAtZero: false, grid: { color: '#f0f0f0' } },
                        x: { grid: { display: false } }
                    }
                }
            });
        }

        function update() {
            const s = document.getElementById('sessSelect').value;

            if ("{{ session['tipo'] }}" === "admin") {
                const u = document.getElementById('userSelect').value;
                window.location.href = `/dashboard_admin?u=${u}&s=${s}`;
            } else {
                window.location.href = `/dashboard_utente?s=${s}`;
            }
        }

        function changeSensor() {
            const s = document.getElementById('sensorSelect').value;
            localStorage.setItem('lastSensor', s);
            render(s);
        }

        window.onload = () => {
            const last = localStorage.getItem('lastSensor') || 'HR';
            document.getElementById('sensorSelect').value = last;
            render(last);
        };

        setTimeout(() => location.reload(), 20000);
    </script>
</body>
</html>
'''

# Template HTML per la pagina delle statistiche, con visualizzazione di alert e threshold, e selezione dinamica di utente e sessione.
HTML_STATISTICHE = '''
<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="UTF-8">
  <title>Empatica E4 - Statistiche</title>
  <style>
    body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f4f7f9; margin: 0; color: #333; }
    .navbar { background: #1a73e8; color: white; padding: 15px 30px; display:flex; justify-content:space-between; align-items:center; box-shadow:0 2px 5px rgba(0,0,0,0.1); }
    .nav-links a { color:white; text-decoration:none; margin-left:20px; font-weight:500; font-size:0.9rem; }
    .nav-links a:hover { text-decoration: underline; }

    .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
    .card-controls {
      background:white;
      padding:20px;
      border-radius:10px;
      box-shadow:0 4px 6px rgba(0,0,0,0.05);
      margin-bottom:25px;
      display:flex;
      gap:20px;
      align-items:center;
      flex-wrap: wrap;
    }

    select { padding:10px; border-radius:5px; border:1px solid #ddd; font-size:14px; background:white; }

    .grid { display:grid; grid-template-columns: repeat(3, 1fr); gap:16px; }

    .card {
      background:white;
      padding:18px;
      border-radius:12px;
      box-shadow:0 10px 20px rgba(0,0,0,0.05);
    }

    .title { color:#1a73e8; font-weight:700; margin:0 0 10px; }

    .kpi { display:flex; flex-direction:column; gap:6px; font-size:0.95rem; }
    .kpi b { color:#555; width:120px; display:inline-block; }

    .muted { color:#888; font-style:italic; }

    .pill {
      display:inline-block;
      padding:6px 10px;
      border-radius:999px;
      background:#e8f0fe;
      color:#1a73e8;
      font-weight:600;
      font-size:0.85rem;
    }

    .alert-box {
      margin-top:12px;
      padding:12px 14px;
      border-radius:10px;
      background:#fdecec;
      border:1px solid #f5c2c2;
      color:#b3261e;
    }

    .alert-title {
      font-weight:700;
      margin-bottom:6px;
    }

    .alert-text {
      font-size:0.95rem;
      line-height:1.5;
    }

    @media (max-width: 980px) {
      .grid { grid-template-columns: repeat(2, 1fr); }
    }

    @media (max-width: 640px) {
      .grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>

  <div class="navbar">
    <h2 style="margin:0; font-size: 1.4rem;">Empatica E4 Dashboard</h2>
    <div class="nav-links">
      {% if session['tipo'] == 'admin' %}
        <a href="/dashboard_admin">Dashboard</a>
        <a href="/live_admin">Dati in tempo reale</a>
        <a href="/statistics_admin" style="text-decoration: underline;">Statistiche</a>
        <a href="/register">Nuovo Utente</a>
      {% else %}
        <a href="/dashboard_utente">Dashboard</a>
        <a href="/statistics_utente" style="text-decoration: underline;">Statistiche</a>
      {% endif %}
      <a href="/logout" style="color:#ffcccc;">Logout</a>
    </div>
  </div>

  <div class="container">
    <div class="card-controls">

      {% if session['tipo'] == 'admin' %}
      <div>
        <label><b>Utente:</b></label>
        <select id="userSelect" onchange="update()">
          {% for u in utenti %}
            <option value="{{ u }}" {% if u == selected_u %}selected{% endif %}>{{ u }}</option>
          {% endfor %}
        </select>
      </div>
      {% else %}
      <div>
        <label><b>ID Utente:</b> {{ session['id_utente'] }}</label>
      </div>
      {% endif %}

      <div>
        <label><b>Sessione:</b></label>
        <select id="sessSelect" onchange="update()">
        {% for label, sessid in session_options %}
            <option value="{{ sessid }}" {% if sessid == selected_s %}selected{% endif %}>{{ label }}</option>
        {% endfor %}
        </select>
      </div>

      <span class="muted">Se non vedi valori, attendi qualche secondo (buffer RAM).</span>
    </div>

    <div class="grid">
    {% for sensor, st in stats.items() %}
        <div class="card">
        <h3 class="title">{{ sensor }}</h3>

        {% if st %}
            <div class="kpi">
              <div><b>Campioni:</b> {{ st.count }}</div>
              <div><b>Media:</b> {{ '%.3f'|format(st.mean) }}</div>
              <div><b>Min:</b> {{ '%.3f'|format(st.min) }}</div>
              <div><b>Max:</b> {{ '%.3f'|format(st.max) }}</div>
              <div><b>Dev Std:</b> {{ '%.3f'|format(st.std) }}</div>
              <div class="muted">Ultimo update: {{ st.updated_at }}</div>

              {% if st.win %}
                <hr style="border:none; border-top:1px solid #eee; margin:12px 0;">
                <div class="muted" style="margin-bottom:6px;">
                  Finestra mobile ultimi {{ st.win.seconds }}s
                </div>

                <div class="kpi">
                  <div><b>Campioni:</b> {{ st.win.count }}</div>
                  <div><b>Media:</b> {{ '%.3f'|format(st.win.mean) }}</div>
                  <div><b>Min:</b> {{ '%.3f'|format(st.win.min) }}</div>
                  <div><b>Max:</b> {{ '%.3f'|format(st.win.max) }}</div>
                  <div><b>Dev Std:</b> {{ '%.3f'|format(st.win.std) }}</div>
                  <div class="muted">Update finestra: {{ st.win.updated_at }}</div>
                </div>
              {% else %}
                <div class="muted" style="margin-top:10px;">
                  Finestra mobile: in attesa dati/flush...
                </div>
              {% endif %}

              {% set mean_thr = mean_shift_thr_view[sensor] %}
              {% set mean_al  = mean_shift_alerts_view[sensor] %}

              <hr style="border:none; border-top:1px solid #eee; margin:12px 0;">

              <div class="muted" style="margin-bottom:6px;">
                Anomalia media (finestra vs globale):
                {% if mean_thr is not none %}
                  soglia = ±{{ '%.2f'|format(mean_thr) }}%
                {% else %}
                  (non definita)
                {% endif %}
              </div>

              {% if mean_al and mean_al|length > 0 %}
                <div class="alert-box">
                  <div class="alert-title">⚠ Superamento soglia</div>
                  <div class="alert-text">{{ mean_al[0] }}</div>
                </div>
              {% else %}
                <div class="muted" style="margin-top:10px;">
                  Media finestra coerente con media globale.
                </div>
              {% endif %}
            </div>

        {% else %}
            <div class="muted">
              Nessuna statistica disponibile (ancora in accumulo o nessun dato).
            </div>
        {% endif %}
        </div>
    {% endfor %}
    </div>
  </div>

<script>
  function update() {
    const s = document.getElementById('sessSelect').value;

    if ("{{ session['tipo'] }}" === "admin") {
      const u = document.getElementById('userSelect').value;
      window.location.href = `/statistics_admin?u=${u}&s=${s}`;
    } else {
      window.location.href = `/statistics_utente?s=${s}`;
    }
  }

  setTimeout(() => location.reload(), 5000);
</script>

</body>
</html>
'''

# Main entry point dell'app Flask, con debug attivo, registrazione degli shutdown hook per Telegram e avvio del polling, e infine avvio del server sulla porta 5000.
if __name__ == '__main__':
    app.debug = True
    register_shutdown_hooks()
    start_telegram_polling()
    app.run(host='0.0.0.0', port=5000, debug=True)
