#!/usr/bin/env bash
# Install Print Scheduler as a background service that starts with your session.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/print-scheduler.service"
PORT="${PRINTSCHED_PORT:-8765}"

command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }
command -v lp >/dev/null || echo "warning: lp not found -- install it with: sudo apt install cups-client" >&2

mkdir -p "$UNIT_DIR"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Print Scheduler
Documentation=file://$HERE/README.md
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$HERE
Environment=PYTHONPATH=$HERE
Environment=PRINTSCHED_PORT=$PORT
ExecStart=/usr/bin/env python3 -m printsched serve --no-browser
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNITEOF

systemctl --user daemon-reload
systemctl --user enable --now print-scheduler.service

# Keep the service alive when you are not logged in graphically.
loginctl enable-linger "$USER" 2>/dev/null || true

echo
echo "Print Scheduler is running at http://127.0.0.1:$PORT"
echo
echo "  status:  systemctl --user status print-scheduler"
echo "  logs:    journalctl --user -u print-scheduler -f"
echo "  stop:    systemctl --user stop print-scheduler"
echo "  remove:  ./uninstall.sh"
