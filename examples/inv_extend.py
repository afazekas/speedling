import ast
from osinsutils import invutil

opt_str = conf.get_args().config_option
try:
   cfg = ast.literal_eval(opt_str)
except:
   print(opt_str)
   raise
inventory = invutil.parse_ansible_invetory_ini(cfg['inventory'])

my_controller_services= {'haproxy', 'mariadb', 'rabbit', 'keystone', 'memcached', 'neutron-server', 'neutron-dhcp-agent', 'neutron-metadata-agent', 'neutron-l3-agent', 'glance-api', 'glance-registry', 'nova-api', 'nova-consoleauth', 'nova-scheduler', 'nova-conductor', 'nova-novncproxy', 'cinder-backup', 'cinder-api', 'cinder-scheduler', 'cinder-volume', 'ceph-osd', 'ceph-mgr', 'ceph-mon',  'nova-placement-api','swift-object', 'swift-container', 'swift-account', 'swift-proxy', 'neutron-meter-agent', 'swift-container-sync', 'neutron-openvswitch-agent', 'openvswitch', 'neutron-metering-agent'}
my_worker_services = {'nova-compute', 'neutron-openvswitch-agent', 'openvswitch'}

hg = inventory['host_in_group']
hosts = inventory['hosts']

# Excpeting fully populated images
global_cfg = {'use_pip': False,
              'use_git': False,
              'use_pkg': False}
util.dict_merge(conf.GLOBAL_CONFIG, global_cfg)

if 'controller' in hg:
   for h in hg['controller']:
       var = hosts[h]
       inv.inventory_register_node(h,
             {'hostname': h,
              'networks': var.get('sl_networks', {}),
              'extra_interfaces': {'br_ex': {'if_type': 'ovs_bridge', 'addresses': '127.0.0.1'}},
              'default_listen_strategy': 'all_if', # alt specific
              'default_ssh_address_strategy': 'inventory', #sshnet
              'ssh_user': 'stack',
              'ssh_address': var['sl_ssh_address'],
              'services': my_controller_services,
              'components': ['python-openstackclient', 'tempest', 'requirements'],
              'board_uuid': 'str_uuid',
              })

if 'compute' in hg:
   for h in hg['compute']:
       var = hosts[h]
       inv.inventory_register_node(h,
             {'hostname': h,
              'networks': var.get('sl_networks', {}),
              'default_listen_strategy': 'all_if', # alt specific
              'default_ssh_address_strategy': 'inventory', #sshnet
              'ssh_user': 'stack',
              'ssh_address': var['sl_ssh_address'],
              'services': my_worker_services,
              'components': ['python-openstackclient', 'requirements'],
              'board_uuid': 'str_uuid',
              })
