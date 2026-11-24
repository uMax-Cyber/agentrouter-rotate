#!/usr/bin/env python3
"""Send the balance changes of one rotate.py pass to Telegram through Hermes.

  report.py RUN_OUTPUT_FILE

Changes are counted from the last delivered report, so a day with Hermes down
is not lost: the next report covers it.
"""
import datetime, json, os, pathlib, subprocess, sys

W = pathlib.Path(__file__).resolve().parent
# Where to relay the report: an SSH target reachable with BatchMode=yes.
# Point HERMES_SSH at your own relay host, e.g. HERMES_SSH=root@your-relay
HERMES = os.environ.get("HERMES_SSH", "root@localhost")
SEND = os.environ.get("HERMES_SEND", "hermes send --to telegram --quiet")


def parse(output):
    """Return ({account: balance}, {account: reason}, fatal message or None)."""
    balances, errors, name, last = {}, {}, None, None
    for line in output.splitlines():
        text = line.strip()
        if not text:
            continue
        if text.startswith("[") and text.endswith("]"):
            name = text[1:-1]
        elif text.startswith("OK {") and name:
            usd = json.loads(text[3:]).get("quota_usd")
            if usd is None:
                errors[name] = "balance not read"
            else:
                balances[name] = usd
        elif text.startswith(("FAILED ", "STOPPED ")) and name:
            errors[name] = text.split(" ", 1)[1][:200]
        elif not text.startswith(("done:", "last url", "network blip", "ACTION NEEDED")):
            last = text
    return balances, errors, None if name else last


def build(output, names, prev, now):
    """Return (message text, new state)."""
    balances, errors, fatal = parse(output)
    old = prev["balances"]
    width = max(len(n) for n in names + ["Итого"])
    rows = [f"{'':{width}} {'Было':>6} {'Стало':>6} {'+/-':>7}"]
    total_new, total_delta, compared, failed = 0.0, 0.0, 0, []
    for n in names:
        before = f"{old[n]:.2f}" if n in old else "—"
        if n not in balances:
            rows.append(f"{n:{width}} {before:>6} {'—':>6} {'ошибка':>7}")
            failed.append(f"{n}: {errors.get(n) or fatal or 'not reached in this run'}")
            continue
        total_new += balances[n]
        delta = "—"
        if n in old:
            total_delta += balances[n] - old[n]
            compared += 1
            delta = f"{balances[n] - old[n]:+.2f}"
        rows.append(f"{n:{width}} {before:>6} {balances[n]:>6.2f} {delta:>7}")
    total = f"{total_delta:+.2f}" if compared else "—"
    rows.append(f"{'Итого':{width}} {'':>6} {total_new:>6.2f} {total:>7}")

    since = f"Изменение с {prev['at']}" if prev["at"] else "Первый отчёт, сравнивать пока не с чем"
    text = f"AgentRouter, баланс в $ на {now}\n{since}\n```\n" + "\n".join(rows) + "\n```"
    if failed:
        text += "\nОшибка:\n" + "\n".join(failed)
    return text, {"at": now, "balances": {**old, **balances}}


def hermes_send(text):
    subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", HERMES, SEND],
                   input=text, text=True, timeout=120, check=True)


def run(output_file, names_file, state_file, send):
    names = [n.strip() for n in pathlib.Path(names_file).read_text().splitlines() if n.strip()]
    state_file = pathlib.Path(state_file)
    prev = json.loads(state_file.read_text()) if state_file.exists() else {"at": None, "balances": {}}
    now = datetime.datetime.now().strftime("%d.%m %H:%M")
    text, state = build(pathlib.Path(output_file).read_text(), names, prev, now)
    try:
        send(text)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"report not delivered: {e}", flush=True)
        return 1
    state_file.write_text(json.dumps(state, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], W / "accounts.txt", W / "balances.json", hermes_send))
