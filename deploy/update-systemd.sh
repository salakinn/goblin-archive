#!/usr/bin/env bash
set -Eeuo pipefail

tag="${1:-}"
root="${GOBLIN_UPDATE_ROOT:-}"
service="${GOBLIN_UPDATE_SERVICE:-goblin-archive.service}"
repo="${GOBLIN_UPDATE_REPO:-https://github.com/salakinn/goblin-archive.git}"
health_url="${GOBLIN_UPDATE_HEALTH_URL:-http://127.0.0.1:8000/api/health}"
health_attempts="${GOBLIN_UPDATE_HEALTH_ATTEMPTS:-30}"
health_interval="${GOBLIN_UPDATE_HEALTH_INTERVAL:-2}"

[[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Ungültiger Versionstag" >&2; exit 2; }
[[ "$root" = /* && -d "$root" ]] || { echo "GOBLIN_UPDATE_ROOT muss ein bestehender absoluter Pfad sein" >&2; exit 2; }
[[ "$service" =~ ^[A-Za-z0-9_.@-]+\.service$ ]] || { echo "Ungültiger Dienstname" >&2; exit 2; }
[[ "$health_attempts" =~ ^[0-9]+$ && "$health_attempts" -ge 1 && "$health_attempts" -le 120 ]] || { echo "Ungültige Zahl für Gesundheitschecks" >&2; exit 2; }
[[ "$health_interval" =~ ^[0-9]+$ && "$health_interval" -le 30 ]] || { echo "Ungültiger Abstand für Gesundheitschecks" >&2; exit 2; }

mkdir -p "$root/releases"
exec 9>"$root/.update.lock"
flock -n 9 || { echo "Ein Update läuft bereits" >&2; exit 1; }

target="$root/releases/$tag"
if [[ -d "$target" && ! -f "$target/.ready" ]]; then
  rm -rf "$target"
fi
if [[ ! -d "$target" ]]; then
  trap 'rm -rf "$target"' EXIT
  git clone --quiet --depth 1 --single-branch --branch "$tag" "$repo" "$target"
  actual="$(python3 - "$target/pyproject.toml" <<'PY'
import sys, tomllib
with open(sys.argv[1], 'rb') as source:
    print(tomllib.load(source)['project']['version'])
PY
)"
  [[ "v$actual" = "$tag" ]] || { echo "Tag und Paketversion stimmen nicht überein" >&2; exit 1; }
  python3 -m venv "$target/.venv"
  "$target/.venv/bin/pip" install --no-cache-dir "$target"
  npm ci --prefix "$target/frontend"
  npm run build --prefix "$target/frontend"
  mkdir -p "$target/backend/static"
  cp -a "$target/frontend/dist/." "$target/backend/static/"
  touch "$target/.ready"
  trap - EXIT
fi
[[ -f "$target/.ready" ]] || { echo "Version ist unvollständig installiert" >&2; exit 1; }

previous="$(readlink "$root/current" || true)"
mkdir -p "$root/backups"
backup="$(mktemp -d "$root/backups/$tag-XXXXXXXX")"
next_link="$root/.current-next-$$"

rollback() {
  trap - ERR
  systemctl --user stop "$service" || true
  if [[ -f "$backup/goblin.db" ]]; then
    rm -f "$root/data/goblin.db-wal" "$root/data/goblin.db-shm"
    cp -p "$backup/goblin.db" "$root/data/goblin.db"
  fi
  if [[ -f "$backup/ai-settings.json" ]]; then
    cp -p "$backup/ai-settings.json" "$root/data/ai-settings.json"
  fi
  if [[ -f "$backup/auth.db" ]]; then
    rm -f "$root/data/auth.db-wal" "$root/data/auth.db-shm"
    cp -p "$backup/auth.db" "$root/data/auth.db"
  fi
  rm -f "$next_link"
  if [[ -n "$previous" ]]; then
    ln -s "$previous" "$next_link"
    mv -Tf "$next_link" "$root/current"
    systemctl --user restart "$service" || true
  else
    rm -f "$root/current"
  fi
}

trap rollback ERR
systemctl --user stop "$service"
if [[ -f "$root/data/goblin.db" ]]; then
  python3 - "$root/data/goblin.db" "$backup/goblin.db" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
fi
if [[ -f "$root/data/ai-settings.json" ]]; then
  cp -p "$root/data/ai-settings.json" "$backup/ai-settings.json"
fi
if [[ -f "$root/data/auth.db" ]]; then
  python3 - "$root/data/auth.db" "$backup/auth.db" <<'PY'
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
fi
ln -s "releases/$tag" "$next_link"
mv -Tf "$next_link" "$root/current"

if ! systemctl --user start "$service"; then
  rollback
  echo "Dienststart fehlgeschlagen; vorherige Version wiederhergestellt" >&2
  exit 1
fi

for ((attempt = 0; attempt < health_attempts; attempt++)); do
  if curl --silent --fail --max-time 3 "$health_url" | python3 -c 'import json,sys; expected=sys.argv[1].removeprefix("v"); sys.exit(json.load(sys.stdin).get("version", "").removeprefix("v") != expected)' "$tag" 2>/dev/null; then
    trap - ERR
    echo "Goblin Archivar $tag läuft"
    exit 0
  fi
  sleep "$health_interval"
done

rollback
echo "Neue Version wurde nicht gesund; vorherige Version wiederhergestellt" >&2
exit 1
