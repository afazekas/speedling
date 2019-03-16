import logging
import time
import urllib.parse
from shlex import quote as cmd_quote

from speedling import facility
from speedling import localsh
from speedling import util

LOG = logging.getLogger(__name__)


def task_rabbit_steps(self):
    rh = self.hosts_with_service('rabbit')
    hostnames_to_invname = {self.get_node(n)['inv']['hostname']: n for n in rh}
    hostnames = list(hostnames_to_invname.keys())
    self.call_do(rh, self.do_rabbit_start)
    # TODO: cluster state must be ensured, nodes likely needs to be stop_app, reset, start_app
    r = self.call_do(rh, self.do_rabbitmq_test_in, c_kwargs={'candidates': hostnames})
    count = {n: 0 for n in hostnames}
    for node, val in r.items():
        for pres in val['return_value']:
            count[pres] += 1
    maxi = 0
    majority_node = next(iter(rh))
    majority_nodes = [majority_node]
    for node, cnt in count.items():
        if cnt > maxi:
            maxi = cnt
            majority_node = node
            majority_nodes = r[hostnames_to_invname[majority_node]]['return_value']
    # NOTE: hostname might differt from inventory name
    if maxi < len(rh):
        LOG.info("Not all rabbit node in cluster, correcting..")
        ok_nodes = {hostnames_to_invname[n] for n in majority_nodes}
        minority_nodes = rh - ok_nodes
        self.call_do(minority_nodes, self.do_rabbitmq_reset_join, c_kwargs={'leader': majority_node})
    retry = 128
    while retry != 0:
        r = self.call_do(rh, self.do_rabbitmq_test_in, c_kwargs={'candidates': hostnames})
        count = 0
        for node, val in r.items():
            if len(val['return_value']) < maxi:
                retry -= 1
                LOG.info('Wating for rabbit to came up..')
                continue
        break

    self.call_do({majority_node}, self.do_rabbit_addusers)


class RabbitMQ(facility.Messaging):

    deploy_source = 'pkg'
    services = {'rabbit': {'deploy_mode': 'standalone'}}

    def __init__(self, **kwargs):
        super(RabbitMQ, self).__init__(**kwargs)
        self.final_task = self.bound_to_instance(task_rabbit_steps)
        self.peer_info = {}
        self.user_registry = {}

    def do_rabbit_start(cname):
        self = facility.get_component(cname)
        self.have_content()
        retry = 128
        # TODO: use state file, or vallet/key_mgr
        self.file_plain('/var/lib/rabbitmq/.erlang.cookie', 'NETTIQETJNDTXLRUSANA',
                        owner='rabbitmq', mode=0o600)
        while True:
            try:
                if self.changed:  # TODO: rolling bounce
                    action = 'reload-or-restart'
                else:
                    action = 'start'
                localsh.run("systemctl {} rabbitmq-server".format(action))
                break
            except util.NonZeroExitCode:
                LOG.warn('Check the RABBIT systemd deps!')
                time.sleep(0.5)
                if not retry:
                    raise
                retry -= 1

    def get_node_packages(self):
        pkgs = super(RabbitMQ, self).get_node_packages()
        pkgs.update({'rabbitmq-server'})
        return pkgs

    def etc_rabbitmq_rabbitmq_config(self):
        rabbit_peer = self.get_this_node()['peers']['rabbitmq']
        nodes = ['rabbit@' + h['hostname'] for h in rabbit_peer]
        logical_repr = {'rabbit': {'cluster_nodes': (nodes, 'disc')},
                        'kernel': {},
                        'rabbitmq_management': {},
                        'rabbitmq_shovel': {'shovels': {}},
                        'rabbitmq_stomp': {},
                        'rabbitmq_mqtt': {},
                        'rabbitmq_amqp1_0': {},
                        'rabbitmq_auth_backend_ldap': {}}
        return logical_repr

    # TODO: WARNING guest:guest not deleted/changed!
    def do_rabbit_addusers(cname):
        self = facility.get_component(cname)
        pwd = cmd_quote(util.get_keymgr()(self.name, 'openstack'))
        localsh.run("""rabbitmqctl add_user openstack {passwd} ||
                    rabbitmqctl change_password openstack {passwd} &&
                    rabbitmqctl set_permissions -p / openstack ".*" ".*" ".*"
                    """.format(passwd=pwd))

    def do_rabbitmq_reset_join(cname, leader):
        localsh.run("""rabbitmqctl stop_app
                       rabbitmqctl reset
                       rabbitmqctl join_cluster {leader}
                       rabbitmqctl start_app
                    """.format(leader='rabbit@' + leader))

    def do_rabbitmq_test_in(cname, candidates):
        r = localsh.ret("rabbitmqctl cluster_status")
        # TODO: parse erlang data
        return [c for c in candidates if ('rabbit@' + c) in r]

    def etc_systemd_system_rabbitmq_server_service_d_limits_conf(self, ):
        return {
            'Service': {'LimitNOFILE': 16384}
        }

    def etc_systemd_system_epmd_socket_d_ports_conf_ports_conf(self): return {
        'Socket': {'ListenStream': ['', '[::]:4369']}
    }

    def etccfg_content(self):
        super(RabbitMQ, self).etccfg_content()
        # TODO raise the connection backlog, minority stalls ..
        # self.file_plain('',
        #                     rabbit_conf, mode=0o644)
        self.file_path('/etc/systemd/system/rabbitmq-server.service.d')
        self.file_ini('/etc/systemd/system/rabbitmq-server.service.d/limits.conf',
                      self.etc_systemd_system_rabbitmq_server_service_d_limits_conf())
        self.file_rabbit('/etc/rabbitmq/rabbitmq.config', self.etc_rabbitmq_rabbitmq_config(),
                         owner='rabbitmq', group='rabbitmq', mode=0o644)
        if util.get_distro()['family'] == 'suse':
            self.file_path('/etc/systemd/system/epmd.socket.d/ports.conf')
            self.file_ini('/etc/systemd/system/epmd.socket.d/ports.conf',
                          self.etc_systemd_system_rabbitmq_server_service_d_limits_conf)

    def get_peer_info(self):
        n = self.get_this_node()
        return n['peers']['rabbitmq']

    def populate_peer(self, nodes):
        rh = self.hosts_with_service('rabbit')
        port = 5672
        if not self.peer_info:
            self.peer_info = []
            for n in rh:
                node = self.get_node(n)
                hostname = node['inv']['hostname']
                addr = self.get_addr_for(node['inv'], 'messaging')
                self.peer_info.append({'hostname': hostname, 'addr': addr,
                                       'port': port})
        for n in nodes:
            node = self.get_node(n)
            node['peers']['rabbitmq'] = self.peer_info

    def transport_url(self, user='openstack', vhost=None):
        rabbit_peer = self.get_peer_info()
        pwd = util.get_keymgr()(self.name, user)
        pwd = urllib.parse.quote_plus(pwd)
        if not vhost:
            vhost = ''
        return 'rabbit://' + ','.join(
            '%s:%s@%s:%s' % (user, pwd, host['addr'], host['port'])
            for host in rabbit_peer) + '/' + vhost

    def compose(self):
        super(RabbitMQ, self).compose()
        rh = self.hosts_with_service('rabbit')
        self.populate_peer(rh)
        util.bless_with_principal(rh, [(self.name, 'openstack')])
