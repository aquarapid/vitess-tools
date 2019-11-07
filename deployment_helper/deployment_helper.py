#!/bin/env python2
import argparse
import json
import os
import random
import readline
import socket
import subprocess
import sys
import time
import types
import urllib2

args = None

VTROOT = None
VTDATAROOT = None
BACKUP_DIR = None
VTTOP = None
VT_MYSQL_ROOT = '/usr'
DEPLOYMENT_DIR = None
CELL = None
KEYSPACE = None
DEPLOYMENT_HELPER_DIR = os.path.abspath(os.path.dirname(__file__))
MYSQL_AUTH_PARAM = None

"""
Add cell?
Add shard?
Add tablet to shard?
Add vtctld?
init_cell different from zk-up.sh?
"""

made_script_file = None

def make_run_script_file():
    global made_script_file
    if made_script_file is None:
        template = read_template('run_script_on_host.sh')
        made_script_file = write_bin_file('run_script_on_host.sh', template)
    return made_script_file

def read_template(filename):
    dirpath = os.path.join(DEPLOYMENT_HELPER_DIR, 'templates')
    with open(os.path.join(dirpath, filename)) as fh:
        return fh.read()

def check_host():
    global VTROOT, VTTOP, VTDATAROOT, VT_MYSQL_ROOT, DEPLOYMENT_DIR, BACKUP_DIR
    VTROOT = os.environ.get('VTROOT')
    VTTOP = os.environ.get('VTTOP')
    VTDATAROOT = os.environ.get('VTDATAROOT')
    VT_MYSQL_ROOT = os.environ.get('VT_MYSQL_ROOT') or VT_MYSQL_ROOT
    if VTROOT is not None and VTDATAROOT is not None and VT_MYSQL_ROOT is not None:
        print 'VTROOT=%s' % VTROOT
        print 'VTDATAROOT=%s' % VTDATAROOT
        print 'VT_MYSQL_ROOT=%s' % VT_MYSQL_ROOT
    else:
        print """
We assume that you are running this on a host on which all the required vitess binaries exist
and you have set the VTROOT and VTDATAROOT environment variable.
VTROOT is the root of vitess installation. We expect to find the vitess binaries under VTROOT/bin.

VTDATAROOT is where the mysql data files, backup files, log files etc. are stored. This would typically be
on a partition where there is enough disk space for your data.

Set the VT_MYSQL_ROOT variable to the root directory of your mysql installation.
This means that the mysql executable should be found at $VT_MYSQL_ROOT/bin/mysql.

"""
        if VTROOT is None:
            VTROOT = os.environ.get('VTROOT') or read_value('Did not find VTROOT in environment. Enter VTROOT:')

        if VTDATAROOT is None:
            VTDATAROOT = os.environ.get('VTDATAROOT') or read_value('Did not find VTDATAROOT in environment. Enter VTDATAROOT:')

        print 'VTROOT=%s' % VTROOT
        print 'VTDATAROOT=%s' % VTDATAROOT
        print 'VT_MYSQL_ROOT=%s' % VT_MYSQL_ROOT

    DEPLOYMENT_DIR = os.getenv('DEPLOYMENT_DIR') or os.path.join(VTROOT, 'vitess-deployment')
    print 'DEPLOYMENT_DIR=%s' % DEPLOYMENT_DIR
    BACKUP_DIR = os.getenv('VT_BACKUP_DIR', os.path.join(VTDATAROOT, 'backups'))
    print

g_local_hostname = socket.getfqdn()

def read_value(prompt, default=''):
    if not prompt.endswith(' '):
        prompt += ' '
    if type(default) is int:
        default = str(default)
    prompt = '%s [%s]:' % (prompt, default)
    val = raw_input(prompt).strip()
    if not val:
        val = default
    return val

def read_list(prompt, default=[]):
    if not prompt.endswith(' '):
        prompt += ' '
    if type(default) is int:
        default = str(default)
    prompt = '%s %s:' % (prompt, default)
    prompt_input = raw_input(prompt).strip()
    lst = [el.strip() for el in prompt_input.split(',')]
    # TODO: hack
    if lst == ['']: lst = []
    if not lst:
        lst = default
    return lst

def set_cell_and_keyspace(default_cell, default_keyspace=''):
    global CELL, KEYSPACE
    CELL = os.environ.get('CELL') or read_value('Enter CELL name:', default_cell)
    if '-' in CELL:
        print("Error: CELL must not contain a '-' character")
        sys.exit(1)
    KEYSPACE = read_value('Enter KEYSPACE name:', default_keyspace)

base_ports = {
    'zk2': dict(leader_port=28881, election_port=38881, client_port=21811),
    'etcd': 2379,
    'vtctld': dict(port=15000, grpc_port=15999),
    'vttablet': dict(web_port=15101, grpc_port=16101, mysql_port=17101),
    'mysqld': 1000,
    'vtgate': dict(web_port=15001, grpc_port=15991, mysql_server_port=15306)
}

class ConfigType(object):
    """Each config variable has a default.
    It is read from a config file.
    Written to a config file.
    Is prompted for and read from user input.
    """
    ConfigTypes = [types.DictType, types.StringType, types.ListType, types.IntType]

    def get_config_file(self):
        config_dir = os.path.join(DEPLOYMENT_DIR, 'config')
        return os.path.join(config_dir, '%s.json' % self.short_name)

    def write_config(self):
        config_file = self.get_config_file()
        if not os.path.exists(os.path.dirname(config_file)):
            os.makedirs(os.path.dirname(config_file))
        out = { k: self.__dict__[k] for k in self.__dict__ if type(self.__dict__[k]) in self.ConfigTypes }
        try:
            with open(config_file, 'w') as fh:
                json.dump(out, fh, indent=4, separators=(',', ': '))
        except Exception as e:
            print e
            print self.__dict__
            for k in self.__dict__:
                print '%s %s' % (k, type(self.__dict__[k]))

    def read_config(self, show_prologue=True):
        config_file = self.get_config_file()
        interactive = not args.use_config_without_prompt
        if not interactive:
            if os.path.exists(config_file):
                with open(config_file) as fh:
                    self.__dict__.update(json.load(fh))
                print 'Using: %s' % config_file
                return
            else:
                print >> sys.stderr, 'ERROR: Could not find config file: %s'
                print >> sys.stderr, 'ERROR: Run with --interactive to generate it.'
                sys.exit(1)

        if os.path.exists(config_file):
            use_file = read_value('Config file "%s" exists, use that? :' % config_file, 'Y')
            interactive = use_file != 'Y'
        if interactive:
            get_hosts = getattr(self, 'get_hosts', None)
            if callable(get_hosts):
                get_hosts()
            self.read_config_interactive()
            self.write_config()
        else:
            with open(config_file) as fh:
                self.__dict__.update(json.load(fh))

    def read_config_add(self):
        self.get_hosts()
        self.read_config_interactive()
        self.write_config()

# We need to keep a per-host, per port-type count of last port used
# So that we can properly increment when there are multiple instances
# on the same host

