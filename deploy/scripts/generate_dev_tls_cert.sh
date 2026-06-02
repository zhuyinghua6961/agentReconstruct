#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT_DIR/deploy/certs}"
DOMAIN="${1:-lifeo4.agent.test}"
IP_ADDRESS="${2:-172.19.14.204}"
VALID_DAYS="${VALID_DAYS:-825}"
CA_VALID_DAYS="${CA_VALID_DAYS:-3650}"

mkdir -p "$CERT_DIR"

ROOT_KEY="$CERT_DIR/rootCA.key"
ROOT_CERT="$CERT_DIR/rootCA.pem"
SERVER_KEY="$CERT_DIR/privkey.pem"
SERVER_CSR="$CERT_DIR/server.csr"
SERVER_CERT="$CERT_DIR/fullchain.pem"

cert_pubkey_sha256() {
  openssl x509 -in "$1" -pubkey -noout \
    | openssl pkey -pubin -outform DER \
    | openssl sha256 \
    | awk '{print $2}'
}

key_pubkey_sha256() {
  openssl pkey -in "$1" -pubout -outform DER \
    | openssl sha256 \
    | awk '{print $2}'
}

if [[ ! -f "$ROOT_KEY" || ! -f "$ROOT_CERT" ]]; then
  openssl genrsa -out "$ROOT_KEY" 4096
  openssl req \
    -x509 \
    -new \
    -nodes \
    -key "$ROOT_KEY" \
    -sha256 \
    -days "$CA_VALID_DAYS" \
    -out "$ROOT_CERT" \
    -subj "/C=CN/O=LiFeO4Agent/CN=LiFeO4Agent Internal Test Root CA"
else
  root_cert_hash="$(cert_pubkey_sha256 "$ROOT_CERT")"
  root_key_hash="$(key_pubkey_sha256 "$ROOT_KEY")"
  if [[ "$root_cert_hash" != "$root_key_hash" ]]; then
    echo "root CA certificate and private key do not match: $ROOT_CERT $ROOT_KEY" >&2
    echo "remove both rootCA.pem and rootCA.key, then rerun this script to regenerate a matched CA" >&2
    exit 1
  fi
fi

TMP_DIR="$(mktemp -d "$CERT_DIR/.tlsgen.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

TMP_SERVER_KEY="$TMP_DIR/privkey.pem"
TMP_SERVER_CSR="$TMP_DIR/server.csr"
TMP_SERVER_CERT="$TMP_DIR/fullchain.pem"
TMP_SERVER_EXT="$TMP_DIR/server-ext.cnf"

cat > "$TMP_SERVER_EXT" <<EOF
[v3_req]
subjectAltName = DNS:$DOMAIN,DNS:localhost,IP:$IP_ADDRESS,IP:127.0.0.1
EOF

openssl genrsa -out "$TMP_SERVER_KEY" 2048
openssl req \
  -new \
  -key "$TMP_SERVER_KEY" \
  -out "$TMP_SERVER_CSR" \
  -subj "/C=CN/O=LiFeO4Agent/CN=$DOMAIN"

openssl x509 \
  -req \
  -in "$TMP_SERVER_CSR" \
  -CA "$ROOT_CERT" \
  -CAkey "$ROOT_KEY" \
  -CAcreateserial \
  -out "$TMP_SERVER_CERT" \
  -days "$VALID_DAYS" \
  -sha256 \
  -extfile "$TMP_SERVER_EXT" \
  -extensions v3_req

server_cert_hash="$(cert_pubkey_sha256 "$TMP_SERVER_CERT")"
server_key_hash="$(key_pubkey_sha256 "$TMP_SERVER_KEY")"
if [[ "$server_cert_hash" != "$server_key_hash" ]]; then
  echo "generated server certificate and private key do not match" >&2
  exit 1
fi

mv "$TMP_SERVER_KEY" "$SERVER_KEY"
mv "$TMP_SERVER_CERT" "$SERVER_CERT"
chmod 600 "$ROOT_KEY" "$SERVER_KEY"
chmod 644 "$ROOT_CERT" "$SERVER_CERT"
rm -f "$SERVER_CSR"

openssl x509 -in "$SERVER_CERT" -noout -subject -issuer -dates -ext subjectAltName
