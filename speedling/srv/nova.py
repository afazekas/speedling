from speedling import util
from speedling import inv
from speedling import tasks
import __main__
from speedling import facility
from speedling import conf
from speedling import gitutils
from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
from speedling.srv import rabbitmq
from speedling.srv import mariadb
import logging

LOG = logging.getLogger(__name__)


def etc_nova_nova_conf():
    # NOTE! mariadb.db_url not required on compute when the use_conductur is False
    gconf = conf.get_global_config()
    pv = conf.get_vip('public')['domain_name']
    neutron_section = util.keystone_authtoken_section('neutron_for_nova')
    neutron_section.update(
             {'service_metadata_proxy': True,
              'metadata_proxy_shared_secret': util.get_keymgr()('shared_secret',
                                                                'neutron_nova_metadata')})
    if util.get_keymanager().has_creds('os', 'placement@default'):
        placement_section = util.keystone_authtoken_section('placement')
    else:
        placement_section = {}
    return {'DEFAULT': {'debug': True,
                        'transport_url': rabbitmq.transport_url(),
                        'compute_driver': 'libvirt.LibvirtDriver',
                        'use_neutron': True,
                        'firewall_driver': "nova.virt.firewall.NoopFirewallDriver",
                        'security_group_api': "neutron",
                        'log_dir': '/var/log/nova',
                        'default_floating_pool': "public",  # ext net needs to match
                        'state_path': '/var/lib/nova'
                        },
            'keystone_authtoken': util.keystone_authtoken_section('nova'),
            'placement':  placement_section,
            'database': {'connection': mariadb.db_url('nova')},
            'api_database': {'connection': mariadb.db_url('nova_api', 'nova')},
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


def do_local_nova_service_start():
    tasks.local_os_service_start_by_component('nova')


def libvirt_etccfg(services, global_service_union):
    pass


def nova_etccfg(services, global_service_union):
    comp = facility.get_component('nova')
    nova_git_dir = gitutils.component_git_dir(comp)
    usrgrp.group('libvirt')
    usrgrp.group('nova', 162)
    usrgrp.user('nova', 162, ['libvirt'])
    util.base_service_dirs('nova')
    cfgfile.ensure_path_exists('/etc/nova/rootwrap.d',
                               owner='nova', group='nova')
    cfgfile.ensure_path_exists('/var/lib/nova/instances',
                               owner='nova', group='nova')

    cfgfile.ini_file_sync('/etc/nova/nova.conf', etc_nova_nova_conf(),
                          owner='nova', group='nova')
    # test_only not recommended as stand alone
    util.unit_file(comp['services']['nova-placement-api']['unit_name'][comp['deploy_source']],
                   '/usr/local/bin/nova-placement-api  --port 8780',
                   'nova')
    if comp['deploy_source'] == 'src':
        cfgfile.install_file('/etc/nova/api-paste.ini',
                             '/'.join((nova_git_dir,
                                      'etc/nova/api-paste.ini')),
                             mode=0o644,
                             owner='nova', group='nova')
        cfgfile.install_file('/etc/nova/rootwrap.conf',
                             '/'.join((nova_git_dir,
                                      'etc/nova/rootwrap.conf')),
                             mode=0o444)
        util.unit_file(comp['services']['nova-api']['unit_name']['src'],
                       '/usr/local/bin/nova-api',
                       'nova')
        util.unit_file(comp['services']['nova-placement-api']['unit_name']['src'],
                       '/usr/local/bin/nova-placement-api  --port 8780',
                       'nova')
        util.unit_file(comp['services']['nova-conductor']['unit_name']['src'],
                       '/usr/local/bin/nova-conductor',
                       'nova')
        util.unit_file(comp['services']['nova-cells']['unit_name']['src'],
                       '/usr/local/bin/nova-cells',
                       'nova')
        util.unit_file(comp['services']['nova-console']['unit_name']['src'],
                       '/usr/local/bin/nova-console',
                       'nova')
        util.unit_file(comp['services']['nova-spicehtml5proxy']['unit_name']['src'],
                       '/usr/local/bin/nova-spicehtml5proxy',
                       'nova')
        util.unit_file(comp['services']['nova-scheduler']['unit_name']['src'],
                       '/usr/local/bin/nova-scheduler',
                       'nova')
        util.unit_file(comp['services']['nova-api-metadata']['unit_name']['src'],
                       '/usr/local/bin/nova-api-metadata',
                       'nova')
        util.unit_file(comp['services']['nova-xvpvncproxy']['unit_name']['src'],
                       '/usr/local/bin/nova-xvpvncproxy',
                       'nova')
        util.unit_file(comp['services']['nova-novncproxy']['unit_name']['src'],
                       '/usr/local/bin/nova-novncproxy',
                       'nova')
        util.unit_file(comp['services']['nova-consoleauth']['unit_name']['src'],
                       '/usr/local/bin/nova-consoleauth',
                       'nova')
        util.unit_file(comp['services']['nova-compute']['unit_name']['src'],
                       '/usr/local/bin/nova-compute',
                       'nova')
    if 'nova-api' in services or 'nova-metadata' in services:
        cfgfile.install_file('/etc/nova/rootwrap.d/api-metadata.filters',
                             '/'.join((nova_git_dir,
                                      'etc/nova/rootwrap.d/api-metadata.filters')),
                             mode=0o444)
    # intersect
    if 'nova-api' in services or 'nova-metadata' in services or 'nova-compute' in services:
        cfgfile.content_file('/etc/sudoers.d/nova', """Defaults:nova !requiretty
nova ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap /etc/nova/rootwrap.conf *
nova ALL = (root) NOPASSWD: /usr/local/bin/nova-rootwrap /etc/nova/rootwrap.conf *
nova ALL = (root) NOPASSWD: /usr/bin/privsep-helper *
nova ALL = (root) NOPASSWD: /usr/local/bin/privsep-helper *
""")

    if 'nova-compute' in services:
        usrgrp.group('nova_migration', 983)
        usrgrp.user('nova_migration', 986)  # TODO: give shell, distribute keys

        cfgfile.ensure_path_exists('/etc/nova/migration',
                                   owner='nova', group='nova')
        cfgfile.ensure_path_exists('/etc/nova/migration/rootwrap.d',
                                   owner='nova', group='nova')
        if comp['deploy_source'] == 'src':
            cfgfile.content_file('/etc/sudoers.d/nova_migration', """Defaults:nova_migration !requiretty

nova_migration ALL = (nova) NOPASSWD: /usr/bin/nc -U /var/run/libvirt/libvirt-sock
nova_migration ALL = (root) NOPASSWD: /usr/bin/nova-rootwrap /etc/nova/migration/rootwrap.conf *
""")
            cfgfile.content_file("/etc/nova/migration/rootwrap.d/cold_migrations.filters", """[Filters]
create_file: PathFilter, /usr/bin/touch, nova, /var/lib/nova/instances/
remove_file: PathFilter, /usr/bin/rm, nova, /var/lib/nova/instances/
create_dir: PathFilter, /usr/bin/mkdir, nova, -p, /var/lib/nova/instances/
remove_dir: PathFilter, /usr/bin/rm, nova, -rf, /var/lib/nova/instances/
copy_file_local_to_remote_recursive: PathFilter, /usr/bin/scp, nova, -r, -t, /var/lib/nova/instances/
copy_file_remote_to_local_recursive: PathFilter, /usr/bin/scp, nova, -r, -f, /var/lib/nova/instances/
copy_file_local_to_remote: PathFilter, /usr/bin/scp, nova, -t, /var/lib/nova/instances/
copy_file_remote_to_local: PathFilter, /usr/bin/scp, nova, -f, /var/lib/nova/instances/
""")
            cfgfile.content_file("/etc/nova/migration/rootwrap.conf", """[DEFAULT]
use_syslog=True
syslog_log_facility=syslog
syslog_log_level=ERROR
filters_path=/etc/nova/migration/rootwrap.d
""")

            cfgfile.install_file('/etc/nova/rootwrap.d/compute.filters',
                                 '/'.join((nova_git_dir,
                                          'etc/nova/rootwrap.d/compute.filters')),
                                 mode=0o444)
            # nova-net only ??, try to delete
            cfgfile.install_file('/etc/nova/rootwrap.d/network.filters',
                                 '/'.join((nova_git_dir,
                                          'etc/nova/rootwrap.d/network.filters')),
                                 mode=0o444)


def do_libvirt():
    localsh.run('systemctl start libvirtd')


# TODO: ceph feature step  register_ceph_libvirt():
# n-cpu used for ironic (or nayting non libvirt) will have a different name
def task_libvirt():
    facility.task_wants(__main__.task_cfg_etccfg_steps)
    # TODO add concept for implied service
    novas = inv.hosts_with_any_service({'nova-compute', 'libvirtd'})
    inv.do_do(novas, do_libvirt)


def do_cell_reg():
    # TODO: make task for single nova-manage node to wait for at least one hypervisor arrive
    # wait is missing
    # also create cron entry (systemd timer)
    localsh.run("nova-manage cell_v2 discover_hosts")


def task_nova_steps():
    comp = facility.get_component('nova')
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready, task_libvirt)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)

    novas = inv.hosts_with_service('nova-api')
    schema_node_candidate = inv.hosts_with_service('nova-api')
    sync_cmd = 'su -s /bin/sh -c "nova-manage api_db sync" nova'
    tasks.subtask_db_sync(schema_node_candidate, schema='nova_api',
                          sync_cmd=sync_cmd, schema_user='nova')
    # actual cell sync is the nova db sync
    sync_cmd = 'su -s /bin/sh -c "nova-manage cell_v2 map_cell0 && (nova-manage cell_v2 list_cells | grep \'^| .*cell1\' -q || nova-manage cell_v2 create_cell --name=cell1 --verbose)"'
    tasks.subtask_db_sync(schema_node_candidate, schema='nova_cell0',
                          sync_cmd=sync_cmd, schema_user='nova')

    sync_cmd = 'su -s /bin/sh -c "nova-manage db sync" nova'
    tasks.subtask_db_sync(schema_node_candidate, schema='nova',
                          sync_cmd=sync_cmd, schema_user='nova')

    facility.task_wants(speedling.srv.rabbitmq.task_rabbit_steps)
    # start services
    n_srv = set(comp['services'].keys())
    inv.do_do(inv.hosts_with_any_service(n_srv), do_local_nova_service_start)

    facility.task_wants(speedling.srv.keystone.step_keystone_ready, task_libvirt)
    inv.do_do(novas, do_cell_reg)


