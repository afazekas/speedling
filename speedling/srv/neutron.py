from speedling import util
from speedling import inv
from speedling import conf
from speedling import gitutils
from speedling import tasks
import speedling
from speedling import facility

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
from speedling.srv import mariadb
from speedling.srv import rabbitmq

import logging

LOG = logging.getLogger(__name__)


def do_ovs():
    localsh.run('systemctl start openvswitch.service')


def ovs_etccfg(services):
    pass


def task_ovs():
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)
    # TODO add concept for implied service
    ovss = inv.hosts_with_any_service({'neutron-openvswitch-agent', 'ovs'})
    inv.do_do(ovss, do_ovs)


q_srv = {'neutron-server', 'neutron-openvswitch-agent', 'neutron-vpn-agent',
         'neutron-dhcp-agent', 'neutron-metadata-agent', 'neutron-l3-agent',
         'neutron-metering-agent', 'neutron-lbaasv2-agent'}


def etc_neutron_conf_d_common_agent_conf(): return {'agent': {'root_helper': "sudo /usr/local/bin/neutron-rootwrap /etc/neutron/rootwrap.conf",
                                                              'root_helper_daemon': "sudo /usr/local/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf"}}


def etc_neutron_metadata_agent_ini():
    ivip = conf.get_vip('internal')['domain_name']
    return {'DEFAULT': {'nova_metadata_ip':  ivip,
                        'metadata_proxy_shared_secret':
                        util.get_keymgr()('shared_secret', 'neutron_nova_metadata')}}


def etc_neutron_neutron_conf():
    gconf = conf.get_global_config()
    service_flags = gconf['global_service_flags']

    service_pulugins = ['neutron.services.l3_router.l3_router_plugin.L3RouterPlugin']
    if 'neutron-fwaas' in service_flags:
        service_pulugins.append('neutron_fwaas.services.firewall.fwaas_plugin.FirewallPlugin')
    if 'neutron-vpn-agent' in service_flags:
        service_pulugins.append('neutron_vpnaas.services.vpn.plugin.VPNDriverPlugin')
    if 'neutron-lbaasv2-agent' in service_flags:
        service_pulugins.append('neutron_lbaas.services.loadbalancer.plugin.LoadBalancerPluginv2')
    if 'neutron-metering-agent' in service_flags:
        service_pulugins.append('neutron.services.metering.metering_plugin.MeteringPlugin')

    return {'DEFAULT': {'debug':  'True',
                        'core_plugin': 'ml2',
                        'service_plugins': ','.join(service_pulugins),
                        'state_path': '/var/lib/neutron',
                        'allow_overlapping_ips': 'True',
                        'transport_url': rabbitmq.transport_url()},
            'database': {'connection': mariadb.db_url('neutron')},
            'keystone_authtoken': util.keystone_authtoken_section('neutron'),
            'nova': util.keystone_authtoken_section('nova_for_neutron'),
            'oslo_concurrency': {'lock_path': '$state_path/lock'}}


def etc_neutron_plugins_ml2_ml2_conf_ini(): return {
    'ml2': {'tenant_network_types': 'vxlan',  # TODO: switch to geneve}
            'mechanism_drivers': 'openvswitch,linuxbridge',
            'extension_drivers': 'port_security'},
    'ml2_type_vxlan': {'vni_ranges': '1001:4001'}
    }


# vpnaas, consider other drivers
def etc_neutron_vpn_agent_ini(): return {
    'vpnagent': {'vpn_device_driver': 'neutron_vpnaas.services.vpn.' +
                                      'device_drivers.fedora_strongswan_ipsec.FedoraStrongSwanDriver'}}


def etc_neutron_neutron_vpnaas_conf(): return {
    'service_providers': {'service_provider': 'VPN:openswan:neutron_vpnaas.services.vpn.service_drivers.ipsec.IPsecVPNDriver:default'}}


# fwaas added, why not.. (TEST ONLY)
def etc_neutron_fwaas_driver_ini(): return {
    'fwaas': {'driver': 'neutron_fwaas.services.firewall.drivers.linux' +
                        '.iptables_fwaas.IptablesFwaasDriver'}}


