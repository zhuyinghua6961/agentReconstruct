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
fi

openssl genrsa -out "$SERVER_KEY" 2048
openssl req \
  -new \
  -key "$SERVER_KEY" \
  -out "$SERVER_CSR" \
  -subj "/C=CN/O=LiFeO4Agent/CN=$DOMAIN" \
  -addext "subjectAltName=DNS:$DOMAIN,DNS:localhost,IP:$IP_ADDRESS,IP:127.0.0.1"

openssl x509 \
  -req \
  -in "$SERVER_CSR" \
  -CA "$ROOT_CERT" \
  -CAkey "$ROOT_KEY" \
  -CAcreateserial \
  -out "$SERVER_CERT" \
  -days "$VALID_DAYS" \
  -sha256 \
  -copy_extensions copy

chmod 600 "$ROOT_KEY" "$SERVER_KEY"
chmod 644 "$ROOT_CERT" "$SERVER_CERT"
rm -f "$SERVER_CSR"

openssl x509 -in "$SERVER_CERT" -noout -subject -issuer -dates -ext subjectAltName
