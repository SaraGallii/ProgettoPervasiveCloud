# ProgettoPervasiveCloud

#INFO UTILI PER ESAME. PUNTO 1
Aprire due terminali in contemporanea e digitare:
python sensor.py (client)
python server.py (server)

http://127.0.0.1:5000/data

#FUNZIONAMENTO HTTP. PUNTO 1
La comunicazione avviene tramite HTTP (HyperText Transfer Protocol).Nel Client (sensor.py) il protocollo è rappresentato dalla libreria requests. Quando si scrive requests.post(...), s sta inviando un "pacchetto" HTTP di tipo POST (usato per inviare dati) verso il server.
Nel Server (server.py) il protocollo è gestito da Flask. Flask rimane "in ascolto" sulla porta 5000 aspettando messaggi che seguano le regole dell'HTTP.

#SPIEGAZIONE DI SENSOR_CONFIGS. PUNTO 1
I sensori reali dell'Empatica E4 campionano i dati a frequenze diverse (Hertz). Per simulare un comportamento reale, il codice deve "aspettare" un tot di tempo prima di leggere la riga successiva del CSV. Grazie a sensor_configs (in sensor.py) viene simulato un sensore vero; il server riceve i dati con la stessa cadenza con cui verrebbero generati dal braccialetto. Inoltre si evita il crash perchè se inviassi milioni di righe istantaneamente, il server Flask probabilmente smetterebbe di rispondere. Viene poi mantenuto l'ordine, si sa esattamente quale file corrisponde a quale sensore.

File,Nome,Intervallo(int),Spiegazione(Hz)
wrist_acc.csv,ACC,0.031s,L'accelerometro produce circa 32 dati al secondo (1/32≈0.031).
wrist_bvp.csv,BVP,0.015s,"Il segnale del volume del sangue è molto veloce, circa 64Hz (1/64≈0.015).
wrist_eda.csv,EDA,0.25s,"L'attività elettrodermica è lenta, 4 dati al secondo (1/4=0.25).
wrist_hr.csv,HR,1.0s,La frequenza cardiaca viene aggiornata una volta al secondo (1Hz).
wrist_temp.csv,TEMP,0.25s,La temperatura viene letta 4 volte al secondo (4Hz).

#PUNTO 2
Installare SQLite Viewer (da Exstension su VisualStudioCode) per visualizzare il database nel quale vengono salvati i dati in tempo reale.

#PUNTO 3. 

Apriamo più terminali:

CON PERCORSO ASSOLUTO
python server.py
python sensor_pt3.py C:\Users\galli\OneDrive\Desktop\fatigueset\fatigueset\01
python sensor_pt3.py C:\Users\galli\OneDrive\Desktop\fatigueset\fatigueset\05 

CON PERCORSO RELATIVO
python server.py
python sensor_pt3.py fatigueset\01
python sensor_pt3.py fatigueset\02

"Inserire dinamicamente un client" significa che se io apro due terminali diversi e lancio lo script due volte su due cartelle diverse, il server deve essere in grado di gestirli contemporaneamente senza confondersi.

#PUNTO 4
python sensor.py
python server_pt4.py
http://127.0.0.1:5000/login

-----------------------------------------------------------------------------------
Non devi eliminare il file Empatica_E4_wristband.db dalla cartella del progetto. Se lo elimini, cancelli fisicamente il "cervello" del programma e a quel punto dovresti ricreare gli utenti da capo.
----------------------------------------------------------------------------------------
Allooora i dati inseriti nei grafici sono ordinati per timestamp

#PUNTO 5
Per ogni utente, per ognuno dei parametri, il server esegue tre operazioni matematiche sui dati raccolti negli ultimi 7 giorni. Media, Moda e Mediana.Salviamo le statistiche nel db statistiche_settimanali.

python sensor.py
python server_pt5.py

http://127.0.0.1:5000/statistics

FILE PROVA
run : 
python sensor.py
python Prova.py
http://127.0.0.1:5000/login

-----------------------------------------------------------------------------------

COMANDI PER ESEGUIRE IL PROGRAMMA SU APP ENGINE (GOOGLE CLOUD PLATFORM):

gcloud init
gcloud config set project progetto-pccloud-1
gcloud services enable appengine.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com

# (consigliato) controlla se App Engine esiste già
gcloud app describe

# esegui SOLO se app describe fallisce
gcloud app create --region=europe-west

git clone https://github.com/SaraGallii/ProgettoPervasiveCloud
cd ProgettoPervasiveCloud
cd server

gcloud app deploy
gcloud app browse


PIPELINE PROGRAMMI:

server/
    main.py
    requirements.txt
    app.yaml
client_iot.py

