#!/usr/bin/env bash
# Started by the timer once per run. Books the next run 24h10m from now, then
# runs rotate.py. Booking first means a failed or rate-limited pass still waits
# for the next day instead of retrying.
set -u
W="$HOME/.local/share/agentrouter-rotate"
CMD="${ROTATE_CMD:-$W/rotate.py}"
DROPIN="${ROTATE_DROPIN:-$HOME/.config/systemd/user/agentrouter-rotate.timer.d/next.conf}"
INTERVAL=87000

next=$(date -d "@$(( $(date +%s) / 60 * 60 + INTERVAL ))" '+%F %H:%M:00')
mkdir -p "$(dirname "$DROPIN")"
printf '[Timer]\nOnCalendar=\nOnCalendar=%s\n' "$next" > "$DROPIN"
systemctl --user daemon-reload

echo "=== $(date '+%F %T'), next run $next"

# The report goes out for a failed pass too, and does not change the pass's result.
out=$(mktemp)
trap 'rm -f "$out"' EXIT
"$CMD" 2>&1 | tee "$out"
rc=${PIPESTATUS[0]}
"${ROTATE_REPORT:-$W/report.py}" "$out"
exit "$rc"
