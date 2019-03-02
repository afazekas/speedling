from speedling import util
from speedling import conf
import speedling
from speedling import facility
from speedling import gitutils

from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
import logging

LOG = logging.getLogger(__name__)


# user and endpoint creation is parallel
# fernet and db related stuff is parallel
def task_keystone_fernet(self):
    keystones = self.hosts_with_service('keystone')
    src_node = util.rand_pick(keystones)
    dst_nodes = keystones - src_node
    assert src_node
    self.call_do(src_node,
                 self.do_fernet_init)
    if dst_nodes:
        ret = self.call_do(src_node,
                           self.do_fetch_fernet_as_tar)
        fernet_tar = ret[next(iter(src_node))]['return_value']
        self.distribute_for_command(dst_nodes, fernet_tar,
                                    'tar -C / -x')


def task_cfg_httpd(self):  # split it into its own componenet delegate wsgi
    facility.task_wants(speedling.srv.common.task_selinux)
    keystones = self.hosts_with_service('keystone')
    self.call_do(keystones, self.do_httpd_restart)
    self.wait_for_components(self.get_memcached())


def task_keystone_db(self):
    self.wait_for_components(self.get_sql())
    # TODO: change the function to near db and near key parts
    schema_node_candidate = self.hosts_with_service('keystone')
    sync_cmd = 'su -s /bin/sh -c "keystone-manage db_sync" keystone'
    self.call_do(schema_node_candidate, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))


def task_cfg_keystone_steps(self):
    facility.task_will_need(self.task_cfg_httpd)
    facility.task_wants(self.task_keystone_db, self.task_keystone_fernet)
    keystones = self.hosts_with_service('keystone')
    self.call_do(util.rand_pick(keystones),
                 self.do_keystone_init)


def task_keystone_endpoints(self):
    facility.task_wants(self.task_cfg_keystone_steps, self.task_cfg_httpd)
    self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                 self.do_keystone_endpoint_sync, c_args=(facility.regions_endpoinds(),))


def task_keystone_users(self):
    facility.task_wants(self.task_cfg_keystone_steps, self.task_cfg_httpd)
    self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                 self.do_keystone_user_sync, c_args=(facility.service_user_dom(),))


def task_keystone_ready(self):
    facility.task_wants(self.task_keystone_users, self.task_keystone_endpoints)
    LOG.info('Keystone data sync completed')


