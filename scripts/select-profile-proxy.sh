#!/usr/bin/env bash
set -euo pipefail

PROFILE="${1:-}"
if [[ "$PROFILE" != "2061" ]]; then
    echo "Proxy selector is only configured for profile 2061" >&2
    exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG_FILE="${CONFIG_DIR:-$PROJECT_ROOT/config}/$PROFILE/config.json"

for PORT in 10930 10931; do
    PROXY_URL="socks5h://127.0.0.1:$PORT"
    if curl --silent --fail --show-error --connect-timeout 5 --max-time 12 \
        --proxy "$PROXY_URL" --output /dev/null https://hh.ru/robots.txt; then
        python3 - "$CONFIG_FILE" "$PROXY_URL" <<'PY'
import json
import os
import pathlib
import stat
import sys
import tempfile

path = pathlib.Path(sys.argv[1])
proxy_url = sys.argv[2]
payload = json.loads(path.read_text(encoding="utf-8"))
payload["proxy_url"] = proxy_url
fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.chmod(temp_name, stat.S_IMODE(path.stat().st_mode))
    os.replace(temp_name, path)
except BaseException:
    try:
        os.unlink(temp_name)
    except FileNotFoundError:
        pass
    raise
PY
        echo "HH_PROXY_SELECTED profile=$PROFILE port=$PORT"
        exit 0
    fi
done

echo "HH_PROXY_UNAVAILABLE profile=$PROFILE ports=10930,10931" >&2
exit 1
