#!/usr/bin/env bash
# Deploy Loyola Networking to the emstech VPS.
#
# Safe to re-run. It only touches its own service, its own nginx vhost, its own
# tunnel and its own database — nothing belonging to the EMS stack on the same
# box is read or written.
#
#   ./deploy/deploy.sh            push code, restart, verify
#   ./deploy/deploy.sh --deps     also reinstall Python dependencies
set -euo pipefail

HOST="${LOYOLA_HOST:-emstech}"
REMOTE="/root/loyola"
SITE="https://loyola.avlokai.com"
WITH_DEPS="${1:-}"

cd "$(dirname "$0")/.."

echo "==> syncing application code to $HOST"
ssh "$HOST" "mkdir -p $REMOTE/app/{routers,services,ocr,sql,static/css,static/js} $REMOTE/scripts"
scp -q app/*.py "$HOST:$REMOTE/app/"
scp -q app/routers/*.py "$HOST:$REMOTE/app/routers/"
scp -q app/services/*.py "$HOST:$REMOTE/app/services/"
scp -q app/ocr/*.py "$HOST:$REMOTE/app/ocr/"
scp -q app/sql/*.sql "$HOST:$REMOTE/app/sql/"
scp -q app/static/css/*.css "$HOST:$REMOTE/app/static/css/"
scp -q app/static/js/*.js "$HOST:$REMOTE/app/static/js/"
scp -q app/static/favicon.svg "$HOST:$REMOTE/app/static/"
scp -rq app/templates "$HOST:$REMOTE/app/"
scp -q scripts/*.py "$HOST:$REMOTE/scripts/"
scp -q requirements.txt "$HOST:$REMOTE/" 2>/dev/null || true

echo "==> syncing service definitions"
scp -q deploy/loyola.service "$HOST:/etc/systemd/system/loyola.service"
scp -q deploy/nginx-loyola.conf "$HOST:/etc/nginx/sites-available/loyola"

if [ "$WITH_DEPS" = "--deps" ]; then
  echo "==> installing Python dependencies"
  ssh "$HOST" "$REMOTE/venv/bin/pip install -q -r $REMOTE/requirements.txt"
fi

echo "==> publishing static assets and restarting"
ssh "$HOST" "set -e
  # nginx runs as www-data and cannot read anything under /root.
  mkdir -p /var/www/loyola
  rsync -a --delete $REMOTE/app/static/ /var/www/loyola/static/
  chown -R www-data:www-data /var/www/loyola

  nginx -t >/dev/null
  systemctl daemon-reload
  systemctl reload nginx
  systemctl restart loyola.service
"

echo "==> waiting for the service to come back"
for i in $(seq 1 20); do
  if ssh "$HOST" "curl -sf -m 5 http://127.0.0.1:8011/healthz >/dev/null 2>&1"; then
    break
  fi
  sleep 2
done

echo "==> health"
ssh "$HOST" "curl -s -m 10 http://127.0.0.1:8011/healthz; echo"
ssh "$HOST" "systemctl is-active loyola.service cloudflared-loyola.service nginx | tr '\n' ' '; echo"

echo "==> confirming the EMS services were not disturbed"
ssh "$HOST" "systemctl is-active ems-api.service ems-backend.service email-automation.service | tr '\n' ' '; echo"

echo
echo "deployed: $SITE"
