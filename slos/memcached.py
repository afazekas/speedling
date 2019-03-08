import logging

from speedling import facility
from speedling import localsh

LOG = logging.getLogger(__name__)


def task_memcached_steps(self):
    h = self.hosts_with_service('memcached')
#   facility.task_wants(speedling.tasks.task_pkg_install)
    self.call_do(h, self.do_memcached_service_start)


class Memcached(facility.Component):
    services = {'memcached': {'deploy_mode': 'standalone'}}
    deploy_source = 'pkg'

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
        pkgs.update({'memcached'})
        return pkgs
