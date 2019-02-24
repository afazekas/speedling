from speedling import util
from speedling import inv
from speedling import conf
from speedling import tasks
from speedling import facility
from speedling import gitutils

from speedling.srv import rabbitmq
from speedling.srv import mariadb

from osinsutils import cfgfile
from osinsutils import usrgrp

import speedling.srv.common
import logging

LOG = logging.getLogger(__name__)


#  multi backend config with one backend, named 'ceph'
def etc_cinder_cinder_conf():
    gconf = conf.get_global_config()
    return {
        'DEFAULT': {'debug': True,
                    'glance_api_version': 2,
                    'enabled_backends': 'ceph',
                    'default_volume_type': 'ceph',
                    'backup_swift_url': 'http://' + conf.get_vip('public')['domain_name'] + ':8080/v1/AUTH_',
                    'transport_url': rabbitmq.transport_url()},
        'database': {'connection': mariadb.db_url('cinder')},
        'keystone_authtoken': util.keystone_authtoken_section('cinder'),
        'oslo_concurrency': {'lock_path': '$state_path/lock'},
        'ceph': {'volume_driver': 'cinder.volume.drivers.rbd.RBDDriver',
                                  'rbd_pool': 'volumes',
                                  'rbd_user': 'cinder',
                                  'rbd_ceph_conf': '/etc/ceph/ceph.conf',
                                  'volume_backend_name': 'ceph',
                                  'rbd_secret_uuid': gconf['cinder_ceph_libvirt_secret_uuid']}}


def cinder_etccfg(services):
    usrgrp.group('cinder', 165)
    usrgrp.user('cinder', 165)
    util.base_service_dirs('cinder')

    cfgfile.ensure_path_exists('/var/lib/cinder/lock',
                               owner='cinder', group='cinder')

    cfgfile.ini_file_sync('/etc/cinder/cinder.conf', etc_cinder_cinder_conf(),
                          owner='cinder', group='cinder')
    comp = facility.get_component('cinder')
    cinder_git_dir = gitutils.component_git_dir(comp)

    cfgfile.install_file('/etc/cinder/api-paste.ini',
                         '/'.join((cinder_git_dir,
                                  'etc/cinder/api-paste.ini')),
                         mode=0o644,
                         owner='cinder', group='cinder')
    cfgfile.install_file('/etc/cinder/resource_filters.json',
                         '/'.join((cinder_git_dir,
                                  'etc/cinder/resource_filters.json')),
                         mode=0o644,
                         owner='cinder', group='cinder')

    comp = facility.get_component('cinder')
    if comp['deploy_source'] == 'src':
        co_srv = comp['services']
        util.unit_file(co_srv['cinder-scheduler']['unit_name']['src'],
                       '/usr/local/bin/cinder-scheduler',
                       'cinder')
        util.unit_file(co_srv['cinder-api']['unit_name']['src'],
                       '/usr/local/bin/cinder-api',
                       'cinder')
        util.unit_file(co_srv['cinder-volume']['unit_name']['src'],
                       '/usr/local/bin/cinder-volume',
                       'cinder')
        util.unit_file(co_srv['cinder-backup']['unit_name']['src'],
                       '/usr/local/bin/cinder-backup',
                       'cinder')
        # TODO handle bin dir
        if 'cinder-volume' in services or 'cinder-backup' in services:
            cfgfile.content_file('/etc/sudoers.d/cinder', """Defaults:cinder !requiretty
cinder ALL = (root) NOPASSWD: /usr/local/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *
cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *
""")
            cfgfile.ensure_path_exists('/etc/cinder/rootwrap.d',
                                       owner='cinder', group='cinder')
            cfgfile.install_file('/etc/cinder/rootwrap.d/volume.filters',
                                 '/'.join((cinder_git_dir,
                                          'etc/cinder/rootwrap.d/volume.filters')),
                                 mode=0o444)
            cfgfile.install_file('/etc/cinder/rootwrap.conf',
                                 '/'.join((cinder_git_dir,
                                          'etc/cinder/rootwrap.conf')),
                                 mode=0o444)


def do_local_cinder_service_start():
    tasks.local_os_service_start_by_component('cinder')


def task_cinder_steps():
    comp = facility.get_component('cinder')
    c_srv = set(comp['services'].keys())
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)

    cins = inv.hosts_with_service('cinder-api')
    sync_cmd = 'su -s /bin/sh -c "cinder-manage db sync" cinder'
    tasks.subtask_db_sync(cins, schema='cinder',
                          sync_cmd=sync_cmd, schema_user='cinder')

    # start services
    inv.do_do(inv.hosts_with_any_service(c_srv), do_local_cinder_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def cinder_pkgs():
    return {'iscsi-initiator-utils', 'lvm2', 'qemu-img',
            'scsi-target-utils', 'targetcli'}


def cinder_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
    url_base = "http://" + conf.get_vip('public')['domain_name']
    dr = conf.get_default_region()

    tasks.compose_prepare_source_cond('cinder')

    facility.register_endpoint_tri(region=dr,
                                   name='cinder',
                                   etype='volume',
                                   description='OpenStack Volume Service',
                                   url_base=url_base + ':8776/v1/$(tenant_id)s')
    facility.register_endpoint_tri(region=dr,
                                   name='cinderv2',
                                   etype='volumev2',
                                   description='OpenStack Volume Service',
                                   url_base=url_base + ':8776/v2/$(tenant_id)s')
    facility.register_endpoint_tri(region=dr,
                                   name='cinderv3',
                                   etype='volumev3',
                                   description='OpenStack Volume Service',
                                   url_base=url_base + ':8776/v3/$(tenant_id)s')
    facility.register_service_admin_user('cinder')
    comp = facility.get_component('cinder')
    cins = inv.hosts_with_any_service(set(comp['services'].keys()))
    mariadb.populate_peer(cins, ['client'])
    rabbitmq.populate_peer(cins)
    util.bless_with_principal(cins, [('os', 'cinder@default'),
                                     ('rabbit', 'openstack'),
                                     ('mysql', 'cinder')])


def register():
    sp = conf.get_service_prefix()
    component = {'origin_repo': 'https://github.com/openstack/cinder.git',
                 'deploy_source': 'src',
                 'deploy_source_options': {'src', 'pkg'},
                 'services': {'cinder-api': {'deploy_mode': 'standalone',
                                             'unit_name': {'src': sp + 'c-api',
                                                           'pkg': 'openstack-cinder-api'}},
                              'cinder-volume': {'deploy_mode': 'standalone',
                                                'unit_name': {'src': sp + 'c-vol',
                                                              'pkg': 'openstack-cinder-volume'}},
                              'cinder-scheduler': {'deploy_mode': 'standalone',
                                                   'unit_name': {'src': sp + 'c-sch',
                                                                 'pkg': 'openstack-cinder-scheduler'}},
                              'cinder-backup': {'deploy_mode': 'standalone',
                                                'unit_name': {'src': sp + 'c-bak',
                                                              'pkg': 'openstack-cinder-backup'}}},
                 'compose': cinder_compose,
                 'component': 'cinder',
                 'pkg_deps': cinder_pkgs,
                 'cfg_step': cinder_etccfg,
                 'goal': task_cinder_steps}
    cc = facility.get_component_config_for('cinder')
    util.dict_merge(component, cc)
    facility.register_component(component)


register()
