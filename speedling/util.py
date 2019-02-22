import osinsutils.keymgrs
from osinsutils import cfgfile

from collections import abc
from speedling import conf
from speedling import inv

import os


try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote  # noqa others can use form here


# make it later after realy have a chace to select


def is_receiver():
    args = conf.get_args()
    return args.receiver


DIR_INITED = set()


def get_state_dir(suffix=''):
    args = conf.get_args()
    state_dir = '/'.join((args.state_dir, ))
    if suffix not in DIR_INITED:
        cfgfile.ensure_path_exists(state_dir,
                                   owner=os.getuid(), group=os.getgid())
        DIR_INITED.add(suffix)
    return state_dir


# TODO: consider chche/singleton decorator usage
# NOTE: passwords should ne be parameter of command, if you see them as command
#       parameter in the log is needs to be fixed __command__
KEYMGR = None
SELECTED_KEYMGR = None


def get_keymanager():
    global SELECTED_KEYMGR
    if SELECTED_KEYMGR:
        return SELECTED_KEYMGR
    if is_receiver():
        node = inv.get_this_node()
        SELECTED_KEYMGR = osinsutils.keymgrs.MemoryKeyMgr(data=node['keys'])
    else:
        state_file = get_state_dir() + '/creds.json'
        SELECTED_KEYMGR = osinsutils.keymgrs.JSONKeyMgr(datafile=state_file)
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
    pwd = get_keymgr()('os', '@'.join((user, domain)))
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


def keystone_authtoken_section(service_user):
    # per docs why not, never tested!
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
        if s not in creds:
            creds[s] = {p: pas}
        else:
            creds[s][p] = pas
    bless_with_creads(nodes, creds)
