#!/usr/bin/env python3
# it is a python >3.6 like DSL, but long lines are allowed! ;-)


import os


from osinsutils import cfgfile

import logging

from speedling import receiver
from speedling import control
from speedling import inv
from speedling import facility
from speedling import util
from speedling import conf

import speedling.tasks

# These import never ment to called like this, it is a transition
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

haproxy = speedling.srv.haproxy.HAProxy()
memcached = speedling.srv.common.Memcached()
mariadb = speedling.srv.mariadb.MariaDB(dependencies={'loadbalancer': haproxy})

osclient = speedling.srv.osclients.PythonOpenstackClient()
keystone = speedling.srv.keystone.Keystone(dependencies={'loadbalancer': haproxy,
                                                         'sql': mariadb,
                                                         'memcached': memcached})
rabbitmq = speedling.srv.rabbitmq.RabbitMQ()
ceph = speedling.srv.ceph.Ceph()
glance = speedling.srv.glance.Glance(dependencies={'loadbalancer': haproxy,
                                                   'sql': mariadb,
                                                   'memcached': memcached,
                                                   'messaging': rabbitmq,
                                                   'keystone': keystone,
                                                   'backends': [{'sname': 'slceph:rbd', 'component': ceph}]})

cinder = speedling.srv.cinder.Cinder(dependencies={'loadbalancer': haproxy,
                                                   'sql': mariadb,
                                                   'memcached': memcached,
                                                   'messaging': rabbitmq,
                                                   'keystone': keystone,
                                                   'backends': [{'sname': 'slceph:rbd', 'component': ceph}]})

swift = speedling.srv.swift.Swift(dependencies={'loadbalancer': haproxy,
                                                'keystone': keystone,
                                                'memcached': memcached})

# The thing which takes care of the vm ports
interface_driver = speedling.srv.neutron.NeutronML2OVS(dependencies={'messaging': rabbitmq})
virt_driver = speedling.srv.nova.Libvirt()


neutron = speedling.srv.neutron.Neutron(dependencies={'loadbalancer': haproxy,
                                                      'sql': mariadb,
                                                      'memcached': memcached,
                                                      'osclient': osclient,
                                                      'messaging': rabbitmq,
                                                      'keystone': keystone,
                                                      'ifdrivers': {'openvswitch': interface_driver}})


nova = speedling.srv.nova.Nova(dependencies={'loadbalancer': haproxy,
                                             'sql': mariadb,
                                             'memcached': memcached,
                                             'messaging': rabbitmq,
                                             'keystone': keystone,
                                             'virtdriver': virt_driver,
                                             'cells': {'cell0': {'messaging': rabbitmq}, 'sql': mariadb},
                                             'networking': neutron,
                                             'backends': [{'sname': 'slceph:rbd', 'component': ceph}]})


# added the things tempest creates resource at setup time
speedling.srv.tempest.Tempest(dependencies={'keystone': keystone,
                                            'osclient': osclient,
                                            'neutron': neutron,
                                            'nova': nova,
                                            'glance': glance,
                                            'enabled_components': [keystone, glance, nova, neutron, swift, cinder]})


LOG = logging.getLogger(__name__)

UNIT_PREFIX = 'sl-'

# TODO: find a handler which prefixes every line and not escaping the '\n' .
logging.basicConfig(level=logging.INFO,
                    format='%(thread)d %(created)f %(levelname)s %(name)s %(message)s')

LOG.info("Started ..")

# TODO: configure netdevs with os-net-config

# This is dummy demo network address fetching code, just for all in one!
# it must be more generic


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

# NOTE: the agent only nodes might not need the db credentials


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
    # any argless function can be a task,
    # it will be called only onece, and only by
    # the `root/controller` node, the task itself has to interact
    # with the remote nodes by calling do_ -s on them

    # facility.add_goals([speedling.tasks.task_net_config,
    #                     speedling.tasks.task_hostname])
    gconf = conf.get_global_config()
    service_flags = gconf['global_service_flags']
    component_flags = gconf['global_component_flags']

#    funs = facility.get_compose(service_flags, component_flags)
#    for f in funs:
#        f()
    facility.compose()

    inv.set_identity()
    goals = facility.get_goals(service_flags, component_flags)
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
