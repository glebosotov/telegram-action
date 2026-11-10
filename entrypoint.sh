#!/bin/sh

set -eu

export GITHUB="true"

if [ -n "${INPUT_WIDGET_FILE:-}" ]; then
  exec python3 /widget.py
fi

exec /bin/drone-telegram
