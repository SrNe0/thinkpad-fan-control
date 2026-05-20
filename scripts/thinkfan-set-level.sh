#!/bin/bash
# Helper script for thinkfan-control GUI (runs as root via sudo)
case "$1" in
  level)
    echo "level $2" > /proc/acpi/ibm/fan
    ;;
  apply-config)
    cp /tmp/thinkfan-profile.conf /etc/thinkfan.conf
    systemctl reload-or-restart thinkfan
    ;;
  stop-service)
    systemctl stop thinkfan
    ;;
  *)
    echo "Uso: $0 {level <0-7|auto|full-speed>|apply-config|stop-service}" >&2
    exit 1
    ;;
esac
