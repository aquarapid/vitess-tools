#!/bin/bash
# This script starts a cluster.

PS_INTERACTIVE=${PS_INTERACTIVE:-"1"}
BACKUP_DIR=${VT_BACKUP_DIR:-${VTDATAROOT}/backups}

function run_interactive()
{
    command=$1
    prompt=${2:-"Run this command? (Y/n):"}
    echo $command
    if [ ${PS_INTERACTIVE} -eq 0 ]; then
	eval $command
    else
	read -p "$prompt" response
	if echo "$response" | grep -iq "^n" ; then
	    echo Not running: $command
	else
	    eval $command
	fi
    fi
}

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo
echo This script will walk you through starting a vitess cluster.
echo
echo Servers in a Vitess cluster find each other by looking for dynamic configuration data stored in a distributed lock service.
echo After the ZooKeeper cluster is running, we only need to tell each Vitess process how to connect to ZooKeeper.
echo Then, each process can find all of the other Vitess processes by coordinating via ZooKeeper.

echo Each of our scripts automatically uses the TOPOLOGY_FLAGS environment variable to point to the global ZooKeeper instance.
echo The global instance in turn is configured to point to the local instance.
echo This demo assumes that they are both hosted in the same ZooKeeper service.

echo
run_interactive "$DIR/zk-up.sh"

echo
echo The vtctld server provides a web interface that displays all of the coordination information stored in ZooKeeper.
echo
run_interactive "$DIR/vtctld-up.sh"

echo
echo Open http://%(vtctld_host)s:15000 to verify that vtctld is running.
echo "There won't be any information there yet, but the menu should come up, which indicates that vtctld is running."

echo The vtctld server also accepts commands from the vtctlclient tool, which is used to administer the cluster.
echo "Note that the port for RPCs (in this case 15999) is different from the web UI port (15000)."
echo These ports can be configured with command-line flags, as demonstrated in vtctld-up.sh.
echo
echo
echo The vttablet-up.sh script brings up vttablets, for all shards
echo
run_interactive "$DIR/vttablet-up.sh"
echo
echo Next, designate one of the tablets to be the initial master.
echo Vitess will automatically connect the other slaves' mysqld instances so that they start replicating from the master's mysqld.
echo This is also when the default database is created. Our keyspace is named %(keyspace)s, and our MySQL database is named %(dbname)s.
echo

orig_shards=$(python -c "import json; print ' '.join(json.loads(open('%(deployment_dir)s/config/vttablet.json').read())['shard_sets'][0])")
first_orig_shard=$(echo $orig_shards | cut  -d " " -f1)
num_orig_shards=$(echo $orig_shards | wc -w)

for shard in $orig_shards; do
    tablet=$($VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ListShardTablets %(keyspace)s/$shard | head -1 | awk '{print $1}')
    run_interactive "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 InitShardMaster -force %(keyspace)s/$shard $tablet"
done

echo
echo After running this command, go back to the Shard Status page in the vtctld web interface.
echo When you refresh the page, you should see that one vttablet is the master and the other two are replicas.
echo
echo You can also see this on the command line:
echo
run_interactive "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ListAllTablets %(cell)s"
echo
echo The vtctlclient tool can be used to apply the database schema across all tablets in a keyspace.
echo The following command creates the table defined in the database_schema.sql file
run_interactive '$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ApplySchema -sql "$(cat $DIR/../config/database_schema.sql)" %(keyspace)s'
echo
echo "Now that the initial schema is applied, it's a good time to take the first backup. This backup will be used to automatically restore any additional replicas that you run, before they connect themselves to the master and catch up on replication. If an existing tablet goes down and comes back up without its data, it will also automatically restore from the latest backup and then resume replication."

for shard in $orig_shards; do
    tablet=$($VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ListShardTablets %(keyspace)s/$shard | head -3 | tail -1 | awk '{print $1}')
    run_interactive "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 Backup $tablet"
done


echo
echo After the backup completes, you can list available backups for the shards:

for shard in $orig_shards; do
    run_interactive "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ListBackups %(keyspace)s/$shard"
done


echo
echo
echo Note: In this example setup, backups are stored at $BACKUP_DIR. In a multi-server deployment, you would usually mount an NFS directory there. You can also change the location by setting the -file_backup_storage_root flag on vtctld and vttablet

echo Initialize Vitess Routing Schema
if [ $num_orig_shards -eq 1 ]; then
echo "In the examples, we are just using a single database with no specific configuration. So we just need to make that (empty) configuration visible for serving. This is done by running the following command:"
run_interactive "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 RebuildVSchemaGraph"
else
echo
echo We will apply the following VSchema:
cat $DIR/../config/vschema.json
echo
run_interactive '$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 ApplyVSchema -vschema "$(cat $DIR/../config/vschema.json)" %(keyspace)s'
fi

echo Start vtgate

echo Vitess uses vtgate to route each client query to the correct vttablet. This local example runs a single vtgate instance, though a real deployment would likely run multiple vtgate instances to share the load.

run_interactive "$DIR/vtgate-up.sh"

echo You can run a simple client application that connects to vtgate and inserst some rows:
python $DIR/client_mysql.py

echo
echo Congratulations, your local cluster is now up and running.
echo
cat << EOF
You can now explore the cluster:

    Access vtctld web UI at http://%(vtctld_host)s:15000
    Send commands to vtctld with: vtctlclient -server %(vtctld_host)s:15999 ...
    Try "$VTROOT/bin/vtctlclient -server %(vtctld_host)s:15999 help".

%(tablet_urls)s

    Access vtgate at http://%(vtgate_host)s:15001/debug/status
    Connect to vtgate either at grpc_port or mysql_port and run queries against vitess.

    Note: Vitess binaries write write logs under $VTDATAROOT/tmp.
EOF

