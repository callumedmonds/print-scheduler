#!/usr/bin/env bash
# Stop the background service and remove its unit file. Your jobs and history
# stay in ~/.local/share/print-scheduler unless you delete that yourself.
set -euo pipefail

UNIT="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/print-scheduler.service"

systemctl --user disable --now print-scheduler.service 2>/dev/null || true
rm -f "$UNIT"
systemctl --user daemon-reload

echo "Print Scheduler service removed."
echo "Your schedules and history are still in ~/.local/share/print-scheduler"
echo "Delete that folder too if you want a clean slate."
