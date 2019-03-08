import logging

from speedling import conf
from speedling import facility
from speedling import gitutils
from speedling import tasks
from speedling import usrgrp
from speedling import util

LOG = logging.getLogger(__name__)

g_srv = {'glance-api', 'glance-registry', 'glance-scrubber'}
sp = 'sl-'


def task_glance_steps(self):
    schema_node_candidate = self.hosts_with_service('glance-api')
    schema_node = util.rand_pick(schema_node_candidate)
    self.wait_for_components(self.sql)
    sync_cmd = 'su -s /bin/sh -c "glance-manage db_sync && glance-manage db load_metadefs" glance'
    self.call_do(schema_node, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))

    self.wait_for_components(self.messaging)
    # start services
    self.call_do(self.hosts_with_any_service(g_srv), self.do_local_glance_service_start)
    facility.task_wants(self.keystone.final_task, *set.union(*(s['component'].get_waits_for_glance_task() for s in self.backends)))


class Glance(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/glance.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'},
    services = {'glance-api': {'deploy_mode': 'standalone',
                                              'unit_name': {'src': sp + 'g-api',
                                                            'pkg': 'openstack-glance-api'}},
                'glance-registry': {'deploy_mode': 'standalone',
                                                   'unit_name': {'src': sp + 'g-reg',
                                                                 'pkg': 'openstack-glance-registry'}},
                'glance-scrubber': {'deploy_mode': 'standalone',
                                                   'unit_name': {'src': sp + 'g-scr',
                                                                 'pkg': 'openstack-glance-scrubber'}}}

    def __init__(self, *args, **kwargs):
        super(Glance, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_glance_steps)
        self.peer_info = {}
        self.upload_image_registry = {}
        self.sql = self.dependencies["sql"]
        self.backends = self.dependencies["backends"]
        self.haproxy = self.dependencies["loadbalancer"]
        self.keystone = self.dependencies["keystone"]
        self.messaging = self.dependencies["messaging"]

    def do_local_glance_service_start(cname):
        self = facility.get_component(cname)
        tasks.local_os_service_start_by_component(self)

    # transport_url ? Do we still need g-reg ?
    def etc_glance_glance_api_conf(self):
        bind_port = 9292
        gconf = conf.get_global_config()
        if 'haproxy' in gconf['global_service_flags']:
            bind_port = 19292
        return {
            'DEFAULT': {'debug': True,
                        'bind_port': bind_port,
                        'show_image_direct_url': True,
                        'show_multiple_locations': True,
                        'enabled_backends': ', '.join(d['sname'] for d in self.backends)},
            'glance_store': {'default_backend': self.backends[0]['sname'].split(':')[0]},
            'keystone_authtoken': self.keystone.authtoken_section('glance'),
            'paste_deploy': {'flavor': 'keystone'},
            'database': {'connection': self.sql.db_url('glance')}
        }

    def etc_glance_glance_registry_conf(self): return {
        'DEFAULT': {'debug': True},
        'keystone_authtoken': self.keystone.authtoken_section('glance'),
        'paste_deploy': {'flavor': 'keystone'},
        'database': {'connection': self.sql.db_url('glance')}
    }

    def etccfg_content(self):
        super(Glance, self).etccfg_content()
        services = self.filter_node_enabled_services(g_srv)
        usrgrp.group('glance', 161)
        usrgrp.user('glance', 'glance')
        util.base_service_dirs('glance')
        self.file_path('/var/lib/glance/images',
                       owner='glance', group='glance')
        self.file_path('/var/lib/glance/image-cache',
                       owner='glance', group='glance')

        if 'glance-api' in services:
            self.file_ini('/etc/glance/glance-api.conf',
                          self.etc_glance_glance_api_conf(),
                          owner='glance', group='glance')
        if 'glance-registry' in services:
            self.file_ini('/etc/glance/glance-registry.conf',
                          self.etc_glance_glance_registry_conf(),
                          owner='glance', group='glance')
        # in case of packages or containers expect it is there already
        comp = self
        if comp.deploy_source == 'src':
            glance_git_dir = gitutils.component_git_dir(comp)

            self.file_sym_link('/etc/glance/metadefs', glance_git_dir + '/etc/metadefs')

            self.file_install('/etc/glance/glance-api-paste.ini',
                              '/'.join((glance_git_dir,
                                        'etc/glance-api-paste.ini')),
                              mode=0o644,
                              owner='glance', group='glance')
            self.file_install('/etc/glance/glance-registry-paste.ini',
                              '/'.join((glance_git_dir,
                                        'etc/glance-registry-paste.ini')),
                              mode=0o644,
                              owner='glance', group='glance')
            self.file_install('/etc/glance/policy.json',
                              '/'.join((glance_git_dir,
                                        'etc/policy.json')),
                              mode=0o644,
                              owner='glance', group='glance')

            util.unit_file(comp.services['glance-api']['unit_name']['src'],
                           '/usr/local/bin/glance-api',
                           'glance')
            util.unit_file(comp.services['glance-registry']['unit_name']['src'],
                           '/usr/local/bin/glance-registry',
                           'glance')
            util.unit_file(comp.services['glance-scrubber']['unit_name']['src'],
                           '/usr/local/bin/glance-scrubber',
                           'glance')

    def get_node_packages(self):
        pkgs = super(Glance, self).get_node_packages()
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-glance'})
        for b in self.backends:
            if b['component'].short_name == 'ceph':
                pkgs.update({'python3-rbd'})
                break
        return pkgs

    def get_beckend_list(self):
        nodes = self.hosts_with_service('glance-api')
        cluster = []
        for n in nodes:
            node = self.get_node(n)
            hostname = node['inv']['hostname']
            addr = self.get_addr_for(node['inv'], 'database')
            cluster.append({'hostname': hostname, 'addr': addr})
        return cluster

    def compose(self):
        super(Glance, self).compose()

        # it can consider the full inventory and config to influnce facility registered
        # resources
        url_base = "http://" + conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()
        glance_port = 9292
        glance_ha_port = 19292
        servers = []
        for b in self.get_beckend_list():
            servers.append(' '.join((b['hostname'], b['addr'] + ':' + str(glance_ha_port), 'check')))
        gconf = conf.get_global_config()
        if 'haproxy' in gconf['global_service_flags']:
            self.haproxy.add_listener('glance', {
                'bind': '*:' + str(glance_port),
                'mode': 'http',
                'http-request': ['set-header X-Forwarded-Proto https if { ssl_fc }',
                                 'set-header X-Forwarded-Proto http if !{ ssl_fc }'],
                'server': servers})

        self.keystone.register_endpoint_tri(region=dr,
                                            name='glance',
                                            etype='image',
                                            description='OpenStack Image Service',
                                            url_base=url_base + ':' + str(glance_port))

        # just auth or admin user ?
        self.keystone.register_service_admin_user('glance')
        glances = self.hosts_with_any_service(g_srv)
        self.sql.register_user_with_schemas('glance', ['glance'])
        self.sql.populate_peer(glances, ['client'])
        util.bless_with_principal(glances,
                                  [(self.keystone.name, 'glance@default'),
                                   (self.sql.name, 'glance'),
                                   (self.messaging.name, 'openstack')])
