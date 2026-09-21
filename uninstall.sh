#!/usr/bin/env bash
# Remove the service, tray autostart and menu entry. Your schedules and history
# stay in ~/.local/share/print-scheduler unless you delete that yourself.
set -euo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"

systemctl --user disable --now print-scheduler.service 2>/dev/null || true
rm -f "$CONFIG_HOME/systemd/user/print-scheduler.service"
rm -f "$DATA_HOME/applications/print-scheduler.desktop"
rm -f "$CONFIG_HOME/autostart/print-scheduler-applet.desktop"
rm -f "$DATA_HOME/icons/hicolor/scalable/apps/print-scheduler.svg"
systemctl --user daemon-reload

pkill -f "printsched applet" 2>/dev/null || true

echo "Print Scheduler removed."
echo "Your schedules and history are still in ~/.local/share/print-scheduler"
