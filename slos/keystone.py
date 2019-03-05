from speedling import util
from speedling import conf
import speedling
from speedling import facility
from speedling import gitutils

from speedling import localsh
from speedling import usrgrp

import speedling.tasks
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
    facility.task_wants(speedling.tasks.task_selinux)
    keystones = self.hosts_with_service('keystone')
    self.call_do(keystones, self.do_httpd_restart)
    self.wait_for_components(self.memcached)


def task_keystone_db(self):
    self.wait_for_components(self.sql)
    # TODO: change the function to near db and near key parts
    schema_node_candidate = self.hosts_with_service('keystone')
    schema_node = util.rand_pick(schema_node_candidate)
    sync_cmd = 'su -s /bin/sh -c "keystone-manage db_sync" keystone'
    self.call_do(schema_node, facility.do_retrycmd_after_content, c_args=(sync_cmd, ))


def task_cfg_keystone_steps(self):
    facility.task_will_need(self.task_cfg_httpd)
    facility.task_wants(self.task_keystone_db, self.task_keystone_fernet)
    keystones = self.hosts_with_service('keystone')
    self.call_do(util.rand_pick(keystones),
                 self.do_keystone_init)


def task_keystone_endpoints(self):
    facility.task_wants(self.task_cfg_keystone_steps, self.task_cfg_httpd)
    self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                 self.do_keystone_endpoint_sync, c_args=(self.registered_endpoints,))


def task_keystone_users(self):
    facility.task_wants(self.task_cfg_keystone_steps, self.task_cfg_httpd)
    self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                 self.do_keystone_user_sync, c_args=(self.registered_user_dom,))


def task_keystone_ready(self):
    facility.task_wants(self.task_keystone_users, self.task_keystone_endpoints)
    LOG.info('Keystone data sync completed')


