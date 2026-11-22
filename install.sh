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

required_packages=(python3 ca-certificates curl unzip)
missing_packages=()
for package in "${required_packages[@]}"; do
  if ! dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null | grep -qx installed; then
    missing_packages+=("$package")
  fi
done

if ((${#missing_packages[@]})); then
  command -v apt-get >/dev/null || {
    echo "Ошибка: отсутствует APT; установите пакеты вручную: ${missing_packages[*]}" >&2
    exit 1
  }
  echo "Устанавливаю обязательные пакеты: ${missing_packages[*]}"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
fi

command -v python3 >/dev/null || {
  echo "Ошибка: Python 3 не установлен после проверки пакетов" >&2
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

Важно: при чистой установке менеджер автоматически проверит пакеты
и при необходимости подключит официальный репозиторий SagerNet.
EOF
