import logging
import threading

from speedling import facility
from speedling import gitutils
from speedling import inv
from speedling import localsh
from speedling import pkgutils

LOG = logging.getLogger(__name__)

PIP_LOCK = threading.Lock()


# NOTE: This ensure lock thingy is repeated, maybe something smart would be nice


SYSTEM_HAS_REQ = None
ENSURE_REQ_LOCK = threading.Lock()

# TODO: make it option for the openstack component
REQUIREMENTS_URL = 'https://github.com/openstack/requirements.git'


def ensure_requirements():
    global SYSTEM_HAS_REQ
    if SYSTEM_HAS_REQ:
        return
    try:
        ENSURE_REQ_LOCK.acquire()
        if SYSTEM_HAS_REQ:
            return
        gitutils.process_git_repo(REQUIREMENTS_URL)
        SYSTEM_HAS_REQ = True
    finally:
        ENSURE_REQ_LOCK.release()


def setup_develop(comp):
    LOG.info('setup_develop: ' + comp.name)
    directory = gitutils.url_to_dir(comp.origin_repo)
    pip_install_req(['-r ' + directory + '/requirements.txt'])
    pip_install(['-e ' + directory])


def req_dir():
    return gitutils.url_to_dir(REQUIREMENTS_URL)


def pip_install_req(targets):
    # target either a 'package' or '-r req.txt', '-e project', input is iterable
    ensure_requirements()
    pkgutils.ensure_compose()
    r_dir = req_dir()
    try:
        PIP_LOCK.acquire()
        localsh.run('pip3 install -c {req_dir}/upper-constraints.txt {targets}'.format(
                    req_dir=r_dir, targets=' '.join(targets)))
    finally:
        PIP_LOCK.release()


def pip_install(targets):
    # target either a 'package' or '-r req.txt', '-e project', input is iterable
    ensure_requirements()
    pkgutils.ensure_compose()
    try:
        PIP_LOCK.acquire()
        localsh.run('pip3 install {targets}'.format(
                    targets=' '.join(targets)))
    finally:
        PIP_LOCK.release()


# NOT removed beeing a comopenent, but I change my mind it will be again
# but it will refuses to have multiple aliases
# not waited, for early start
def task_requirements():
    # TODO: limit to nodes with srv component
    inv.do_do(inv.ALL_NODES, ensure_requirements)


def register():
    # pseudo component for having git config
    component = {
        'origin_repo': 'https://github.com/openstack/requirements.git',
        'deploy_source': 'src',
        'deploy_source_options': {'src'},
        'component': 'requirements',
        'pkg_deps': lambda: {'git'},
        'goal': task_requirements,
    }

    facility.register_component(component)