def neutron_etccfg(services):
    comp = facility.get_component('neutron')
    gconf = conf.get_global_config()
    global_service_union = gconf['global_service_flags']
    usrgrp.group('neutron', 996)
    usrgrp.user('neutron', 993)
    util.base_service_dirs('neutron')
    cfgfile.ensure_path_exists('/etc/neutron/conf.d',
                               owner='neutron', group='neutron')
    cfgfile.ensure_path_exists('/etc/neutron/conf.d/common',
                               owner='neutron', group='neutron')
    cfgfile.ini_file_sync('/etc/neutron/conf.d/common/agent.conf',
                          etc_neutron_conf_d_common_agent_conf(),
                          owner='neutron', group='neutron')
    neutron_git_dir = gitutils.component_git_dir(comp)
    # consider alternate data paths
    # var/lib/neutron/dhcp needs to be reachable by the dnsmasq user
    cfgfile.ensure_path_exists('/var/lib/neutron',
                               owner='neutron', group='neutron',
                               mode=0o755)
    cfgfile.ensure_path_exists('/var/lib/neutron/lock',
                               owner='neutron', group='neutron')

    cfgfile.ensure_path_exists('/etc/neutron/plugins',
                               owner='neutron', group='neutron')
    cfgfile.ensure_path_exists('/etc/neutron/plugins/ml2',
                               owner='neutron', group='neutron')
    cfgfile.ini_file_sync('/etc/neutron/neutron.conf', etc_neutron_neutron_conf(),
                          owner='neutron', group='neutron')
    cfgfile.ensure_sym_link('/etc/neutron/plugin.ini',
                            '/etc/neutron/plugins/ml2/ml2_conf.ini')
    # move to common ?
    cfgfile.ini_file_sync('/etc/neutron/plugins/ml2/ml2_conf.ini',
                          etc_neutron_plugins_ml2_ml2_conf_ini(),
                          owner='neutron', group='neutron')

    q_srv = set(comp['services'].keys())
    if comp['deploy_source'] == 'src':
        if services.intersection(q_srv - {'neutron-server'}):
            cfgfile.content_file('/etc/sudoers.d/neutron', """Defaults:neutron !requiretty

neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap /etc/neutron/rootwrap.conf *
neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf
neutron ALL = (root) NOPASSWD: /usr/local/bin/neutron-rootwrap /etc/neutron/rootwrap.conf *
neutron ALL = (root) NOPASSWD: /usr/local/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf
""")
            cfgfile.ensure_path_exists('/etc/neutron/rootwrap.d',
                                       owner='root')
            # TODO: exclude stuff based on config
            for filter_file in ['debug.filters', 'dibbler.filters', 'ipset-firewall.filters',
                                'l3.filters', 'netns-cleanup.filters', 'privsep.filters',
                                'dhcp.filters', 'ebtables.filters', 'iptables-firewall.filters',
                                'linuxbridge-plugin.filters', 'openvswitch-plugin.filters']:

                cfgfile.install_file('/etc/neutron/rootwrap.d/' + filter_file,
                                     '/'.join((neutron_git_dir,
                                              'etc/neutron/rootwrap.d', filter_file)),
                                     mode=0o444)
        cfgfile.install_file('/etc/neutron/rootwrap.conf',
                             '/'.join((neutron_git_dir,
                                      'etc/rootwrap.conf')),
                             mode=0o444)