def nova_pkgs():
    return {'conntrack-tools', 'curl', 'dnsmasq-utils', 'ebtables', 'gawk',
            'genisoimage', 'iptables', 'iputils', 'kernel-modules', 'kpartx',
            'm2crypto', 'mysql-devel', 'numpy', 'parted',
            'polkit', 'sqlite', 'sudo'}


def libvirt_pkgs():
    return {'python3-libguestfs', 'libvirt', 'libvirt-client'}


def nova_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
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
    tasks.compose_prepare_source_cond('nova')
    # TODO: revisit which components needs what and skip it from cfg
    rh = inv.hosts_with_any_service({'nova-api', 'nova-compute',
                                     'nova-scheduler', 'nova-conductor',
                                     'nova-cells'})
    rabbitmq.populate_peer(rh)
    comp = facility.get_component('nova')
    n_srv = set(comp['services'].keys())
    novas = inv.hosts_with_any_service(n_srv)
    util.bless_with_principal(novas,
                              [('os', 'nova@default'),
                               ('os', 'neutron_for_nova@default'),
                               ('shared_secret', 'neutron_nova_metadata'),
                               ('mysql', 'nova'),
                               ('rabbit', 'openstack')])
    placement = inv.hosts_with_service('nova-placement-api')
    util.bless_with_principal(novas, [('os', 'placement@default')])  # n-cpu using it
    mariadb.populate_peer(rh, ['client'])  # TODO: maybe not all node needs it