class Keystone(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/keystone.git'
    deploy_source = 'src',
    services = {'keystone': {'deploy_mode': 'mod_wsgi'}}

    def __init__(self, *args, **kwargs):
        super(Keystone, self).__init__(*args, **kwargs)
        self.peer_info = {}
        self.final_task = self.bound_to_instance(task_keystone_ready)
        for f in [task_keystone_users, task_keystone_endpoints, task_cfg_keystone_steps, task_keystone_db, task_cfg_httpd, task_keystone_fernet]:
            self.bound_to_instance(f)
        self.sql = self.dependencies["sql"]  # raises
        self.memcached = self.dependencies["memcached"]  # raises
        self.loadbalancer = self.dependencies.get("loadbalancer", None)

        # consider the Default domain always existing
        self.registered_user_dom = {'Default': {}}
        self.registered_endpoints = {}

    def do_keystone_endpoint_sync(cname, enp):
        self = facility.get_component(cname)
        from keystoneauth1.identity import v3
        import slos.ossync
        auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                           password=util.get_keymgr()(self.name, 'admin@default'), project_name='admin',
                           user_domain_name='Default',
                           project_domain_name='Default')
        # session object is not thread safe, using auth ;(((
        # TODO: wipe python client usage, looks like,
        # I cannot use the same token in all threads
        endpoint_override = 'http://localhost:5000/v3'
        slos.ossync.endpoint_sync(auth, enp, endpoint_override=endpoint_override)

    def do_keystone_user_sync(cname, dom):
        self = facility.get_component(cname)
        from keystoneauth1.identity import v3
        import slos.ossync
        auth = v3.Password(auth_url='http://localhost:5000/v3', username='admin',
                           password=util.get_keymgr()(self.name, 'admin@default'), project_name='admin',
                           user_domain_name='Default',
                           project_domain_name='Default')
        # session object is not thread safe, using auth ;(((
        # TODO: wipe python client usage, looks like,
        # I cannot use the same token in all threads
        endpoint_override = 'http://localhost:5000/v3'
        slos.ossync.user_dom_sync(auth, dom, endpoint_override=endpoint_override)

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
            'database': {'connection': self.sql.db_url('keystone')},
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
        pkgs.update({'srv-http\\apache-httpd', 'lib-dev\\openldap',
                     'lib-http-py3\\mod_wsgi', 'lib-py3\\pymemcached',
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
                     self.do_keystone_endpoint_sync, c_args=(self.registered_endpoints))

    def step_keystone_users(self):
        facility.task_wants(task_cfg_keystone_steps, self.task_cfg_httpd)
        self.call_do(util.rand_pick(self.hosts_with_service('keystone')),
                     self.do_keystone_user_sync, c_args=(facility.service_user_dom(),))

    def step_keystone_ready(self):
        facility.task_wants(self.step_keystone_users, self.step_keystone_endpoints)
        LOG.info('Keystone data sync completed')

    def compose(self):
        super(Keystone, self).compose()
        url_base = "http://" + conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()
        self.register_endpoints(region=dr,
                                name='keystone',
                                etype='identity',
                                description='OpenStack Identity',
                                eps={'admin': url_base + ':35357',
                                     'internal': url_base + ':5000',
                                     'public': url_base + ':5000'})
        self.register_project_in_domain('Default', 'admin', 'members are full admins')
        self.register_user_in_domain('Default', 'admin',
                                     password=util.get_keymgr()(self.name, 'admin@default'),
                                     project_roles={('Default', 'admin'): ['admin']})
        keystones = self.hosts_with_service('keystone')
        self.sql.populate_peer(keystones, ['client'])
        sql = self.sql
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

    @staticmethod
    def endp_triple(url):
        return {'admin': url, 'public': url, 'internal': url}

    def _access_region(self, region):
        if region in self.registered_endpoints:
            r_dict = self.registered_endpoints[region]
        else:
            r_dict = {}
            self.registered_endpoints[region] = r_dict
        return r_dict

    def set_parent_region(self, region, parent):
        r = self._access_region(region)
        self._access_region(parent)
        r['parent_region_id'] = parent

    def set_region_description(self, region, description):
        r = self._access_region(region)
        r['description'] = description

    def _access_services(self, region):
        if 'services' in region:
            return region['services']
        services = []
        region['services'] = services
        return services

    def _find_named_service(self, srvs, name):
        # warning linear search
        for d in srvs:
            if d['name'] == name:
                return d

    def register_endpoints(self, region, name, etype, description, eps):
        r = self._access_region(region)
        srvs = self._access_services(r)
        # handle name as primary key
        s = self._find_named_service(srvs, name)
        if s:
            LOG.warning("Redeclaring {name} service in the {region}".format(name=name, region=region))
        else:
            s = {'name': name}
            srvs.append(s)
        s['type'] = etype
        s['description'] = description
        s['endpoints'] = eps

    def register_endpoint_tri(self, region, name, etype, description, url_base):
        eps = self.endp_triple(url_base)
        self.register_endpoints(region, name, etype, description, eps)

    # TODO: not all service requires admin role, fix it,
    # the auth named ones does not expected to be used in place
    # where admin ness is really needed
    # the cross service user usually requires admin ness

    # `the admin` user was created by the kystone-manage bootstrap

    # domain name here case sensitive, but may not be in keystone
    def register_domain(self, name):
        if name in self.registered_user_dom:
            return self.registered_user_dom[name]
        d = {}
        self.registered_user_dom[name] = d
        return d

    def register_group_in_domain(self, domain, group):
        raise NotImplementedError

    # it is also lookup thing, description applied from the first call
    def register_project_in_domain(self, domain, name, description=None):
        dom = self.register_domain(domain)
        if 'projects' not in dom:
            projects = {}
            dom['projects'] = projects
        else:
            projects = dom['projects']
        if name not in projects:
            if description:
                p = {'description': description}
            else:
                p = {}
            projects[name] = p
            return p
        return projects[name]

    def register_user_in_domain(self, domain, user, password, project_roles, email=None):
        dom = self.register_domain(domain)
        if 'users' not in dom:
            users = {}
            dom['users'] = users
        else:
            users = dom['users']
        u = {'name': user, 'password': password, 'project_roles': project_roles}
        if email:
            u['email'] = email
        users[user] = u

    # TODO: move to keystone
    # users just for token verify
    # in the future it will create less privilgeded user
    def register_auth_user(self, user, password=None):
        keymgr = util.get_keymgr()
        if not password:
            password = keymgr('keystone', user + '@default')  # TODO: multi keystone
        self.register_project_in_domain('Default', 'service', 'dummy service project')
        # TODO: try with 'service' role
        self.register_user_in_domain(domain='Default', user=user, password=password,
                                     project_roles={('Default', 'service'): ['admin']})

    def register_service_admin_user(self, user, password=None):
        keymgr = util.get_keymgr()
        if not password:
            password = keymgr('keystone', user + '@default')
        self.register_project_in_domain('Default', 'service', 'dummy service project')
        self.register_user_in_domain(domain='Default', user=user, password=password,
                                     project_roles={('Default', 'service'): ['admin']})