class Keystone(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/keystone.git'
    deploy_source = 'src',
    services = {'keystone': {'deploy_mode': 'mod_wsgi'}}

    def get_balancer(self):
        return self.dependencies.get("loadbalancer", None)

    def get_sql(self):
        return self.dependencies["sql"]  # raises

    def get_memcached(self):
        return self.dependencies["memcached"]  # raises

    def __init__(self, *args, **kwargs):
        super(Keystone, self).__init__(*args, **kwargs)
        self.peer_info = {}
        self.final_task = self.bound_to_instance(task_keystone_ready)
        for f in [task_keystone_users, task_keystone_endpoints, task_cfg_keystone_steps, task_keystone_db, task_cfg_httpd, task_keystone_fernet]:
            self.bound_to_instance(f)

    def do_keystone_endpoint_sync(cname, enp):
        self = facility.get_component(cname)
        from keystoneauth1.identity import v3
        from osinsutils import ossync
        auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                           password=util.get_keymgr()(self.name, 'admin@default'), project_name='admin',
                           user_domain_name='Default',
                           project_domain_name='Default')
        # session object is not thread safe, using auth ;(((
        # TODO: wipe python client usage, looks like,
        # I cannot use the same token in all threads
        endpoint_override = 'http://localhost:5000/v3'
        ossync.endpoint_sync(auth, enp, endpoint_override=endpoint_override)

    def do_keystone_user_sync(cname, dom):
        self = facility.get_component(cname)
        from keystoneauth1.identity import v3
        from osinsutils import ossync
        auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                           password=util.get_keymgr()(self.name, 'admin@default'), project_name='admin',
                           user_domain_name='Default',
                           project_domain_name='Default')
        # session object is not thread safe, using auth ;(((
        # TODO: wipe python client usage, looks like,
        # I cannot use the same token in all threads
        endpoint_override = 'http://localhost:5000/v3'
        ossync.user_dom_sync(auth, dom, endpoint_override=endpoint_override)

    def do_fernet_init(cname):
        self = facility.get_component(cname)
        self.have_content()
        localsh.run("""
            mkdir -p /etc/keystone/fernet-keys # replace with install
            chown keystone:keystone /etc/keystone/fernet-keys
            chmod 770 /etc/keystone/fernet-keys
            keystone-manage fernet_setup --keystone-user keystone --keystone-group keystone
        """)

    def do_keystone_init(cname):
        self = facility.get_component(cname)
        self.have_content()
        localsh.run("keystone-manage bootstrap --bootstrap-password %s" %
                    util.cmd_quote(util.get_keymgr()(self.name, 'admin@default')))

    def etc_keystone_keystone_conf(self): return {
            'DEFAULT': {'debug': True},
            'database': {'connection': self.get_sql().db_url('keystone')},
            'token': {'provider': 'fernet'},
            'cache': {'backend': 'dogpile.cache.memcached'}  # TODO: non local memcachedS
            }

    def etc_httpd_conf_d_wsgi_keystone_conf(self):
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

    def etccfg_content(self):
        super(Keystone, self).etccfg_content()
        keystone_git_dir = gitutils.component_git_dir(self)
        usrgrp.group('keystone', 163)
        usrgrp.user('keystone', 163, home=keystone_git_dir)
        self.ensure_path_exists('/etc/keystone',
                                owner='keystone', group='keystone')
        self.ini_file_sync('/etc/keystone/keystone.conf',
                           self.etc_keystone_keystone_conf(),
                           owner='keystone', group='keystone')

        self.content_file('/etc/httpd/conf.d/wsgi-keystone.conf',
                          self.etc_httpd_conf_d_wsgi_keystone_conf(),
                          mode=0o644)

    def get_node_packages(self):
        pkgs = super(Keystone, self).get_node_packages()
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-keystone'})
        pkgs.update({'httpd', 'openldap-devel', 'python3-mod_wsgi',
                     'python3-keystoneauth1', 'python3-keystoneclient'})
        # until the httpd does not gets it's own module
        return pkgs

    def do_httpd_restart(cname):
        self = facility.get_component(cname)
        self.have_content()
        localsh.run("systemctl reload-or-restart httpd")
    # TODO: httpd needs ot be moved and spacially ahndled (consider multiple instances)

    def do_fetch_fernet_as_tar(cname):
        return localsh.ret('tar -c /etc/keystone/fernet-keys', binary=True)

    def step_keystone_endpoints(self):
        facility.task_wants(task_cfg_keystone_steps, self.task_cfg_httpd)
        self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                     self.do_keystone_endpoint_sync, c_args=(facility.regions_endpoinds(),))

    def step_keystone_users(self):
        facility.task_wants(task_cfg_keystone_steps, self.task_cfg_httpd)
        self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                     self.do_keystone_user_sync, c_args=(facility.service_user_dom(),))

    def step_keystone_ready(self):
        facility.task_wants(self.step_keystone_users, self.step_keystone_endpoints)
        LOG.info('Keystone data sync completed')

    def compose(self):
        super(Keystone, self).compose()
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
                                         password=util.get_keymgr()(self.name, 'admin@default'),
                                         project_roles={('Default', 'admin'): ['admin']})
        keystones = self.hosts_with_service('keystone')
        self.get_sql().populate_peer(keystones, ['client'])
        sql = self.get_sql()
        sql.register_user_with_schemas('keystone', ['keystone'])
        util.bless_with_principal(keystones,
                                  [(self.name, 'admin@default'), (sql.name, 'keystone')])

    def authtoken_section(self, service_user):
        d = {"auth_url": 'http://' + conf.get_vip('public')['domain_name'] + ':5000/',
             "project_domain_name": 'Default',
             "project_name": 'service',
             "password": util.get_keymgr()(self.name, service_user + '@default'),
             "user_domain_name": 'Default',
             "username": service_user,
             "auth_type": 'password'}
        return d

    def get_peer_info(self):
        n = self.get_this_node()
        return n['peers']['keystone']

    def populate_peer(self, nodes):
        port = 35357
        if not self.peer_info:
            hostname = addr = conf.get_vip('internal')['domain_name']
            self.peer_info['client'] = {'hostname': hostname, 'addr': addr,
                                        'port': port}

        for n in nodes:
            node = self.get_node(n)
            node['peers']['keystone'] = self.peer_info
