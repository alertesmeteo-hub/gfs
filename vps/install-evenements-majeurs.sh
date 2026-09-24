#!/usr/bin/env bash
# Installation de evenements-majeurs.alertes-meteo.com sur le VPS OVH (Ubuntu, nginx, certbot déjà présents)
set -euo pipefail
DOM=evenements-majeurs.alertes-meteo.com
ROOT=/var/www/evenements-majeurs
sudo mkdir -p "$ROOT" && sudo chown "$USER" "$ROOT"
rm -rf /tmp/em && mkdir -p /tmp/em
curl -fsSL https://codeload.github.com/alertesmeteo-hub/gfs/tar.gz/refs/heads/claude/modest-cannon-sidw4j | tar xz -C /tmp/em --strip-components=2 --wildcards '*/vps/*'
ls /tmp/em/resumes | wc -l | xargs echo 'Lots de résumés :'; ls /tmp/em/articles 2>/dev/null | wc -l | xargs echo 'Articles :'
echo "1/3 Téléchargement des 469 fiches et génération du site (environ 30 min à 1 h, relançable sans perte)…"
python3 /tmp/em/build_site.py "$ROOT" 2>&1 | tee "$ROOT/_build.log" | grep --line-buffered -E "événements|TERMINÉ|ABSENT|ok [0-9]*0 "
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
