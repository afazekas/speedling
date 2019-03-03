from speedling import util
from speedling import tasks
from speedling import facility
from speedling import conf
from speedling import gitutils
from speedling import localsh
from speedling import usrgrp

import logging

LOG = logging.getLogger(__name__)
sp = 'sl-'


# TODO: ceph feature step  register_ceph_libvirt():
# n-cpu used for ironic (or nayting non libvirt) will have a different name
def task_libvirt(self):
    # TODO add concept for implied service
    novas = self.hosts_with_any_service({'nova-compute', 'libvirtd'})
    self.call_do(novas, self.do_libvirt)


class Libvirt(facility.VirtDriver):
    deploy_source = 'pkg'
    deploy_source_options = 'pkg'
    deploy_mode = 'standalone',
    services = {'libvirtd': {'deploy_mode': 'standalone',
                                            'unit_name': {'src': sp + 'libvirtd',
                                                          'pkg': 'libvirtd'}}}

    def __init__(self, *args, **kwargs):
        super(Libvirt, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_libvirt)

    def get_node_packages(self):
        pkgs = super(Libvirt, self).get_node_packages()
        pkgs.update({'python3-libguestfs', 'libvirt',
                     'libvirt-client', 'python3-libvirt'})
        return pkgs

    def do_libvirt(cname):
        self = facility.get_component(cname)
        self.have_content()
        localsh.run('systemctl start libvirtd')


def task_nova_steps(self):
    self.wait_for_components(self.messaging, self.keystone)
    novas = self.hosts_with_service('nova-api')
    schema_node_candidate = self.hosts_with_service('nova-api')

    sync_cmd = 'su -s /bin/sh -c "nova-manage api_db sync" nova'
    self.call_do(schema_node_candidate, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))

    # actual cell sync is the nova db sync
    sync_cmd = 'su -s /bin/sh -c "nova-manage cell_v2 map_cell0 && (nova-manage cell_v2 list_cells | grep \'^| .*cell1\' -q || nova-manage cell_v2 create_cell --name=cell1 --verbose)"'
    self.call_do(schema_node_candidate, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))

    sync_cmd = 'su -s /bin/sh -c "nova-manage db sync" nova'
    self.call_do(schema_node_candidate, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))

    self.wait_for_components(self.messaging)
    # start services
    n_srv = set(self.services.keys())
    self.call_do(self.hosts_with_any_service(n_srv), self.do_local_nova_service_start)

    self.wait_for_components(self.keystone, self.virtdriver)
    self.call_do(novas, self.do_cell_reg)


