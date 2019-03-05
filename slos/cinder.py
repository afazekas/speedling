from speedling import util
from speedling import conf
from speedling import tasks
from speedling import facility
from speedling import gitutils

from speedling import usrgrp

import logging

LOG = logging.getLogger(__name__)
sp = 'sl-'


def task_cinder_steps(self):
    c_srv = set(self.services.keys())
    self.wait_for_components(self.sql)

    schema_node_candidate = self.hosts_with_service('cinder-api')
    schema_node = util.rand_pick(schema_node_candidate)
    sync_cmd = 'su -s /bin/sh -c "cinder-manage db sync" cinder'
    self.call_do(schema_node, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))

    # start services
    self.call_do(self.hosts_with_any_service(c_srv), self.do_local_cinder_service_start)
    self.wait_for_components(self.messaging, self.keystone)
    facility.task_wants(self.keystone.final_task, self.messaging.final_task,
                        *set.union(*(s['component'].get_waits_for_cinder_task() for s in self.backends)))


class Cinder(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/cinder.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'},
    services = {'cinder-api': {'deploy_mode': 'standalone',
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
                                                'pkg': 'openstack-cinder-backup'}}}

    def __init__(self, *args, **kwargs):
        super(Cinder, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_cinder_steps)
        self.peer_info = {}
        self.upload_image_registry = {}
        self.sql = self.dependencies["sql"]
        self.backends = self.dependencies["backends"]
        self.haproxy = self.dependencies["loadbalancer"]
        self.keystone = self.dependencies["keystone"]
        self.messaging = self.dependencies["messaging"]

    #  multi backend config with one backend, named 'ceph'
    def etc_cinder_cinder_conf(self):
        gconf = conf.get_global_config()
        return {
            'DEFAULT': {'debug': True,
                        'glance_api_version': 2,
                        'enabled_backends': 'ceph',
                        'default_volume_type': 'ceph',
                        'backup_swift_url': 'http://' + conf.get_vip('public')['domain_name'] + ':8080/v1/AUTH_',
                        'transport_url': self.messaging.transport_url()},
            'database': {'connection': self.sql.db_url('cinder')},
            'keystone_authtoken': self.keystone.authtoken_section('cinder'),
            'oslo_concurrency': {'lock_path': '$state_path/lock'},
            'ceph': {'volume_driver': 'cinder.volume.drivers.rbd.RBDDriver',
                                      'rbd_pool': 'volumes',
                                      'rbd_user': 'cinder',
                                      'rbd_ceph_conf': '/etc/ceph/ceph.conf',
                                      'volume_backend_name': 'ceph',
                                      'rbd_secret_uuid': gconf['cinder_ceph_libvirt_secret_uuid']}}

    def etccfg_content(self):
        super(Cinder, self).etccfg_content()
        c_srv = set(self.services.keys())
        usrgrp.group('cinder', 165)
        usrgrp.user('cinder', 165)
        util.base_service_dirs('cinder')
        comp = self
        self.ensure_path_exists('/var/lib/cinder/lock',
                                owner='cinder', group='cinder')

        self.ini_file_sync('/etc/cinder/cinder.conf', self.etc_cinder_cinder_conf(),
                           owner='cinder', group='cinder')
        cinder_git_dir = gitutils.component_git_dir(comp)

        self.install_file('/etc/cinder/api-paste.ini',
                          '/'.join((cinder_git_dir,
                                   'etc/cinder/api-paste.ini')),
                          mode=0o644, owner='cinder', group='cinder')
        self.install_file('/etc/cinder/resource_filters.json',
                          '/'.join((cinder_git_dir,
                                   'etc/cinder/resource_filters.json')),
                          mode=0o644,
                          owner='cinder', group='cinder')
        services = self.filter_node_enabled_services(c_srv)
        if comp.deploy_source == 'src':
            co_srv = comp.services
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
                self.content_file('/etc/sudoers.d/cinder', """Defaults:cinder !requiretty
cinder ALL = (root) NOPASSWD: /usr/local/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *
cinder ALL = (root) NOPASSWD: /usr/bin/cinder-rootwrap /etc/cinder/rootwrap.conf *
""")
                self.ensure_path_exists('/etc/cinder/rootwrap.d',
                                        owner='cinder', group='cinder')
                self.install_file('/etc/cinder/rootwrap.d/volume.filters',
                                  '/'.join((cinder_git_dir,
                                           'etc/cinder/rootwrap.d/volume.filters')),
                                  mode=0o444)
                self.install_file('/etc/cinder/rootwrap.conf',
                                  '/'.join((cinder_git_dir,
                                           'etc/cinder/rootwrap.conf')),
                                  mode=0o444)

    def do_local_cinder_service_start(cname):
        self = facility.get_component(cname)
        tasks.local_os_service_start_by_component(self)

    def get_node_packages(self):
        pkgs = super(Cinder, self).get_node_packages()
        pkgs.update({'lvm2', 'util-cli\\qemu-img'})
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-cinder'})
        return pkgs

    def compose(self):
        # it can consider the full inventory and config to influnce facility registered
        # resources
        super(Cinder, self).compose()
        url_base = "http://" + conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()

        self.keystone.register_endpoint_tri(region=dr,
                                            name='cinder',
                                            etype='volume',
                                            description='OpenStack Volume Service',
                                            url_base=url_base + ':8776/v1/$(tenant_id)s')
        self.keystone.register_endpoint_tri(region=dr,
                                            name='cinderv2',
                                            etype='volumev2',
                                            description='OpenStack Volume Service',
                                            url_base=url_base + ':8776/v2/$(tenant_id)s')
        self.keystone.register_endpoint_tri(region=dr,
                                            name='cinderv3',
                                            etype='volumev3',
                                            description='OpenStack Volume Service',
                                            url_base=url_base + ':8776/v3/$(tenant_id)s')
        self.keystone.register_service_admin_user('cinder')
        comp = self
        cins = self.hosts_with_any_service(set(comp.services.keys()))
        self.sql.register_user_with_schemas('cinder', ['cinder'])
        self.sql.populate_peer(cins, ['client'])
        self.messaging.populate_peer(cins)
        util.bless_with_principal(cins, [(self.keystone.name, 'cinder@default'),
                                         (self.messaging.name, 'openstack'),
                                         (self.sql.name, 'cinder')])
