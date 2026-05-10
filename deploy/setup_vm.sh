#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y python3-venv python3-pip nginx

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo cp deploy/nginx/empatica.conf /etc/nginx/sites-available/empatica
sudo ln -sf /etc/nginx/sites-available/empatica /etc/nginx/sites-enabled/empatica
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx

sudo cp deploy/systemd/empatica@.service /etc/systemd/system/empatica@.service
sudo systemctl daemon-reload
sudo systemctl enable empatica@$(whoami)
sudo systemctl restart empatica@$(whoami)

echo "OK: apri http://$(curl -s ifconfig.me)/"