class Nova(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/nova.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'}
    services = {'nova-api': {'deploy_mode': 'standalone',
                             'unit_name': {'src': sp + 'n-api',
                                           'pkg': 'openstack-nova-api'}},
                'nova-api-metadata': {'deploy_mode': 'standalone',
                                      'unit_name': {'src': sp + 'n-meta',
                                                    'pkg': 'openstack-nova-metadata'}},
                'nova-compute': {'deploy_mode': 'standalone',
                                 'unit_name': {'src': sp + 'n-cpu',
                                               'pkg': 'openstack-nova-compute'}},
                'nova-placement-api': {'deploy_mode': 'standalone',
                                       'unit_name': {'src': sp + 'n-place',
                                                     'pkg': 'openstack-nova-placement-api'}},
                'nova-consoleauth': {'deploy_mode': 'standalone',  # Deprecated
                                     'unit_name': {'src': sp + 'n-cauth',
                                                   'pkg': 'openstack-nova-consoleauth'}},
                'nova-scheduler': {'deploy_mode': 'standalone',
                                   'unit_name': {'src': sp + 'n-sch',
                                                 'pkg': 'openstack-nova-scheduler'}},
                'nova-conductor': {'deploy_mode': 'standalone',
                                   'unit_name': {'src': sp + 'n-cond',
                                                 'pkg': 'openstack-nova-conductor'}},
                'nova-console': {'deploy_mode': 'standalone',
                                 'unit_name': {'src': sp + 'n-console',
                                               'pkg': 'openstack-nova-console'}},
                'nova-cells': {'deploy_mode': 'standalone',
                               'unit_name': {'src': sp + 'n-cell',
                                             'pkg': 'openstack-nova-cells'}},
                'nova-novncproxy': {'deploy_mode': 'standalone',
                                    'unit_name': {'src': sp + 'n-novnc',
                                                  'pkg': 'openstack-nova-novncproxy'}},
                'nova-spicehtml5proxy': {'deploy_mode': 'standalone',
                                         'unit_name': {'src': sp + 'n-spice',
                                                       'pkg': 'openstack-nova-spicehtml5proxy'}},
                'nova-xvpvncproxy': {'deploy_mode': 'standalone',
                                     'unit_name': {'src': sp + 'n-xvnc',
                                                   'pkg': 'openstack-nova-xvpvncproxy'}}}
    deploy_mode = 'standalone'

    def __init__(self, *args, **kwargs):
        super(Nova, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_nova_steps)
        self.peer_info = {}
        self.sql = self.dependencies["sql"]
        self.backends = self.dependencies["backends"]
        self.haproxy = self.dependencies["loadbalancer"]
        self.keystone = self.dependencies["keystone"]
        self.messaging = self.dependencies["messaging"]
        self.virtdriver = self.dependencies["virtdriver"]

    def etc_nova_nova_conf(self):
        # NOTE! mariadb.db_url not required on compute when the use_conductur is False
        gconf = conf.get_global_config()
        pv = conf.get_vip('public')['domain_name']
        neutron_section = self.keystone.authtoken_section('neutron_for_nova')
        neutron_section.update(
                 {'service_metadata_proxy': True,
                  'metadata_proxy_shared_secret': util.get_keymgr()('shared_secret',
                                                                    'neutron_nova_metadata')})  # add dual suffix
        if util.get_keymanager().has_creds(self.keystone.name, 'placement@default'):
            placement_section = self.keystone.authtoken_section('placement')
        else:
            placement_section = {}
        # TODO: exclude sql on compute
        return {'DEFAULT': {'debug': True,
                            'transport_url': self.messaging.transport_url(),
                            'compute_driver': 'libvirt.LibvirtDriver',
                            'use_neutron': True,
                            'firewall_driver': "nova.virt.firewall.NoopFirewallDriver",
                            'security_group_api': "neutron",
                            'log_dir': '/var/log/nova',
                            'default_floating_pool': "public",  # ext net needs to match
                            'state_path': '/var/lib/nova'
                            },
                'keystone_authtoken': self.keystone.authtoken_section('nova'),
                'placement':  placement_section,
                'database': {'connection': self.sql.db_url('nova')},
                'api_database': {'connection': self.sql.db_url('nova_api', 'nova')},
                'glance': {'api_servers':
                           'http://' + pv + ':9292'},
                'scheduler': {'discover_hosts_in_cells_interval': '300'},
                'neutron': neutron_section,
                # TODO: create a nova ceph user, with the same privileges
                'libvirt': {'rbd_user': 'cinder',
                            'rbd_secret_uuid': gconf['cinder_ceph_libvirt_secret_uuid'],
                            'disk_cachemodes': "network=writeback",  # file=unsafe ?
                            'virt_type': 'qemu',  # untile nested is fixed
                            'images_type': 'rbd',
                            'images_rbd_pool': 'vms',
                            'images_rbd_ceph_conf': '/etc/ceph/ceph.conf'},
                'filter_scheduler': {'enabled_filters': 'RetryFilter,AvailabilityZoneFilter,RamFilter,DiskFilter,ComputeFilter,ComputeCapabilitiesFilter,ImagePropertiesFilter,ServerGroupAntiAffinityFilter,ServerGroupAffinityFilter,SameHostFilter,DifferentHostFilter'}  # tempest likes the SameHostFilter,DifferentHostFilter
                }

    def do_local_nova_service_start(cname):
        self = facility.get_component(cname)
        tasks.local_os_service_start_by_component(self)

    def etccfg_content(self):
        super(Nova, self).etccfg_content()
        nova_git_dir = gitutils.component_git_dir(self)
        usrgrp.group('libvirt')
        usrgrp.group('nova', 162)
        usrgrp.user('nova', 162, ['libvirt'])
        util.base_service_dirs('nova')
        self.ensure_path_exists('/etc/nova/rootwrap.d',
                                owner='nova', group='nova')
        self.ensure_path_exists('/var/lib/nova/instances',
                                owner='nova', group='nova')

        self.ini_file_sync('/etc/nova/nova.conf', self.etc_nova_nova_conf(),
                           owner='nova', group='nova')
        # test_only not recommended as stand alone
        util.unit_file(self.services['nova-placement-api']['unit_name'][self.deploy_source],
                       '/usr/local/bin/nova-placement-api  --port 8780',
                       'nova')
        if self.deploy_source == 'src':
            self.install_file('/etc/nova/api-paste.ini',
                              '/'.join((nova_git_dir,
                                       'etc/nova/api-paste.ini')),
                              mode=0o644,
                              owner='nova', group='nova')
            self.install_file('/etc/nova/rootwrap.conf',
                              '/'.join((nova_git_dir,
                                       'etc/nova/rootwrap.conf')),
                              mode=0o444)
            util.unit_file(self.services['nova-api']['unit_name']['src'],
                           '/usr/local/bin/nova-api',
                           'nova')
            util.unit_file(self.services['nova-placement-api']['unit_name']['src'],
                           '/usr/local/bin/nova-placement-api  --port 8780',
                           'nova')
            util.unit_file(self.services['nova-conductor']['unit_name']['src'],
                           '/usr/local/bin/nova-conductor',
                           'nova')
            util.unit_file(self.services['nova-cells']['unit_name']['src'],
                           '/usr/local/bin/nova-cells',
                           'nova')
            util.unit_file(self.services['nova-console']['unit_name']['src'],
                           '/usr/local/bin/nova-console',
                           'nova')
            util.unit_file(self.services['nova-spicehtml5proxy']['unit_name']['src'],
                           '/usr/local/bin/nova-spicehtml5proxy',
                           'nova')
            util.unit_file(self.services['nova-scheduler']['unit_name']['src'],
                           '/usr/local/bin/nova-scheduler',
                           'nova')
            util.unit_file(self.services['nova-api-metadata']['unit_name']['src'],
                           '/usr/local/bin/nova-api-metadata',
                           'nova')
            util.unit_file(self.services['nova-xvpvncproxy']['unit_name']['src'],
                           '/usr/local/bin/nova-xvpvncproxy',
                           'nova')
            util.unit_file(self.services['nova-novncproxy']['unit_name']['src'],
                           '/usr/local/bin/nova-novncproxy',
                           'nova')
            util.unit_file(self.services['nova-consoleauth']['unit_name']['src'],
                           '/usr/local/bin/nova-consoleauth',
                           'nova')
            util.unit_file(self.services['nova-compute']['unit_name']['src'],
                           '/usr/local/bin/nova-compute',
                           'nova')
        services = self.filter_node_enabled_services(self.services.keys())
        if 'nova-api' in services or 'nova-metadata' in services:
            self.install_file('/etc/nova/rootwrap.d/api-metadata.filters',
                              '/'.join((nova_git_dir,
                                       'etc/nova/rootwrap.d/api-metadata.filters')),
                              mode=0o444)
        # intersect
        if 'nova-api' in services or 'nova-metadata' in services or 'nova-compute' in services:
            self.content_file('/etc/sudoers.d/nova', """Defaults:nova !requiretty
nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap /etc/nova/rootwrap.conf *
nova ALL = (root) NOPASSWD: /usr/local/bin/nova-rootwrap /etc/nova/rootwrap.conf *
nova ALL = (root) NOPASSWD: /usr/bin/privsep-helper *
nova ALL = (root) NOPASSWD: /usr/local/bin/privsep-helper *
""")

        if 'nova-compute' in services:
            usrgrp.group('nova_migration', 983)
            usrgrp.user('nova_migration', 986)  # TODO: give shell, distribute keys

            self.ensure_path_exists('/etc/nova/migration',
                                    owner='nova', group='nova')
            self.ensure_path_exists('/etc/nova/migration/rootwrap.d',
                                    owner='nova', group='nova')
            if self.deploy_source == 'src':
                self.content_file('/etc/sudoers.d/nova_migration', """Defaults:nova_migration !requiretty

nova_migration ALL = (nova) NOPASSWD: /usr/bin/nc -U /var/run/libvirt/libvirt-sock
nova_migration ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap /etc/nova/migration/rootwrap.conf *
""")
                self.content_file("/etc/nova/migration/rootwrap.d/cold_migrations.filters", """[Filters]
create_file: PathFilter, /usr/bin/touch, nova, /var/lib/nova/instances/
remove_file: PathFilter, /usr/bin/rm, nova, /var/lib/nova/instances/
create_dir: PathFilter, /usr/bin/mkdir, nova, -p, /var/lib/nova/instances/
remove_dir: PathFilter, /usr/bin/rm, nova, -rf, /var/lib/nova/instances/
copy_file_local_to_remote_recursive: PathFilter, /usr/bin/scp, nova, -r, -t, /var/lib/nova/instances/
copy_file_remote_to_local_recursive: PathFilter, /usr/bin/scp, nova, -r, -f, /var/lib/nova/instances/
copy_file_local_to_remote: PathFilter, /usr/bin/scp, nova, -t, /var/lib/nova/instances/
copy_file_remote_to_local: PathFilter, /usr/bin/scp, nova, -f, /var/lib/nova/instances/
""")
                self.content_file("/etc/nova/migration/rootwrap.conf", """[DEFAULT]
use_syslog=True
syslog_log_facility=syslog
syslog_log_level=ERROR
filters_path=/etc/nova/migration/rootwrap.d
""")

                self.install_file('/etc/nova/rootwrap.d/compute.filters',
                                  '/'.join((nova_git_dir,
                                           'etc/nova/rootwrap.d/compute.filters')),
                                  mode=0o444)
                # nova-net only ??, try to delete
                self.install_file('/etc/nova/rootwrap.d/network.filters',
                                  '/'.join((nova_git_dir,
                                           'etc/nova/rootwrap.d/network.filters')),
                                  mode=0o444)

    def do_cell_reg(cname):
        # TODO: make task for single nova-manage node to wait for at least one hypervisor arrive
        # wait is missing
        # also create cron entry (systemd timer)
        localsh.run("nova-manage cell_v2 discover_hosts")

    def get_node_packages(self):
        pkgs = super(Nova, self).get_node_packages()
        pkgs.update({'conntrack-tools', 'curl', 'dnsmasq-utils', 'ebtables', 'gawk',
                     'genisoimage', 'iptables', 'iputils', 'kernel-modules', 'kpartx',
                     'm2crypto', 'mysql-devel', 'numpy', 'parted',
                     'polkit', 'sqlite', 'sudo'})
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-nova'})
        return pkgs

    def compose(self):
        # it can consider the full inventory and config to influnce facility registered
        # resources
        super(Nova, self).compose()
        pv = conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()
        url_base = "http://" + pv

        facility.register_endpoint_tri(region=dr,
                                       name='nova',
                                       etype='compute',
                                       description='OpenStack Compute Service',
                                       url_base=url_base + ':8774/v2.1/$(tenant_id)s')
        facility.register_endpoint_tri(region=dr,
                                       name='placement',
                                       etype='placement',
                                       description='OpenStack Nova Placement Service',
                                       url_base=url_base + ':8780')
        facility.register_service_admin_user('nova')
        facility.register_service_admin_user('placement')
        facility.register_service_admin_user('neutron_for_nova')
        # TODO: revisit which components needs what and skip it from cfg
        rh = self.hosts_with_any_service({'nova-api', 'nova-compute',
                                          'nova-scheduler', 'nova-conductor',
                                          'nova-cells'})
        self.messaging.populate_peer(rh)
        n_srv = set(self.services.keys())
        novas = self.hosts_with_any_service(n_srv)
        self.sql.register_user_with_schemas('nova', ['nova', 'nova_api', 'nova_cell0'])  # TODO: use the cell deps
        util.bless_with_principal(novas,
                                  [(self.keystone.name, 'nova@default'),
                                   (self.keystone.name, 'neutron_for_nova@default'),
                                   ('shared_secret', 'neutron_nova_metadata'),
                                   (self.sql.name, 'nova'),
                                   (self.messaging.name, 'openstack')])
        util.bless_with_principal(novas, [(self.keystone.name, 'placement@default')])  # n-cpu using it
        self.sql.populate_peer(rh, ['client'])  # TODO: maybe not all node needs it


def register(self):

    libvirt = {'name': 'libvirt',
               'deploy_source': 'pkg',
               'deploy_source_options': {'pkg'},
               'deploy_mode': 'standalone',
               'component': 'libvirt',
               'services': {'libvirtd': {'deploy_mode': 'standalone',
                                         'unit_name': {'src': sp + 'libvirtd',
                                                       'pkg': 'libvirtd'}}},
               'goal': task_libvirt}

    facility.register_component(libvirt)
