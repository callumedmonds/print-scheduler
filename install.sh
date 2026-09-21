#!/usr/bin/env bash
# Install Print Scheduler: a background service that starts at boot, a tray
# applet that starts with your desktop session, and a menu entry.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXEC="$HERE/bin/printsched"
PORT="${PRINTSCHED_PORT:-8765}"

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
UNIT_DIR="$CONFIG_HOME/systemd/user"
APPS_DIR="$DATA_HOME/applications"
AUTOSTART_DIR="$CONFIG_HOME/autostart"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"

command -v python3 >/dev/null || { echo "error: python3 is required" >&2; exit 1; }
command -v lp >/dev/null || echo "warning: lp not found -- sudo apt install cups-client" >&2

mkdir -p "$UNIT_DIR" "$APPS_DIR" "$AUTOSTART_DIR" "$ICON_DIR"

# --- background service, started at boot -------------------------------
cat > "$UNIT_DIR/print-scheduler.service" <<UNITEOF
[Unit]
Description=Print Scheduler
Documentation=file://$HERE/README.md

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

# --- icon, menu entry, tray applet autostart ---------------------------
install -m644 "$HERE/printsched/desktop/print-scheduler.svg" "$ICON_DIR/print-scheduler.svg"

sed "s|__EXEC__|$EXEC|g" "$HERE/printsched/desktop/print-scheduler.desktop" \
  > "$APPS_DIR/print-scheduler.desktop"
sed "s|__EXEC__|$EXEC|g" "$HERE/printsched/desktop/print-scheduler-applet.desktop" \
  > "$AUTOSTART_DIR/print-scheduler-applet.desktop"
chmod 644 "$APPS_DIR/print-scheduler.desktop" "$AUTOSTART_DIR/print-scheduler-applet.desktop"

command -v update-desktop-database >/dev/null && update-desktop-database "$APPS_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache  >/dev/null && gtk-update-icon-cache -qtf "$DATA_HOME/icons/hicolor" 2>/dev/null || true

# --- enable ------------------------------------------------------------
systemctl --user daemon-reload
systemctl --user enable --now print-scheduler.service

# Without lingering, user services only run while you are logged in. This is
# what makes "on boot" actually mean on boot.
if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo
  echo "To let the scheduler run at boot before you log in, enable lingering:"
  echo "    sudo loginctl enable-linger $USER"
  echo "(Without it the scheduler starts when you log in, which is fine for a desktop.)"
fi

echo
echo "Installed."
echo "  Web app:  http://127.0.0.1:$PORT"
echo "  Menu:     Print Scheduler (under Utility)"
echo "  Tray:     starts automatically at your next login"
echo
echo "Start the tray icon now without logging out:"
echo "    $EXEC applet &"
echo
echo "  status:   systemctl --user status print-scheduler"
echo "  logs:     journalctl --user -u print-scheduler -f"
echo "  remove:   ./uninstall.sh"
