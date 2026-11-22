#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  echo "Ошибка: запустите установщик от root: sudo ./install.sh" >&2
  exit 1
fi

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
MANAGER_SRC="$ROOT_DIR/src/vless-vpn-manager"
CORE_SRC="$ROOT_DIR/src/update-remnawave-nodes"

for file in "$MANAGER_SRC" "$CORE_SRC"; do
  if [[ ! -f "$file" ]]; then
    echo "Ошибка: отсутствует $file" >&2
    exit 1
  fi
done

command -v python3 >/dev/null || {
  echo "Ошибка: требуется Python 3" >&2
  exit 1
}

install -D -m 0755 "$MANAGER_SRC" /usr/local/sbin/vless-vpn-manager
install -D -m 0755 "$CORE_SRC" /usr/local/sbin/update-remnawave-nodes
install -d -m 0700 /etc/vless-vpn-manager
if [[ ! -e /etc/vless-vpn-manager/links.txt ]]; then
  if [[ -f /home/odmin/remnawave.txt ]]; then
    install -m 0600 /home/odmin/remnawave.txt /etc/vless-vpn-manager/links.txt
    echo "Перенесён существующий список /home/odmin/remnawave.txt"
  else
    touch /etc/vless-vpn-manager/links.txt
  fi
fi
chmod 0600 /etc/vless-vpn-manager/links.txt

cat <<'EOF'
VLESS VPN Manager установлен.

Запуск:
  sudo vless-vpn-manager

Список ссылок:
  /etc/vless-vpn-manager/links.txt

Важно: перед чистой установкой убедитесь, что пакет sing-box
доступен в настроенных APT-репозиториях.
EOF
