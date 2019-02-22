from speedling import util
from speedling import inv
from speedling import conf
from speedling import tasks
import __main__
from speedling import facility
from speedling import gitutils
from speedling.srv import mariadb

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
import logging

LOG = logging.getLogger(__name__)


# not required at this point, but both horizion and keystone could use it
# ### localsh.run("systemctl start memcached redis mongod")

# does it waits for horizon or keystone ?


def do_keystone_endpoint_sync(enp):
    from keystoneauth1.identity import v3
    from osinsutils import ossync
    auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                       password=util.get_keymgr()('os', 'admin@default'), project_name='admin',
                       user_domain_name='Default',
                       project_domain_name='Default')
    # session object is not thread safe, using auth ;(((
    # TODO: wipe python client usage, looks like,
    # I cannot use the same token in all threads
    endpoint_override = 'http://localhost:5000/v3'
    ossync.endpoint_sync(auth, enp, endpoint_override=endpoint_override)


def do_keystone_user_sync(dom):
    from keystoneauth1.identity import v3
    from osinsutils import ossync
    auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                       password=util.get_keymgr()('os', 'admin@default'), project_name='admin',
                       user_domain_name='Default',
                       project_domain_name='Default')
    # session object is not thread safe, using auth ;(((
    # TODO: wipe python client usage, looks like,
    # I cannot use the same token in all threads
    endpoint_override = 'http://localhost:5000/v3'
    ossync.user_dom_sync(auth, dom, endpoint_override=endpoint_override)


def do_fernet_init():
    localsh.run("""
        mkdir -p /etc/keystone/fernet-keys # replace with install
        chown keystone:keystone /etc/keystone/fernet-keys
        chmod 770 /etc/keystone/fernet-keys
        keystone-manage fernet_setup --keystone-user keystone --keystone-group keystone
    """)


def do_keystone_init():
    localsh.run("keystone-manage bootstrap --bootstrap-password %s" %
                util.cmd_quote(util.get_keymgr()('os', 'admin@default')))


def etc_keystone_keystone_conf(): return {
        'DEFAULT': {'debug': True},
        'database': {'connection': mariadb.db_url('keystone')},
        'token': {'provider': 'fernet'},
        'cache': {'backend': 'dogpile.cache.memcached'}
        }


def etc_httpd_conf_d_wsgi_keystone_conf():
    return """Listen 5000
Listen 35357

<VirtualHost *:5000>
    WSGIDaemonProcess keystone-public processes=5 threads=1 user=keystone group=keystone display-name=%{{GROUP}}
    WSGIProcessGroup keystone-public
    WSGIScriptAlias / {bin_dir}/keystone-wsgi-public
    WSGIApplicationGroup %{{GLOBAL}}
    WSGIPassAuthorization On
    <IfVersion >= 2.4>
      ErrorLogFormat "%{{cu}}t %M"
    </IfVersion>
    ErrorLog /var/log/httpd/keystone-error.log
    CustomLog /var/log/httpd/keystone-access.log combined

    <Directory {bin_dir}>
        <IfVersion >= 2.4>
            Require all granted
        </IfVersion>
        <IfVersion < 2.4>
            Order allow,deny
            Allow from all
        </IfVersion>
    </Directory>
</VirtualHost>

<VirtualHost *:35357>
    WSGIDaemonProcess keystone-admin processes=5 threads=1 user=keystone group=keystone display-name=%{{GROUP}}
    WSGIProcessGroup keystone-admin
    WSGIScriptAlias / {bin_dir}/keystone-wsgi-admin
    WSGIApplicationGroup %{{GLOBAL}}
    WSGIPassAuthorization On
    <IfVersion >= 2.4>
      ErrorLogFormat "%{{cu}}t %M"
    </IfVersion>
    ErrorLog /var/log/httpd/keystone-error.log
    CustomLog /var/log/httpd/keystone-access.log combined

    <Directory {bin_dir}>
        <IfVersion >= 2.4>
            Require all granted
        </IfVersion>
        <IfVersion < 2.4>
            Order allow,deny
            Allow from all
        </IfVersion>
    </Directory>
</VirtualHost>
""".format(bin_dir='/usr/local/bin')


def keystone_etccfg(services, global_service_union):
    comp = facility.get_component('keystone')
    keystone_git_dir = gitutils.component_git_dir(comp)  # TODO: only if from source
    usrgrp.group('keystone', 163)
    usrgrp.user('keystone', 163, home=keystone_git_dir)
    cfgfile.ensure_path_exists('/etc/keystone',
                               owner='keystone', group='keystone')
    cfgfile.ini_file_sync('/etc/keystone/keystone.conf',
                          etc_keystone_keystone_conf(),
                          owner='keystone', group='keystone')

    cfgfile.content_file('/etc/httpd/conf.d/wsgi-keystone.conf',
                         etc_httpd_conf_d_wsgi_keystone_conf(),
                         mode=0o644)


