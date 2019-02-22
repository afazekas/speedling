global_cfg = {'use_pip': True,
              'use_git': True,
              'use_pkg': True}
util.dict_merge(conf.GLOBAL_CONFIG, global_cfg)
# You can skip steps if your image is well prepeared ^ use_

my_controller_services = {'nova-api', 'nova-consoleauth', 'nova-scheduler', 'nova-conductor', 'nova-novncproxy','nova-placement-api',
                          'glance-api', 'glance-registry', 'glance-scrubber',
                          'neutron-server', 'neutron-openvswitch-agent', 'neutron-dhcp-agent', 'neutron-metadata-agent', 'neutron-metering-agent', 'neutron-lbaasv2-agent', 'neutron-l3-agent'
                          'cinder-backup', 'cinder-api', 'cinder-scheduler', 'cinder-volume',
                          'heat-api', 'heat-api-cfn', 'heat-engine',
                          'ironic-api', 'ironic-conductor',
                          'aodh', 'aodh-evaluator', 'aodh-notifier', 'aodh-listener',
                          'gnocchi', 'gnocchi-metricd',
                          'designate-api', 'designate-central', 'designate-zone-manager', 'designate-mdns', 'designate-pool-manager', 'designate-sink',
                          'manila-scheduler', 'manila-api', 'manila-scheduler',
                          'sahara-api', 'sahara-engine',
                          'ceilometer-api', 'ceilometer-notification', 'ceilometer-central', 'ceilometer-collector',
                          'swift-object', 'swift-container', 'swift-account', 'swift-proxy',
                          'zaqar',
                          'mariadb', 'rabbitmq', 'haproxy', 'swift_demo', 'dns_demo', 'swift_proxy', 'swift-container-sync',
                          'mistral-api', 'mistral-engine', 'ceph-mon', 'ceph-osd', 'ceph-mgr', 'openvswitch'}


my_worker_services = {'nova-compute', 'neutron-openvswitch-agent', 'ceilometer-compute'}
#TODO del this: Temp for test:
my_controller_services= {'haproxy', 'mariadb', 'rabbit', 'keystone', 'memcached', 'neutron-server', 'neutron-dhcp-agent', 'neutron-metadata-agent', 'neutron-l3-agent', 'glance-api', 'glance-registry', 'nova-api', 'nova-consoleauth', 'nova-scheduler', 'nova-conductor', 'nova-novncproxy', 'cinder-backup', 'cinder-api', 'cinder-scheduler', 'cinder-volume', 'nova-placement-api','swift-object', 'swift-container', 'swift-account', 'swift-proxy', 'swift-container-sync', 'neutron-metering-agent', 'ceph-mon', 'ceph-osd', 'ceph-mgr', 'openvswitch'}
my_worker_services = {'nova-compute', 'neutron-openvswitch-agent', 'openvswitch'}

import socket

hostname = socket.gethostname()

from  osinsutils import netutils

addr = netutils.discover_default_route_src_addr()

# pseudo net_addresses
# sshed address, the address used for make the ssh connection
# default_gw, the address of the interface with defaul_gw
inv.inventory_register_node(hostname,
             {'hostname': hostname,
              'addresses': {'ssh': addr, 'listen_ip': addr,
                            'tunnel_ip': addr},
              'networks': {
#                             'data_bond': {'interfces': {'eth5','eth6'}, 'mtu':9200},
#                             'data': {'vlan':42, 'child_of': 'data_bond', 'preferred_addr_type': 'ipv6'},
#                             'management': {'interfaces': {'eth0'}, 'addresses': 'default_gw', 'pourpuses': {'sshnet', 'managemenet', 'image'} },
                          },
              'routes': { 'target': '10.0.0.0/24', 'via_ifs': {'eth7','eth8'}, 'next_hop': {'10.0.0.42', '10.0.0.43'} }, # specify if OR addr
              'extra_interfaces': {'br_ex': {'if_type': 'ovs_bridge', 'addresses': '127.0.0.1'}},
              'default_listen_strategy': 'all_if', # alt specific
              'default_ssh_address_strategy': 'inventory', #sshnet
              'ssh_user': 'stack',
              'ssh_address': addr,
               'apache_wsgi_services': {'dashboard', 'keystone', 'aodh', 'gnocchi'},
              'apache_wsgi_services': {'keystone'},
              'services': my_controller_services.union(my_worker_services),
              'components': ['python-openstackclient', 'tempest', 'requirements'],
              'uwsgi_services': ['zaqar'],
              'swift_object_disks': [],
              'swift_account_disks': [],
              'swift_container_disks': [],
              'ceph_osd_disks': [],
              'board_uuid': 'str_uuid',
              'devices': {'disks', 'nics', 'other_pci'},  # TODO: reshape this
              'nic_phy_log_con': {'eth0': 'openstack_net'}})

