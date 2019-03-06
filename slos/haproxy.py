from speedling import facility
from speedling import util
import speedling
import speedling.tasks

from speedling import localsh
import logging

LOG = logging.getLogger(__name__)


CFG_DATA = {}


def task_haproxy_steps(self):
    facility.task_wants(speedling.tasks.task_selinux)
    proxies = self.hosts_with_service('haproxy')
    cfg = self.etc_haproxy_haproxy_cfg()
    self.call_do(proxies, self.do_proxy, c_kwargs={'cfg': cfg})


class HAProxy(facility.LoadBalancer):
    services = {'haproxy': {'deploy_mode': 'standalone'}}

    def __init__(self, *args, **kwargs):
        super(HAProxy, self).__init__(*args, **kwargs)
        self.cfg_data = {}
        self.final_task = self.bound_to_instance(task_haproxy_steps)
        assert self.final_task is not task_haproxy_steps
        assert self.final_task is self.task_haproxy_steps

    def get_node_packages(self):
        pkgs = super(HAProxy, self).get_node_packages()
        pkgs.update({'haproxy'})
        return pkgs
        # high pririty package we might want to install it sooner

    def compose(self):
        super(HAProxy, self).compose()
        util.dict_merge(self.cfg_data, self.get_defaults())
        self.add_stats_lister()

    def get_defaults(self):
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

    # The content will be compesed by other services
    def etc_haproxy_haproxy_cfg(self):
        return self.cfg_data

    def add_listener(self, name, cfg):
        listeners = self.cfg_data.setdefault('listen', {})
        assert name not in listeners
        listeners[name] = cfg

    def add_stats_lister(self):
        keymgr = util.get_keymgr()
        pwd = keymgr('haproxy' + self.suffix, 'admin')
        escaped = "'admin:" + pwd.replace("'", r"\'") + "'"
        stats = {'bind': '*:1993 transparent',
                 'mode': 'http',
                 'stats': {'enable': '',
                           'uri': '/',
                           'auth': escaped}}
        self.add_listener('haproxy.stats', stats)

    def etc_systemd_system_haproxy_service_d_limits_conf(self): return {
        'Service': {'LimitNOFILE': 16384}
    }

    def do_proxy(cname, cfg):
        self = facility.get_component(cname)
        self.have_content()
        self.ensure_path_exists('/etc/systemd/system/haproxy.service.d')
        self.ini_file_sync('/etc/systemd/system/haproxy.service.d/limits.conf',
                           self.etc_systemd_system_haproxy_service_d_limits_conf())
        self.haproxy_file('/etc/haproxy/haproxy.cfg', cfg)
        localsh.run('systemctl daemon-reload && systemctl reload-or-restart haproxy')