class HostClass(ConfigType):
    up_filename = None
    down_filename = None
    name = ""
    description = ""
    hardware_recommendation = ""
    host_number_calculation = ""
    num_recommended_hosts = 0

    def read_config(self, show_prologue=True):
        if show_prologue:
            self.prologue()
        super(HostClass, self).read_config()

    def prologue(self):
        name = self.name or self.short_name
        name = '  %s  ' % name
        print
        print name.center(80, '*')
        print
        if self.description:
            print
            print self.description
            print
        if self.hardware_recommendation:
            print self.hardware_recommendation
            print
            print self.host_number_calculation
            print

    def get_hosts(self):
        print 'Configured hosts = %s' % self.configured_hosts
        print """Please specify additional hosts to use for this component.
To specify hosts, you can enter hostnames separated by commas or
you can specify a file (one host per line) as "file:/path/to/file"."""
        public_hostname = get_public_hostname()
        host_prompt = 'Specify hosts for "%s":' % self.short_name
        host_input = read_value(host_prompt, public_hostname)
        if host_input.lower().startswith('file:'):
            _, path = host_input.split(':')
            while not os.path.isfile(path):
                print 'Could not find file: "%s"' % path
                host_input = read_value(host_prompt, host_input)
                _, path = host_input.split(':')
            with open(path) as fh:
                new_hosts = [l.strip() for l in fh.readlines()]
        else:
            new_hosts = host_input.split(',')
        for h in new_hosts:
            if h not in self.configured_hosts:
                self.configured_hosts.append(h)

    def read_config_interactive(self):
        raise NotImplemented

    def up_commands(self):
        raise NotImplemented

    def instance_content(self, i, ftype):
        if ftype == 'up':
            header = self.instance_header_up(i)
            template = read_template(self.up_instance_template)
        else:
            header = self.instance_header_down(i)
            template = read_template(self.down_instance_template)
        return header + template

    def write_instance_script(self, i, host, ftype):
        fname = self.instance_filename(i, ftype)
        content = self.instance_content(i, ftype)
        fpath = os.path.join(DEPLOYMENT_DIR, 'bin', host, fname)
        fdir = os.path.dirname(fpath)
        if not os.path.exists(fdir):
            os.makedirs(fdir)
        with open(fpath, 'w') as fh:
            fh.write(content)
        os.chmod(fpath, 0755)
        return fpath

    def generate(self):
        if self.up_filename:
            out = self.up_commands()
            if type(out) in (str, unicode):
                write_bin_file(self.up_filename, out)
                print '\t%s' % self.up_filename
            else:
                for host, out in out.iteritems():
                    fname = os.path.join(host, self.up_filename)
                    write_bin_file(fname, out)
                    print '\t%s' % fname

        if self.down_filename:
            out = self.down_commands()
            if type(out) in (str, unicode):
                write_bin_file(self.down_filename, out)
                print '\t%s' % self.down_filename
            else:
                for host, out in out.iteritems():
                    fname = os.path.join(host, self.up_filename)
                    write_bin_file(fname, out)
                    print '\t%s' % fname

    def start(self):
        if getattr(self, 'up_filename') is None:
            print 'Could not find attribute "up_filename" for %s' % self
            return
        start_command = os.path.join(DEPLOYMENT_DIR, 'bin', self.up_filename)
        if args.interactive:
            response = read_value('Run "%s" to start %s now? :' % (start_command, self.short_name), 'Y')
        else:
            response = 'Y'
        if response == 'Y':
            print 'Running: %s' % start_command
            subprocess.call(['bash', start_command])

    def stop(self):
        stop_command = os.path.join(DEPLOYMENT_DIR, 'bin', self.down_filename)
        if args.interactive:
            response = read_value('Run "%s" to stop %s now? :' % (stop_command, self.short_name), 'Y')
        else:
            response = 'Y'
        if response == 'Y':
            print 'Running: %s' % stop_command
            subprocess.call(['bash', stop_command])

    def run_action(self, action):
        if action == 'generate':
            self.generate()
        elif action == 'start':
            self.start()
        elif action == 'stop':
            self.stop()
        else:
            print 'ERROR: action "%s" is not defined in %s' % (action, self)
            sys.exit(1)

class Deployment(object):
    pass

class LockServer(HostClass):
    short_name = 'lockserver'
    def __init__(self):
        self.ls = None
        self.get_hosts = None
        self.ls_type = None
        self.configured_hosts = []
        if args.vtctld_addr is not None:
            self.init_from_vtctld(args.vtctld_addr)
        else:
            # TODO:  get these defaults from somewhere?
            set_cell_and_keyspace('uswest', 'messagedb')
            self.read_config()

    def init_from_vtctld(self, vtctld_endpoint):
        print 'Connecting to vtctld to get topological information at "%s".' % vtctld_endpoint
        cmd = ['$VTROOT/bin/vtctlclient', '-server', vtctld_endpoint, 'GetCellInfoNames']
        cells = [ c for c in subprocess.check_output(cmd).split('\n') if c]
        print 'Found cells: %s' % cells
        set_cell_and_keyspace(cells[0])
        cmd = ['$VTROOT/bin/vtctlclient', '-server', vtctld_endpoint, 'GetCellInfo', CELL]
        cell_info = json.loads(subprocess.check_output(cmd))
        self.set_topology_from_vtctld(cell_info)

    def read_config_interactive(self):
        print 'Vitess supports two types of lockservers, zookeeper (zk2) and etcd (etcd)'
        print
        self.ls_type = read_value('Enter the type of lockserver you want to use {"zk2", "etcd"} :', 'zk2')
        print

    def read_config(self):
        super(LockServer, self).read_config()

        if self.ls_type != 'zk2':
            print >> sys.stderr, 'ERROR: Not Implemented'
            sys.exit(1)

        self.ls = Zk2()
        self.ls.read_config()
        self.ls.set_topology()
        self.topology_flags = self.ls.topology_flags
        self.num_instances = self.ls.num_instances
        self.up_filename = self.ls.up_filename
        self.down_filename = self.ls.down_filename

    def generate(self):
        self.ls.generate()

    def set_topology_from_vtctld(self, cell_info):
        if '21811' in cell_info['server_address']:
            self.ls_type = 'zk2'
            self.server_var = cell_info['server_address']
            self.topology_flags = ' '.join(['-topo_implementation %s' % self.ls_type,
                                       '-topo_global_server_address %s' % self.server_var,
                                       '-topo_global_root /vitess/global'])
        else:
            raise Exception('etcd not supported at this time.')

