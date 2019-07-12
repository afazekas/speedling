import collections
import os
import random
import re
import threading
from collections import abc

import speedling.keymgrs
from speedling import cfgfile
from speedling import conf
from speedling import inv
from speedling import pkgutils

RE_HUMAN_SIZE = re.compile(r'(\d+)(.*)')
UNITS = {'k': 1024, 'm': 2**20, 'g': 2**30, 't': 2**40, 'p': 2**50, 'e': 2**60}


def human_byte_to_int(human_str):
    s = human_str.strip()
    m = RE_HUMAN_SIZE.search(s)
    si = int(m.group(1))
    u = m.group(2).strip()[0].lower()
    return si * UNITS[u]


def lock_sigleton_call(the_do_do):
    lock = threading.Lock()
    done = False

    def lock_sigleton_caller(*args, **kwargs):
        nonlocal lock
        nonlocal done
        if done:
            return
        try:
            lock.acquire()
            if done:
                return
            the_do_do(*args, **kwargs)  # can raise multiple times
            done = True
        finally:
            lock.release()

    return lock_sigleton_caller


def is_receiver():
    args = conf.get_args()
    return args.receiver


DIR_INITED = set()


def rand_pick(hosts, k=1):
    return set(random.sample(hosts, k))


def get_state_dir(suffix=''):
    args = conf.get_args()
    state_dir = '/'.join((args.state_dir, ))
    if suffix not in DIR_INITED:
        cfgfile.ensure_path_exists(state_dir,
                                   owner=os.getuid(), group=os.getgid())
        DIR_INITED.add(suffix)
    return state_dir


# TODO: consider cache/singleton decorator usage
# NOTE: passwords should not be parameter of command, if you see them as command
#       argument, it is needs to be fixed __command__
#       export/stdin can be hidded and it is ok
KEYMGR = None
SELECTED_KEYMGR = None


def get_keymanager():
    global SELECTED_KEYMGR
    if SELECTED_KEYMGR:
        return SELECTED_KEYMGR
    if is_receiver():
        node = inv.get_this_node()
        SELECTED_KEYMGR = speedling.keymgrs.MemoryKeyMgr(data=node['keys'])
    else:
        state_file = get_state_dir() + '/creds.json'
        SELECTED_KEYMGR = speedling.keymgrs.JSONKeyMgr(datafile=state_file)
    return SELECTED_KEYMGR


def get_keymgr():
    global KEYMGR
    if KEYMGR:
        return KEYMGR
    KEYMGR = get_keymanager().get_creds
    return KEYMGR


def base_service_dirs(service):
    cfgfile.ensure_path_exists('/etc/' + service,
                               owner=service, group=service)
    cfgfile.ensure_path_exists('/var/lib/' + service,
                               owner=service, group=service)
    cfgfile.ensure_path_exists('/var/log/' + service,
                               owner=service, group=service)


# Not cycle tolerant
def dict_merge(destination, source):

    for key, value in source.items():
        if isinstance(value, abc.Mapping):
            node = destination.setdefault(key, {})
            dict_merge(node, value)
        else:
            destination[key] = value

    return destination


def userrc_script(user, project=None, domain='default'):
    if not project:
        project = user
    pwd = get_keymgr()('keystone', '@'.join((user, domain)))  # TODO: multikeystone
    return """export OS_PROJECT_DOMAIN_ID={domain}
export OS_USER_DOMAIN_ID={domain}
export OS_PROJECT_NAME={project}
export OS_TENANT_NAME={project}
export OS_USERNAME={user}
export OS_PASSWORD='{pwd}'
export OS_AUTH_URL=http://{vip}:35357/v3
export OS_IDENTITY_API_VERSION=3;""".format(pwd=pwd,
                                            vip=conf.get_vip('public')['domain_name'],
                                            user=user,
                                            project=project,
                                            domain=domain)


def unit_file(unit_name, start_cmd, user, requires=None, restart=None):
    d = {'Unit': {'Description': 'Speedling {unit_name}.service'
                  .format(unit_name=unit_name)},
         'Service': {'ExecReload': '/usr/bin/kill -HUP $MAINPID',
                     'TimeoutStopSec': 300,
                     'KillMode': 'process',
                     'ExecStart': start_cmd,
                     'User': user},
         'Install': {'WantedBy':  'multi-user.target'}}

    if requires:
        d['Unit']['Requires'] = requires
    if restart:
        d['Service']['Restart'] = restart
    cfgfile.ini_file_sync('/etc/systemd/system/{unit_name}.service'
                          .format(unit_name=unit_name), d)


# moved to keystone
def _keystone_authtoken_section(service_user):
    d = {"auth_url": 'http://' + conf.get_vip('public')['domain_name'] + ':5000/',
         "project_domain_name": 'Default',
         "project_name": 'service',
         "password": get_keymgr()('os', service_user + '@default'),
         "user_domain_name": 'Default',
         "username": service_user,
         "auth_type": 'password'}
    return d


def bless_with_creads(nodes, creds):
    for n in nodes:
        node = inv.get_node(n)
        dict_merge(node['keys'], creds)


def bless_with_principal(nodes, prlist):
    creds = {}
    keymgr = get_keymgr()
    for (s, p) in prlist:
        pas = keymgr(s, p)
        s = speedling.keymgrs.real_name(s)
        if s not in creds:
            creds[s] = {p: pas}
        else:
            creds[s][p] = pas
    bless_with_creads(nodes, creds)


DISTRO = collections.OrderedDict()


def get_distro():
    # dummy logic, just for 3 supported distro is good enough,
    # but later we will need to sue lsb_release and some fallbacks
    global DISTRO
    if DISTRO:
        return DISTRO
    pkg_mgr = pkgutils.detect_pkg_mgr()
    if 'dnf' == pkg_mgr:
        DISTRO['family'] = 'redhat'
        DISTRO['variant'] = 'fedora'
        DISTRO['version'] = '30'
    elif 'apt-get' == pkg_mgr:
        DISTRO['family'] = 'debian'
        DISTRO['variant'] = 'ubuntu'
        DISTRO['version'] = '18.10'
    elif 'zypper' == pkg_mgr:
        DISTRO['family'] = 'suse'
        DISTRO['variant'] = 'opensuse'
        DISTRO['version'] = '15.0'
    else:
        raise Exception('Unable to figure out the Linux distribution')
    return DISTRO


class SpeedlingException(Exception):
    pass


class NonZeroExitCode(SpeedlingException):
    pass


class TaskAbort(SpeedlingException):
    pass
