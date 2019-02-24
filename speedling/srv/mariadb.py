from speedling import facility
from speedling import inv
from speedling import util
from speedling import conf
from osinsutils import localsh
from speedling.srv import haproxy
import os
import re
import time
import speedling

from osinsutils import cfgfile

import logging
import urllib


LOG = logging.getLogger(__name__)


# innodb_flush_log_at_trx_commit 1
# less fsync/fdatasync call
# with replication it is only an issue of all node
# losses power within seconds

# innodb_flush_method O_DIRECT
# depending on your system, but it might be terrible option.
# Typically the data written with O_DIRECT
# are directly written to the disk (bypassing os page cache)
# if you have good disk or controller it can ensure power loss
# tolerence for data which is just in his cache

def etc_my_cnf_d_mariadb_openstack_cnf(seed=False):
    # TODO: create a monitoring script for lock waits
    peers = get_peer_info('cluster')
    big = False
    mysql_conf = """[mysqld]
default-storage-engine = innodb
innodb_file_per_table
collation-server = utf8_general_ci
init-connect = 'SET NAMES utf8'
character-set-server = utf8
skip-name-resolve
max_connections = 15360
innodb_flush_log_at_trx_commit = 0
innodb_flush_method = O_DIRECT
slow-query-log = 1
slow-query-log-file = /var/log/mariadb/slow_query.log
long_query_time = 0.1
"""

# innodb_log_file_size changes requires manual file deletion,
# inorder to enusre you know what you are doing
# on new system it can be configure easly before the first start

# TODO: increase the wsresp/galera related sizes
# for tolerate longer memeber outage!

# TODO: configure `point in time recovery` able setup

# NOTE: thread pools can be good idea, if you have lot of idle connection

    if big:
        mysql_conf += """innodb_log_file_size = 1500M
innodb_log_files_in_group = 2
innodb_buffer_pool_size = 16G
"""
    if (len(peers) > 1):
        mysql_conf += """
innodb_autoinc_lock_mode = 2
binlog_format=ROW
wsrep_provider=/usr/lib64/galera/libgalera_smm.so
wsrep_provider_options="gcache.size=300M; gcache.page_size=300M"
"""
    if not seed:
        mysql_conf += """
        wsrep_cluster_address="gcomm://{nodes}"
""".format(nodes=','.join(n['addr'] for n in peers))

    return mysql_conf


def etc_systemd_system_mariadb_service_d_limits_conf(): return {
        'Service': {'LimitNOFILE': 16384}
    }


# TODO: reconsider xinitd version or creating an another way
def etc_systemd_system_mysqlchk_socket(): return {
        'Socket': {'ListenStream': 9200,
                   'Accept': 'yes'},
        'Unit': {'Description': 'Galera monitoring socket for proxies'},
        'Install': {'WantedBy': 'sockets.target'}
    }


def etc_systemd_system_mysqlchk_service(): return {
        'Service': {'ExecStart': '-/usr/bin/clustercheck',
                    'StandardInput': 'socket'},
        'Unit': {'Description': 'Galera monitoring service for proxies'}
    }


def etc_sysconfig_clustercheck():
    password = util.get_keymgr()('mysql', 'clustercheckuser')
    return """MYSQL_USERNAME="clustercheckuser"
MYSQL_PASSWORD={pwd}
MYSQL_HOST=localhost
MYSQL_PORT="3306"
ERR_FILE="/tmp/clustercheckuser_42328756"
AVAILABLE_WHEN_DONOR=0
AVAILABLE_WHEN_READONLY=0
DEFAULTS_EXTRA_FILE=/etc/my.cnf""".format(pwd=util.cmd_quote(password))