class Zk2(HostClass):
    up_filename = 'zk-up.sh'
    up_instance_template = 'zk-up-instance.sh'
    down_instance_template = 'zk-down-instance.sh'
    down_filename = 'zk-down.sh'
    name = 'Zookeeper (zk2)'
    short_name = 'zk2'
    description = 'Zookeeper is a popular open source lock server written in Java'
    hardware_recommendation = 'We recommend a host with x cpus and y memory for each Zookeeper instance'
    host_number_calculation = """A LockServer needs odd number of instances to establish quorum, we recommend at least 3
on 3 different hosts. If you are running the local cluster demo, you can run all three on one host."""

    def __init__(self):
        self.hosts = []
        self.zk_config = []
        self.configured_hosts = []

    def get_default_host(self, i):
        return self.configured_hosts[i % len(self.configured_hosts)]

    def read_config_interactive(self):
        print
        self.num_instances = int(read_value('Enter number of instances :', '3'))

        for i in xrange(self.num_instances):
            instance_num = i + 1
            host = read_value('For instance %d, enter hostname: ' % instance_num, self.get_default_host(i))
            self.hosts.append(host)
            leader_port = base_ports['zk2']['leader_port'] + self.hosts.count(host) - 1
            election_port = base_ports['zk2']['election_port'] + self.hosts.count(host) - 1
            client_port = base_ports['zk2']['client_port'] + self.hosts.count(host) - 1
            def_ports = '%(leader_port)s:%(election_port)s:%(client_port)s' % locals()
            zk_ports = read_value('For instance %d, enter leader_port:election_port:client_port: ' % instance_num, def_ports)
            print
            self.zk_config.append((host,zk_ports))

    def set_topology(self):
        zk_cfg_lines = []
        zk_server_lines = []
        for i, (host, zk_ports) in enumerate(self.zk_config):
            count = i + 1
            client_port = zk_ports.split(':')[-1]
            zk_cfg_lines.append('%(count)s@%(host)s:%(zk_ports)s' %locals())
            zk_server_lines.append('%(host)s:%(client_port)s' %locals())
        self.zk_server_var = ','.join(zk_server_lines)
        self.zk_config_var = ','.join(zk_cfg_lines)
        self.topology_flags = ' '.join(['-topo_implementation zk2',
                                       '-topo_global_server_address %s' % self.zk_server_var,
                                       '-topo_global_root /vitess/global'])

    def instance_header_up(self, i):
        return self.instance_header(i)

    def instance_header_down(self, i):
        return self.instance_header(i)

    def instance_header(self, i):
        vtdataroot = VTDATAROOT
        vtroot = VTROOT
        zk_id = i
        zk_dir = 'zk_%03d' % i
        zk_config = self.zk_config_var
        return """#!/bin/bash
# Generated file, edit at your own risk.

export VTROOT=%(vtroot)s
export VTDATAROOT=%(vtdataroot)s
ZK_ID=%(zk_id)s
ZK_DIR=%(zk_dir)s
ZK_CONFIG=%(zk_config)s

""" % locals()

    def instance_filename(self, i, ftype):
        return 'zk-%s-instance-%03d.sh' % (ftype, i)

    def make_header(self):
        zk_config_var = self.zk_config_var
        topology_flags = self.topology_flags
        zk_server_var = self.zk_server_var
        cell = CELL
        vtdataroot = VTDATAROOT
        vtroot = VTROOT
        vttop = VTTOP
        return """#!/bin/bash

ZK_CONFIG="%(zk_config_var)s"
ZK_SERVER="%(zk_server_var)s"
TOPOLOGY_FLAGS="%(topology_flags)s"
CELL="%(cell)s"

""" % locals()

    def down_commands(self):
        script_file = make_run_script_file()
        out = [self.make_header()]
        out.append('echo "Stopping zk servers..."')
        for i, (host, zk_ports) in enumerate(self.zk_config):
            count = i + 1
            # Create 'down' script for instance.
            script = self.write_instance_script(count, host, "down")
            # Write line to call this
            out.append('')
            out.append('%s %s %s' % (script_file, host, script))
        out.append('')
        return '\n'.join(out)

    def up_commands(self):
        script_file = make_run_script_file()
        out = [self.make_header()]
        out.append('echo "Starting zk servers..."')
        for i, (host, zk_ports) in enumerate(self.zk_config):
            count = i + 1
            # Create 'up' script for instance.
            script = self.write_instance_script(count, host, "up")
            # Write line to call this
            out.append('')
            out.append('%s %s %s' % (script_file, host, script))
            out.append('')
        out.append('')
        out.append('# Create /vitess/global and /vitess/CELLNAME paths if they do not exist.')
        cmd = [os.path.join(VTROOT, 'bin/zk'),
               '-server', '${ZK_SERVER}',
               'touch','-p','/vitess/global']

        out.append(' '.join(cmd))
        cmd = [os.path.join(VTROOT, 'bin/zk'),
               '-server', '${ZK_SERVER}',
               'touch','-p','/vitess/${CELL}']
        out.append(' '.join(cmd))

        out.append('')
        out.append('# Initialize cell.')
        cmd = [os.path.join(VTROOT, 'bin/vtctl'),
               '${TOPOLOGY_FLAGS}',
               'AddCellInfo',
               '-root /vitess/${CELL}',
               '-server_address', '${ZK_SERVER}',
               '${CELL}']
        out.append(' '.join(cmd))
        out.append('')
        rv = '\n'.join(out)
        return rv

def write_bin_file(fname, out):
    return write_dep_file('bin', fname, out)

def write_dep_file(subdir, fname, out):
    fpath = os.path.join(DEPLOYMENT_DIR, subdir, fname)
    dirpath = os.path.dirname(fpath)
    if not os.path.isdir(dirpath):
        os.makedirs(dirpath)
    with open(fpath, 'w') as fh:
        fh.write(out)
    if subdir == 'bin':
        os.chmod(os.path.join(fpath), 0755)
    return fpath

class VtCtld(HostClass):
    name = 'VtCtld server'

    description = """The vtctld server provides a web interface that displays all of the coordination information stored in ZooKeeper.
The vtctld server also accepts commands from the vtctlclient tool, which is used to administer the cluster."""
    hardware_recommendation = 'We recommend a host with x cpus and y memory for the vtctld instance.'
    host_number_calculation = 'You typically need only 1 vtctld instance in a cluster.'
    up_filename = 'vtctld-up.sh'
    down_filename = 'vtctld-down.sh'
    up_instance_template = 'vtctld-up-instance.sh'
    down_instance_template = 'vtctld-down-instance.sh'
    short_name = 'vtctld'

    def __init__(self, hostname, ls):
        self.hostname = hostname
        self.ls = ls
        self.ports = dict(web_port=15000, grpc_port=15999)
        self.configured_hosts = []
        if args.vtctld_addr is None:
            self.read_config()

    def read_config_interactive(self):
        pass

    def instance_filename(self, i, ftype):
        return 'vtctld-%s-instance-%d.sh' % (ftype, i)

    def down_commands(self):
        return self.make_commands('down')

    def up_commands(self):
        return self.make_commands('up')

    def make_commands(self, ftype):
        if ftype == 'up':
            action = 'Starting'
        else:
            action = 'Stopping'
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo %s vtctld...' % action)
        for i, host in enumerate(self.configured_hosts):
            script = self.write_instance_script(i, host, ftype)
            out.append('')
            out.append('%s %s %s' % (script_file, host, script))
        out.append('')
        return '\n'.join(out)

    def instance_header_up(self, i):
        return self.instance_header()

    def instance_header_down(self, i):
        return self.instance_header()

    def instance_header(self):
        topology_flags = self.ls.topology_flags
        cell = CELL
        grpc_port = self.ports['grpc_port']
        web_port = self.ports['web_port']
        hostname = self.hostname
        vtdataroot = VTDATAROOT
        vtroot = VTROOT
        mysql_auth_param = MYSQL_AUTH_PARAM
        backup_dir = BACKUP_DIR
        return r"""
#!/bin/bash
set -e

export VTROOT=%(vtroot)s
export VTDATAROOT=%(vtdataroot)s

HOSTNAME="%(hostname)s"
TOPOLOGY_FLAGS="%(topology_flags)s"
CELL="%(cell)s"
GRPC_PORT=%(grpc_port)s
WEB_PORT=%(web_port)s
MYSQL_AUTH_PARAM="%(mysql_auth_param)s"
BACKUP_DIR="%(backup_dir)s"
""" % locals()

