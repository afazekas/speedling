from speedling import facility
from speedling import inv
from speedling import util
import __main__
import speedling

from osinsutils import cfgfile
from osinsutils import localsh
import logging

LOG = logging.getLogger(__name__)


# this time the config is assembled on the caller
def do_haproxy_config(data):
    facility.task_wants(__main__.task_pkg_install)


CFG_DATA = {}


def get_defaults():
    d = {'global': {'chroot': '/var/lib/haproxy',
                    'pidfile': '/var/run/haproxy.pid',
                    'daemon': '',
                    'stats': 'socket /var/lib/haproxy/stats',
                    'user': 'haproxy',
                    'group': 'haproxy',
                    'maxconn': 16384,
                    'ssl-default-bind-ciphers': 'PROFILE=SYSTEM',
                    'ssl-default-server-ciphers': 'PROFILE=SYSTEM',
                    'log': '/dev/log local0'},
         'defaults': {'maxconn': 4096,
                      'retries': 3,
                      'timeout': {'http-request': '10s',
                                  'queue': '2m',
                                  'connect': '10s',
                                  'client': '10m',
                                  'server': '10m'}}}
    return d


def add_listener(name, cfg):
    listeners = CFG_DATA.setdefault('listen', {})
    assert name not in listeners
    listeners[name] = cfg


def add_stats_lister():
    keymgr = util.get_keymgr()
    pwd = keymgr('haproxy', 'admin')
    escaped = "'admin:" + pwd.replace("'", r"\'") + "'"
    stats = {'bind': '*:1993 transparent',
             'mode': 'http',
             'stats': {'enable': '',
                       'uri': '/',
                       'auth': escaped}}
    add_listener('haproxy.stats', stats)


# The content will be compesed by other services
def etc_haproxy_haproxy_cfg():
    return CFG_DATA


def etc_systemd_system_haproxy_service_d_limits_conf(): return {
        'Service': {'LimitNOFILE': 16384}
    }


def do_proxy(cfg):
    cfgfile.ensure_path_exists('/etc/systemd/system/haproxy.service.d')
    cfgfile.ini_file_sync('/etc/systemd/system/haproxy.service.d/limits.conf',
                          etc_systemd_system_haproxy_service_d_limits_conf())
    cfgfile.haproxy_file('/etc/haproxy/haproxy.cfg', cfg)
    localsh.run('systemctl daemon-reload && systemctl start haproxy')


def task_haproxy_steps():
    facility.task_wants(speedling.srv.common.task_selinux,
                        __main__.task_pkg_install)
    proxies = inv.hosts_with_service('haproxy')
    cfg = etc_haproxy_haproxy_cfg()
    inv.do_do(proxies, do_proxy, c_kwargs={'cfg': cfg})


def haproxy_compose():
    util.dict_merge(CFG_DATA, get_defaults())
    add_stats_lister()


def register():
    haproxy = {'component': 'haproxy',
               'deploy_source': 'pkg',
               'compose': haproxy_compose,
               'services': {'haproxy': {'deploy_mode': 'standalone'}},
               'pkg_deps': lambda: set(('haproxy',)),
               'goal': task_haproxy_steps}

    cc = facility.get_component_config_for('haproxy')
    util.dict_merge(haproxy, cc)
    facility.register_component(haproxy)


register()