def keystone_pkgs():
    return set()


def do_httpd_restart():
    localsh.run("systemctl reload-or-restart httpd")
# TODO: httpd needs ot be moved and spacially ahndled (consider multiple instances)


def task_cfg_httpd():
    facility.task_will_need(speedling.srv.common.task_memcached_steps)
    facility.task_wants(__main__.task_cfg_etccfg_steps, speedling.srv.common.task_selinux)
    keystones = inv.hosts_with_service('keystone')
    inv.do_do(keystones, do_httpd_restart)
    facility.task_wants(speedling.srv.common.task_memcached_steps)


def fetch_fernet_as_tar():
    return localsh.ret('tar -c /etc/keystone/fernet-keys', binary=True)


def task_keystone_fernet():
    facility.task_wants(__main__.task_cfg_etccfg_steps)
    keystones = inv.hosts_with_service('keystone')
    src_node = inv.rand_pick(keystones)
    dst_nodes = keystones - src_node
    assert src_node
    inv.do_do(src_node,
              do_fernet_init)
    if dst_nodes:
        ret = inv.do_do(src_node,
                        fetch_fernet_as_tar)
        fernet_tar = ret[next(iter(src_node))]['return_value']
        inv.distribute_for_command(dst_nodes, fernet_tar,
                                   'tar -C / -x')


def task_keystone_db():
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    # TODO: change the function to near db and near key parts
    schema_node_candidate = inv.hosts_with_service('keystone')
    sync_cmd = 'su -s /bin/sh -c "keystone-manage db_sync" keystone'
    tasks.subtask_db_sync(schema_node_candidate, schema='keystone',
                          sync_cmd=sync_cmd, schema_user='keystone')


def task_cfg_keystone_steps():
    facility.task_will_need(task_cfg_httpd)
    facility.task_wants(task_keystone_db, task_keystone_fernet)
    keystones = inv.hosts_with_service('keystone')
    inv.do_do(inv.rand_pick(keystones),
              do_keystone_init)


def step_keystone_endpoints():
    facility.task_wants(task_cfg_keystone_steps, task_cfg_httpd)
    inv.do_do(inv.rand_pick(inv.hosts_with_service('keystone')),
              do_keystone_endpoint_sync, c_args=(facility.regions_endpoinds(),))


def step_keystone_users():
    facility.task_wants(task_cfg_keystone_steps, task_cfg_httpd)
    inv.do_do(inv.rand_pick(inv.hosts_with_service('keystone')),
              do_keystone_user_sync, c_args=(facility.service_user_dom(),))


def step_keystone_ready():
    facility.task_wants(step_keystone_users, step_keystone_endpoints)
    LOG.info('Keystone data sync completed')


def keystone_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
    url_base = "http://" + conf.get_vip('public')['domain_name']
    dr = conf.get_default_region()
    facility.register_endpoints(region=dr,
                                name='keystone',
                                etype='identity',
                                description='OpenStack Identity',
                                eps={'admin': url_base + ':35357',
                                     'internal': url_base + ':5000',
                                     'public': url_base + ':5000'})
    facility.register_project_in_domain('Default', 'admin', 'members are full admins')
    facility.register_user_in_domain('Default', 'admin',
                                     password=util.get_keymgr()('os', 'admin@default'),
                                     project_roles={('Default', 'admin'): ['admin']})
    tasks.compose_prepare_source_cond('keystone')
    keystones = inv.hosts_with_service('keystone')
    mariadb.populate_peer(keystones, ['client'])
    util.bless_with_principal(keystones,
                              [('os', 'admin@default'), ('mysql', 'keystone')])


def get_peer_info():
    n = inv.get_this_node()
    return n['peers']['keystone']


PEER_INFO = {}


def populate_peer(nodes):
    port = 35357
    if not PEER_INFO:
        hostname = addr = conf.get_vip('internal')['domain_name']
        PEER_INFO['client'] = {'hostname': hostname, 'addr': addr,
                               'port': port}

    for n in nodes:
        node = inv.get_node(n)
        node['peers']['keystone'] = PEER_INFO


def register():
    keystone = {'name': 'keystone',
                'component': 'keystone',
                'origin_repo': 'https://github.com/openstack/keystone.git',
                'deploy_source': 'src',
                'services': {'keystone': {'deploy_mode': 'mod_wsgi'}},
                'compose': keystone_compose,
                'pkg_deps': keystone_pkgs,
                'cfg_step': keystone_etccfg,
                'goal': step_keystone_ready}
    cc = facility.get_component_config_for('keystone')
    util.dict_merge(keystone, cc)
    facility.register_component(keystone)


register()