def mariadb_etccfg(services):
    cfgfile.ensure_path_exists('/etc/systemd/system/mariadb.service.d')
    cfgfile.ini_file_sync('/etc/systemd/system/mariadb.service.d/limits.conf',
                          etc_systemd_system_mariadb_service_d_limits_conf())
    cfgfile.ini_file_sync('/etc/systemd/system/mysqlchk@.service', etc_systemd_system_mysqlchk_service())
    cfgfile.ini_file_sync('/etc/systemd/system/mysqlchk.socket', etc_systemd_system_mysqlchk_socket())
    cfgfile.content_file('/etc/sysconfig/clustercheck', etc_sysconfig_clustercheck(),
                         mode=0o640)


def mariadb_pkgs():
    return {'mariadb-server-galera'}


def do_mariadb():
    localsh.run("systemctl enable mysqlchk.socket && systemctl start mysqlchk.socket")
    localsh.run("systemctl start mariadb")
    localsh.run("mysql <<<\"SHOW GLOBAL STATUS LIKE 'wsrep_%';\" >/tmp/wsrep_init_state ")


def do_mariadb_cfg(nodes, seed=False):
    cfgfile.content_file('/etc/my.cnf.d/mariadb_openstack.cnf',
                         etc_my_cnf_d_mariadb_openstack_cnf(seed), mode=0o644)


def do_create_clustr_user():
    passwd = util.get_keymgr()('mysql', 'clustercheckuser')
    pwd = passwd.replace('\\', '\\\\').replace("'", r"\'").replace('$', '\$')
    sql = "GRANT PROCESS ON *.* TO 'clustercheckuser'@'localhost' IDENTIFIED BY '{pwd}'".format(pwd=pwd)
    # $ for shell, the others for mysql
    retry = 1024  # wating for mariadb become ready
    while True:
        try:
            script = 'mysql -u root <<EOF\n{sql}\nEOF\n'.format(sql=sql)
            localsh.run(script)
            break
        except:
            if retry:
                time.sleep(0.2)
                retry -= 1
            else:
                raise


def do_mariadb_galera_seed():
    localsh.run("galera_new_cluster")


def get_mariadb_state_dir():
    args = conf.get_args()
    state_dir = args.state_dir
    return state_dir + '/mariadb'


def is_mariadb_bootstrapped():
    state_dir = get_mariadb_state_dir()
    return os.path.isfile(state_dir + '/' + 'bootstrapped')


def mark_mariadb_boostrap():
    state_dir = get_mariadb_state_dir()
    b_file = state_dir + '/' + 'bootstrapped'
    cfgfile.content_file(b_file, '', owner=os.getuid(), group=os.getgid())


SECOND_THING = re.compile('\w+\s+(\w+)')


def do_mariadb_query_state():
    wsrep_cluster_size = None
    wsrep_cluster_conf_id = None
    r = localsh.ret("mysql <<<\"select * from INFORMATION_SCHEMA.GLOBAL_STATUS where VARIABLE_NAME = 'wsrep_cluster_size' or VARIABLE_NAME = 'wsrep_cluster_conf_id';\"")
    r.split('\n')
    for l in r:
        if l.startswith('WSREP_CLUSTER_SIZE'):
            wsrep_cluster_size = SECOND_THING.match(l)[0]
        if l.startswith('WSREP_CLUSTER_CONF_ID'):
            wsrep_cluster_conf_id = SECOND_THING.match(l)[0]
    return {'wsrep_cluster_size': wsrep_cluster_size, 'wsrep_cluster_conf_id': wsrep_cluster_conf_id}


def get_peer_info(mode):
    n = inv.get_this_node()
    return n['peers']['mariadb'][mode]


PEER_INFO = {}


def get_cluster_info():
    port = 3306
    nodes = inv.hosts_with_service('mariadb')
    cluster = []
    for n in nodes:
        node = inv.get_node(n)
        hostname = node['inv']['hostname']
        addr = inv.get_addr_for(node['inv'], 'database')
        cluster.append({'hostname': hostname, 'addr': addr,
                        'port': port})
    return cluster


