#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="${FRONTEND_DIR:-$ROOT_DIR/frontend-vue}"

SSH_KEY_PATH="${SSH_KEY_PATH:-/home/cqy/.ssh/aliyun_ecs}"
REMOTE_USER_HOST="${REMOTE_USER_HOST:-zyh@182.92.69.36}"
REMOTE_HOME="${REMOTE_HOME:-}"
REMOTE_SOURCE_DIR="${REMOTE_SOURCE_DIR:-highthinking_frontend_src/frontend-vue}"
REMOTE_NGINX_ROOT="${REMOTE_NGINX_ROOT:-highthinking_frontend_nginx}"
PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://182.92.69.36:9093}"
RELEASE_TAG="${RELEASE_TAG:-$(date +%Y%m%d%H%M%S)}"
RELEASE_KEEP_COUNT="${RELEASE_KEEP_COUNT:-5}"
SYNC_DELETE="${SYNC_DELETE:-1}"

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

if [[ ! -d "$FRONTEND_DIR" ]]; then
  echo "frontend directory not found: $FRONTEND_DIR" >&2
  exit 1
fi

if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
  echo "frontend package.json not found: $FRONTEND_DIR/package.json" >&2
  exit 1
fi

require_bin ssh
require_bin rsync
require_bin curl

SSH_OPTIONS=(
  -i "$SSH_KEY_PATH"
  -o BatchMode=yes
  -o StrictHostKeyChecking=yes
  -o ConnectTimeout=10
)

printf -v RSYNC_RSH 'ssh -i %q -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10' "$SSH_KEY_PATH"

if [[ -z "$REMOTE_HOME" ]]; then
  REMOTE_HOME="$(ssh "${SSH_OPTIONS[@]}" "$REMOTE_USER_HOST" 'printf %s "$HOME"')"
fi

if [[ -z "$REMOTE_HOME" ]]; then
  echo "failed to resolve remote home directory for $REMOTE_USER_HOST" >&2
  exit 1
fi

normalize_remote_path() {
  local path="$1"
  if [[ "$path" == "~" || "$path" == '$HOME' ]]; then
    printf '%s\n' "$REMOTE_HOME"
    return 0
  fi
  if [[ "$path" == "~/"* ]]; then
    printf '%s/%s\n' "$REMOTE_HOME" "${path#~/}"
    return 0
  fi
  if [[ "$path" == '$HOME/'* ]]; then
    printf '%s/%s\n' "$REMOTE_HOME" "${path#\$HOME/}"
    return 0
  fi
  if [[ "$path" == /* ]]; then
    printf '%s\n' "$path"
    return 0
  fi
  printf '%s/%s\n' "$REMOTE_HOME" "${path#./}"
}

REMOTE_SOURCE_DIR="$(normalize_remote_path "$REMOTE_SOURCE_DIR")"
REMOTE_NGINX_ROOT="$(normalize_remote_path "$REMOTE_NGINX_ROOT")"

DELETE_ARGS=()
if [[ "$SYNC_DELETE" == "1" || "$SYNC_DELETE" == "true" || "$SYNC_DELETE" == "yes" ]]; then
  DELETE_ARGS+=(--delete)
fi

echo "preparing remote directories on $REMOTE_USER_HOST"
ssh "${SSH_OPTIONS[@]}" "$REMOTE_USER_HOST" \
  "mkdir -p \"$REMOTE_SOURCE_DIR\" \"$REMOTE_NGINX_ROOT/releases\" \"$REMOTE_NGINX_ROOT/runtime\" \"$REMOTE_NGINX_ROOT/logs\""

echo "syncing frontend source to $REMOTE_USER_HOST:$REMOTE_SOURCE_DIR"
rsync -az "${DELETE_ARGS[@]}" \
  --exclude node_modules \
  --exclude dist \
  --exclude .runtime \
  --exclude .git \
  --exclude .DS_Store \
  -e "$RSYNC_RSH" \
  "$FRONTEND_DIR/" \
  "$REMOTE_USER_HOST:$REMOTE_SOURCE_DIR/"

echo "building frontend remotely and switching dist release=$RELEASE_TAG"
ssh "${SSH_OPTIONS[@]}" "$REMOTE_USER_HOST" \
  "REMOTE_SOURCE_DIR=\"$REMOTE_SOURCE_DIR\" REMOTE_NGINX_ROOT=\"$REMOTE_NGINX_ROOT\" RELEASE_TAG=\"$RELEASE_TAG\" RELEASE_KEEP_COUNT=\"$RELEASE_KEEP_COUNT\" bash -s" <<'EOF'
set -euo pipefail

require_bin() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_bin npm
require_bin find
require_bin sort

REMOTE_RELEASES_DIR="$REMOTE_NGINX_ROOT/releases"
REMOTE_RELEASE_DIR="$REMOTE_RELEASES_DIR/$RELEASE_TAG"
REMOTE_DIST_LINK="$REMOTE_NGINX_ROOT/dist"

cd "$REMOTE_SOURCE_DIR"

if [[ -f package-lock.json ]]; then
  npm ci --no-audit --no-fund
else
  npm install --no-audit --no-fund
fi

npm run build

if [[ ! -f dist/index.html ]]; then
  echo "remote build did not produce dist/index.html" >&2
  exit 1
fi

mkdir -p "$REMOTE_RELEASE_DIR"
cp -a dist/. "$REMOTE_RELEASE_DIR/"

if [[ -L "$REMOTE_DIST_LINK" ]]; then
  ln -sfn "$REMOTE_RELEASE_DIR" "$REMOTE_NGINX_ROOT/dist.next"
  mv -Tf "$REMOTE_NGINX_ROOT/dist.next" "$REMOTE_DIST_LINK"
elif [[ -d "$REMOTE_DIST_LINK" ]]; then
  mv "$REMOTE_DIST_LINK" "$REMOTE_NGINX_ROOT/dist.backup.$RELEASE_TAG"
  ln -s "$REMOTE_RELEASE_DIR" "$REMOTE_DIST_LINK"
else
  ln -s "$REMOTE_RELEASE_DIR" "$REMOTE_DIST_LINK"
fi

if [[ "$RELEASE_KEEP_COUNT" =~ ^[0-9]+$ ]] && (( RELEASE_KEEP_COUNT > 0 )); then
  mapfile -t existing_releases < <(find "$REMOTE_RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d | sort)
  if (( ${#existing_releases[@]} > RELEASE_KEEP_COUNT )); then
    prune_count=$((${#existing_releases[@]} - RELEASE_KEEP_COUNT))
    for old_release in "${existing_releases[@]:0:prune_count}"; do
      rm -rf "$old_release"
    done
  fi
fi

echo "remote build complete"
echo "remote_source=$REMOTE_SOURCE_DIR"
echo "remote_release=$REMOTE_RELEASE_DIR"
echo "remote_dist=$REMOTE_DIST_LINK"
EOF

echo "verifying public health via $PUBLIC_BASE_URL/health"
curl -fsS --connect-timeout 5 --max-time 10 "$PUBLIC_BASE_URL/health" >/dev/null

echo "frontend synced and deployed successfully"
echo "remote_user_host=$REMOTE_USER_HOST"
echo "remote_source_dir=$REMOTE_SOURCE_DIR"
echo "remote_nginx_root=$REMOTE_NGINX_ROOT"
echo "release_tag=$RELEASE_TAG"
