#!/usr/bin/env python3
# it is a python >3.6 like DSL, but long lines are allowed! ;-)


import logging
import os
import socket

import slos.ceph
import slos.cinder
import slos.glance
import slos.haproxy
import slos.keystone
import slos.mariadb
import slos.memcached
import slos.neutron
import slos.nova
import slos.osclients
import slos.rabbitmq
import slos.swift
import slos.tempest
import speedling.sl
from speedling import cfgfile
from speedling import conf
from speedling import facility
from speedling import inv
from speedling import invutil
from speedling import netutils
from speedling import util

LOG = logging.getLogger(__name__)
UNIT_PREFIX = 'sl-'

# TODO: find a handler which prefixes every line and not escaping the '\n' .
logging.basicConfig(level=logging.INFO,
                    format='%(thread)d %(created)f %(levelname)s %(name)s %(message)s')

LOG.info("Started ..")


def createmodel():
    myhaproxy = slos.haproxy.HAProxy()
    mymemcached = slos.memcached.Memcached()
    mymariadb = slos.mariadb.MariaDB(dependencies={'loadbalancer': myhaproxy})

    myosclient = slos.osclients.PythonOpenstackClient()
    mykeystone = slos.keystone.Keystone(dependencies={'loadbalancer': myhaproxy,
                                                      'sql': mymariadb,
                                                      'memcached': mymemcached})
    myrabbitmq = slos.rabbitmq.RabbitMQ()
    myceph = slos.ceph.Ceph()
    myglance = slos.glance.Glance(dependencies={'loadbalancer': myhaproxy,
                                                'sql': mymariadb,
                                                'memcached': mymemcached,
                                                'messaging': myrabbitmq,
                                                'keystone': mykeystone,
                                                'backends': [{'sname': 'slceph:rbd', 'component': myceph}]})

    mycinder = slos.cinder.Cinder(dependencies={'loadbalancer': myhaproxy,
                                                'sql': mymariadb,
                                                'memcached': mymemcached,
                                                'messaging': myrabbitmq,
                                                'keystone': mykeystone,
                                                'backends': [{'sname': 'slceph:rbd', 'component': myceph}]})

    myswift = slos.swift.Swift(dependencies={'loadbalancer': myhaproxy,
                                             'keystone': mykeystone,
                                             'memcached': mymemcached})

    # The thing which takes care of the vm ports
    interface_driver = slos.neutron.NeutronML2OVS(dependencies={'messaging': myrabbitmq})
    myvirt_driver = slos.nova.Libvirt()

    myneutron = slos.neutron.Neutron(dependencies={'loadbalancer': myhaproxy,
                                                   'sql': mymariadb,
                                                   'memcached': mymemcached,
                                                   'osclient': myosclient,
                                                   'messaging': myrabbitmq,
                                                   'keystone': mykeystone,
                                                   'ifdrivers': {'openvswitch': interface_driver}})

    mynovnc = slos.nova.NoVNC()

    mynova = slos.nova.Nova(dependencies={'loadbalancer': myhaproxy,
                                          'sql': mymariadb,
                                          'memcached': mymemcached,
                                          'messaging': myrabbitmq,
                                          'keystone': mykeystone,
                                          'novncweb': mynovnc,
                                          'virtdriver': myvirt_driver,
                                          'cells': {'cell0': {'messaging': myrabbitmq}, 'sql': mymariadb},
                                          'networking': myneutron,
                                          'backends': [{'sname': 'slceph:rbd', 'component': myceph}]})

    # added the things tempest creates resource at setup time
    slos.tempest.Tempest(dependencies={'keystone': mykeystone,
                                       'osclient': myosclient,
                                       'neutron': myneutron,
                                       'nova': mynova,
                                       'glance': myglance,
                                       'enabled_components': [mykeystone, myglance, mynova, myneutron, myswift, mycinder]})


# TODO: purge(db) cron jobs

# not required at this point, but both horizion and keystone could use it
# ### localsh.run("systemctl start memcached redis mongod")

# NOTE: the agent only nodes might not need the db credentials

# 1 establish sources
# 2 fetch packages and (data files start in bg)
# 3 print cfg files
# 4 start lb | start ceph install | start swift install | start rabbit
# start mysql | ntp
# 5 manage keystone # wait for db access | manage all other dbs (gnocchi
#                                                          wait for ceph)
# 6 start keystone # wait for access
# 7 sync endpoint | sync os users
# 8 realod_or_restart anything after his db sync
# and all keystone sync finished finished
# 9 neutron sync


def all_in_one_inv():

    hostname = socket.gethostname()

    addr = netutils.discover_default_route_src_addr()

    # pseudo net_addresses
    # sshed address, the address used for make the ssh connection
    # default_gw, the address of the interface with defaul_gw
    inventory = {}
    hosts = {hostname: {'hostname': hostname,
                        'addresses': {'ssh': addr, 'listen_ip': addr},
                        'ssh_user': 'stack',
                        'sl_ssh_address': addr,
                        'ssh_address': addr}}

    inventory['hosts'] = hosts
    inventory['host_in_group'] = {'aio': [hostname]}
    return inventory