class VtGate(HostClass):
    up_filename = 'vtgate-up.sh'
    down_filename = 'vtgate-down.sh'
    up_instance_template = 'vtgate-up-instance.sh'
    down_instance_template = 'vtgate-down-instance.sh'
    short_name = 'vtgate'

    def __init__(self, hostname, ls):
        self.hostname = hostname
        self.ls = ls
        self.ports = dict(web_port=15001, grpc_port=15991, mysql_server_port=15306)
        self.configured_hosts = []
        self.read_config()

    def read_config_interactive(self):
        pass

    def instance_filename(self, i, ftype):
        return 'vtgate-%s-instance-%d.sh' % (ftype, i)

    def down_commands(self):
        return self.make_commands('down')

    def up_commands(self):
        return self.make_commands('up')

    def make_commands(self, ftype):
        if ftype == 'up':
            action = 'Starting'
        else:
            action = 'Stopping'
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo %s vtgate...' % action)
        for i, host in enumerate(self.configured_hosts):
            script = self.write_instance_script(i, host, ftype)
            out.append('')
            out.append('%s %s %s' % (script_file, host, script))
        out.append('')
        return '\n'.join(out)

    def instance_header_up(self, i):
        return self.instance_header()

    def instance_header_down(self, i):
        return self.instance_header()

    def instance_header(self):
        topology_flags = self.ls.topology_flags
        cell = CELL
        grpc_port = self.ports['grpc_port']
        web_port = self.ports['web_port']
        mysql_server_port = self.ports['mysql_server_port']
        hostname = self.hostname
        vtroot = VTROOT
        vtdataroot = VTDATAROOT
        mysql_auth_param = MYSQL_AUTH_PARAM
        backup_dir = BACKUP_DIR
        return """
#!/bin/bash
set -e

# This is an example script that starts a single vtgate.

export VTROOT=%(vtroot)s
export VTDATAROOT=%(vtdataroot)s

HOSTNAME="%(hostname)s"
TOPOLOGY_FLAGS="%(topology_flags)s"
CELL="%(cell)s"
GRPC_PORT=%(grpc_port)s
WEB_PORT=%(web_port)s
MYSQL_SERVER_PORT=%(mysql_server_port)s
MYSQL_AUTH_PARAM="%(mysql_auth_param)s"
BACKUP_DIR="%(backup_dir)s"
""" % locals()


NUM_BYTES = 1
MAX_SHARDS = 2 ** (NUM_BYTES * 8)

def make_shards(num_shards):
    """
    Return canonical shard names covering the range with the number of
    shards provided
    """
    def get_str(x):
        if x in (hex(0), hex(MAX_SHARDS)):
            return ''
        rv = '%s' % x
        return rv.replace('0x', '')

    start = hex(0)
    end = None
    size = MAX_SHARDS / num_shards
    shards = []
    for i in xrange(1, num_shards + 1):
        end = hex(i * size)
        shard = '%s-%s' % (get_str(start), get_str(end))
        if shard == '-':
            shard = '0'
        shards.append(shard)
        start = end
    return shards

def distribute_tablets(shards, configured_hosts):
    """
    Distributes tablets for shards evenly over configured hosts while
    trying to maintain tablet type diversity.
    """
    def evaluate_host(shard, tablets):
        count = len(tablets)
        shard_count = 0
        for tablet in tablets:
            tshard, ttype, _ = tablet
            if tshard == shard:
                shard_count += 1
                if ttype == 'master':
                    shard_count += 1
        return count + shard_count

    tablets_per_host = {}
    host_per_tablet = {}
    for tablet_type in [ 'master', 'replica', 'rdonly']:
        tablets = []
        for shard in shards:
            num_instances = shards[shard]['num_instances']
            tablets += [(shard, tablet_type, i) for i in xrange(1, int(num_instances[tablet_type]) + 1)]
        for tablet in tablets:
            shard, tablet_type, _ = tablet
            used_hosts = set(tablets_per_host.keys())
            unused_hosts = set(configured_hosts) - used_hosts
            if unused_hosts:
                host = random.choice(list(unused_hosts))
            else:
                candidates = configured_hosts
                candidates.sort(key=lambda h: evaluate_host(shard, tablets_per_host[h]))
                host = candidates[0]
            if host not in tablets_per_host:
                tablets_per_host[host] = []
            tablets_per_host[host].append(tablet)
            host_per_tablet[tablet] = host

    return tablets_per_host, host_per_tablet

class MySqld(HostClass):

    up_instance_template = 'mysqld-up-instance.sh'
    down_instance_template = 'mysqld-down-instance.sh'
    short_name = 'mysqld'

    def __init__(self, vttablet):
        self.up_filename = 'mysqld-%s-up.sh' % KEYSPACE
        self.down_filename = 'mysqld-%s-down.sh' % KEYSPACE 
        self.vttablet = vttablet
        self.shards = self.vttablet.shards
        self.tablets = self.vttablet.tablets
        self.dbconfig = DbConnectionTypes()
        if args.external_mysql:
            self.up_instance_template = 'mysqld-up-instance-external-mysql.sh'

    def read_config_interactive(self):
        pass

    def instance_header_up(self, tablet):
        return self.vttablet.instance_header(tablet)

    def instance_header_down(self, tablet):
        return self.vttablet.instance_header(tablet)

    def instance_filename(self, tablet, ftype="up"):
        return 'mysqld-%s-instance-%s.sh' % (ftype, tablet['unique_id'])

    def down_commands_shard(self, shard):
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Stopping mysqld for shard "%s" ...' % shard)

        for tablet in self.tablets:
            if shard != tablet['shard']:
                continue
            script = self.write_instance_script(tablet, tablet['host'], "down")
            out.append('')
            out.append('%s %s %s' % (script_file, tablet['host'], script))
        out.append('')
        return '\n'.join(out)

    def up_commands_shard(self, shard):
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Starting mysqld for shard "%s" ...' % shard)
        init_db_sql = os.path.join(DEPLOYMENT_DIR, 'config', self.dbconfig.init_file)
        for tablet in self.tablets:
            if shard != tablet['shard']:
                continue
            script = self.write_instance_script(tablet, tablet['host'], "up")
            out.append('')
            out.append('%s %s %s %s' % (script_file, tablet['host'], script, init_db_sql))
        out.append('')
        return '\n'.join(out)

    def up_commands(self):
        out = []
        out.append('#!/bin/bash')
        out.append('')

        out.append('echo Starting mysqld for all shards')
        out.append('')
        for shard in self.shards:
            shard_out = self.up_commands_shard(shard)
            script = write_bin_file('mysqld-up-%s-shard-%s.sh' % (KEYSPACE, shard), shard_out)
            out.append(script)
            out.append('')

        return '\n'.join(out)

    def down_commands(self):
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Stopping mysqld for all shards')
        out.append('')
        for shard in self.shards:
            shard_out = self.down_commands_shard(shard)
            script = write_bin_file('mysqld-down-shard-%s.sh' % shard, shard_out)
            out.append(script)
            out.append('')
        return '\n'.join(out)

    def generate(self):
        super(MySqld, self).generate()
        self.dbconfig.generate()

