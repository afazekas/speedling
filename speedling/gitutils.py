# temporary stuff, it will work completly differently
import errno
import logging
import os
import subprocess
import threading
import urllib.parse

from speedling import localsh
from speedling import pkgutils

LOG = logging.getLogger(__name__)


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
        if not localsh.test("git --version"):
            pkgutils.get_pkgmgr().install({'git'})
        localsh.run("git --version")
        SYSTEM_HAS_GIT = True
    finally:
        ENSURE_GIT_LOCK.release()


def process_component_repo(component):
    url = component.origin_repo
    process_git_repo(url)


def component_git_dir(component):
    return url_to_dir(component.origin_repo)
