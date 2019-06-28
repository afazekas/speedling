import logging

from speedling import conf
from speedling import facility
from speedling import localsh
from speedling import tasks
from speedling import usrgrp
from speedling import util

LOG = logging.getLogger(__name__)
sp = 'sl-'

# TODO: add more
s_srv = {'swift-object', 'swift-container', 'swift-account', 'swift-proxy',
         'swift-container-sync'}
s_store = {'swift-object', 'swift-container', 'swift-account'}


def task_swift_steps(self):
    # TODO: /etc/container-sync-realms.conf
    # not multinode firendly!
    self.call_do(self.hosts_with_any_service(s_store), self.do_swift_deploy_demo_local)
    self.call_do(self.hosts_with_any_service(s_srv), self.do_swift_service_start)


class Swift(facility.OpenStack, facility.StorageBackend):
    origin_repo = 'https://github.com/openstack/swift.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'}
    services = {'swift-proxy': {'deploy_mode': 'standalone',
                                'unit_name': {'src': sp + 's-proxy',
                                              'pkg': 'openstack-swift-server'}},
                'swift-account': {'deploy_mode': 'standalone',
                                  'unit_name': {'src': sp + 's-acc',
                                                'pkg': 'openstack-swift-account'}},
                'swift-object': {'deploy_mode': 'standalone',
                                 'unit_name': {'src': sp + 's-obj',
                                               'pkg': 'openstack-swift-object'}},
                'swift-container': {'deploy_mode': 'standalone',
                                    'unit_name': {'src': sp + 's-cont',
                                                  'pkg': 'openstack-swift-container'}},
                'swift-container-sync': {'deploy_mode': 'standalone',
                                         'unit_name': {'src': sp + 's-cont-sync',
                                                       'pkg': 'openstack-swift-container-sync'}}}
    deploy_mode = 'standalone'

    def __init__(self, *args, **kwargs):
        super(Swift, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_swift_steps)
        self.peer_info = {}
        self.haproxy = self.dependencies["loadbalancer"]
        self.keystone = self.dependencies["keystone"]

    # crossdomain, insecure setting, everybody is truseted, XSS like issue can happen!
    # TODO: add ceilometer to the pipeline
    # TODO: add memcached
    # TODO: readd swift3
    def etc_swift_proxy_server_conf(self):
        pv = conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()

        filters = ['catch_errors', 'gatekeeper', 'healthcheck', 'proxy-logging',
                   'memcache', 'container_sync', 'bulk', 'tempurl', 'ratelimit',
                   's3token', 'crossdomain', 'authtoken', 'keystoneauth',
                   'formpost', 'staticweb', 'container-quotas', 'account-quotas',
                   'slo', 'dlo', 'versioned_writes']
        r = {}
        for f in filters:
            r['filter:' + f] = {'use': 'egg:swift#' + f.replace('-', '_')}

        r['filter:authtoken'] = self.keystone.authtoken_section('swift')
        r['filter:authtoken']['delay_auth_decision'] = 1
        r['filter:authtoken']['paste.filter_factory'] = 'keystonemiddleware.auth_token:filter_factory'
        r['filter:keystoneauth']['operator_roles'] = "user, admin"
        r['filter:keystoneauth']['reseller_admin_role'] = "admin"
        proxy_ip = self.get_addr_for(self.get_this_inv(), 'internal_listen',
                                     service=self.services['swift-proxy'],
                                     net_attr='swift_proxy_network')

        pipeline_str = ' '.join(filters + ['proxy-logging', 'proxy-server'])
        r.update({'DEFAULT': {'bind_port': 8080,
                              'bind_ip': proxy_ip},
                  'pipeline:main': {'pipeline': pipeline_str},
                  'app:proxy-server': {'use': 'egg:swift#proxy',
                                       'account_autocreate': True},
                  'filter:s3token': {'paste.filter_factory':  'keystonemiddleware.s3_token:filter_factory',
                                     'auth_port': 5000,
                                     'auth_host': pv,
                                     'admin_user': 'swfit',
                                     'amind_tenant_name': 'service'},
                  'filter:swift3': {'use': 'egg:swift3#swift3',
                                    'location': dr}
                  })
        return r

    def etc_swift_container_server_conf(self):
        object_ip = self.get_addr_for(self.get_this_inv(), 'backing_object',
                                      service=self.services['swift-container'],
                                      net_attr='swift_object_network')
        # container-sync should exists even if everything is on default
        r = {'DEFAULT': {'mount_check': False,  # consider setting to true on prod
                         'bind_ip': object_ip,
                         'bind_port': 6201},
             'pipeline:main': {'pipeline': 'container-server'},
             'app:container-server': {'use': 'egg:swift#container',
                                      'allow_versions': True},
             'container-sync': {'log_name': 'container-sync'}}
        return r

    # NOTE: just to make tempest happy
    # TODO: use secure key
    def etc_swift_container_sync_realms_conf(self):
        return {'realm1': {'key': 'realm1key',
                           'cluster_name1': 'http://' + conf.get_vip('public')['domain_name'] + ':8080/v1/'}}

    def etc_swift_object_server_conf(self):
        object_ip = self.get_addr_for(self.get_this_inv(), 'backing_object',
                                      service=self.services['swift-object'],
                                      net_attr='swift_object_network')

        r = {'DEFAULT': {'mount_check': False,  # consider setting to true on prod
                         'bind_ip': object_ip,
                         'bind_port': 6200},
             'pipeline:main': {'pipeline': 'object-server'},
             'app:object-server': {'use': 'egg:swift#object',
                                   'replication_concurrency': 0}}  # https://bugs.launchpad.net/swift/+bug/1691075
        return r

    def etc_swift_account_server_conf(self):
        object_ip = self.get_addr_for(self.get_this_inv(), 'backing_object',
                                      service=self.services['swift-account'],
                                      net_attr='swift_object_network')
        r = {'DEFAULT': {'mount_check': False,  # consider setting to true on prod
                         'bind_ip': object_ip,
                         'bind_port': 6202},
             'pipeline:main': {'pipeline': 'account-server'},
             'app:account-server': {'use': 'egg:swift#account'}}
        return r

    def etc_swift_swift_conf(self):
        return {'swift-hash': {'swift_hash_path_suffix': '1234123412341234',
                               'swift_hash_path_prefix': 'changeme'},
                'storage-policy:0': {'name': 'Policy-0',
                                     'default': 'yes',
                                     'aliases': 'yellow, orange'},
                'swift-constraints': {'max_file_size': 5368709122,
                                      'max_header_size': 16384}}

    def etccfg_content(self):
        super(Swift, self).etccfg_content()
        usrgrp.group('swift', 160)
        usrgrp.user('swift', 'swift')
        self.file_path('/etc/swift',
                       owner='swift', group='swift')

        self.file_ini('/etc/swift/swift.conf',
                      self.etc_swift_swift_conf(),
                      owner='swift', group='swift')
        comp = facility.get_component('swift')
        if comp.deploy_source == 'src':
            util.unit_file(self.services['swift-account']['unit_name']['src'],
                           '/usr/local/bin/swift-account-server /etc/swift/account-server.conf',
                           'swift')
            util.unit_file(self.services['swift-object']['unit_name']['src'],
                           '/usr/local/bin/swift-object-server /etc/swift/object-server.conf',
                           'swift')
            util.unit_file(self.services['swift-container']['unit_name']['src'],
                           '/usr/local/bin/swift-container-server /etc/swift/container-server.conf',
                           'swift')
            util.unit_file(self.services['swift-container-sync']['unit_name']['src'],
                           '/usr/local/bin/swift-container-sync /etc/swift/container-server.conf',
                           'swift')
            util.unit_file(self.services['swift-proxy']['unit_name']['src'],
                           '/usr/local/bin/swift-proxy-server /etc/swift/proxy-server.conf',
                           'swift')

        services = self.filter_node_enabled_services(self.services.keys())
        if 'swift-proxy' in services:
            self.file_ini('/etc/swift/proxy-server.conf',
                          self.etc_swift_proxy_server_conf(),
                          owner='swift', group='swift')

        if 'swift-container' in services or 'swift-demo' in services:
            self.file_ini('/etc/swift/container-server.conf',
                          self.etc_swift_container_server_conf(),
                          owner='swift', group='swift')

        if 'swift-proxy' in services or 'swift-container' in services:
            self.file_ini('/etc/swift/container-sync-realms.conf',
                          self.etc_swift_container_sync_realms_conf(),
                          owner='swift', group='swift')

        if 'swift-object' in services or 'swift-demo' in services:
            self.file_ini('/etc/swift/object-server.conf',
                          self.etc_swift_object_server_conf(),
                          owner='swift', group='swift')

        if 'swift-account' in services or 'swift-demo' in services:
            self.file_ini('/etc/swift/account-server.conf',
                          self.etc_swift_account_server_conf(),
                          owner='swift', group='swift')

        if set(services).intersection(s_store):
            self.file_path('/srv/node', owner='swift', group='swift')
            # TODO use node config
            self.file_path('/srv/node/disk1',
                           owner='swift', group='swift')
            self.file_path('/var/lock/rsyncd',
                           owner='swift', group='swift')
            # self.file_ini('/etc/rsyncd.conf',
            #                   self.etc_rsyncd_conf(),
            #                   owner='root', group='swift', mode=0o644)

    def do_swift_service_start(cname):
        self = facility.get_component(cname)
        tasks.local_os_service_start_by_component(self)

        # NOTE: other service will be started implictly
        selected_services = set(self.get_enabled_services_from_component())
        if selected_services.intersection(s_store):
            localsh.run('systemctl start rsyncd')

    # this is not for multidisk, multinode
    # it is a temporary hack
    # no real disk mounted
    def do_swift_deploy_demo_local(cname):
        self = facility.get_component(cname)
        # prepare swift
        # this is from the all in script, it needs to be completly rewritten
        object_ip = self.get_addr_for(self.get_this_inv(), 'backing_object',
                                      net_attr='swift_object_network')
        # replica_ip = self.get_addr_for(self.get_this_inv(), 'replication',
        #                                net_attr='swift_object_replica_network')
        self.have_content()
        script = """
INSTALLER_DATA_DIR="%s"
BACKING_IP="%s"
mkdir $INSTALLER_DATA_DIR/swift
cd $INSTALLER_DATA_DIR/swift
# old demo only script!

for ring in account container object; do
   swift-ring-builder "$ring.builder" create 10 1 1 # 2^10 partiotions, 1 replicas (no replication), 1 hour move limit
done

# device is the name of directory in the /srv/node , normally it is a mounted xfs
swift-ring-builder account.builder add --region 1 --zone 1 --ip "$BACKING_IP" --port 6202 --device disk1 --weight 100
swift-ring-builder container.builder add --region 1 --zone 1 --ip "$BACKING_IP" --port 6201 --device disk1 --weight 100
swift-ring-builder object.builder add --region 1 --zone 1 --ip "$BACKING_IP" --port 6200 --device disk1 --weight 100

# update the ring file and copy to ALL SWIFT STORAGE SERVERS
# it should be rsync-d or scp -ed not cp -d, (or remote copied by the script itself)

for ring in account container object; do
  swift-ring-builder $ring.builder rebalance
  cp "$ring.ring.gz" /etc/swift/ # TODO: use install
done
""" % ('/tmp', object_ip)
        # we would need to use the inventory ips, and iterate over the full map
        localsh.run(script)

    def get_node_packages(self):
        pkgs = super(Swift, self).get_node_packages()
        pkgs.update({'curl', 'lib-dev\\erasurecode', 'memcached', 'lib-py3\\pyxattr',
                     'srv-rsync\\rsyncd', 'sqlite', 'xfsprogs'})
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-swift'})
        return pkgs

    def compose(self):
        super(Swift, self).compose()
        # it can consider the full inventory and config to influnce facility registered
        # resources
        url_base = "http://" + conf.get_vip('public')['domain_name']
        dr = conf.get_default_region()

        self.keystone.register_endpoint_tri(region=dr,
                                            name='swift',
                                            etype='object-store',
                                            description='Swift Storage Service',
                                            url_base=url_base + ':8080/v1/AUTH_$(tenant_id)s')
        self.keystone.register_service_admin_user('swift')
        sp = self.hosts_with_service('swift-proxy')
        util.bless_with_principal(sp,
                                  [(self.keystone.name, 'swift@default')])