#        cfgfile.install_file('/etc/neutron/policy.json',
#                             '/'.join((neutron_git_dir,
#                             'etc/policy.json')),
#                             mode=0o644,
#                             owner='neutron', group='neutron')
        cfgfile.install_file('/etc/neutron/api-paste.ini',
                             '/'.join((neutron_git_dir,
                                      'etc/api-paste.ini')),
                             mode=0o644,
                             owner='neutron', group='neutron')
        c_srv = comp['services']
        util.unit_file(c_srv['neutron-server']['unit_name']['src'],
                       '/usr/local/bin/neutron-server --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/plugin.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-metadata-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-metadata-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/metadata_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-l3-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-l3-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/l3_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-metering-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-metering-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/metering_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-vpn-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-vpn-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/l3_agent.ini --config-file /etc/neutron/vpn_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-dhcp-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-dhcp-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/dhcp_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-lbaasv2-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-lbaasv2-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/lbaas_agent.ini',
                       'neutron')
        util.unit_file(c_srv['neutron-openvswitch-agent']['unit_name']['src'],
                       '/usr/local/bin/neutron-openvswitch-agent --config-file /etc/neutron/neutron.conf --config-dir /etc/neutron/conf.d/common --config-file /etc/neutron/plugins/ml2/openvswitch_agent.ini',
                       'neutron', requires='openvswitch.service', restart='on-failure')

    if 'neutron-metadata-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/metadata_agent.ini',
                              etc_neutron_metadata_agent_ini(),
                              owner='neutron', group='neutron')

    if 'neutron-vpn-agent' in services or 'neutron-l3-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/l3_agent.ini', {
            'DEFAULT': {'interface_driver': 'openvswitch',
                        'debug': True}
            }, owner='neutron', group='neutron')

    if 'neutron-metering-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/metering_agent.ini', {
            'DEFAULT': {'interface_driver': 'openvswitch',
                        'debug': True}
            }, owner='neutron', group='neutron')

    if 'neutron-vpn-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/vpn_agent.ini',
                              etc_neutron_vpn_agent_ini(),
                              owner='neutron', group='neutron')

    if 'neutron-dhcp-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/dhcp_agent.ini', {
                'DEFAULT': {'interface_driver': 'openvswitch',
                            'dnsmasq_local_resolv': True,
                            'debug': True}
                }, owner='neutron', group='neutron')

    if 'neutron-lbaasv2-agent' in services:
        cfgfile.ini_file_sync('/etc/neutron/lbaas_agent.ini', {
                              'DEFAULT': {'interface_driver': 'openvswitch',
                                          'debug': True}},
                              owner='neutron', group='neutron')

    if 'neutron-openvswitch-agent' in services:
        tunnel_ip = inv.get_addr_for(inv.get_this_inv(), 'tunneling',
                                     component=comp,
                                     service=comp['services']['neutron-openvswitch-agent'],
                                     net_attr='tunneling_network')
        ovs = {'local_ip': tunnel_ip}
        if 'neutron-l3-agent' in services:
            ovs['bridge_mappings'] = 'extnet:br-ex'
        cfgfile.ini_file_sync('/etc/neutron/plugins/ml2/openvswitch_agent.ini', {
                'securitygroup': {'firewall_driver': 'iptables_hybrid'},
                'ovs': ovs,
                'agent': {'tunnel_types': 'vxlan'}},
            owner='neutron', group='neutron')

    # the inv version is not transfered, let it be part of the global config
    #    global_service_union = inv.get_enabled_services()

    # NOTE: check these fwass,lbaas, vpaans conditions,
    # we might want to update them even if they not present
    if ('neutron-lbaasv2-agent' in services or ('neutron-lbaasv2-agent' in global_service_union and
                                                'neutron-server' in services)):
        cfgfile.ini_file_sync('/etc/neutron/neutron_lbaas.conf', {
                              'service_providers': {'service_provider':
                                                    'LOADBALANCERV2:Haproxy:' +
                                                    'neutron_lbaas.drivers.haproxy.plugin_driver.HaproxyOnHostPluginDriver' +
                                                    ':default'}}, owner='neutron', group='neutron')
    if ('neutron-vpn-agent' in services or ('neutron-vpn-agent' in global_service_union and
                                            'neutron-server' in services)):
        cfgfile.ini_file_sync('/etc/neutron/neutron_vpnaas.conf',
                              etc_neutron_neutron_vpnaas_conf(),
                              owner='neutron', group='neutron')

    if 'neutron-fwaas' in global_service_union:
        cfgfile.ini_file_sync('/etc/neutron/fwaas_driver.ini',
                              etc_neutron_fwaas_driver_ini(),
                              owner='neutron', group='neutron')


def do_dummy_public_net():
    # guest net hack
    # 192.0.2.1 expected to be configured on an interface
    localsh.run(util.userrc_script('admin') + """
    (
    # --shared vs. test_external_network_visibility
    neutron net-create public --router:external=True --is-default=True --provider:network_type flat --provider:physical_network extnet
    FLOATING_IP_CIDR=${FLOATING_IP_CIDR:-"192.0.2.0/24"}
    FLOATING_IP_START=${FLOATING_IP_START:-"192.0.2.32"}
    FLOATING_IP_END=${FLOATING_IP_END:-"192.0.2.196"}
    EXTERNAL_NETWORK_GATEWAY=${EXTERNAL_NETWORK_GATEWAY:-"192.0.2.1"}
    neutron subnet-create --name ext-subnet --allocation-pool start=$FLOATING_IP_START,end=$FLOATING_IP_END --disable-dhcp --gateway $EXTERNAL_NETWORK_GATEWAY public $FLOATING_IP_CIDR
    # for auto allocation test
    openstack subnet pool create --share --default --pool-prefix 192.0.3.0/24 --default-prefix-length 26  shared-default
    openstack subnet pool create --share --default --pool-prefix 2001:db8:8000::/48 --default-prefix-length 64 default-v6
    )""")


def do_local_neutron_service_start():
    tasks.local_os_service_start_by_component('neutron')


