# IaaS (Compute Engine) + Firestore con layout stile FatigueSet

## Layout Firestore
I dati sono salvati con struttura gerarchica simile al dataset:

- `participants/{participantId}` (es. `01`)
  - `sessions/{sessionId}` (es. `01`)
    - `wrist_acc/{sample}`
    - `wrist_bvp/{sample}`
    - `wrist_eda/{sample}`
    - `wrist_hr/{sample}`
    - `wrist_ibi/{sample}`
    - `wrist_skin_temperature/{sample}`
    - `weekly_stats/{sensor}` (statistiche ultimi 7 giorni)

Il client converte `P01 -> 01`, quindi su Firestore userai **solo numeri** (`01`, `02`, ...).

## Avvio locale
```bash
pip install -r requirements.txt
python server.py
python client.py
```

## Deploy su VM (consigliato)
1) Copia la repo nella VM in `~/app`
2) Esegui:
```bash
cd ~/app
chmod +x deploy/setup_vm.sh
./deploy/setup_vm.sh
```

## Client verso VM
```bash
export SERVER_URL=http://<IP_PUBBLICO_VM>/data
python client.py
```

## Note
- Le credenziali Firestore devono arrivare via ADC: su Compute Engine è il Service Account della VM.
- Non includere chiavi JSON nel repo.
