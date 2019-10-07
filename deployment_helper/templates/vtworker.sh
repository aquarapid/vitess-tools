#!/bin/bash
# This script runs interactive vtworker.
set -e
echo vtworker.sh $@
read -p "Hit Enter to run the above command ..."
TOPOLOGY_FLAGS="%(topology_flags)s"
exec $VTROOT/bin/vtworker \
  $TOPOLOGY_FLAGS \
  -cell %(cell)s \
  -log_dir $VTDATAROOT/tmp \
  -alsologtostderr \
  -use_v3_resharding_mode \
  "$@"