class VtTablet(HostClass):
    up_instance_template = 'vttablet-up-instance.sh'
    down_instance_template = 'vttablet-down-instance.sh'
    short_name = 'vttablet'
    offset_base = int(os.getenv('TABLET_ID_BASE', '100'))
    shard_config = {}
    shards = []
    tablets = []
    base_ports = dict(web=15100, grpc=16100, mysql=17100)
    tablet_types = ['master', 'replica', 'rdonly']

    def __init__(self, hostname, ls, vtctld):
        self.up_filename = 'vttablet-%s-up.sh' % KEYSPACE
        self.down_filename = 'vttablet-%s-down.sh' % KEYSPACE        
        self.manage_mysqld = True
        self.hostname = hostname
        self.ls = ls
        self.vtctld = vtctld
        self.configured_hosts = []
        self.shard_sets = []
        self.read_config()
        if args.add:
            self.read_config_add()
        self.mysqld = MySqld(self)
        self.dbconfig = self.mysqld.dbconfig

    def read_config_interactive(self):
        print
        print 'A Vitess Tablet is comprised of a vttablet process and a mysqld process.'
        manage_mysqld = read_value('Do you want mysqld managed with vttablets?', 'Y')
        self.manage_mysqld = manage_mysqld.startswith('Y') or manage_mysqld.startswith('y')
        print
        print
        print 'Now we will gather information about vttablets for shards'
        print
        print 'Current shards: %s' % self.shards
        print
        print 'We will add new shards'
        print
        num_shards = read_value('Enter number of new shards:','1')
        new_shard_candidates = make_shards(int(num_shards))
        default_shards = ','.join(new_shard_candidates)
        new_shards_read = read_value('Enter shard names separated by commas "0", "-80" "80-" etc.:', default_shards)
        new_shards = new_shards_read.split(',')
        self.shard_sets.append(new_shards)
        all_shards = self.shards + new_shards

        print
        print "Tablets can be of the follwing types: %s" % self.tablet_types
        print 'Every shard has one "master" tablet. This starts out as a tablet of type "replica" and is then promoted to "master"'
        print 'We recommend two more tablets of type "replica" to enable semi-sync replication and master fail over'
        print 'For resharding workflow to work, you need at least 2 tablets of type "rdonly".'
        print 'Thus we recommend a total of 5 tablets, 1 of type "master", 2 of type "replica" and 2 of type "rdonly".'
        print
        print 'Now we will gather information about tablet type numbers for each shard'
        print

        shard_config = {}
        hosts = {}
        for shard in new_shards:
            num_instances = {}
            print 'For shard: "%s":' % shard
            print 'Number of tablets of type "replica" to be converted to master: 1'
            num_instances['master'] = 1
            num_instances['replica'] = read_value('Number of additional tablets of type "replica":', '2')
            num_instances['rdonly'] = read_value('Number of tablets of type "rdonly":', '2')
            shard_config[shard] = dict(num_instances=num_instances)

        print
        print 'Now we will gather information about each tablet'
        print
        tablets_per_host, host_per_tablet = distribute_tablets(shard_config, self.configured_hosts)
        print 'Distributed %d tablets across %d hosts.' % (len(host_per_tablet), len(tablets_per_host))
        print 'The hosts will be presented to you as defaults.'
        print
        tablets = []
        for shard in new_shards:
            shard_config[shard]['tablets'] = []
            base_offset = self.offset_base * (all_shards.index(shard) + 1)
            cnt = int(os.getenv('TABLET_ID_OFFSET', '0'))
            for ttype in self.tablet_types:
                num_instances = int(shard_config[shard]['num_instances'][ttype])
                for i in xrange(1, num_instances + 1):
                    cnt += 1
                    default_host = host_per_tablet[(shard, ttype, i)]
                    unique_id = base_offset + cnt - 1
                    cell = CELL
                    alias = '%s-%010d' %(cell, unique_id)
                    tablet_dir ='vt_%010d' % unique_id
                    print 'Tablet "%(alias)s" (cell="%(cell)s",shard="%(shard)s",type="%(ttype)s",num=%(i)d):' % locals()
                    prompt = '\tEnter host name:'
                    host = read_value(prompt, default_host)
                    prompt = '\tEnter web port number:'
                    default = self.base_ports['web'] + base_offset + cnt
                    web_port = read_value(prompt, default)
                    prompt = '\tEnter grpc port number:'
                    default = self.base_ports['grpc'] + base_offset + cnt
                    grpc_port = read_value(prompt, default)
                    prompt = '\tEnter mysql host:'
                    default = host
                    mysql_host = read_value(prompt, default)
                    if mysql_host == host:
                        default = self.base_ports['mysql'] + base_offset + cnt
                    else:
                        default = 3306
                    prompt = '\tEnter mysql port number:'
                    mysql_port = read_value(prompt, default)
                    print
                    tablet = dict(host=host,
                                  grpc_port=grpc_port,
                                  web_port=web_port,
                                  mysql_host=mysql_host,
                                  mysql_port=mysql_port,
                                  alias=alias,
                                  tablet_dir=tablet_dir,
                                  unique_id=unique_id,
                                  shard=shard,
                                  ttype=ttype,
                                  )

                    tablets.append(tablet)
                    if host not in hosts:
                        hosts[host] = []
                    hosts[host].append(tablet)
        self.shards += new_shards
        self.tablets += tablets
        self.hosts = hosts
        self.shard_config.update(shard_config)

    def generate(self):
        super(VtTablet, self).generate()
        if self.manage_mysqld:
            self.mysqld.generate()

    def make_header(self):
        topology_flags = self.ls.topology_flags

        vtdataroot = VTDATAROOT
        vtroot = VTROOT
        vttop = VTTOP
        cell = CELL
        backup_dir = BACKUP_DIR
        return """#!/bin/bash
# This script creates a single shard vttablet deployment.
VTDATAROOT=%(vtdataroot)s
VTROOT=%(vtroot)s
VTTOP=%(vttop)s

set -e

mkdir -p ${VTDATAROOT}/tmp
BACKUP_DIR="%(backup_dir)s"
mkdir -p ${BACKUP_DIR}

CELL=%(cell)s
TOPOLOGY_FLAGS="%(topology_flags)s"
""" % locals()

    def instance_header(self, tablet):
        # if mysql_host and host are not the same, we need
        # to do things differently.
        external_mysql = 0
        if args.external_mysql:
            external_mysql = 1
        if external_mysql:
            extra_params = "-mycnf_server_id %s" % tablet['unique_id']
            mysql_host = tablet['mysql_host']
            mysql_port = tablet['mysql_port']
        else:
            if args.external_mysql:
                extra_params = '-enable_replication_reporter'
            else:
                #extra_params = '-enable_semi_sync -enable_replication_reporter'
                extra_params = '-enable_replication_reporter'
            mysql_host = ''
            mysql_port = ''
        topology_flags = self.ls.topology_flags
        vtdataroot = VTDATAROOT
        vtroot = VTROOT
        vttop = VTTOP
        cell = CELL
        dbconfig_dba_flags = self.dbconfig.get_dba_flags()
        dbconfig_flags = self.dbconfig.get_flags(host=mysql_host, port=mysql_port)
        init_file = os.path.join(DEPLOYMENT_DIR, 'config', self.dbconfig.init_file)
        vtctld_host = self.vtctld.hostname
        vtctld_web_port = self.vtctld.ports['web_port']
        keyspace = KEYSPACE
        vt_mysql_root = VT_MYSQL_ROOT
        dbname = self.dbconfig.get_dbname()
        mysql_auth_param = MYSQL_AUTH_PARAM
        backup_dir = BACKUP_DIR
        all_vars = locals()
        all_vars.update(tablet)
        if all_vars['ttype'] == 'master':
            all_vars['ttype'] = 'replica'
        return """#!/bin/bash

export VTDATAROOT=%(vtdataroot)s
export VTROOT=%(vtroot)s
export VTTOP=%(vttop)s
export VT_MYSQL_ROOT=%(vt_mysql_root)s

MYSQL_AUTH_PARAM="%(mysql_auth_param)s"

DBNAME=%(dbname)s
KEYSPACE=%(keyspace)s
TOPOLOGY_FLAGS="%(topology_flags)s"
DBCONFIG_DBA_FLAGS=%(dbconfig_dba_flags)s
DBCONFIG_FLAGS=%(dbconfig_flags)s
INIT_DB_SQL_FILE=%(init_file)s
VTCTLD_HOST=%(vtctld_host)s
VTCTLD_WEB_PORT=%(vtctld_web_port)s
HOSTNAME=%(host)s

TABLET_DIR=%(tablet_dir)s
UNIQUE_ID=%(unique_id)s
MYSQL_PORT=%(mysql_port)s
WEB_PORT=%(web_port)s
GRPC_PORT=%(grpc_port)s
ALIAS=%(alias)s
SHARD=%(shard)s
TABLET_TYPE=%(ttype)s
EXTRA_PARAMS="%(extra_params)s"
EXTERNAL_MYSQL=%(external_mysql)s
BACKUP_DIR="%(backup_dir)s"
""" % all_vars

    def instance_header_up(self, tablet):
        return self.instance_header(tablet)

    def instance_header_down(self, tablet):
        return self.instance_header(tablet)

    def instance_filename(self, tablet, ftype="up"):
        return 'vttablet-%s-instance-%s.sh' % (ftype, tablet['unique_id'])

    def down_commands_shard(self, shard):
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Stopping vttablets for shard "%s" ...' % shard)

        for tablet in self.tablets:
            if shard != tablet['shard']:
                continue
            script = self.write_instance_script(tablet, tablet['host'], "down")
            out.append('')
            out.append('%s %s %s' % (script_file, tablet['host'], script))
        out.append('')
        return '\n'.join(out)

    def up_commands_shard(self, shard):
        script_file = make_run_script_file()
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Starting tablets for shard "%s" ...' % shard)
        for tablet in self.tablets:
            if shard != tablet['shard']:
                continue
            script = self.write_instance_script(tablet, tablet['host'], "up")
            out.append('')
            out.append('%s %s %s' % (script_file, tablet['host'], script))
        out.append('')
        return '\n'.join(out)

    def up_commands(self):
        out = []
        out.append('#!/bin/bash')
        out.append('')
        if self.manage_mysqld:
            mysqld_up_script = os.path.join(DEPLOYMENT_DIR, 'bin', self.mysqld.up_filename)
            out.append('# Start mysqld for all shards')
            out.append(mysqld_up_script)
            out.append('')
        out.append('echo Starting vttablets for all shards')
        out.append('')
        for shard in self.shards:
            shard_out = self.up_commands_shard(shard)
            script = write_bin_file('vttablet-up-%s-shard-%s.sh' % (KEYSPACE, shard), shard_out)
            out.append(script)
            out.append('')

        return '\n'.join(out)

    def down_commands(self):
        out = []
        out.append('#!/bin/bash')
        out.append('')
        out.append('echo Stopping vttablets for all shards')
        out.append('')
        for shard in self.shards:
            shard_out = self.down_commands_shard(shard)
            script = write_bin_file('vttablet-down-shard-%s.sh' % shard, shard_out)
            out.append(script)
            out.append('')
        if self.manage_mysqld:
            mysqld_down_script = os.path.join(DEPLOYMENT_DIR, 'bin', self.mysqld.down_filename)
            out.append('# Stop mysqld for all shards')
            out.append(mysqld_down_script)
            out.append('')

        return '\n'.join(out)

