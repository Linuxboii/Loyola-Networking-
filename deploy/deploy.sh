#!/usr/bin/env bash
# Deploy Loyola Networking to an operator-supplied host.
#
# Safe to re-run. It only touches its own service, its own nginx vhost, its own
# service and database. Host access and co-hosted services remain operator-only.
#
#   ./deploy/deploy.sh                 push code, restart, verify
#   ./deploy/deploy.sh --deps          also reinstall Python dependencies
#   ./deploy/deploy.sh --apk FILE …    also publish that Android build, so the
#                                      phones already out there update themselves
#
# The --apk form takes the same flags as scripts/publish_release.py after the
# file, e.g.:
#
#   ./deploy/deploy.sh --apk mobile/build/app/outputs/flutter-apk/app-release.apk \
#       --notes "Fixes the crash when a post has no image."
set -euo pipefail

HOST="${LOYOLA_HOST:?Set LOYOLA_HOST to the SSH host alias}"
REMOTE="/root/loyola"
SITE="https://loyola.avlokai.com"

WITH_DEPS=""
APK=""
PUBLISH_ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --deps) WITH_DEPS="--deps"; shift ;;
    --apk) APK="$2"; shift 2 ;;
    *) PUBLISH_ARGS+=("$1"); shift ;;
  esac
done

cd "$(dirname "$0")/.."

if [ -n "$APK" ] && [ ! -f "$APK" ]; then
  echo "no APK at $APK" >&2
  exit 1
fi

echo "==> syncing application code to $HOST"
ssh "$HOST" "mkdir -p $REMOTE/app/{routers,services,ocr,sql,api/routes,static/css,static/js} $REMOTE/scripts"
scp -q app/*.py "$HOST:$REMOTE/app/"
scp -q app/routers/*.py "$HOST:$REMOTE/app/routers/"
scp -q app/services/*.py "$HOST:$REMOTE/app/services/"
# The JSON API tree the Android app is built against, including the release
# channel that tells it when to update itself.
scp -q app/api/*.py "$HOST:$REMOTE/app/api/"
scp -q app/api/routes/*.py "$HOST:$REMOTE/app/api/routes/"
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

if [ -n "$APK" ]; then
  # The version name and build number come from the pubspec *here*: the server
  # has no copy of the Flutter project, and the number the app reports about
  # itself has to be the number the channel compares it against.
  VERSION=$(sed -n 's/^version:[[:space:]]*\([0-9.]*\)+\([0-9]*\).*/\1/p' mobile/pubspec.yaml)
  BUILD=$(sed -n 's/^version:[[:space:]]*\([0-9.]*\)+\([0-9]*\).*/\2/p' mobile/pubspec.yaml)
  if [ -z "$VERSION" ] || [ -z "$BUILD" ]; then
    echo "could not read 'version: x.y.z+n' from mobile/pubspec.yaml" >&2
    exit 1
  fi

  NAME="$(basename "$APK")"
  EXTRA=""
  if [ ${#PUBLISH_ARGS[@]} -gt 0 ]; then
    EXTRA=$(printf ' %q' "${PUBLISH_ARGS[@]}")
  fi

  echo "==> publishing $VERSION (build $BUILD) to the update channel"
  ssh "$HOST" "mkdir -p $REMOTE/var/releases/android/incoming"
  scp -q "$APK" "$HOST:$REMOTE/var/releases/android/incoming/$NAME"
  # Publishing comes last on purpose: the manifest only names a build once the
  # server that has to serve it is already running the new code.
  ssh "$HOST" "cd $REMOTE && venv/bin/python scripts/publish_release.py \
    --apk var/releases/android/incoming/$NAME --version $VERSION --build $BUILD$EXTRA"
  ssh "$HOST" "rm -f $REMOTE/var/releases/android/incoming/$NAME"
fi

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

echo
echo "deployed: $SITE"
