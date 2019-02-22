#!/usr/bin/env python3
# it is a python >3.6 like DSL, but long lines are allowed! ;-)


import os


from osinsutils import cfgfile
from osinsutils import localsh

import logging

from speedling import receiver
from speedling import control
from speedling import inv
from speedling import facility
from speedling import util
from speedling import conf

import speedling.tasks
import speedling.srv.common
import speedling.srv.rabbitmq
import speedling.srv.mariadb
import speedling.srv.keystone
import speedling.srv.glance
import speedling.srv.nova
import speedling.srv.neutron
import speedling.srv.cinder
import speedling.srv.swift
import speedling.srv.tempest
import speedling.srv.osclients
import speedling.srv.ceph
import speedling.srv.haproxy

LOG = logging.getLogger(__name__)

UNIT_PREFIX = 'sl-'

# TODO: find a handler which prefixes every line and not escaping the '\n' .
logging.basicConfig(level=logging.INFO,
                    format='%(thread)d %(created)f %(levelname)s %(name)s %(message)s')

LOG.info("Started ..")

# TODO: configure netdevs with os-net-config

# This is dummy demo network address fetching code, just for all in one!
# it must be more generic


def default_packages(distro, distro_version):
    # NOTE: fedora rsync-daemon not just rsync

    # I am expecting fully poppulated images
    # it just makes sure it is ok
    # TODO:add other ditros
    # TODO: split to per component
    # TODO: add option for pkg/pip
    # TODO: add option for containers
    # TODO: check install (skipped pkg)
    return set(['python3-devel',
                'python2-devel', 'graphviz', 'novnc', 'openldap-devel', 'python3-mod_wsgi',
                'httpd', 'libffi-devel', 'libxslt-devel', 'mariadb-server', 'mariadb-devel', 'galera',
                'httpd-devel', 'rabbitmq-server', 'openssl-devel',
                'python3-numpy', 'python3-ldap', 'python3-dateutil', 'python3-psutil', 'pyxattr', 'xfsprogs', 'liberasurecode-devel',
                'python3-libguestfs', 'cryptsetup', 'libvirt-client',
                'memcached',
                'iptables', 'haproxy', 'ipset', 'radvd', 'openvswitch', 'conntrack-tools',
                'pcp-system-tools',
                'python3-libguestfs',
                'gcc-c++', 'pcs', 'pacemaker',
                'rsync-daemon', 'python2-keystonemiddleware', 'python3-PyMySQL',
                'ceph-mds', 'ceph-mgr', 'ceph-mon', 'ceph-osd', 'ceph-radosgw', 'redis python3-redis', 'python3-memcached',
                'python3-libvirt', 'python3-keystoneauth1', 'python3-keystoneclient', 'python3-rbd',
                'python2-subunit', 'python2-jsonschema', 'python2-paramiko'])
    # rsyslog, os-net-config, jq ..


# TODO: add option for skipping package install step (long, frequenly not needed)
def do_pkg_fetch():
    pkgs = default_packages('fedora', '29')
    inv_entry = inv.get_this_inv()
    comp = set(inv_entry.get('components', tuple()))
    func_set = set()
    for c in comp:
        component = facility.get_component(c)
        f = component.get('pkg_deps', None)
        if f:
            func_set.add(f)

    selected_services = inv_entry['services']
    for srv in selected_services:
        try:
            s = facility.get_service_by_name(srv)
            f = s['component'].get('pkg_deps', None)
            if f:
                func_set.add(f)
        except Exception:
            # TODO: remove excption, let it fail, preferably earlier
            LOG.warn('Service "{srv}" is not a registered service'.format(srv=srv))
    u = set.union(*[f() for f in func_set])
    LOG.info("Installing packages ..")
    localsh.run("yum update -y || yum update -y || yum update -y || yum update -y ")
    localsh.run(' || '.join(["yum install -y {pkgs}".format(pkgs=' '.join(pkgs.union(u)))]*4))


def tempest_deployer_input_conf(): return {
    'auth': {'tempest_roles': 'user'},
    'compute-feature-enabled': {'console_output': True,
                                'attach_encrypted_volume': False},
    'object-storage': {'operator_role':  'user',
                       'reseller_admin_role': 'admin'},
    'orchestration': {'stack_owner_role': 'user'},
    'volume': {'backends_name': 'ceph',  # glo var ?
               'storage_protocol': 'ceph'},
    'volume-feature-enabled': {'bootable': True}}


# TODO: purge(db) cron jobs

# not required at this point, but both horizion and keystone could use it
# ### localsh.run("systemctl start memcached redis mongod")

# NOTE: the agent only nodes might not need the db credntials


# seams cheaper to have one task for all etc like cfg steps,
# than managing many small functions, even tough it could be paralell op with multi functions
# 'service_union' union of all services from all hosts,
# in order to know for example do we have lbaas anywhere
# globale feature flag for example: 'neutron-fwaas'


# we might split this funcion later ..
def local_etccfg_steps(host_record, service_union_global_flags):
    # aws = host_record.get('apache_wsgi_services', set())
    # uws = host_record.get('uwsgi_services', set())  # spread
    services = host_record.get('services', set())

    cfgfile.ensure_path_exists('/srv', mode=0o755)

    steps = facility.get_cfg_steps(services)
    for step in steps:
        # TODO: do not pass args they can get it..
        step(services=services, global_service_union=service_union_global_flags)

    localsh.run('systemctl daemon-reload')


