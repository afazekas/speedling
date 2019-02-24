from speedling import inv
import speedling
from speedling import facility
from speedling import tasks
from speedling import piputils


import logging


LOG = logging.getLogger(__name__)


def do_pip():
    piputils.pip_install_req(['python-openstackclient'])


def task_osclients_steps():
    facility.task_wants(speedling.tasks.task_pkg_install)
    comp = facility.get_component('python-openstackclient')
    if comp['deploy_source'] == 'pip':
        inv.do_do(inv.hosts_with_component('python-openstackclient'), do_pip)


def osclients_pkgs():
    comp = facility.get_component('python-openstackclient')
    pkg = ['python-openstackclient']
    if comp['deploy_source'] == 'pkg':
        return set(pkg)
    return set()


def osclients_compose():
    tasks.compose_prepare_source_cond('python-openstackclient')


def register():
    component = {
      'origin_repo': 'https://github.com/openstack/python-openstackclient.git',
      'deploy_source': 'pip',
      'deploy_source_options': {'src', 'pkg', 'pip'},
      'component': 'python-openstackclient',
      'compose': osclients_compose,
      'pkg_deps': osclients_pkgs,
      'goal': task_osclients_steps,
    }

    # component related config validations here
    facility.register_component(component)


register()