def task_neutron_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps,
                            speedling.srv.keystone.step_keystone_ready,
                            speedling.tasks.task_net_config,
                            speedling.srv.osclients.task_osclients_steps)
    comp = facility.get_component('neutron')
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    schema_node_candidate = inv.hosts_with_service('neutron-server')

    sync_cmd = 'su -s /bin/sh -c "neutron-db-manage --config-file /etc/neutron/neutron.conf --config-file /etc/neutron/plugins/ml2/ml2_conf.ini upgrade head" neutron'
    tasks.subtask_db_sync(schema_node_candidate, schema='neutron',
                          sync_cmd=sync_cmd, schema_user='neutron')
    facility.task_wants(speedling.srv.rabbitmq.task_rabbit_steps, speedling.tasks.task_net_config)

    comp = facility.get_component('neutron')
    q_srv = set(comp['services'].keys())
    inv.do_do(inv.hosts_with_any_service(q_srv), do_local_neutron_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready,
                        speedling.srv.osclients.task_osclients_steps)
    inv.do_do(inv.rand_pick(inv.hosts_with_service('neutron-server')), do_dummy_public_net)


def neutron_pkgs():
    return {'acl', 'dnsmasq', 'dnsmasq-utils', 'ebtables', 'haproxy',
            'iptables', 'iputils', 'mysql-devel', 'radvd', 'sqlite',
            'sudo', 'conntrack-tools', 'keepalived', 'ipset'}


def ovs_pkgs():
    return {'openvswitch'}


def neutron_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
    url_base = "http://" + conf.get_vip('public')['domain_name']
    dr = conf.get_default_region()

    facility.register_endpoint_tri(region=dr,
                                   name='neutron',
                                   etype='network',
                                   description='OpenStack Network Service',
                                   url_base=url_base + ':9696/')
    facility.register_service_admin_user('neutron')
    facility.register_service_admin_user('nova_for_neutron')
    tasks.compose_prepare_source_cond('neutron')
    comp = facility.get_component('neutron')
    neutrons = inv.hosts_with_any_service(set(comp['services'].keys()))
    rabbitmq.populate_peer(neutrons)
    mariadb.populate_peer(neutrons, ['client'])  # TODO: maybe not all node needs it
    util.bless_with_principal(neutrons,
                              [('os', 'neutron@default'),
                               ('os', 'nova_for_neutron@default'),
                               ('mysql', 'neutron'),
                               ('shared_secret', 'neutron_nova_metadata'),
                               ('rabbit', 'openstack')])


def register():
    sp = conf.get_service_prefix()
    component = {
      'origin_repo': 'https://github.com/openstack/neutron.git',
      'deploy_source': 'src',
      'deploy_source_options': {'src', 'pkg'},
      'component': 'neutron',
      'services': {'neutron-server': {'deploy_mode': 'standalone',
                                      'unit_name': {'src': sp + 'q-svc',
                                                    'pkg': 'neutron-server'}},
                   'neutron-metadata-agent': {'deploy_mode': 'standalone',
                                              'unit_name': {'src': sp + 'q-meta',
                                                            'pkg': 'neutron-metadata-agent'}},
                   'neutron-l3-agent': {'deploy_mode': 'standalone',
                                        'unit_name': {'src': sp + 'q-l3',
                                                      'pkg': 'neutron-l3-agent'}},
                   'neutron-metering-agent': {'deploy_mode': 'standalone',
                                              'unit_name': {'src': sp + 'q-metering',
                                                            'pkg': 'neutron-metering-agent'}},
                   'neutron-vpn-agent': {'deploy_mode': 'standalone',
                                         'unit_name': {'src': sp + 'q-vpn',
                                                       'pkg': 'neutron-vpn-agent'}},
                   'neutron-dhcp-agent': {'deploy_mode': 'standalone',
                                          'unit_name': {'src': sp + 'q-dhcp',
                                                        'pkg': 'neutron-dhcp-agent'}},
                   'neutron-lbaasv2-agent': {'deploy_mode': 'standalone',
                                             'unit_name': {'src': sp + 'q-lbaas',
                                                           'pkg': 'neutron-vpn-agent'}},
                   'neutron-openvswitch-agent': {'deploy_mode': 'standalone',
                                                 'unit_name': {'src': sp + 'q-ovs',  # q-agt
                                                               'pkg': 'neutron-openvswitch-agent'}}},
      'compose': neutron_compose,
      'pkg_deps': neutron_pkgs,
      'cfg_step': neutron_etccfg,
      'goal': task_neutron_steps
    }
    cc = facility.get_component_config_for('neutron')
    util.dict_merge(component, cc)
    facility.register_component(component)

    ovs = {'component': 'openvswitch',
           'deploy_source': 'pkg',
           'deploy_source_options': {'pkg'},
           'services': {'openvswitch': {'deploy_mode': 'standalone',
                                        'unit_name': {'src': sp + 'ovs',
                                                      'pkg': 'openvswitch'}}},
           'pkg_deps': ovs_pkgs,
           'cfg_step': ovs_etccfg,
           'goal': task_ovs}

    cc = facility.get_component_config_for('openvswitch')
    util.dict_merge(ovs, cc)
    facility.register_component(ovs)


register()
