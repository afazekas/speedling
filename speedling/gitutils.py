# temporary stuff, it will work completly differently
import multiprocessing
import threading
import subprocess
import os
import errno
import logging
import urllib.parse

from osinsutils import localsh

LOG = logging.getLogger(__name__)


# Deprecated
def gen_repo_urls():
    # TODO: mirror , fuction translates url
    return (
       "https://github.com/openstack/nova.git",
       "https://github.com/openstack/neutron.git",
       "https://github.com/openstack/glance.git",
       "https://github.com/openstack/cinder.git",
       "https://github.com/openstack/keystone.git",
       "https://github.com/openstack/swift.git",
       "https://github.com/openstack/tempest.git",
       "https://github.com/openstack/requirements.git",
    )


REPO_ROOT = '/opt/stack'


def url_to_dir(url):
    parsed = urllib.parse.urlparse(url)
    dir_name_git = parsed.path.split('/')[-1]
    dir_name = '.'.join(dir_name_git.split('.')[:-1])
    # TODO(afazekas): use normal regexp
    return os.path.join(REPO_ROOT, dir_name)


# NOTE: this code is temporary the git clone will be parellel to other steps
def process_git_repo(url, branch='master', extra_remotes={}, pull_strage=None,
                     initial_clone_url=None):
    ensure_git()
    try:
        os.makedirs(REPO_ROOT)
    except OSError as e:
        if e.errno != errno.EEXIST:
            raise
    try:
        dir_name = url_to_dir(url)
        dest_dir = os.sep.join((REPO_ROOT, dir_name))
        if not os.path.isdir(dest_dir):
            subprocess.call(('git', 'clone', url), cwd=REPO_ROOT)
        else:
            # TODO(afazekas): try stash
            subprocess.call(('git', 'pull', url), cwd=dest_dir)
    except Exception as exc:
        LOG.exception(exc)
        raise exc
    return True


SYSTEM_HAS_GIT = None
ENSURE_GIT_LOCK = threading.Lock()


def ensure_git():
    global SYSTEM_HAS_GIT
    if SYSTEM_HAS_GIT:
        return
    try:
        ENSURE_GIT_LOCK.acquire()
        if SYSTEM_HAS_GIT:
            return
        localsh.run("git --version ||  yum install -y git || git --version")
        SYSTEM_HAS_GIT = True
    finally:
        ENSURE_GIT_LOCK.release()


def procoss_component_repo(component):
    url = component['origin_repo']
    process_git_repo(url)


def component_git_dir(component):
    return url_to_dir(component['origin_repo'])


# Deprecated
def git_fetch_all():
    p = multiprocessing.Pool(multiprocessing.cpu_count())
    return p.map(process_git_repo, gen_repo_urls())
