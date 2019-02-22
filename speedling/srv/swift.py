from speedling import util
from speedling import inv
from speedling import conf
from speedling import tasks
import __main__
from speedling import facility

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import logging

LOG = logging.getLogger(__name__)

# TODO: add more
s_srv = {'swift-object', 'swift-container', 'swift-account', 'swift-proxy',
         'swift-container-sync'}
s_store = {'swift-object', 'swift-container', 'swift-account'}


# crossdomain, insecure setting, everybody is truseted, XSS like issue can happen!
# TODO: add ceilometer to the pipeline
# TODO: add memcached
# TODO: readd swift3
def etc_swift_proxy_server_conf():
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

    r['filter:authtoken'] = util.keystone_authtoken_section('swift')
    r['filter:authtoken']['delay_auth_decision'] = 1
    r['filter:authtoken']['paste.filter_factory'] = 'keystonemiddleware.auth_token:filter_factory'
    r['filter:keystoneauth']['operator_roles'] = "user, admin"
    r['filter:keystoneauth']['reseller_admin_role'] = "admin"
    comp = facility.get_component('swift')
    proxy_ip = inv.get_addr_for(inv.get_this_inv(), 'internal_listen',
                                component=comp,
                                service=comp['services']['swift-proxy'],
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


# TODO: it is poissble to get the listen/replication bind data from the keyring
#  worker/port
def etc_swift_container_server_conf():
    comp = facility.get_component('swift')
    object_ip = inv.get_addr_for(inv.get_this_inv(), 'backing_object',
                                 service=comp['services']['swift-container'],
                                 component=comp,
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
def etc_swift_container_sync_realms_conf():
    return {'realm1': {'key': 'realm1key',
            'cluster_name1': 'http://' + conf.get_vip('public')['domain_name'] + ':8080/v1/'}}


def etc_swift_object_server_conf():
    comp = facility.get_component('swift')
    object_ip = inv.get_addr_for(inv.get_this_inv(), 'backing_object',
                                 service=comp['services']['swift-object'],
                                 component=comp,
                                 net_attr='swift_object_network')

    r = {'DEFAULT': {'mount_check': False,  # consider setting to true on prod
                     'bind_ip': object_ip,
                     'bind_port': 6200},
         'pipeline:main': {'pipeline': 'object-server'},
         'app:object-server': {'use': 'egg:swift#object'}}
    return r


def etc_swift_account_server_conf():
    comp = facility.get_component('swift')
    object_ip = inv.get_addr_for(inv.get_this_inv(), 'backing_object',
                                 service=comp['services']['swift-account'],
                                 component=comp,
                                 net_attr='swift_object_network')
    r = {'DEFAULT': {'mount_check': False,  # consider setting to true on prod
                     'bind_ip': object_ip,
                     'bind_port': 6202},
         'pipeline:main': {'pipeline': 'account-server'},
         'app:account-server': {'use': 'egg:swift#account'}}
    return r


# all in one joke ;-)
def etc_rsyncd_conf():
    comp = facility.get_component('swift')
    object_ip = inv.get_addr_for(inv.get_this_inv(), 'backing_object',
                                 service=comp['services']['swift-object'],
                                 component=comp,
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


def etc_swift_swift_conf():
    return {'swift-hash': {'swift_hash_path_suffix': '1234123412341234',
                           'swift_hash_path_prefix': 'changeme'},
            'storage-policy:0': {'name': 'Policy-0',
                                 'default': 'yes',
                                 'aliases': 'yellow, orange'},
            'swift-constraints': {'max_file_size': 5368709122,
                                  'max_header_size': 16384}}


def swift_etccfg(services, global_service_union):
    usrgrp.group('swift', 160)
    usrgrp.user('swift', 160)
    cfgfile.ensure_path_exists('/etc/swift',
                               owner='swift', group='swift')

    cfgfile.ini_file_sync('/etc/swift/swift.conf',
                          etc_swift_swift_conf(),
                          owner='swift', group='swift')
    comp = facility.get_component('swift')
    if comp['deploy_source'] == 'src':
        c_srv = comp['services']
        util.unit_file(c_srv['swift-account']['unit_name']['src'],
                       '/usr/bin/swift-account-server /etc/swift/account-server.conf',
                       'swift')
        util.unit_file(c_srv['swift-object']['unit_name']['src'],
                       '/usr/bin/swift-object-server /etc/swift/object-server.conf',
                       'swift')
        util.unit_file(c_srv['swift-container']['unit_name']['src'],
                       '/usr/bin/swift-container-server /etc/swift/container-server.conf',
                       'swift')
        util.unit_file(c_srv['swift-container-sync']['unit_name']['src'],
                       '/usr/bin/swift-container-sync /etc/swift/container-server.conf',
                       'swift')
        util.unit_file(c_srv['swift-proxy']['unit_name']['src'],
                       '/usr/bin/swift-proxy-server /etc/swift/proxy-server.conf',
                       'swift')
    if 'swift-proxy' in services:
        cfgfile.ini_file_sync('/etc/swift/proxy-server.conf',
                              etc_swift_proxy_server_conf(),
                              owner='swift', group='swift')

    if 'swift-container' in services or 'swift-demo' in services:
        cfgfile.ini_file_sync('/etc/swift/container-server.conf',
                              etc_swift_container_server_conf(),
                              owner='swift', group='swift')

    if 'swift-proxy' in services or 'swift-container' in services:
        cfgfile.ini_file_sync('/etc/swift/container-sync-realms.conf',
                              etc_swift_container_sync_realms_conf(),
                              owner='swift', group='swift')

    if 'swift-object' in services or 'swift-demo' in services:
        cfgfile.ini_file_sync('/etc/swift/object-server.conf',
                              etc_swift_object_server_conf(),
                              owner='swift', group='swift')

    if 'swift-account' in services or 'swift-demo' in services:
        cfgfile.ini_file_sync('/etc/swift/account-server.conf',
                              etc_swift_account_server_conf(),
                              owner='swift', group='swift')

    if services.intersection(s_store):
        cfgfile.ensure_path_exists('/srv/node', owner='swift', group='swift')
        # TODO use node config
        cfgfile.ensure_path_exists('/srv/node/disk1',
                                   owner='swift', group='swift')
        cfgfile.ensure_path_exists('/var/lock/rsyncd',
                                   owner='swift', group='swift')
        cfgfile.ini_file_sync('/etc/rsyncd.conf',
                              etc_rsyncd_conf(),
                              owner='root', group='swift', mode=0o644)


def do_swift_service_start():
    tasks.local_os_service_start_by_component('swift')

    # NOTE: other service will be started implictly
    selected_services = inv.get_this_inv()['services']
    if selected_services.intersection(s_store):
        localsh.run('systemctl start rsyncd')


# this is not for multidisk, multinode
# it is a temporary hack
# no real disk mounted
def do_swift_deploy_demo_local():
    comp = facility.get_component('swift')
    # prepare swift
    # this is from the all in script, it needs to be completly rewritten
    object_ip = inv.get_addr_for(inv.get_this_inv(), 'backing_object',
                                 component=comp,
                                 net_attr='swift_object_network')
    replica_ip = inv.get_addr_for(inv.get_this_inv(), 'replication',  # NOQA
                                  component=comp,
                                  net_attr='swift_object_replica_network')
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


def task_swift_steps():
    # TODO: /etc/container-sync-realms.conf
    facility.task_wants(__main__.task_cfg_etccfg_steps)
    # not multinode firendly!
    inv.do_do(inv.hosts_with_any_service(s_store), do_swift_deploy_demo_local)
    inv.do_do(inv.hosts_with_any_service(s_srv), do_swift_service_start)


def swift_pkgs():
    return {'curl', 'liberasurecode-devel', 'memcached', 'pyxattr',
            'rsync-daemon', 'sqlite', 'xfsprogs', 'xinetd'}


def swift_compose():
    # it can consider the full inventory and config to influnce facility registered
    # resources
    url_base = "http://" + conf.get_vip('public')['domain_name']
    dr = conf.get_default_region()

    facility.register_endpoint_tri(region=dr,
                                   name='swift',
                                   etype='object-store',
                                   description='Swift Storage Service',
                                   url_base=url_base + ':8080/v1/AUTH_$(tenant_id)s')
    facility.register_service_admin_user('swift')
    tasks.compose_prepare_source_cond('swift', pip2=True)
    sp = inv.hosts_with_service('swift-proxy')
    util.bless_with_principal(sp,
                              [('os', 'swift@default')])


def register():
    sp = conf.get_service_prefix()
    component = {'origin_repo': 'https://github.com/openstack/swift.git',
                 'deploy_source': 'src',
                 'deploy_source_options': {'src', 'pkg'},
                 'services': {'swift-proxy': {'deploy_mode': 'standalone',
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
                                                                     'pkg': 'openstack-swift-container-sync'}}},
                 'deploy_mode': 'standalone',
                 'component': 'swift',
                 'compose': swift_compose,
                 'pkg_deps': swift_pkgs,
                 'cfg_step': swift_etccfg,
                 'goal': task_swift_steps}
    facility.register_component(component)

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


register()
