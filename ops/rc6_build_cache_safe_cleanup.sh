#!/usr/bin/env bash
set -Eeuo pipefail
cd /opt/porota-trading
PRE=$(sudo -n docker exec -i porota_production_observer python -c "import sqlite3,os;c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True);c.execute('pragma query_only=on');q=c.execute('pragma quick_check').fetchone()[0];m,r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone();raw=c.execute('select count(*) from historical_raw_archive').fetchone()[0];c.close();print(f'{q}|{m}|{r}|{raw}|{os.getenv(\"PPI_HISTORY_RAW_STORAGE_MODE\")}')")
test "$PRE" = 'ok|PRODUCTION_PAPER|0|0|EXTERNAL_EXACT_V1'
sudo -n docker inspect porota_production_observer porota_production_dashboard >/dev/null
BEFORE=$(df -PB1 / | awk 'NR==2{print $4}')
# A681 shared-host invariant: Docker build cache is global to the engine and
# can be used by Boca or any third app. No owner-proof exists per cache record;
# a `docker builder prune` would mutate foreign applications' assets.
# Retain the former runtime verification as a read-only diagnostic only.
echo CLEANUP_SCOPE=SHARED_HOST_BUILD_CACHE_OBSERVE_ONLY
echo CLEANUP_RESULT=SKIPPED_UNPROVEN_EXCLUSIVE_OWNERSHIP
AFTER=$(df -PB1 / | awk 'NR==2{print $4}')
POST=$(sudo -n docker exec -i porota_production_observer python -c "import sqlite3,os;c=sqlite3.connect('file:/app/data/paper_v17/observer_v17.db?mode=ro',uri=True);c.execute('pragma query_only=on');q=c.execute('pragma quick_check').fetchone()[0];m,r=c.execute('select mode,real_orders_sent from observer_state where id=1').fetchone();raw=c.execute('select count(*) from historical_raw_archive').fetchone()[0];c.close();print(f'{q}|{m}|{r}|{raw}|{os.getenv(\"PPI_HISTORY_RAW_STORAGE_MODE\")}')")
test "$POST" = 'ok|PRODUCTION_PAPER|0|0|EXTERNAL_EXACT_V1'
echo "RECLAIMED_BYTES=$((AFTER-BEFORE))"
echo CLEANUP_SCOPE=SHARED_HOST_OBSERVE_ONLY
echo IMAGES_DELETED=NO
echo CONTAINERS_DELETED=NO
echo VOLUMES_DELETED=NO
echo REAL_ORDER_TEST=NOT_PERFORMED
echo ORDER_ROUTES=NOT_CALLED
echo ROLLBACK_AUTOMATICO=NO
