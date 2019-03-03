from speedling import util
from speedling import conf
from speedling import gitutils
from speedling import tasks
import speedling
from speedling import facility

from speedling import localsh
from speedling import usrgrp

import logging

LOG = logging.getLogger(__name__)
sp = 'sl-'


def do_ovs(cname):
    localsh.run('systemctl start openvswitch.service')


def task_ovs(self):
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)
    # TODO add concept for implied service
    ovss = self.hosts_with_any_service({'neutron-openvswitch-agent', 'ovs'})
    self.call_do(ovss, do_ovs)


def ovs_pkgs(self):
    return {'openvswitch'}


def task_net_config(self):
    # This is temporary here, normally it should do interface persistent config
    self.call_do(self.hosts_with_service('neutron-l3-agent'),
                 self.do_dummy_netconfig)


def task_neutron_steps(self):
    self.wait_for_components(self.sql)
    schema_node_candidate = self.hosts_with_service('neutron-server')
    schema_node = util.rand_pick(schema_node_candidate)

    sync_cmd = 'su -s /bin/sh -c "neutron-db-manage --config-file /etc/neutron/neutron.conf --config-file /etc/neutron/plugins/ml2/ml2_conf.ini upgrade head" neutron'
    self.call_do(schema_node, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))
    facility.task_will_need(self.task_net_config)
    self.wait_for_components(self.messaging)
    facility.task_wants(self.task_net_config)

    q_srv = set(self.services.keys())
    self.call_do(self.hosts_with_any_service(q_srv), self.do_local_neutron_service_start)
    self.wait_for_components(self.messaging)
    self.wait_for_components(self.osclient)
    self.call_do(util.rand_pick(self.hosts_with_service('neutron-server')), self.do_dummy_public_net)


q_srv = {'neutron-server', 'neutron-openvswitch-agent', 'neutron-vpn-agent',
         'neutron-dhcp-agent', 'neutron-metadata-agent', 'neutron-l3-agent',
         'neutron-metering-agent', 'neutron-lbaasv2-agent'}


class NeutronML2OVS(facility.OpenStack):
    pass


