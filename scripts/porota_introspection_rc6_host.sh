#!/bin/sh
set -eu
exec docker exec porota_production_observer python /app/ops_introspection_rc6.py --enqueue-critical
