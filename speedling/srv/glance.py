from speedling import util
from speedling import inv
from speedling import conf
from speedling import facility
from speedling import tasks
from speedling import gitutils
from speedling.srv import mariadb
from speedling.srv import haproxy


from osinsutils import cfgfile
from osinsutils import usrgrp

import speedling.srv.common
import logging


LOG = logging.getLogger(__name__)

g_srv = {'glance-api', 'glance-registry', 'glance-scrubber'}


def do_local_glance_service_start():
    tasks.local_os_service_start_by_component('glance')


def task_glance_steps():
    # nova cert store ??
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps,
                            speedling.srv.keystone.step_keystone_ready,
                            speedling.srv.ceph.task_ceph_steps)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    schema_node_candidate = inv.hosts_with_service('glance-api')
    sync_cmd = 'su -s /bin/sh -c "glance-manage db_sync && glance-manage db load_metadefs" glance'
    tasks.subtask_db_sync(schema_node_candidate, schema='glance',
                          sync_cmd=sync_cmd, schema_user='glance')

    facility.task_wants(speedling.srv.rabbitmq.task_rabbit_steps)
    # start services
    inv.do_do(inv.hosts_with_any_service(g_srv), do_local_glance_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready,
                        speedling.srv.ceph.task_ceph_steps)


# transport_url ? Do we still need g-reg ?
def etc_glance_glance_api_conf():
    bind_port = 9292
    gconf = conf.get_global_config()
    if 'haproxy' in gconf['global_service_flags']:
        bind_port = 19292
    return {
            'DEFAULT': {'debug': True,
                        'bind_port': bind_port,
                        'show_image_direct_url': True,
                        'show_multiple_locations': True},
            'glance_store': {'stores': "file,http,rbd",
                             'default_store': "rbd",
                             'rbd_store_user': "glance",
                             'rbd_store_pool': "images",
                             'rbd_store_ceph_conf': '/etc/ceph/ceph.conf',
                             'rbd_store_chunk_size': 8},
            'keystone_authtoken': util.keystone_authtoken_section('glance'),
            'paste_deploy': {'flavor': 'keystone'},
            'database': {'connection': mariadb.db_url('glance')}
        }


def etc_glance_glance_registry_conf(): return {
            'DEFAULT': {'debug': True},
            'keystone_authtoken': util.keystone_authtoken_section('glance'),
            'paste_deploy': {'flavor': 'keystone'},
            'database': {'connection': mariadb.db_url('glance')}
        }


def glance_etccfg(services):
    usrgrp.group('glance', 161)
    usrgrp.user('glance', 161)
    util.base_service_dirs('glance')
    cfgfile.ensure_path_exists('/var/lib/glance/images',
                               owner='glance', group='glance')
    cfgfile.ensure_path_exists('/var/lib/glance/image-cache',
                               owner='glance', group='glance')

    if 'glance-api' in services:
        cfgfile.ini_file_sync('/etc/glance/glance-api.conf',
                              etc_glance_glance_api_conf(),
                              owner='glance', group='glance')
    if 'glance-registry' in services:
        cfgfile.ini_file_sync('/etc/glance/glance-registry.conf',
                              etc_glance_glance_registry_conf(),
                              owner='glance', group='glance')
    # in case of packages or containers expect it is there already
    comp = facility.get_component('glance')
    if comp['deploy_source'] == 'src':
        glance_git_dir = gitutils.component_git_dir(comp)

        cfgfile.ensure_sym_link('/etc/glance/metadefs', glance_git_dir + '/etc/metadefs')

        cfgfile.install_file('/etc/glance/glance-api-paste.ini',
                             '/'.join((glance_git_dir,
                                      'etc/glance-api-paste.ini')),
                             mode=0o644,
                             owner='glance', group='glance')
        cfgfile.install_file('/etc/glance/glance-registry-paste.ini',
                             '/'.join((glance_git_dir,
                                      'etc/glance-registry-paste.ini')),
                             mode=0o644,
                             owner='glance', group='glance')
        cfgfile.install_file('/etc/glance/policy.json',
                             '/'.join((glance_git_dir,
                                      'etc/policy.json')),
                             mode=0o644,
                             owner='glance', group='glance')

        util.unit_file(comp['services']['glance-api']['unit_name']['src'],
                       '/usr/local/bin/glance-api',
                       'glance')
        util.unit_file(comp['services']['glance-registry']['unit_name']['src'],
                       '/usr/local/bin/glance-registry',
                       'glance')
        util.unit_file(comp['services']['glance-scrubber']['unit_name']['src'],
                       '/usr/local/bin/glance-scrubber',
                       'glance')


def glance_pkgs():
    comp = facility.get_component('glance')
    if comp['deploy_source'] == 'pkg':
        return set('openstack-glance')
    return set()


def get_beckend_list():
    nodes = inv.hosts_with_service('glance-api')
    cluster = []
    for n in nodes:
        node = inv.get_node(n)
        hostname = node['inv']['hostname']
        addr = inv.get_addr_for(node['inv'], 'database')
        cluster.append({'hostname': hostname, 'addr': addr})
    return cluster


def glance_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
    url_base = "http://" + conf.get_vip('public')['domain_name']
    dr = conf.get_default_region()
    glance_port = 9292
    glance_ha_port = 19292
    servers = []
    for b in get_beckend_list():
        servers.append(' '.join((b['hostname'], b['addr'] + ':' + str(glance_ha_port), 'check')))
    gconf = conf.get_global_config()
    if 'haproxy' in gconf['global_service_flags']:
        haproxy.add_listener('glance', {
                             'bind': '*:' + str(glance_port),
                             'mode': 'http',
                             'http-request': ['set-header X-Forwarded-Proto https if { ssl_fc }',
                                              'set-header X-Forwarded-Proto http if !{ ssl_fc }'],
                             'server': servers})

    facility.register_endpoint_tri(region=dr,
                                   name='glance',
                                   etype='image',
                                   description='OpenStack Image Service',
                                   url_base=url_base + ':' + str(glance_port))

    # just auth or admin user ?
    facility.register_service_admin_user('glance')
    tasks.compose_prepare_source_cond('glance')
    glances = inv.hosts_with_any_service(g_srv)
    mariadb.populate_peer(glances, ['client'])
    util.bless_with_principal(glances,
                              [('os', 'glance@default'),
                               ('mysql', 'glance'),
                               ('rabbit', 'openstack')])


def register():
    sp = conf.get_service_prefix()
    glance_c = {'origin_repo': 'https://github.com/openstack/glance.git',
                'deploy_source': 'src',
                'deploy_source_options': {'src', 'pkg'},
                'services': {'glance-api': {'deploy_mode': 'standalone',
                                            'unit_name': {'src': sp + 'g-api',
                                                          'pkg': 'openstack-glance-api'}},
                             'glance-registry': {'deploy_mode': 'standalone',
                                                 'unit_name': {'src': sp + 'g-reg',
                                                               'pkg': 'openstack-glance-registry'}},
                             'glance-scrubber': {'deploy_mode': 'standalone',
                                                 'unit_name': {'src': sp + 'g-scr',
                                                               'pkg': 'openstack-glance-scrubber'}}},
                'component': 'glance',
                'compose': glance_compose,
                'pkg_deps': glance_pkgs,
                'cfg_step': glance_etccfg,
                'goal': task_glance_steps}
    # component related config validations here
    facility.register_component(glance_c)


register()