# any argless function can be a task,
# it will be called only onece, and only by
# the `root` node, the task itself has to interact
# with the remote nodes


def task_establish_repos():
    # rdo_repos()
    pass


def task_pkg_install():
    # facility.task_wants(task_establish_repos)
    gconf = conf.get_global_config()
    need_pkgs = gconf.get('use_pkg', True)
    if need_pkgs:
        inv.do_do(inv.ALL_NODES, do_pkg_fetch)


def do_local_etccfg_steps():
    local_etccfg_steps(inv.get_this_inv(), set())


def task_cfg_etccfg_steps():
    facility.task_wants(task_pkg_install)
    assert inv.ALL_NODES
    inv.do_do(inv.ALL_NODES, do_local_etccfg_steps)


def create_inventory_and_glb():
    c = open(conf.get_args().config)
    cfg = c.read()
    c.close()
    exec(cfg)
    # move to the peering logic
    gconf = conf.get_global_config()
    service_flags = set()
    EMPTY_SET = set()
    global_component_flags = set()
    gconf['global_service_flags'] = service_flags
    gconf['global_component_flags'] = global_component_flags
    for n, node in inv.INVENTORY.items():
        services = node.get('services', EMPTY_SET)
        service_flags.update(services)
        components = node.get('components', EMPTY_SET)
        global_component_flags.update(components)


def do_dummy_netconfig():
    localsh.run('systemctl start openvswitch.service')

    # TODO switch to os-net-config
    # wait (no --no-wait)
    localsh.run('ovs-vsctl --may-exist add-br br-ex')

    # add ip to external bridge instead of adding a phyisical if
    localsh.run("""
    ifconfig br-ex 192.0.2.1
    ip link set br-ex up
    ROUTE_TO_INTERNET=$(ip route get 8.8.8.8)
    OBOUND_DEV=$(echo ${ROUTE_TO_INTERNET#*dev} | awk '{print $1}')
    iptables -t nat -A POSTROUTING -o $OBOUND_DEV -j MASQUERADE
    tee /proc/sys/net/ipv4/ip_forward <<<1 >/dev/null
    """)


def task_net_config():
    # This is temporary here, normally it should do interface persistent config
    facility.task_wants(task_pkg_install)
    inv.do_do(inv.hosts_with_service('neutron-l3-agent'),
              do_dummy_netconfig)


def _main():
    args = conf.get_args()
    # nic_phy_log_con, physical L2 native network's logical name or id, the one which could be
    # advertised by a router/switch/other computer  and other nodes can use it for autodetect
    # NOTE: instead of nic names, we might use pci bus address reported by the discovery

    # hostname should be fqdn, the first part should be uniq, the legth should be _less_ than 64 character
    state_dir = args.state_dir
    cfgfile.content_file(state_dir + '/admin-openrc.sh',
                         util.userrc_script('admin'), owner=os.getuid(), group=os.getgid())
    facility.register_project_in_domain('Default', 'demo', 'demo project')
    facility.register_user_in_domain('Default', 'demo',
                                     password=util.get_keymgr()('os', 'demo@default'),
                                     email='demo_user@noreply.com',
                                     project_roles={('Default', 'demo'): ['user']})

    cfgfile.content_file(state_dir + '/demo-openrc.sh',
                         util.userrc_script('demo'), owner=os.getuid(), group=os.getgid())

    cfgfile.ini_file_sync(state_dir + '/tempest-deployer-input.conf',
                          tempest_deployer_input_conf(), owner=os.getuid(), group=os.getgid())
    goals = [task_net_config, speedling.tasks.task_hostname]
    # NOTE: less repeatetive not too confusing way ?
    gconf = conf.get_global_config()
    service_flags = gconf['global_service_flags']
    component_flags = gconf['global_component_flags']

    funs = facility.get_compose(service_flags, component_flags)
    for f in funs:
        f()

    inv.set_identity()
    inv.distribute_as_file(inv.ALL_NODES, b'test_content', '/tmp/test')
    inv.distribute_for_command(inv.ALL_NODES, b'test shell content', 'tee -a /tmp/testcmd')
    goals.extend(facility.get_goals(service_flags, component_flags))
    facility.start_pending()
    facility.task_wants(*goals)
    # facility.task_will_need(task_ntp, task_selinux, ctx)
    # TODO make thes function calls float weighted dict elements and
    #    iterate by float
    # TODO add patch extension
    # 0 init credentials manager
    #   init()
    # 1 establish sources

    # 2 fetch packages and (data files start in bg)
    # 3 print cfg files
    # 4 start lb | start ceph install | start swift install | start rabbit
    #    # start mysql | ntp
    # 5 manage keystone # wait for db access | manage all other dbs (gnocchi
    #                                                          wait for ceph)
    # 6 start keystone # wait for access
    # 7 sync endpoint | sync os users
    # 8 realod_or_restart anything after his db sync
    # and all keystone sync finished finished
    # 9 neutron sync


def main():
    args = conf.get_args()

    if util.is_receiver():
        receiver.initiate(globals())
        return  # waiting for child threads
    else:
        create_inventory_and_glb()
    if args.identity:
        inv.inventory_set_local_node(args.identity)
    inv.process_net()
    remotes = inv.ALL_NODES - inv.THIS_NODE
    for r in remotes:
        control.init_connection(r, host_address=inv.INVENTORY[r].get('ssh_address', r), user='stack')
    _main()


if __name__ == '__main__':
    main()
