import threading
from osinsutils import localsh
from speedling import facility
from speedling import util
from speedling import gitutils
from speedling import inv


PIP_LOCK = threading.Lock()
PIP2_LOCK = threading.Lock()


# NOTE: This ensure lock thingy is repeated, maybe something smart would be nice
SYSTEM_HAS_PIP = None
SYSTEM_HAS_PIP2 = None
ENSURE_PIP_LOCK = threading.Lock()
ENSURE_PIP2_LOCK = threading.Lock()


def ensure_pip():
    global SYSTEM_HAS_PIP
    if SYSTEM_HAS_PIP:
        return
    try:
        ENSURE_PIP_LOCK.acquire()
        if SYSTEM_HAS_PIP:
            return
        localsh.run("pip3 --version ||  yum install -y python3-pip || pip3 --version")
        SYSTEM_HAS_PIP = True
    finally:
        ENSURE_PIP_LOCK.release()


def ensure_pip2():
    global SYSTEM_HAS_PIP2
    if SYSTEM_HAS_PIP2:
        return
    try:
        ENSURE_PIP2_LOCK.acquire()
        if SYSTEM_HAS_PIP2:
            return
        localsh.run("pip2 --version ||  yum install -y python2-pip || pip2 --version")
        SYSTEM_HAS_PIP2 = True
    finally:
        ENSURE_PIP2_LOCK.release()


SYSTEM_HAS_REQ = None
ENSURE_REQ_LOCK = threading.Lock()


def ensure_requirements():
    global SYSTEM_HAS_REQ
    if SYSTEM_HAS_REQ:
        return
    try:
        ENSURE_REQ_LOCK.acquire()
        if SYSTEM_HAS_REQ:
            return
        comp = facility.get_component('requirements')
        gitutils.procoss_component_repo(comp)
        SYSTEM_HAS_REQ = True
    finally:
        ENSURE_REQ_LOCK.release()


def setup_develop(comp):
    directory = gitutils.component_git_dir(comp)
    pip_install_req(['-r ' + directory + '/requirements.txt'])
    pip_install(['-e ' + directory])


def setup_develop2(comp):
    directory = gitutils.component_git_dir(comp)
    pip2_install_req(['-r ' + directory + '/requirements.txt'])
    pip2_install(['-e ' + directory])


def req_dir():
    comp = facility.get_component('requirements')
    return gitutils.component_git_dir(comp)


def pip_install_req(targets):
    # target either a 'package' or '-r req.txt', '-e project', input is iterable
    ensure_requirements()
    ensure_pip()
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
    ensure_pip()
    try:
        PIP_LOCK.acquire()
        localsh.run('pip3 install {targets}'.format(
                    targets=' '.join(targets)))
    finally:
        PIP_LOCK.release()


def pip2_install_req(targets):
    ensure_requirements()
    ensure_pip2()
    r_dir = req_dir()
    try:
        PIP2_LOCK.acquire()
        localsh.run('pip2 install --ignore-installed -c {req_dir}/upper-constraints.txt {targets}'.format(
                    req_dir=r_dir, targets=' '.join(targets)))
    finally:
        PIP2_LOCK.release()


def pip2_install(targets):
    ensure_requirements()
    ensure_pip2()
    try:
        PIP2_LOCK.acquire()
        localsh.run('pip2 install {targets}'.format(
                    targets=' '.join(targets)))
    finally:
        PIP2_LOCK.release()


# not wited, for early start
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

    cc = facility.get_component_config_for('requirements')
    # component related config validations here
    util.dict_merge(component, cc)
    facility.register_component(component)


register()
