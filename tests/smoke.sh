#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

cat >"$TMP_DIR/links.txt" <<'EOF'
vless://11111111-1111-1111-1111-111111111111@example.com:443?security=tls&type=tcp&sni=example.com
vless://22222222-2222-2222-2222-222222222222@example.org:443?security=tls&type=xhttp&sni=example.org&path=%2Fapi
EOF

cat >"$TMP_DIR/sing-box.json" <<'EOF'
{"outbounds":[{"type":"direct","tag":"direct"}]}
EOF

python3 -m py_compile \
  "$ROOT_DIR/src/vless-vpn-manager" \
  "$ROOT_DIR/src/update-remnawave-nodes"

python3 -m unittest -v "$ROOT_DIR/tests/test_dependencies.py"

VLESS_LINKS_FILE="$TMP_DIR/links.txt" \
VLESS_MANAGER_CORE="$ROOT_DIR/src/update-remnawave-nodes" \
VLESS_MANAGER_LOCK_FILE="$TMP_DIR/manager.lock" \
VLESS_SING_CONFIG="$TMP_DIR/sing-box.json" \
VLESS_XRAY_CONFIG="$TMP_DIR/xray.json" \
python3 "$ROOT_DIR/src/vless-vpn-manager" --self-test

printf '\320y\n' | VLESS_LINKS_FILE="$TMP_DIR/links.txt" \
  python3 -c "import importlib.machinery as m; x=m.SourceFileLoader('manager', '$ROOT_DIR/src/vless-vpn-manager').load_module(); assert x.safe_input().strip() == 'y'"

printf 'Smoke tests passed.\n'
