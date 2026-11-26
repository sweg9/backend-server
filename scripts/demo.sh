#!/usr/bin/env bash
# The demo on one laptop: the server, the hub with the demo scenarios, and the Dashboard.
#
#   scripts/demo.sh up            fresh demo home, then server + hub + Dashboard; Ctrl-C stops all three
#   scripts/demo.sh up --keep-db  keep data/hub/app.db (for example scenario 3 after scenario 1 in an earlier run)
#   scripts/demo.sh up --no-web   without the Dashboard
#   scripts/demo.sh play 1        scenario 1 (new_control), then pass or fail; 3 is one_press, demo is 1 then 3
#   scripts/demo.sh play 3 --speed 2
#
# The first `up` asks for a new app password and keeps only its hash in data/demo.env (git-ignored, C-10).
# `play` asks for that password (or reads JUDGE_PASSWORD). The hand comes from settings/hub.yaml:
# `hand: script` records the presses, `hand: linear_actuators` drives the Arduino (firmware/README.md).
set -euo pipefail

cd "$(dirname "$0")/.."
WEB_DIR="${WEB_DIR:-../live-demo}"
PORT="${PORT:-8000}"
ENV_FILE="${DEMO_ENV:-data/demo.env}"
LOG_DIR=data/demo

stop_all() {  # uv and npm start the real programs as children: stop those too
  local p
  for p in "${pids[@]:-}"; do
    [[ -n $p ]] || continue
    pkill -TERM -P "$p" 2>/dev/null || true
    kill -TERM "$p" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

up() {
  local keep_db=0 web=1
  for a in "$@"; do
    case "$a" in
      --keep-db) keep_db=1 ;;
      --no-web) web=0 ;;
      *) echo "unknown option $a" >&2; exit 2 ;;
    esac
  done
  mkdir -p data "$LOG_DIR"

  if [[ ! -f $ENV_FILE ]]; then
    echo "First run: choose the app password for the demo (only its hash is kept in $ENV_FILE)."
    local hash
    hash=$(uv run backend hash-password)
    umask 077
    printf 'HUB_TOKEN=%s\nAPP_PASSWORD_HASH=%q\n' "$(openssl rand -hex 16)" "$hash" >"$ENV_FILE"
  fi
  set -a; source "$ENV_FILE"; set +a

  if (( ! keep_db )); then
    rm -f data/hub/app.db data/hub/app.db-wal data/hub/app.db-shm
  fi
  if [[ ! -f data/hub/app.db ]]; then
    uv run backend seed-demo
  fi

  pids=()
  trap 'echo; echo "stopping"; stop_all; exit 0' INT TERM
  trap stop_all EXIT

  uv run backend server --port "$PORT" >"$LOG_DIR/server.log" 2>&1 &
  pids+=($!)
  for _ in $(seq 50); do
    curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null && break
    sleep 0.2
  done
  curl -fs "http://127.0.0.1:$PORT/healthz" >/dev/null || { echo "the server did not start: $LOG_DIR/server.log" >&2; exit 1; }

  uv run backend hub --stdout --demo-scenarios --server-url "ws://127.0.0.1:$PORT/ws/hub" >"$LOG_DIR/hub.log" 2>&1 &
  pids+=($!)

  if (( web )); then
    if [[ -d $WEB_DIR/node_modules ]]; then
      (cd "$WEB_DIR" && exec npm run dev:real -- --strictPort) >"$LOG_DIR/web.log" 2>&1 &
      pids+=($!)
    else
      echo "no $WEB_DIR/node_modules: run npm install there, or use --no-web" >&2
      web=0
    fi
  fi

  sleep 2
  for p in "${pids[@]}"; do
    kill -0 "$p" 2>/dev/null || { echo "something stopped at start; see $LOG_DIR/*.log" >&2; exit 1; }
  done
  echo "server  http://127.0.0.1:$PORT   (log $LOG_DIR/server.log)"
  echo "hub     hand: $(sed -n 's/^hand: *\([a-z_]*\).*/\1/p' settings/hub.yaml)   (log $LOG_DIR/hub.log)"
  (( web )) && echo "web     http://localhost:5173   (log $LOG_DIR/web.log)"
  echo "start a scenario in another terminal: scripts/demo.sh play 1   (then play 3)"
  echo "Ctrl-C stops everything"
  wait
}

play() {
  local which="${1:-}"
  shift || true
  case "$which" in
    1) which=new_control ;;
    3) which=one_press ;;
    demo|new_control|one_press|leave_home) ;;
    *) echo "play 1, play 3 or play demo" >&2; exit 2 ;;
  esac
  exec uv run backend judge --url "http://127.0.0.1:$PORT" --scenario "$which" "$@"
}

case "${1:-}" in
  up) shift; up "$@" ;;
  play) shift; play "$@" ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
