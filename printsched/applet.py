"""System tray applet.

A thin client over the running server's API: it shows what is coming up and
gives you the common actions without opening a browser. It deliberately does
not schedule anything itself -- the background service owns that, so the tray
icon can be closed and reopened without affecting a single print.

Uses AyatanaAppIndicator when it is installed, and falls back to Gtk.StatusIcon
otherwise. Cinnamon, XFCE, MATE and KDE all show the fallback correctly.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from . import config

SERVICE = "print-scheduler.service"
ICON_NAME = "print-scheduler"
POLL_SECONDS = 20


def _require_gtk():
    """Import GTK with a message that says what to install if it is missing."""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk

        return gi, GLib, Gtk
    except (ImportError, ValueError) as exc:
        sys.exit(
            f"The tray applet needs GTK's Python bindings ({exc}).\n"
            "Install them with:  sudo apt install python3-gi gir1.2-gtk-3.0"
        )


def _indicator_class(gi):
    """AyatanaAppIndicator3 if present, else None to signal the fallback."""
    for namespace, version in (("AyatanaAppIndicator3", "0.1"), ("AppIndicator3", "0.1")):
        try:
            gi.require_version(namespace, version)
            from gi.repository import AyatanaAppIndicator3 as module  # noqa: F401

            return module
        except (ImportError, ValueError):
            try:
                gi.require_version(namespace, version)
                from gi.repository import AppIndicator3 as module  # noqa: F401

                return module
            except (ImportError, ValueError):
                continue
    return None


class AppletState:
    """Everything the menu needs, fetched from the server in one call."""

    def __init__(self) -> None:
        self.reachable = False
        self.jobs: list[dict] = []
        self.queue: list[str] = []
        self.error = ""

    @property
    def base(self) -> str:
        return f"http://{config.HOST}:{config.PORT}"

    def refresh(self) -> None:
        request = urllib.request.Request(f"{self.base}/api/state", headers={"X-Printsched": "1"})
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                payload = json.load(response)
            self.jobs = payload.get("jobs", [])
            self.queue = payload.get("queue", [])
            self.reachable = True
            self.error = ""
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            self.reachable = False
            self.jobs, self.queue = [], []
            self.error = str(getattr(exc, "reason", exc))

    def upcoming(self, limit: int = 5) -> list[dict]:
        active = [j for j in self.jobs if j.get("enabled") and j.get("next_run")]
        return sorted(active, key=lambda j: j["next_run"])[:limit]

    def summary(self) -> str:
        if not self.reachable:
            return "Print Scheduler - not running"
        active = [j for j in self.jobs if j.get("enabled")]
        if not active:
            return "Print Scheduler - nothing scheduled"
        nxt = self.upcoming(1)
        when = nxt[0]["next_run"].replace("T", " ")[:16] if nxt else "?"
        plural = "s" if len(active) != 1 else ""
        return f"Print Scheduler - {len(active)} job{plural}, next {when}"


def _service(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


class Applet:
    def __init__(self) -> None:
        self.gi, self.GLib, self.Gtk = _require_gtk()
        self.state = AppletState()
        self.state.refresh()

        indicator_module = _indicator_class(self.gi)
        self.menu = self.Gtk.Menu()
        self.rebuild_menu()

        if indicator_module is not None:
            self.indicator = indicator_module.Indicator.new(
                "print-scheduler", ICON_NAME,
                indicator_module.IndicatorCategory.APPLICATION_STATUS,
            )
            self.indicator.set_status(indicator_module.IndicatorStatus.ACTIVE)
            self.indicator.set_title("Print Scheduler")
            self.indicator.set_menu(self.menu)
            self.status_icon = None
        else:
            # Gtk.StatusIcon is deprecated upstream but is what Cinnamon's tray
            # speaks natively, and needs no extra package.
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                self.status_icon = self.Gtk.StatusIcon()
            self.status_icon.set_from_icon_name(ICON_NAME)
            self.status_icon.set_tooltip_text(self.state.summary())
            self.status_icon.connect("activate", lambda *_: self.open_ui())
            self.status_icon.connect("popup-menu", self._popup)
            self.indicator = None

        self.GLib.timeout_add_seconds(POLL_SECONDS, self._tick)

    # --- menu ------------------------------------------------------------
    def _item(self, label: str, handler=None, sensitive: bool = True):
        item = self.Gtk.MenuItem(label=label)
        if handler:
            item.connect("activate", handler)
        item.set_sensitive(sensitive and handler is not None)
        item.show()
        return item

    def _separator(self):
        sep = self.Gtk.SeparatorMenuItem()
        sep.show()
        return sep

    def rebuild_menu(self) -> None:
        for child in self.menu.get_children():
            self.menu.remove(child)

        header = "● Scheduler running" if self.state.reachable else "○ Scheduler not running"
        self.menu.append(self._item(header, None))
        self.menu.append(self._separator())

        if self.state.reachable:
            upcoming = self.state.upcoming()
            if upcoming:
                self.menu.append(self._item("Coming up", None))
                for job in upcoming:
                    when = job["next_run"].replace("T", " ")[:16]
                    copies = f" ×{job['copies']}" if job.get("copies", 1) > 1 else ""
                    label = f"   {when}   {job['name'][:28]}{copies}"
                    self.menu.append(self._item(label, self._make_run_handler(job)))
            else:
                self.menu.append(self._item("   Nothing scheduled", None))

            if self.state.queue:
                self.menu.append(self._separator())
                count = len(self.state.queue)
                self.menu.append(self._item(f"{count} waiting in the printer queue", None))

            self.menu.append(self._separator())
            self.menu.append(self._item("Open Print Scheduler…", lambda *_: self.open_ui()))
            self.menu.append(self._item("Refresh now", lambda *_: self._tick()))
            self.menu.append(self._separator())
            self.menu.append(self._item("Stop scheduler", lambda *_: self.control("stop")))
        else:
            self.menu.append(self._item("Start scheduler", lambda *_: self.control("start")))
            if self.state.error:
                self.menu.append(self._item(f"   ({self.state.error[:44]})", None))

        self.menu.append(self._separator())
        self.menu.append(self._item("Quit applet", lambda *_: self.Gtk.main_quit()))

    def _make_run_handler(self, job: dict):
        """Clicking an upcoming job prints it now, without moving its schedule."""
        def handler(*_):
            request = urllib.request.Request(
                f"{self.state.base}/api/jobs/{job['id']}/run",
                method="POST", headers={"X-Printsched": "1"},
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    message = json.load(response).get("message", "sent")
                self.notify(f"{job['name']}: {message}")
            except urllib.error.HTTPError as exc:
                detail = json.load(exc).get("error", str(exc)) if exc.fp else str(exc)
                self.notify(f"{job['name']}: {detail}", urgent=True)
            except (urllib.error.URLError, OSError) as exc:
                self.notify(f"Could not reach the scheduler: {exc}", urgent=True)
            self._tick()
        return handler

    # --- actions ---------------------------------------------------------
    def open_ui(self) -> None:
        webbrowser.open(self.state.base)

    def control(self, action: str) -> None:
        result = _service(action, SERVICE)
        if result.returncode != 0:
            self.notify(f"Could not {action} the scheduler: {result.stderr.strip()[:120]}", urgent=True)
        self.GLib.timeout_add_seconds(2, self._tick)

    def notify(self, message: str, urgent: bool = False) -> None:
        subprocess.run(
            ["notify-send", "-a", "Print Scheduler", "-i", ICON_NAME,
             "-u", "critical" if urgent else "normal", "Print Scheduler", message],
            capture_output=True,
        )

    def _popup(self, icon, button, time) -> None:
        self.menu.popup(None, None, self.Gtk.StatusIcon.position_menu, icon, button, time)

    def _tick(self) -> bool:
        self.state.refresh()
        self.rebuild_menu()
        if self.status_icon is not None:
            self.status_icon.set_tooltip_text(self.state.summary())
        elif self.indicator is not None:
            self.indicator.set_menu(self.menu)
            self.indicator.set_title(self.state.summary())
        return True  # keep the timer alive

    def run(self) -> None:
        self.Gtk.main()


def main() -> int:
    applet = Applet()
    try:
        applet.run()
    except KeyboardInterrupt:
        pass
    return 0