def get_public_hostname():
    fqdn = socket.getfqdn()
    # If we are on aws, use the public address.
    if fqdn.endswith('compute.internal'):
        response = urllib2.urlopen('http://169.254.169.254/latest/meta-data/public-hostname')
        return response.read()
    elif fqdn == "localhost.localdomain":
        return "127.0.0.1"
    else:
        return fqdn

DEFAULT_PERMISSIONS = ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'DROP', 'RELOAD', 'PROCESS', 'FILE',
                       'REFERENCES', 'INDEX', 'ALTER', 'SHOW DATABASES', 'CREATE TEMPORARY TABLES',
                       'LOCK TABLES', 'EXECUTE', 'REPLICATION SLAVE', 'REPLICATION CLIENT', 'CREATE VIEW',
                       'SHOW VIEW', 'CREATE ROUTINE', 'ALTER ROUTINE', 'CREATE USER', 'EVENT', 'TRIGGER']
DB_USERS = {
    'app': {
        'description': 'User for app traffic, with global read-write access.',
        'permissions': DEFAULT_PERMISSIONS,
        },
    'allprivs': {
        'description': 'User for administrative operations that need to be executed as non-SUPER.',
        'permissions': DEFAULT_PERMISSIONS,
        },
    'repl': {
        'description': 'User for slave replication connections.',
        'permissions': ['REPLICATION SLAVE'],
    },
    'filtered': {
        'description': 'User for Vitess filtered replication (binlog player).',
        'permissions': DEFAULT_PERMISSIONS,
        },
    'dba': {
        'description': 'Admin user with all privileges.',
        'permissions': ['ALL'],
    },
}

GLOBAL_PARAMS = {
    'charset': 'utf8',
    'dbname': 'vt_%(keyspace)s',

}

