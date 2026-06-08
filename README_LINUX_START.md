# Linux start

This directory is the deploy root for the static analysis platform.

Start in background:

```bash
cd /path/to/analysis-platform
bash ./start_basic_data_site_linux.sh --host 0.0.0.0 --port 7676
```

The script prints the PID and log path, then exits. Console output is appended to:

```text
./logs/basic_data_site_7676.log
```

Follow logs:

```bash
tail -f ./logs/basic_data_site_7676.log
```

Run in foreground for debugging:

```bash
bash ./start_basic_data_site_linux.sh --foreground --host 0.0.0.0 --port 7676
```

Open:

```text
http://<server-ip>:7676/
```

The root page redirects to `basic_data/index.html`. This server only serves static
files. It does not run the Windows data refresh scripts.