def populate_peer(nodes, modes):
    port = 3306
    if not PEER_INFO:
        PEER_INFO['cluster'] = get_cluster_info()

        gconf = conf.get_global_config()
        if 'haproxy' in gconf['global_service_flags']:
            port = 13306

        # use different port with vip
        hostname = addr = conf.get_vip('internal')['domain_name']
        PEER_INFO['client'] = {'hostname': hostname, 'addr': addr,
                               'port': port}

    for n in nodes:
        node = inv.get_node(n)
        peer_rec = node['peers'].setdefault('mariadb', {})
        if 'client' in modes:
            peer_rec['client'] = PEER_INFO['client']
        if 'cluster' in modes:
            peer_rec['cluster'] = PEER_INFO['cluster']


def db_url(db, user=None):
    pi = get_peer_info('client')
    host = pi['addr']
    port = pi['port']
    if user is None:
        user = db
        # utf8 is the default nowadays
        # TODO: source_ip
    pwd = urllib.parse.quote_plus(util.get_keymgr()('mysql', user))
    return 'mysql+pymysql://%s:%s@%s:%s/%s' % (user, pwd,
                                               host, port, db)


# TODO: wait for pkgs instead etc, do not forget systemdconfig
# also wait for hostname
def task_mariadb_steps():
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)
    h = inv.hosts_with_service('mariadb')
    host_ips = []
    for n in h:
        i = inv.get_node(n)['inv']
        addr = inv.get_addr_for(i, 'listen')
        host_ips.append(addr)

    inv.do_do(h, do_mariadb_cfg, c_kwargs={'nodes': host_ips})
    state_dir = get_mariadb_state_dir()
    cfgfile.ensure_path_exists(state_dir,
                               owner=os.getuid(),
                               group=os.getgid())

    seed = inv.rand_pick(h)
    inv.do_do(seed, do_mariadb_galera_seed)
    mark_mariadb_boostrap()
    inv.do_do(h, do_mariadb)
    while True:
        r = inv.do_do(h, do_mariadb_query_state)
        wsrep_cluster_conf_id = set()
        for n, res in r.items():
            wsrep_cluster_conf_id.add(res['return_value']['wsrep_cluster_conf_id'])
            if res['return_value']['wsrep_cluster_size'] != len(h):
                LOG.info('Wating for mariadb cluster size to change')
                time.sleep(0.1)
                continue
        if len(wsrep_cluster_conf_id) != 1:
            LOG.info('Wating for mariadb cluster conf sync')
            time.sleep(0.1)
        else:
            break
    inv.do_do(seed, do_create_clustr_user)


def mariadb_compose():
    h = inv.hosts_with_service('mariadb')
    gconf = conf.get_global_config()
    populate_peer(h, ['cluster'])
    ci = get_cluster_info()
    servers = []
    if len(h) > 1:
        check = ' check inter 3s on-marked-down shutdown-sessions port 9200'
    else:
        check = ''
    for i in ci:
        servers.append(' '.join((i['hostname'], i['addr'] + ':' + str(i['port']),
                       'backup' + check)))

    if 'haproxy' in gconf['global_service_flags']:
        haproxy.add_listener('mariadb', {
                             'bind': '*:13306',
                             'stick': 'on dst',
                             'stick-table': 'type ip size 1024',
                             'option': ['tcpka', 'httpchk'],
                             'timeout': {'client': '128m',
                                         'server': '128m'},
                             'server': servers})

    # clustercheckuser allowed from localhost only
    util.bless_with_principal(h, [('mysql', 'clustercheckuser')])


def register():
    mariadb = {'component': 'mariadb',
               'deploy_source': 'pkg',
               'services': {'mariadb': {'deploy_mode': 'standalone'}},
               'variant': 'galera',
               'compose': mariadb_compose,
               'pkg_deps': mariadb_pkgs,
               'cfg_step': mariadb_etccfg,
               'goal': task_mariadb_steps}
    cc = facility.get_component_config_for('mariadb')
    util.dict_merge(mariadb, cc)
    facility.register_component(mariadb)


register()
