#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
host="${HOST:-0.0.0.0}"
port="${PORT:-7676}"
log_file="${LOG_FILE:-}"
pid_file="${PID_FILE:-}"
foreground=0
if [[ -n "${SITE_DIR:-}" ]]; then
  site_dir="$SITE_DIR"
elif [[ -f "$project_root/basic_data/index.html" ]]; then
  site_dir="$project_root"
else
  site_dir="$project_root/site/basic_data"
fi
python_bin="${PYTHON_BIN:-}"

usage() {
  cat <<'EOF'
Usage:
  bash start_basic_data_site_linux.sh [--host HOST] [--port PORT] [--site-dir DIR] [--python PYTHON]
  bash start_basic_data_site_linux.sh --foreground

Defaults:
  --host     0.0.0.0
  --port     7676
  --site-dir auto: deploy root when ./basic_data/index.html exists, otherwise ./site/basic_data
  --log-file ./logs/basic_data_site_<port>.log
  --pid-file ./logs/basic_data_site_<port>.pid

By default the static server starts in the background, writes console output to
the log file, prints the PID and log path, then exits. Use --foreground when
debugging interactively.

This script only starts the static HTML page server. It does not run data collection,
database updates, ADB, or the Windows incremental refresh chain.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host)
      host="${2:?missing value for --host}"
      shift 2
      ;;
    --port)
      port="${2:?missing value for --port}"
      shift 2
      ;;
    --site-dir)
      site_dir="${2:?missing value for --site-dir}"
      shift 2
      ;;
    --python)
      python_bin="${2:?missing value for --python}"
      shift 2
      ;;
    --log-file)
      log_file="${2:?missing value for --log-file}"
      shift 2
      ;;
    --pid-file)
      pid_file="${2:?missing value for --pid-file}"
      shift 2
      ;;
    --foreground)
      foreground=1
      shift
      ;;
    --background)
      foreground=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$python_bin" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    python_bin="python3"
  elif command -v python >/dev/null 2>&1; then
    python_bin="python"
  else
    echo "Python is required: install python3 or pass --python /path/to/python." >&2
    exit 1
  fi
fi

log_dir="${LOG_DIR:-$project_root/logs}"
if [[ -z "$log_file" ]]; then
  log_file="$log_dir/basic_data_site_${port}.log"
fi
if [[ -z "$pid_file" ]]; then
  pid_file="$log_dir/basic_data_site_${port}.pid"
fi
mkdir -p "$(dirname "$log_file")" "$(dirname "$pid_file")"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S %z'
}

log_line() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$log_file"
}

if [[ ! -f "$site_dir/index.html" ]]; then
  echo "Site index not found: $site_dir/index.html" >&2
  echo "Run the Windows incremental/export flow first, or point --site-dir to an existing static site directory." >&2
  exit 1
fi

if [[ -f "$pid_file" ]]; then
  old_pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    log_line "[INFO] Server already running with PID $old_pid"
    log_line "[INFO] Log file     : $log_file"
    exit 0
  fi
fi

log_line "[INFO] Project root : $project_root"
log_line "[INFO] Site dir     : $site_dir"
log_line "[INFO] Listen       : $host:$port"
log_line "[INFO] Local URL    : http://127.0.0.1:$port/"
log_line "[INFO] LAN URL      : http://<server-ip>:$port/"
if [[ -f "$site_dir/basic_data/index.html" ]]; then
  log_line "[INFO] Default page : http://<server-ip>:$port/basic_data/"
fi
log_line "[INFO] Log file     : $log_file"
log_line "[INFO] PID file     : $pid_file"
log_line "[INFO] Static page server only; no data refresh will be started."

if [[ "$foreground" -eq 1 ]]; then
  log_line "[INFO] Starting in foreground mode"
  cd "$site_dir"
  "$python_bin" -m http.server "$port" --bind "$host" 2>&1 | tee -a "$log_file"
else
  log_line "[INFO] Starting in background mode"
  (
    cd "$site_dir"
    nohup "$python_bin" -m http.server "$port" --bind "$host" >> "$log_file" 2>&1 &
    echo "$!" > "$pid_file"
  )
  server_pid="$(cat "$pid_file")"
  sleep 1
  if ! kill -0 "$server_pid" 2>/dev/null; then
    log_line "[ERROR] Server failed to start. Last log lines:"
    tail -n 40 "$log_file" >&2 || true
    exit 1
  fi
  log_line "[INFO] Server started with PID $server_pid"
  log_line "[INFO] Startup complete"
fi
