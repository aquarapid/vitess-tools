#!/bin/bash
# This script destroys a local cluster.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

$DIR/vttablet-down.sh
pkill -f vttablet
pkill -f mysql
$DIR/vtgate-down.sh
pkill -f vtgate
$DIR/vtctld-down.sh
pkill -f vtctld
$DIR/zk-down.sh
pkill -f zksrc
pkill -f zk.cfg
ps -ef | grep zk
ps -ef | grep mysql
ps -ef | grep vt
echo
read -p "Do you want to run: rm -rf $VTDATAROOT [y/N]:"
if [ "$REPLY" == "Y" ] ; then
    rm -rf $VTDATAROOT
    echo Removed $VTDATAROOT
else
    echo NOT running rm -rf $VTDATAROOT
fi

echo

