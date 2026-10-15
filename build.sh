#!/usr/bin/env bash
# Build de Cloudflare Pages: copia solo el sitio web a dist/ y deja fuera whatsapp-bot/.
# Configuración en Pages: Build command = "bash build.sh", Build output directory = "dist".
set -euo pipefail
rm -rf dist
mkdir dist
for f in *; do
  case "$f" in
    whatsapp-bot|dist|build.sh) ;;
    *) cp -R "$f" dist/ ;;
  esac
done
echo "Sitio copiado a dist/:" && ls dist
