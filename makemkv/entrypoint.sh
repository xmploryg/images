#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
  exec /usr/local/bin/rip-media scan /data
fi

case "$1" in
  scan|process)
    exec /usr/local/bin/rip-media "$@"
    ;;
  ingest|ingest-media)
    shift
    exec /usr/local/bin/ingest-media "$@"
    ;;
  subtitle-workflow)
    shift
    exec /usr/local/bin/subtitle-workflow "$@"
    ;;
  rip-media)
    shift
    exec /usr/local/bin/rip-media "$@"
    ;;
  *)
    exec "$@"
    ;;
esac