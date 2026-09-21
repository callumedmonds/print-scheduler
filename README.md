# Print Scheduler

Queue documents to print at a time you choose — once, every morning, on chosen
weekdays, or on a repeating interval. Runs entirely on your own machine and
talks to your existing printers through CUPS.

![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![No dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![License MIT](https://img.shields.io/badge/license-MIT-lightgrey)

## Why

Printing something at a fixed time normally means remembering to do it. This
keeps the schedule instead: drop in the job sheet once and it prints at 08:30
every weekday, whether or not anyone thinks about it.

## What it gives you

- **A small web app** at `http://127.0.0.1:8765` — drag a file in, pick when, done
- **A tray applet** showing what's coming up, with one click to print early
- **A CLI** for the same thing, handy for scripting
- **Starts at boot**, so a schedule set once keeps running
- **Four schedule types** — once, daily, chosen weekdays, or every N minutes/hours
- **Snapshot or live files** — print a frozen copy, or re-read the file each time
  so a regenerated document always prints its latest version
- **Printer options** — copies, duplex, paper size, anything `lp` accepts
- **History** of every print, with the CUPS job id and any error
- **Sensible catch-up** — a daily job the computer slept through is skipped
  rather than printing a stale pile when you open the lid

## Requirements

Python 3.11 or newer and the CUPS client tools. Nothing else — no pip install,
no virtualenv, no Node. On Debian or Ubuntu:

```bash
sudo apt install python3 cups-client
```

## Run it

```bash
git clone https://github.com/callumedmonds/print-scheduler.git
cd print-scheduler
./bin/printsched serve
```

That opens the web app and starts the scheduler, for as long as the terminal
stays open.

## Install it properly

```bash
./install.sh
```

That sets up three things:

| | |
|---|---|
| **Background service** | systemd user unit, enabled at boot, restarts on failure |
| **Tray applet** | autostarts with your desktop session |
| **Menu entry** | "Print Scheduler" under Utility |

The applet appears in your system tray. Left-click opens the web app;
right-click lists what's coming up — clicking a job prints it early without
disturbing its schedule — and lets you start or stop the scheduler.

Start the tray icon straight away without logging out:

```bash
./bin/printsched applet &
```

### Running before you log in

A systemd *user* service normally starts when you log in. To have the
scheduler run from boot on a machine nobody is sitting at:

```bash
sudo loginctl enable-linger $USER
```

On a normal desktop you can skip this — starting at login is usually what you
want, and the applet needs a desktop session anyway.

Remove all of it with `./uninstall.sh`. Your schedules and history are kept.

### Desktop support

The applet uses AppIndicator where it exists and falls back to
`Gtk.StatusIcon`, which Cinnamon, XFCE, MATE and KDE all show. It needs
`python3-gi` and `gir1.2-gtk-3.0`, both standard on a Debian desktop.

GNOME 45+ hides legacy tray icons; there, install AppIndicator support:

```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1
```

## Using the CLI

```bash
# Print something once, at a specific time
printsched add invoice.pdf --at "2026-09-22 09:00"

# Every morning, two copies
printsched add jobsheet.pdf --daily 08:30 --copies 2

# Mondays and Fridays, re-reading the file each time it prints
printsched add rota.pdf --weekly mon,fri@07:45 --live

# Every two hours, double sided
printsched add checklist.pdf --every 2h -o sides=two-sided-long-edge

printsched open                 # open the web app
printsched applet               # show the tray icon
printsched list                 # what is scheduled
printsched runs                 # what has printed
printsched run 3                # print job 3 right now
printsched pause 3              # stop it without deleting it
printsched resume 3
printsched rm 3
printsched printers             # queues CUPS knows about
```

`bin/printsched` runs from the checkout with no install. To call it as just
`printsched`, link it onto your PATH:

```bash
mkdir -p ~/.local/bin && ln -sf "$PWD/bin/printsched" ~/.local/bin/printsched
```

## Snapshot vs live files

| | What gets printed | Use it for |
|---|---|---|
| **Snapshot** (default) | A copy taken when you scheduled it | A fixed document — editing or deleting the original later changes nothing |
| **Live** (`--live`) | Whatever is at that path at print time | A file something else regenerates, like a nightly export |

A live job whose file has gone missing records an error and keeps its schedule,
so it recovers by itself once the file is back.

## Printer options

Anything after `-o` goes straight to `lp`:

```bash
-o sides=two-sided-long-edge      # duplex
-o media=A4                       # paper size
-o print-color-mode=monochrome    # greyscale
-o page-ranges=1-4                # a subset of pages
-o job-hold-until=indefinite      # queue it but hold, useful for testing
```

`lpoptions -l -p YOUR_PRINTER` lists what your printer supports.

## Where things live

| | |
|---|---|
| Schedules and history | `~/.local/share/print-scheduler/scheduler.db` |
| Snapshot copies | `~/.local/share/print-scheduler/spool/` |
| Log | `~/.local/share/print-scheduler/printsched.log` |

Environment variables: `PRINTSCHED_HOME`, `PRINTSCHED_PORT` (default 8765),
`PRINTSCHED_HOST` (default 127.0.0.1), `PRINTSCHED_TICK` (seconds between
checks, default 15), `PRINTSCHED_GRACE` (missed-slot window in minutes,
default 60).

## Notes on how it behaves

**Missed slots.** The scheduler checks every 15 seconds. If a recurring job's
time passed while the machine was off, it prints only if the slot is less than
an hour old, otherwise it waits for the next one. A one-off job always prints
even if it is late — you asked for that document specifically.

**"Print now" doesn't disturb the schedule.** Printing by hand leaves the next
scheduled slot exactly where it was.

**A failed print doesn't cancel tomorrow's.** The error is recorded against the
job and the schedule carries on.

**Times are local wall-clock.** A job set for 09:00 prints at 09:00 before and
after a daylight-saving change.

**Success means CUPS accepted the job**, not that ink hit paper. A printer
that is off will hold the job in its queue until it comes back.

## Security

The server binds to `127.0.0.1`, so nothing outside your machine can reach it.
Because a web page you visit could otherwise POST to localhost, every
state-changing request must carry an `X-Printsched` header and a same-origin
`Origin` — which a cross-site form post cannot do without a preflight.

Anyone with access to your desktop session can schedule prints. That is the
same trust boundary as your printer itself.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

73 tests, no dependencies, nothing printed — the CUPS layer is mocked.

## Project layout

```
printsched/
  schedule.py    when a job should next print (pure functions)
  store.py       SQLite jobs and run history
  printing.py    building and running lp commands
  scheduler.py   the background loop
  server.py      JSON API and static files
  cli.py         command line front end
  applet.py      system tray icon and menu
  web/           the browser UI (no build step, no CDN)
  desktop/       icon, menu entry and autostart templates
```

## Licence

MIT — see [LICENSE](LICENSE).