def register():
    sp = conf.get_service_prefix()
    component = {
      'origin_repo': 'https://github.com/openstack/nova.git',
      'deploy_source': 'src',
      'deploy_source_options': {'src', 'pkg'},
      'services': {'nova-api': {'deploy_mode': 'standalone',
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
                                                      'pkg': 'openstack-nova-xvpvncproxy'}}},
      'deploy_mode': 'standalone',
      'component': 'nova',
      'compose': nova_compose,
      'pkg_deps': nova_pkgs,
      'cfg_step': nova_etccfg,
      'goal': task_nova_steps
    }
    cc = facility.get_component_config_for('nova')
    util.dict_merge(component, cc)
    facility.register_component(component)

    libvirt = {'name': 'libvirt',
               'deploy_source': 'pkg',
               'deploy_source_options': {'pkg'},
               'deploy_mode': 'standalone',
               'component': 'libvirt',
               'services': {'libvirtd': {'deploy_mode': 'standalone',
                                         'unit_name': {'src': sp + 'libvirtd',
                                                       'pkg': 'libvirtd'}}},
               'pkg_deps': libvirt_pkgs,
               'cfg_step': libvirt_etccfg,
               'goal': task_libvirt}
    cc = facility.get_component_config_for('libvirt')
    util.dict_merge(libvirt, cc)

    facility.register_component(libvirt)


register()
