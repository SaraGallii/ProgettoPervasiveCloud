# Deploy su Google Cloud (IaaS - Compute Engine) + Firestore

Questo progetto è la versione *IaaS* del prototipo:
- Server su **Compute Engine VM**
- Persistenza su **Cloud Firestore (Native mode)**

## Cosa cambia rispetto alla versione locale
- SQLite rimosso: i dati finiscono in Firestore.
- Le 2 basi dati sono rese come 2 **collezioni** Firestore:
  - `dati_sensori` (raw)
  - `metriche_settimanali` (stats)

## Collezioni
- `dati_sensori`: un documento per ogni riga inviata dal client
- `index_users`: indice per dropdown utenti e sessioni
- `utenti`: login demo
- `metriche_settimanali`: statistiche calcolate (ricalcolate ad ogni visita)

## Prerequisiti (GCP)
1) Abilita Firestore (Native mode) e l'API.
2) Associa alla VM un Service Account con permessi Firestore (es. `roles/datastore.user` o `roles/datastore.editor`).
3) Evita di includere chiavi JSON nel repo: su Compute Engine usa ADC.

## VM (gcloud) – esempio
```bash
gcloud compute instances create empatica-vm   --zone=europe-west8-a   --machine-type=e2-medium   --tags=flask-app

# Consigliato esporre solo 80 con Nginx
gcloud compute firewall-rules create allow-http-flask   --allow tcp:80   --target-tags flask-app
```

## Install & Run sulla VM
Copia il progetto nella VM in `~/app`, poi:
```bash
cd ~/app
chmod +x deploy/setup_vm.sh
./deploy/setup_vm.sh
```

Apri: `http://<IP_PUBBLICO_VM>/`

## Client
In locale (o sulla VM):
```bash
export SERVER_URL=http://<IP_PUBBLICO_VM>/data
python client.py
```

## Nota sugli indici Firestore
La dashboard fa query con `where(user) + where(session) + where(sensor) + order_by(ts_ms)`.
Firestore potrebbe richiedere un **composite index**: se accade, l'errore contiene un link diretto per crearlo.
