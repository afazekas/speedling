from speedling import facility
from speedling import inv
from osinsutils import localsh
import speedling

import logging

LOG = logging.getLogger(__name__)


# NOTE: changing sebools is sloow
# NOTE: it is possible the httpd_use_openstack is unkown at this time
# NOTE: we might consider setenforce 0 only when we are wating for bool changes
# TODO: do not forget to reenable selinux
# NOTE: At image creation time you can enable those by default
def do_selinux():
    localsh.run("""
    setenforce 0 # please report the detected issues!
    setsebool -P httpd_can_network_connect on
    setsebool -P httpd_use_openstack on
    setsebool -P haproxy_connect_any=1
    """)


def do_selinux_permissive():
    localsh.run("""
    setenforce 0 # please report the detected issues!""")
    # persist ?


def task_selinux():
    facility.task_wants(speedling.tasks.task_pkg_install)
    inv.do_do(inv.ALL_NODES, do_selinux_permissive)  # TODO: persistent config
    return  # excluded
    inv.do_do(inv.ALL_NODES, do_selinux)  # httpd nodes differs..


def memcached_pkgs():
    return {'memcached'}


# DANGER: no auth service
# TODO: call by the users
def do_memcached_service_start():
    localsh.run('systemctl start memcached')


def task_memcached_steps():
    h = inv.hosts_with_service('memcached')
    facility.task_wants(speedling.tasks.task_pkg_install)
    inv.do_do(h, do_memcached_service_start)


# WARNING mongo bind_ip to all INSECURE  especially without authentiction!!!
# if we do not specifi the bind_ip (as we don't) it will bind all

def mongo_conf(): return {None: {
    'fork': True,
    'pidfilepath': '/var/run/mongodb/mongod.pid',
    'logpath': '/var/log/mongodb/mongod.log',
    'unixSocketPrefix': '/var/run/mongodb',
    'dbpath': '/var/lib/mongodb'}
}


def register():
    memcached = {'component': 'memcached',
                 'deploy_source': 'pkg',
                 'services': {'memcached': {'deploy_mode': 'standalone'}},
                 'pkg_deps': memcached_pkgs,
                 'goal': task_memcached_steps}
    facility.register_component(memcached)


register()