# all in one joke ;-)
def etc_rsyncd_conf(self, ):
    object_ip = self.get_addr_for(self.get_this_inv(), 'backing_object',
                                  service=self.services['swift-object'],
                                  net_attr='swift_object_network')

    return {None: {'uid': 'swift',
                   'gid': 'swift',
                   'log file': '/var/log/rsyncd.log',
                   'pid file': '/var/run/rsyncd.pid',
                   'address': object_ip},

            'account': {'max connections': 3,
                        'path': '/srv/node/',
                        'read only':  False,
                        'lock file': '/var/lock/rsyncd/account.lock'},
            'containers': {'max connections': 3,
                           'path': '/srv/node/',
                           'read only':  False,
                           'lock file': '/var/lock/rsyncd/containers.lock'},
            'object': {'max connections': 3,
                       'path': '/srv/node/',
                       'read only':  False,
                       'lock file': '/var/lock/rsyncd/object.lock'}}


def register(self):
    sp = conf.get_service_prefix()

    rsyncd = {'name': 'rsyncd',
              'deploy_source': 'pkg',
              'deploy_source_options': {'pkg'},
              'component': 'rsyncd',
              'services': {'rsyncd': {'deploy_mode': 'standalone',
                                      'unit_name': {'src': sp + 'rsyncd',
                                                    'pkg': 'rsyncd'}}}}
#      'pkg_deps': rsyncd_pkgs,
#      'cfg_step': rsyncd_etccfg,
#      'goal': task_rsyncd

    facility.register_component(rsyncd)