class DbConnectionTypes(ConfigType):
    short_name = 'db_config'
    def __init__(self):
        self.dbconfig = {}
        self.vars = {}
        self.db_types = DB_USERS.keys()
        self.init_file = 'init_db.sql'
        self.cred_file_path = None
        self.read_config()

    def read_config_interactive(self):
        print
        print 'Vitess uses a "Sidecar Database" to store metadata.'
        print 'At the moment, the default name, "_vt" may be hardcoded in vitess at a few places.'
        print 'If you have changed this, enter the new name, otherwise, accept the default.'
        self.sidecar_dbname = read_value('Enter sidecar db name:', '_vt')
        print
        print 'Vitess uses the following connection types to connect to mysql: %s' % DB_USERS.keys()
        print 'Each connection type uses a different user and is used for a different purpose.'
        print 'You can grant each user different privileges.'
        print 'First we will prompt you for usernames, passwords and privileges you want to grant for each of these users.'
        print
        password_set = False
        for db_type in DB_USERS:
            self.dbconfig[db_type] = {}
            print '[%s]: %s' % (db_type, DB_USERS[db_type]['description'])
            prompt = 'Enter username for "%s":' % db_type
            if args.external_mysql:
                default = 'mysql_user'
            else:
                default = 'vt_%s' % db_type
            user = read_value(prompt, default)
            self.dbconfig[db_type]['user'] = user
            if args.external_mysql:
                default = 'mysql_password'
            else:
                default = ''
            prompt = 'Enter password for %s (press Enter for no password):' % user
            password = read_value(prompt, default)
            #password_set = password_set or bool(password)
            self.dbconfig[db_type]['user'] = user
            self.dbconfig[db_type]['password'] = password
            perms = read_value('Enter privileges to be granted to user "%s":' % user, ', '.join(DB_USERS[db_type]['permissions']))
            self.dbconfig[db_type]['permissions'] = perms
            print

        if password_set:
            self.write_mysql_creds()

        print 'Now we will ask you for parameters used for creating mysql connections that are shared by all connection types'
        self.dbconfig['global'] = {}
        cell = CELL
        keyspace = KEYSPACE
        for param, default in GLOBAL_PARAMS.iteritems():
            if '%' in param:
                param = param % locals()
            if '%' in default:
                default = default % locals()
            self.dbconfig['global'][param] = read_value('Enter "%s":' % param, default)

    def get_mysql_auth_param(self):
        if self.cred_file_path:
            return '-mysql_auth_server_static_file %s' % self.cred_file_path
        else:
            return ''

    def write_mysql_creds(self):
        creds = {}
        for db_type in DB_USERS:
            password = self.dbconfig[db_type]['password']
            user = self.dbconfig[db_type]['user']
            creds[user] = [{'Password': password, 'UserData': user}]

        self.cred_file_path = os.path.join(DEPLOYMENT_DIR, 'config', 'mysql_creds.json')
        with open(self.cred_file_path, 'w') as fh:
            json.dump(creds, fh, indent=4, separators=(',', ': '))

    def generate(self):
        out = self.make_init_db_sql()
        write_dep_file('config', self.init_file, out)
        for fname in ('vschema.json', 'database_schema.sql'):
            out = read_template(fname)
            write_dep_file('config', fname, out)
        for fname in ('client.sh', 'client_grpc.py', 'client_mysql.py'):
            out = read_template(fname)
            write_bin_file(fname, out)

    def get_dbname(self):
        return self.dbconfig['global']['dbname']

    def get_dba_flags(self):
        charset = self.dbconfig['global']['charset']
        flags = []
        fmt = '-db-config-%(db_type)s-uname %(user)s'
        passfmt = '-db-config-%(db_type)s-pass %(password)s'
        for db_type in ['dba']:
            user = self.dbconfig[db_type]['user']
            password = self.dbconfig[db_type]['password']
            flags.append(fmt % locals())
            if password:
                flags.append(passfmt % locals())
            for param, value in self.dbconfig['global'].iteritems():
                if param == 'dbname':
                    continue
                flags.append('-db-config-%(db_type)s-%(param)s %(value)s' % locals())
        return '"%s"' % ' '.join(flags)

    def get_flags(self, host=None, port=None):
        keyspace = '%s_keyspace' % CELL
        charset = self.dbconfig['global']['charset']
        flags = []
        fmt = '-db-config-%(db_type)s-uname %(user)s'
        passfmt = '-db-config-%(db_type)s-pass %(password)s'
        for db_type in DB_USERS:
            user = self.dbconfig[db_type]['user']
            password = self.dbconfig[db_type]['password']
            flags.append(fmt % locals())
            if password:
                flags.append(passfmt % locals())
            for param, value in self.dbconfig['global'].iteritems():
                if param == 'dbname':
                    continue
                flags.append('-db-config-%(db_type)s-%(param)s %(value)s' % locals())
            if host:
                flags.append('-db-config-%(db_type)s-host %(host)s' % locals())
            if port:
                flags.append('-db-config-%(db_type)s-port %(port)s' % locals())
        if host:
            flags.append('-db_host %(host)s' % locals())
        if port:
            flags.append('-db_port %(port)s' % locals())

        return '"%s"' % ' '.join(flags)


    def make_init_db_sql(self):
        sidecar_dbname = self.sidecar_dbname
        header = """# This file is executed immediately after mysql_install_db,
# to initialize a fresh data directory.

##########################################
# Equivalent of mysql_secure_installation
##########################################

# Remove anonymous users.
DELETE FROM mysql.user WHERE User = '';

# Disable remote root access (only allow UNIX socket).
DELETE FROM mysql.user WHERE User = 'root' AND Host != 'localhost';

# Remove test database.
DROP DATABASE IF EXISTS test;

##########################################
# Vitess defaults
##########################################

# Vitess-internal database.
CREATE DATABASE IF NOT EXISTS %(sidecar_dbname)s;
# Note that definitions of local_metadata and shard_metadata should be the same
# as in production which is defined in go/vt/mysqlctl/metadata_tables.go.
CREATE TABLE IF NOT EXISTS %(sidecar_dbname)s.local_metadata (
  name VARCHAR(255) NOT NULL,
  value VARCHAR(255) NOT NULL,
  PRIMARY KEY (name)
  ) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS %(sidecar_dbname)s.shard_metadata (
  name VARCHAR(255) NOT NULL,
  value MEDIUMBLOB NOT NULL,
  PRIMARY KEY (name)
  ) ENGINE=InnoDB;

# User for app traffic, with global read-write access.
CREATE USER IF NOT EXISTS 'mysql_user'@'%%' IDENTIFIED BY 'mysql_password';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, PROCESS, FILE, REFERENCES, INDEX, ALTER, SHOW DATABASES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, REPLICATION SLAVE, REPLICATION CLIENT, CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, CREATE USER, EVENT, TRIGGER ON *.* TO 'mysql_user'@'%%';

""" % locals()

        footer = """
# User for Orchestrator (https://github.com/github/orchestrator).
CREATE USER IF NOT EXISTS 'orc_client_user' IDENTIFIED BY 'orc_client_user_password';
GRANT SUPER, PROCESS, REPLICATION SLAVE, RELOAD
  ON *.* TO 'orc_client_user'@'%%';
GRANT SELECT
  ON %(sidecar_dbname)s.* TO 'orc_client_user'@'%%';

FLUSH PRIVILEGES;

""" % locals()
        out = []
        for db_type in self.db_types:
            line = '# %s' % DB_USERS[db_type]['description']
            out.append(line)

            perms = self.dbconfig[db_type]['permissions']
            user = self.dbconfig[db_type]['user']
            password = self.dbconfig[db_type]['password']
            hostname = 'localhost'
            if db_type == 'repl':
                hostname = '%'
            line = "CREATE USER IF NOT EXISTS '%(user)s'@'%(hostname)s'" % locals()
            if password:
                line += " IDENTIFIED BY '%(password)s';" % locals()
            else:
                line += ";"
            line += "\nGRANT %(perms)s ON *.* TO '%(user)s'@'%(hostname)s';" % locals()
            out.append(line)
            # If vt_dba add another line.
            if db_type == 'dba':
                line = "GRANT GRANT OPTION ON *.* TO '%(user)s'@'localhost';" % locals()
                out.append(line)
            out.append('')

        return header + '\n'.join(out) + footer

