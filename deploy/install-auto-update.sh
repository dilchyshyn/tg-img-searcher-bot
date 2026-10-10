#!/usr/bin/env bash
# Встановлює systemd-таймер (без sudo), який кожні 2 хвилини запускає auto-update.sh.
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
units="$HOME/.config/systemd/user"
mkdir -p "$units"

cat > "$units/tg-img-bot-update.service" <<UNIT
[Unit]
Description=Оновлення tg-img-searcher-bot з GitHub

[Service]
Type=oneshot
WorkingDirectory=$repo
ExecStart=$repo/deploy/auto-update.sh
UNIT

cat > "$units/tg-img-bot-update.timer" <<UNIT
[Unit]
Description=Перевірка оновлень tg-img-searcher-bot кожні 2 хвилини

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now tg-img-bot-update.timer
echo "Готово. Логи: journalctl --user -u tg-img-bot-update -f"
