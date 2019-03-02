from speedling import facility
from speedling import inv
from osinsutils import localsh

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


# move to per node stuff
def task_selinux():
    #    facility.task_wants(speedling.tasks.task_pkg_install)
    inv.do_do(inv.ALL_NODES, do_selinux_permissive)  # TODO: persistent config
    return  # excluded
    inv.so_do(inv.ALL_NODES, do_selinux)  # httpd nodes differs..


def memcached_pkgs(self, ):
    return {'memcached'}


def task_memcached_steps(self):
    h = self.hosts_with_service('memcached')
#   facility.task_wants(speedling.tasks.task_pkg_install)
    self.call_do(h, self.do_memcached_service_start)


class Memcached(facility.Component):
    services = {'memcached': {'deploy_mode': 'standalone'}}
    default_deploy_source = 'pkg'

    def __init__(self, **kwargs):
        super(Memcached, self).__init__()
        self.cfg_data = {}
        self.final_task = self.bound_to_instance(task_memcached_steps)

    # DANGER: no auth service
    def do_memcached_service_start(cname):
        self = facility.get_component(cname)
        self.have_content()
        localsh.run('systemctl start memcached')

    def get_node_packages(self):
        pkgs = super(Memcached, self).get_node_packages()
        pkgs.update({'memcached', 'python3-memcached'})
        return pkgs
