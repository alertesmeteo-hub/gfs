#!/usr/bin/env bash
# Installation de evenements-majeurs.alertes-meteo.com sur le VPS OVH (Ubuntu, nginx, certbot déjà présents)
set -euo pipefail
DOM=evenements-majeurs.alertes-meteo.com
ROOT=/var/www/evenements-majeurs
sudo mkdir -p "$ROOT" && sudo chown "$USER" "$ROOT"
curl -fsSL -o /tmp/build_site.py https://raw.githubusercontent.com/alertesmeteo-hub/gfs/claude/modest-cannon-sidw4j/vps/build_site.py
echo "1/3 Téléchargement et génération du site (1 à 3 h, relançable sans perte)…"
python3 /tmp/build_site.py "$ROOT" 2>&1 | tee "$ROOT/_build.log" | grep --line-buffered -E "événements|TERMINÉ|ABSENT|ok [0-9]*0 "
echo "2/3 Configuration nginx…"
sudo tee /etc/nginx/sites-available/$DOM >/dev/null <<NGX
server {
    listen 80;
    server_name $DOM;
    root $ROOT;
    index index.html;
    location /_cache { deny all; }
    location / { try_files \$uri \$uri/ =404; }
}
NGX
sudo ln -sf /etc/nginx/sites-available/$DOM /etc/nginx/sites-enabled/$DOM
sudo nginx -t && sudo systemctl reload nginx
echo "3/3 Certificat HTTPS…"
sudo certbot --nginx -d $DOM --redirect --non-interactive --agree-tos --keep-until-expiring
echo "✅ En ligne : https://$DOM"