class Neutron(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/neutron.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'}
    component = 'neutron'
    services = {'neutron-server': {'deploy_mode': 'standalone',
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
                                                            'pkg': 'neutron-openvswitch-agent'}}}

    def __init__(self, *args, **kwargs):
        super(Neutron, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_neutron_steps)
        self.bound_to_instance(task_net_config)
        self.peer_info = {}
        self.sql = self.dependencies["sql"]
        self.haproxy = self.dependencies["loadbalancer"]
        self.keystone = self.dependencies["keystone"]
        self.messaging = self.dependencies["messaging"]
        self.osclient = self.dependencies["osclient"]

    def find_nova_comp_shared(self):
        return [c for c in self.consumers.keys() if c.short_name == 'nova'] + [self]

    def etc_neutron_conf_d_common_agent_conf(self): return {'agent': {'root_helper': "sudo /usr/local/bin/neutron-rootwrap /etc/neutron/rootwrap.conf",
                                                                      'root_helper_daemon': "sudo /usr/local/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf"}}

    def etc_neutron_metadata_agent_ini(self):
        ivip = conf.get_vip('internal')['domain_name']
        return {'DEFAULT': {'nova_metadata_ip':  ivip,
                            'metadata_proxy_shared_secret':
                            util.get_keymgr()(self.find_nova_comp_shared(),
                                              'neutron_nova_metadata')}}

    def etc_neutron_neutron_conf(self):
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
                            'transport_url': self.messaging.transport_url()},
                'database': {'connection': self.sql.db_url('neutron')},
                'keystone_authtoken': self.keystone.authtoken_section('neutron'),
                'nova': self.keystone.authtoken_section('nova_for_neutron'),
                'oslo_concurrency': {'lock_path': '$state_path/lock'}}

    def etc_neutron_plugins_ml2_ml2_conf_ini(self): return {
        'ml2': {'tenant_network_types': 'vxlan',  # TODO: switch to geneve}
                'mechanism_drivers': 'openvswitch,linuxbridge',
                'extension_drivers': 'port_security'},
        'ml2_type_vxlan': {'vni_ranges': '1001:4001'}
        }

    # vpnaas, consider other drivers
    def etc_neutron_vpn_agent_ini(self): return {
        'vpnagent': {'vpn_device_driver': 'neutron_vpnaas.services.vpn.' +
                                          'device_drivers.fedora_strongswan_ipsec.FedoraStrongSwanDriver'}}

    def etc_neutron_neutron_vpnaas_conf(self): return {
        'service_providers': {'service_provider': 'VPN:openswan:neutron_vpnaas.services.vpn.service_drivers.ipsec.IPsecVPNDriver:default'}}

    # fwaas added, why not.. (TEST ONLY)
    def etc_neutron_fwaas_driver_ini(self): return {
        'fwaas': {'driver': 'neutron_fwaas.services.firewall.drivers.linux' +
                            '.iptables_fwaas.IptablesFwaasDriver'}}

    def etccfg_content(self):
        super(Neutron, self).etccfg_content()
        gconf = conf.get_global_config()
        global_service_union = gconf['global_service_flags']
        usrgrp.group('neutron', 996)
        usrgrp.user('neutron', 993)
        util.base_service_dirs('neutron')
        self.ensure_path_exists('/etc/neutron/conf.d',
                                owner='neutron', group='neutron')
        self.ensure_path_exists('/etc/neutron/conf.d/common',
                                owner='neutron', group='neutron')
        self.ini_file_sync('/etc/neutron/conf.d/common/agent.conf',
                           self.etc_neutron_conf_d_common_agent_conf(),
                           owner='neutron', group='neutron')
        neutron_git_dir = gitutils.component_git_dir(self)
        # consider alternate data paths
        # var/lib/neutron/dhcp needs to be reachable by the dnsmasq user
        self.ensure_path_exists('/var/lib/neutron',
                                owner='neutron', group='neutron',
                                mode=0o755)
        self.ensure_path_exists('/var/lib/neutron/lock',
                                owner='neutron', group='neutron')

        self.ensure_path_exists('/etc/neutron/plugins',
                                owner='neutron', group='neutron')
        self.ensure_path_exists('/etc/neutron/plugins/ml2',
                                owner='neutron', group='neutron')
        self.ini_file_sync('/etc/neutron/neutron.conf', self.etc_neutron_neutron_conf(),
                           owner='neutron', group='neutron')
        self.ensure_sym_link('/etc/neutron/plugin.ini',
                             '/etc/neutron/plugins/ml2/ml2_conf.ini')
        # move to common ?
        self.ini_file_sync('/etc/neutron/plugins/ml2/ml2_conf.ini',
                           self.etc_neutron_plugins_ml2_ml2_conf_ini(),
                           owner='neutron', group='neutron')

        services = self.filter_node_enabled_services(self.services.keys())
        if self.deploy_source == 'src':
            if services.intersection(q_srv - {'neutron-server'}):
                self.content_file('/etc/sudoers.d/neutron', """Defaults:neutron !requiretty
neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap /etc/neutron/rootwrap.conf *
neutron ALL = (root) NOPASSWD: /usr/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf
neutron ALL = (root) NOPASSWD: /usr/local/bin/neutron-rootwrap /etc/neutron/rootwrap.conf *
neutron ALL = (root) NOPASSWD: /usr/local/bin/neutron-rootwrap-daemon /etc/neutron/rootwrap.conf
""")
                self.ensure_path_exists('/etc/neutron/rootwrap.d',
                                        owner='root')
                # TODO: exclude stuff based on config
                for filter_file in ['debug.filters', 'dibbler.filters', 'ipset-firewall.filters',
                                    'l3.filters', 'netns-cleanup.filters', 'privsep.filters',
                                    'dhcp.filters', 'ebtables.filters', 'iptables-firewall.filters',
                                    'linuxbridge-plugin.filters', 'openvswitch-plugin.filters']:

                    self.install_file('/etc/neutron/rootwrap.d/' + filter_file,
                                      '/'.join((neutron_git_dir,
                                               'etc/neutron/rootwrap.d', filter_file)),
                                      mode=0o444)
            self.install_file('/etc/neutron/rootwrap.conf',
                              '/'.join((neutron_git_dir,
                                       'etc/rootwrap.conf')),
                              mode=0o444)

            self.install_file('/etc/neutron/api-paste.ini',
                              '/'.join((neutron_git_dir,
                                       'etc/api-paste.ini')),
                              mode=0o644,
                              owner='neutron', group='neutron')
            c_srv = self.services
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
            self.ini_file_sync('/etc/neutron/metadata_agent.ini',
                               self.etc_neutron_metadata_agent_ini(),
                               owner='neutron', group='neutron')

        if 'neutron-vpn-agent' in services or 'neutron-l3-agent' in services:
            self.ini_file_sync('/etc/neutron/l3_agent.ini', {
                'DEFAULT': {'interface_driver': 'openvswitch',
                            'debug': True}
                }, owner='neutron', group='neutron')

        if 'neutron-metering-agent' in services:
            self.ini_file_sync('/etc/neutron/metering_agent.ini', {
                'DEFAULT': {'interface_driver': 'openvswitch',
                            'debug': True}
                }, owner='neutron', group='neutron')

        if 'neutron-vpn-agent' in services:
            self.ini_file_sync('/etc/neutron/vpn_agent.ini',
                               self.etc_neutron_vpn_agent_ini(),
                               owner='neutron', group='neutron')

        if 'neutron-dhcp-agent' in services:
            self.ini_file_sync('/etc/neutron/dhcp_agent.ini', {
                    'DEFAULT': {'interface_driver': 'openvswitch',
                                'dnsmasq_local_resolv': True,
                                'debug': True}
                    }, owner='neutron', group='neutron')

        if 'neutron-lbaasv2-agent' in services:
            self.ini_file_sync('/etc/neutron/lbaas_agent.ini', {
                                  'DEFAULT': {'interface_driver': 'openvswitch',
                                              'debug': True}},
                                  owner='neutron', group='neutron')

        if 'neutron-openvswitch-agent' in services:
            tunnel_ip = self.get_addr_for(self.get_this_inv(), 'tunneling',
                                          service=self.services['neutron-openvswitch-agent'],
                                          net_attr='tunneling_network')
            ovs = {'local_ip': tunnel_ip}
            if 'neutron-l3-agent' in services:
                ovs['bridge_mappings'] = 'extnet:br-ex'
            self.ini_file_sync('/etc/neutron/plugins/ml2/openvswitch_agent.ini', {
                    'securitygroup': {'firewall_driver': 'iptables_hybrid'},
                    'ovs': ovs,
                    'agent': {'tunnel_types': 'vxlan'}},
                owner='neutron', group='neutron')

        # the inv version is not transfered, let it be part of the global config
        #    global_service_union = self.get_enabled_services()

        # NOTE: check these fwass,lbaas, vpaans conditions,
        # we might want to update them even if they not present
        if ('neutron-lbaasv2-agent' in services or ('neutron-lbaasv2-agent' in global_service_union and
                                                    'neutron-server' in services)):
            self.ini_file_sync('/etc/neutron/neutron_lbaas.conf', {
                               'service_providers': {'service_provider':
                                                     'LOADBALANCERV2:Haproxy:' +
                                                     'neutron_lbaas.drivers.haproxy.plugin_driver.HaproxyOnHostPluginDriver' +
                                                     ':default'}}, owner='neutron', group='neutron')
        if ('neutron-vpn-agent' in services or ('neutron-vpn-agent' in global_service_union and
                                                'neutron-server' in services)):
            self.ini_file_sync('/etc/neutron/neutron_vpnaas.conf',
                               self.etc_neutron_neutron_vpnaas_conf(),
                               owner='neutron', group='neutron')

        if 'neutron-fwaas' in global_service_union:
            self.ini_file_sync('/etc/neutron/fwaas_driver.ini',
                               self.etc_neutron_fwaas_driver_ini(),
                               owner='neutron', group='neutron')

    def do_dummy_public_net(cname):
        # guest net hack
        # 192.0.2.1 expected to be configured on an interface
        localsh.run(util.userrc_script('admin') + """
        (
        retry=30
        while ! neutron net-create public --router:external=True --is-default=True --provider:network_type flat --provider:physical_network extnet ; do
           ((retry--))
           if [[ retry == 0 ]]; then
              break;
           fi
        done
        FLOATING_IP_CIDR=${FLOATING_IP_CIDR:-"192.0.2.0/24"}
        FLOATING_IP_START=${FLOATING_IP_START:-"192.0.2.32"}
        FLOATING_IP_END=${FLOATING_IP_END:-"192.0.2.196"}
        EXTERNAL_NETWORK_GATEWAY=${EXTERNAL_NETWORK_GATEWAY:-"192.0.2.1"}
        neutron subnet-create --name ext-subnet --allocation-pool start=$FLOATING_IP_START,end=$FLOATING_IP_END --disable-dhcp --gateway $EXTERNAL_NETWORK_GATEWAY public $FLOATING_IP_CIDR
        # for auto allocation test
        openstack subnet pool create --share --default --pool-prefix 192.0.3.0/24 --default-prefix-length 26  shared-default
        openstack subnet pool create --share --default --pool-prefix 2001:db8:8000::/48 --default-prefix-length 64 default-v6
        )""")

    def do_dummy_netconfig(cname):
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

    def do_local_neutron_service_start(cname):
        self = facility.get_component(cname)
        tasks.local_os_service_start_by_component(self)

    def get_node_packages(self):
        pkgs = super(Neutron, self).get_node_packages()
        pkgs.update({'acl', 'dnsmasq', 'dnsmasq-utils', 'ebtables', 'haproxy',
                     'iptables', 'iputils', 'mysql-devel', 'radvd', 'sqlite',
                     'sudo', 'conntrack-tools', 'keepalived', 'ipset', 'openvswitch'})
        # move ovs to ovs
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-neutron'})
        return pkgs

    def compose(self):
        super(Neutron, self).compose()
        # it can consider the full inventory and config to influnce facility registered
        # resources
        url_base = "http://" + conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()

        self.keystone.register_endpoint_tri(region=dr,
                                            name='neutron',
                                            etype='network',
                                            description='OpenStack Network Service',
                                            url_base=url_base + ':9696/')
        self.keystone.register_service_admin_user('neutron')
        self.keystone.register_service_admin_user('nova_for_neutron')
        neutrons = self.hosts_with_any_service(set(self.services.keys()))
        self.messaging.populate_peer(neutrons)
        self.sql.register_user_with_schemas('neutron', ['neutron'])
        self.sql.populate_peer(neutrons, ['client'])  # TODO: maybe not all node needs it
        secret_service = self.find_nova_comp_shared()
        util.bless_with_principal(neutrons,
                                  [(self.keystone, 'neutron@default'),
                                   (self.keystone, 'nova_for_neutron@default'),
                                   (self.sql, 'neutron'),
                                   (secret_service, 'neutron_nova_metadata'),
                                   (self.messaging.name, 'openstack')])


def register(self, ):
    sp = conf.get_service_prefix()

    ovs = {'component': 'openvswitch',
           'deploy_source': 'pkg',
           'deploy_source_options': {'pkg'},
           'services': {'openvswitch': {'deploy_mode': 'standalone',
                                        'unit_name': {'src': sp + 'ovs',
                                                      'pkg': 'openvswitch'}}},
           'pkg_deps': ovs_pkgs,
           'goal': task_ovs}

    facility.register_component(ovs)
