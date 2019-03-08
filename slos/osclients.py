import logging

from speedling import facility
from speedling import piputils

LOG = logging.getLogger(__name__)


def task_osclients_steps(self):
    if self.deploy_source == 'pip':
        self.call_do(self.hosts_with_component('pythonopenstackclient'), self.do_pip)


class PythonOpenstackClient(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/python-openstackclient.git'
    deploy_source = 'pip'
    deploy_source_options = {'src', 'pkg', 'pip'}

    def __init__(self, *args, **kwargs):
        super(PythonOpenstackClient, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_osclients_steps)

    def do_pip(cname):
        piputils.pip_install_req(['python-openstackclient'])

    def get_node_packages(self):
        pkgs = super(PythonOpenstackClient, self).get_node_packages()
        if self.deploy_source == 'pkg':
            pkgs.update({'python-openstackclient'})
        return pkgs