ACTION_CHOICES = [ 'generate', 'start', 'stop', 'run_demo']
COMPONENT_CHOICES = ['lockserver', 'vtctld', 'vttablet', 'vtgate', 'all']

def define_args():
    ap = argparse.ArgumentParser(description='Vitess Cluster Management helper.')

    ap.add_argument('--action', default='generate',
                    choices=ACTION_CHOICES,
                    nargs='*',
                    help='Specify action[s]')

    ap.add_argument('--component', default='all',
                    nargs='*',
                    choices=COMPONENT_CHOICES,
                    help='Specify the component[s] to act on.')

    ap.add_argument('--interactive', type=str2bool, nargs='?',
                    default=True, const=True,
                    metavar='',
                    help='Turn interactive mode on or off.')

    ap.add_argument('--external-mysql', type=str2bool, nargs='?',
                    default=False, const=True,
                    metavar='',
                    help='Generate scripts that work with a RDS')

    ap.add_argument('--use-config-without-prompt', type=str2bool, nargs='?',
                    default=False, const=True,
                    metavar='',
                    help='If we find a config, use it without asking.')

    ap.add_argument('--verbose', type=str2bool, nargs='?',
                    default=True, const=True,
                    metavar='',
                    help='Turn verbose mode on or off.')

    ap.add_argument('--add', type=str2bool, nargs='?',
                    default=False, const=True,
                    metavar='',
                    help='Add to currently configured components.')

    ap.add_argument('--vtctld-addr',
                    metavar='',
                    help='Specify vtctld-addr (useful in non-interactive mode).')
    return ap

def create_start_cluster(vtctld_host, vtgate_host, tablets, dbname):
    cell = CELL
    keyspace = KEYSPACE
    deployment_dir = DEPLOYMENT_DIR
    tlines = []
    for t in tablets:
        alias = t['alias']
        host = t['host']
        web_port = t['web_port']
        l = '\tAccess tablet %(alias)s at http://%(host)s:%(web_port)s/debug/status' % locals()
        tlines.append(l)
    tablet_urls = '\n'.join(tlines)
    write_bin_file('start_cluster.sh', read_template('start_cluster.sh') % locals())

def create_destroy_cluster():
    cell = CELL
    write_bin_file('destroy_cluster.sh', read_template('destroy_cluster.sh'))

def fix_google_init_file():
    init_file = os.path.join(VTROOT, 'dist/grpc/usr/local/lib/python2.7/site-packages/google/__init__.py')
    if not os.path.exists(init_file) and os.path.exists(os.path.dirname(init_file)):
        subprocess.call(['touch', init_file])

def create_sharding_workflow_script(ls, vtctld):
    topology_flags = ls.topology_flags
    vtctld_host = vtctld.hostname
    cell = CELL
    keyspace = KEYSPACE
    deployment_dir = DEPLOYMENT_DIR
    deployment_helper_dir = DEPLOYMENT_HELPER_DIR
    write_bin_file('vtworker.sh', read_template('vtworker.sh') % locals())
    write_bin_file('run_sharding_workflow.sh', read_template('run_sharding_workflow.sh') % locals())

def str2bool(v):
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')

def main():
    global args
    parser = define_args()

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)

    args = parser.parse_args()
    actions = args.action
    components = args.component

    if type(actions) is str:
        actions = [actions]
    if type(components) is str:
        components = [components]
    if 'run_demo' in actions:
        actions = ['generate', 'run_demo']
        # the default for components is 'all' which is what we want.
    if 'all' in components:
        components = COMPONENT_CHOICES
        components.remove('all')

    print 'Will perform the action[s]: %s' % ' '.join(['"%s"' % a for a in actions])
    print 'On component[s]: %s' % ' '.join(['"%s"' % c for c in components])
    print
    public_hostname = get_public_hostname()
    check_host()
    c_instances = {}
    c_instances['lockserver'] = LockServer()
    if 'vtctld' in components or 'vttablet' in components:
        c_instances['vtctld'] = VtCtld(public_hostname, c_instances['lockserver'])
    if 'vtgate' in components:
        c_instances['vtgate'] = VtGate(public_hostname, c_instances['lockserver'])
    if 'vttablet' in components:
        c_instances['vttablet'] = VtTablet(public_hostname, c_instances['lockserver'], c_instances['vtctld'])
        global MYSQL_AUTH_PARAM
        MYSQL_AUTH_PARAM = c_instances['vttablet'].dbconfig.get_mysql_auth_param()
    # TODO: sort actions
    # TODO: sort components
    for action in actions:
        if action == 'run_demo':
            continue
        if action == 'generate':
            print
            print 'Generating scripts under: %s' % os.path.join(DEPLOYMENT_DIR, 'bin')
            print
        for component in components:
            c_instances[component].run_action(action)

    if 'run_demo' in actions:
            run_demo(c_instances['lockserver'], c_instances['vtctld'], c_instances['vtgate'], c_instances['vttablet'])

def run_demo(ls, vtctld, vtgate, vttablets):
    create_start_cluster(vtctld.hostname, vtgate.hostname, vttablets.tablets, vttablets.dbconfig.get_dbname())
    create_destroy_cluster()
    create_sharding_workflow_script(ls, vtctld)
    print '\t%s' % 'start_cluster.sh'
    print '\t%s' % 'destroy_cluster.sh'
    print '\t%s' % 'run_sharding_workflow.sh'
    print
    start_cluster = os.path.join(DEPLOYMENT_DIR, 'bin', 'start_cluster.sh')
    response = read_value('Run "%s" to start cluster now? :' % start_cluster, 'Y')
    if response == 'Y':
        try:
            subprocess.call(['bash', start_cluster])
        except KeyboardInterrupt:
            sys.stdout.flush()
            time.sleep(1)
    print
    run_sharding = os.path.join(DEPLOYMENT_DIR, 'bin', 'run_sharding_workflow.sh')
    response = read_value('Run "%s" to demo sharding workflow now? :' % run_sharding, 'Y')
    if response == 'Y':
        subprocess.call(['bash', run_sharding])

if __name__ == '__main__':
    main()
