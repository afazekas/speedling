from speedling import facility
from speedling import inv
from speedling import util
from osinsutils import localsh
import time
import speedling

from osinsutils import cfgfile

import logging
import urllib.parse

LOG = logging.getLogger(__name__)


def do_rabbit_start():
    retry = 1024
    # TODO: use state file, or vallet/key_mgr
    cfgfile.content_file('/var/lib/rabbitmq/.erlang.cookie', 'NETTIQETJNDTXLRUSANA',
                         owner='rabbitmq', mode=0o600)
    while True:
        try:
            localsh.run("systemctl start rabbitmq-server")
            break
        except:
            LOG.warn('Check the RABBIT systemd deps!')
            time.sleep(0.2)
            if not retry:
                raise
            retry -= 1


def rabbit_pkgs():
    return {'rabbitmq-server'}


def do_create_rabbit_cfg():
    rabbit_peer = inv.get_this_node()['peers']['rabbitmq']
    nodes = ['rabbit@' + h['hostname'] for h in rabbit_peer]
    logical_repr = {'rabbit': {'cluster_nodes': (nodes, 'disc')},
                    'kernel': {},
                    'rabbitmq_management': {},
                    'rabbitmq_shovel': {'shovels': {}},
                    'rabbitmq_stomp': {},
                    'rabbitmq_mqtt': {},
                    'rabbitmq_amqp1_0': {},
                    'rabbitmq_auth_backend_ldap': {}}
    cfgfile.rabbit_file('/etc/rabbitmq/rabbitmq.config', logical_repr,
                        owner='rabbitmq', group='rabbitmq', mode=0o644)


# TODO: WARNING guest:guest not deleted/changed!
def do_rabbit_addusers():
    pwd = util.cmd_quote(util.get_keymgr()('rabbit', 'openstack'))
    localsh.run("""rabbitmqctl add_user openstack {passwd} ||
                rabbitmqctl change_password openstack {passwd} &&
                rabbitmqctl set_permissions -p / openstack ".*" ".*" ".*"
                """.format(passwd=pwd))


def do_rabbitmq_reset_join(leader):
    localsh.run("""rabbitmqctl stop_app
                   rabbitmqctl reset
                   rabbitmqctl join_cluster {leader}
                   rabbitmqctl start_app
                """.format(leader='rabbit@' + leader))


def do_rabbitmq_test_in(candidates):
    r = localsh.ret("rabbitmqctl cluster_status")
    # TODO: parse erlang data
    return [c for c in candidates if ('rabbit@' + c) in r]


def task_rabbit_steps():
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)
    rh = inv.hosts_with_service('rabbit')
    hostnames_to_invname = {inv.get_node(n)['inv']['hostname']: n for n in rh}
    hostnames = list(hostnames_to_invname.keys())
    inv.do_do(rh, do_create_rabbit_cfg)
    inv.do_do(rh, do_rabbit_start)
    # TODO: cluster state must be ensured, nodes likely needs to be stop_app, reset, start_app
    r = inv.do_do(rh, do_rabbitmq_test_in, c_kwargs={'candidates': hostnames})
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
        inv.do_do(minority_nodes, do_rabbitmq_reset_join, c_kwargs={'leader': majority_node})
    retry = 128
    while retry != 0:
        r = inv.do_do(rh, do_rabbitmq_test_in, c_kwargs={'candidates': hostnames})
        count = 0
        for node, val in r.items():
            if len(val['return_value']) < maxi:
                retry -= 1
                LOG.info('Wating for rabbit to came up..')
                continue
        break

    inv.do_do({majority_node}, do_rabbit_addusers)


def etc_systemd_system_rabbitmq_server_service_d_limits_conf(): return {
        'Service': {'LimitNOFILE': 16384}
    }


def rabbit_etccfg(services):
    # TODO raise the connection backlog, minority stalls ..
    # cfgfile.content_file('',
    #                     rabbit_conf, mode=0o644)
    cfgfile.ensure_path_exists('/etc/systemd/system/rabbitmq-server.service.d')
    cfgfile.ini_file_sync('/etc/systemd/system/rabbitmq-server.service.d/limits.conf',
                          etc_systemd_system_rabbitmq_server_service_d_limits_conf())


def get_peer_info():
    n = inv.get_this_node()
    return n['peers']['rabbitmq']


PEER_INFO = None


def populate_peer(nodes):
    global PEER_INFO
    rh = inv.hosts_with_service('rabbit')
    port = 5672
    if not PEER_INFO:
        PEER_INFO = []
        for n in rh:
            node = inv.get_node(n)
            hostname = node['inv']['hostname']
            addr = inv.get_addr_for(node['inv'], 'messaging')
            PEER_INFO.append({'hostname': hostname, 'addr': addr,
                              'port': port})
    for n in nodes:
        node = inv.get_node(n)
        node['peers']['rabbitmq'] = PEER_INFO


def transport_url(user='openstack', vhost=None):
    rabbit_peer = get_peer_info()
    pwd = util.get_keymgr()('rabbit', user)
    pwd = urllib.parse.quote_plus(pwd)
    if not vhost:
        vhost = ''
    return 'rabbit://' + ','.join(
        '%s:%s@%s:%s' % (user, pwd, host['addr'], host['port'])
        for host in rabbit_peer) + '/' + vhost


def rabbit_compose():
    rh = inv.hosts_with_service('rabbit')
    populate_peer(rh)
    util.bless_with_principal(rh, [('rabbit', 'openstack')])


def register():
    rabbit = {'component': 'rabbit',
              'deploy_source': 'pkg',
              'services': {'rabbit': {'deploy_mode': 'standalone'}},
              'compose': rabbit_compose,
              'pkg_deps': rabbit_pkgs,
              'cfg_step': rabbit_etccfg,
              'goal': task_rabbit_steps}
    facility.register_component(rabbit)


register()