def inv_extend(inventory, my_controller_services, my_worker_services):

    hg = inventory['host_in_group']
    hosts = inventory['hosts']

    if 'controller' in hg:
        for h in hg['controller']:
            var = hosts[h]
            inv.inventory_register_node(h, {'hostname': h,
                                            'networks': var.get('sl_networks', {}),
                                            'ssh_user': 'stack',
                                            'ssh_address': var['sl_ssh_address'],
                                            'services': my_controller_services,
                                            'extra_components': ['pythonopenstackclient', 'tempest', 'novnc', 'requirements']})

    if 'worker' in hg:
        for h in hg['worker']:
            var = hosts[h]
            inv.inventory_register_node(h, {'hostname': h,
                                            'networks': var.get('sl_networks', {}),
                                            'ssh_user': 'stack',
                                            'ssh_address': var['sl_ssh_address'],
                                            'services': my_worker_services,
                                            'extra_components': ['pythonopenstackclient', 'requirements']})
    if 'aio' in hg:
        for h in hg['aio']:
            var = hosts[h]
            inv.inventory_register_node(h, {'hostname': h,
                                            'networks': var.get('sl_networks', {}),
                                            'ssh_user': 'stack',
                                            'ssh_address': var['sl_ssh_address'],
                                            'services': set.union(my_worker_services, my_controller_services),
                                            'extra_components': ['pythonopenstackclient', 'requirements', 'tempest', 'novnc']})


def create_inventory_and_glb():
    args = conf.get_args()
    gconf = conf.GLOBAL_CONFIG
    if args.all_in_one:
        invent = all_in_one_inv()
        # hack until not switching to the new form
        addr = list(invent['hosts'].values())[0]['ssh_address']
        gconf['vip'] = {'public': {'domain_name': addr, 'internal_address': addr},
                        'internal': {'domain_name': addr, 'internal_address': addr}}
    else:
        invent = invutil.parse_ansible_invetory_ini(args.inv_extend)
        # hack until not switching to the new form , also not ordered..
        controller = next(iter(invent['host_in_group']['controller']))
        addr = invent['hosts'][controller]['sl_ssh_address']
        gconf['vip'] = {'public': {'domain_name': addr, 'internal_address': addr},
                        'internal': {'domain_name': addr, 'internal_address': addr}}

    my_controller_services = {'haproxy', 'mariadb', 'rabbit', 'keystone', 'memcached',
                              'neutron-server', 'neutron-dhcp-agent', 'neutron-metadata-agent',
                              'neutron-l3-agent', 'glance-api', 'glance-registry', 'nova-api',
                              'nova-consoleauth', 'nova-scheduler', 'nova-conductor',
                              'nova-novncproxy', 'cinder-backup', 'cinder-api',
                              'cinder-scheduler', 'cinder-volume', 'ceph-osd',
                              'ceph-mgr', 'ceph-mon',  'nova-placement-api', 'swift-object',
                              'swift-container', 'swift-account', 'swift-proxy',
                              'neutron-metering-agent', 'swift-container-sync',
                              'neutron-openvswitch-agent', 'openvswitch', 'neutron-metering-agent'}
    my_worker_services = {'nova-compute', 'neutron-openvswitch-agent', 'openvswitch', 'libvirtd'}

    inv_extend(invent, my_controller_services, my_worker_services)

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


def extra_config_opts(parser):
    inventory_opt = parser.add_mutually_exclusive_group()
    inventory_opt.add_argument('-i', '--inv-extend',
                               help='Intial inventory file for transformation',
                               default='speedling.ini')  # TODO: move to yaml
    inventory_opt.add_argument('--all-in-one',
                               action='store_true',
                               help='make on inventory from the local node in group of aio')


def pre_flight():
    args = conf.get_args()
    state_dir = args.state_dir
    cfgfile.content_file(state_dir + '/admin-openrc.sh',
                         util.userrc_script('admin'), owner=os.getuid(), group=os.getgid())
    keystone = facility.get_component('keystone')
    keystone.register_project_in_domain('Default', 'demo', 'demo project')
    keystone.register_user_in_domain('Default', 'demo',
                                     password=util.get_keymgr()('keystone', 'demo@default'),
                                     email='demo_user@noreply.com',
                                     project_roles={('Default', 'demo'): ['user']})

    cfgfile.content_file(state_dir + '/demo-openrc.sh',
                         util.userrc_script('demo'), owner=os.getuid(), group=os.getgid())


def main():
    createmodel()
    speedling.sl.main(create_inventory_and_glb, globals(),
                      extra_config_opts=extra_config_opts,
                      pre_flight=pre_flight)


if __name__ == '__main__':
    main()
