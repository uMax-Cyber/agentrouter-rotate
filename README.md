# agentrouter-rotate

Daily automated sign-in ("autoclaim") for [agentrouter.org](https://agentrouter.org):
once a day the script signs in with each GitHub account from `accounts.txt`,
reads the account balance, signs out, and sends a balance-change report.

Driven by a headless Opera profile over a localhost debug port, so it also runs
while the desktop is locked. It never solves a slider/captcha and never approves
a new OAuth grant: it reports what is needed, waits 90 s, then gives up on that
account. A human can only step in with `--headed`, which opens a window.

## Files

| File | Purpose |
|---|---|
| `rotate.py` | the sign-in pass itself (Playwright over Chrome DevTools Protocol) |
| `run-and-reschedule.sh` | books the next run ~24 h later, then runs `rotate.py` and the report |
| `report.py` | sends balance changes to a relay host (which forwards to Telegram) |
| `systemd/` | user service + timer that start `run-and-reschedule.sh` once per run |

Not in the repo (kept out by `.gitignore`): `accounts.txt` (your GitHub
usernames), `balances.json` (report state), logs, and the Opera profile.

## Requirements

- Linux with a desktop-ish environment, Opera installed at `/usr/bin/opera`
- Python 3 with [Playwright](https://playwright.dev/python/) (`pip install playwright`)
- A working GitHub session for the **first** account, inside the Opera profile
  (sign in manually once with `rotate.py --headed`); additional accounts are
  added via <https://github.com/switch_account> and picked up by the script
- Optional report relay: SSH access with `BatchMode=yes` to a host running
  `hermes send --to telegram` (or set your own command via `HERMES_SEND`)

## Setup

```sh
mkdir -p ~/.local/share/agentrouter-rotate
# put the scripts there, then:
printf 'your-github-name\n' > ~/.local/share/agentrouter-rotate/accounts.txt

mkdir -p ~/.config/systemd/user
cp systemd/* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now agentrouter-rotate.timer
```

## Usage

```sh
rotate.py                  # all accounts in accounts.txt
rotate.py --only NAME      # a single account
rotate.py --headed         # show the Opera window, for a step that needs a human
rotate.py --keep-open      # leave Opera running afterwards
```

## Environment

- `ROTATE_CMD` — script to run (default: the local `rotate.py`)
- `ROTATE_REPORT` — report script (default: the local `report.py`)
- `ROTATE_DROPIN` — systemd timer drop-in path for rescheduling
- `HERMES_SSH` — SSH relay target for reports (default `root@localhost`)
- `HERMES_SEND` — command executed on the relay (default `hermes send --to telegram --quiet`)

## How a pass works

1. `switch_github()` — switches the shared Opera profile to the next GitHub
   account via `github.com/switch_account`.
2. `login()` — opens `agentrouter.org/login`, clicks *Continue with GitHub*,
   submits the already-authorized OAuth account form, and waits for the site
   session. Detects HTTP 429 and aborts the whole pass to avoid hammering.
3. Reads `/api/user/self` for the balance (`quota / 500000` = USD).
4. `logout()` — clears the site session, cookies and localStorage.
5. `report.py` diffs the balances against the last delivered report and sends
   the delta table, so a day with the relay down is not lost.
