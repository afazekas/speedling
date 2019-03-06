from speedling import facility
from speedling import util
from speedling import conf
from speedling import localsh
import os
import re
import time

import logging
import urllib


LOG = logging.getLogger(__name__)
SECOND_THING = re.compile('\w+\s+(\w+)')


# TODO: wait for pkgs instead etc, do not forget systemdconfig
# also wait for hostname
def task_mariadb_steps(self):
    h = self.hosts_with_service('mariadb')
    host_ips = []
    for n in h:
        i = self.get_node(n)['inv']
        addr = self.get_addr_for(i, 'listen')
        host_ips.append(addr)

    self.call_do(h, self.do_mariadb_cfg, c_kwargs={'nodes': host_ips})
    state_dir = self.get_mariadb_state_dir()
    self.ensure_path_exists(state_dir,
                            owner=os.getuid(),
                            group=os.getgid())

    seed = util.rand_pick(h)
    self.call_do(seed, self.do_mariadb_galera_seed)
    self.mark_mariadb_boostrap()
    self.call_do(h, self.do_mariadb)
    while True:
        r = self.call_do(h, self.do_mariadb_query_state)
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
    self.call_do(seed, self.do_create_clustr_user)
    self.call_do(seed, self.do_handle_schemas, c_kwargs={'schemas': self.schema_registry})


class MariaDB(facility.SQLDB):
    services = {'mariadb': {'deploy_mode': 'standalone'}}

    def get_balancer(self):
        return self.dependencies.get("loadbalancer", None)

    def __init__(self, **kwargs):
        super(MariaDB, self).__init__(**kwargs)
        self.final_task = self.bound_to_instance(task_mariadb_steps)
        self.peer_info = {}
        self.schema_registry = []  # to dict later schmea -> user or user-> schema

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
    # TODO: create inifile like handler, it is a little different than std ini
    def etc_my_cnf_d_mariadb_openstack_cnf(self, seed=False):
        # TODO: create a monitoring script for lock waits
        peers = self.get_peer_info('cluster')
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
bind-address = ::
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

    def etc_systemd_system_mariadb_service_d_limits_conf(self): return {
            'Service': {'LimitNOFILE': 16384}
        }

    def etc_systemd_system_mysqlchk_socket(self): return {
            'Socket': {'ListenStream': 9200,
                       'Accept': 'yes'},
            'Unit': {'Description': 'Galera monitoring socket for proxies'},
            'Install': {'WantedBy': 'sockets.target'}
        }

    def etc_systemd_system_mysqlchk_service(self): return {
            'Service': {'ExecStart': '-/usr/bin/clustercheck',
                        'StandardInput': 'socket'},
            'Unit': {'Description': 'Galera monitoring service for proxies'}
        }

    def get_etcconf_d(self):
        if util.get_distro()['family'] != 'debian':
            return '/etc/my.cnf.d'
        else:
            return '/etc/mysql/mariadb.conf.d'

    def etc_sysconfig_clustercheck(self):
        password = util.get_keymgr()(self.name, 'clustercheckuser')
        return """MYSQL_USERNAME="clustercheckuser"
MYSQL_PASSWORD={pwd}
MYSQL_HOST=localhost
MYSQL_PORT="3306"
ERR_FILE="/tmp/clustercheckuser_42328756"
AVAILABLE_WHEN_DONOR=0
AVAILABLE_WHEN_READONLY=0
DEFAULTS_EXTRA_FILE=/etc/my.cnf""".format(pwd=util.cmd_quote(password))

    def etccfg_content(self):
        super(MariaDB, self).etccfg_content()
        self.ensure_path_exists('/etc/systemd/system/mariadb.service.d')
        self.ini_file_sync('/etc/systemd/system/mariadb.service.d/limits.conf',
                           self.etc_systemd_system_mariadb_service_d_limits_conf())
        if util.get_distro()['family'] != 'debian':
            self.ini_file_sync('/etc/systemd/system/mysqlchk@.service',
                               self.etc_systemd_system_mysqlchk_service())
            self.ini_file_sync('/etc/systemd/system/mysqlchk.socket',
                               self.etc_systemd_system_mysqlchk_socket())
            self.content_file('/etc/sysconfig/clustercheck',
                              self.etc_sysconfig_clustercheck(),
                              mode=0o640)

    def get_node_packages(self):
        pkgs = super(MariaDB, self).get_node_packages()
        pkgs.update({'srv-sql\\mariadb-galera'})
        return pkgs

    def do_mariadb(cname):
        if util.get_distro()['family'] != 'debian':
            localsh.run("systemctl enable mysqlchk.socket && systemctl start mysqlchk.socket")
        localsh.run("systemctl start mariadb")
        localsh.run("mysql <<<\"SHOW GLOBAL STATUS LIKE 'wsrep_%';\" >/tmp/wsrep_init_state" + cname)

    def do_mariadb_cfg(cname, nodes, seed=False):
        self = facility.get_component(cname)
        self.have_content()
        self.content_file(self.get_etcconf_d() + '/80-mariadb_openstack.cnf',
                          self.etc_my_cnf_d_mariadb_openstack_cnf(seed), mode=0o644)

    def do_create_clustr_user(cname):
        self = facility.get_component(cname)
        passwd = util.get_keymgr()(self.name, 'clustercheckuser')
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

    def do_mariadb_galera_seed(self):
        localsh.run("systemctl stop mariadb; galera_new_cluster")

    def get_mariadb_state_dir(self):
        args = conf.get_args()
        state_dir = args.state_dir
        return state_dir + '/mariadb'

    def is_mariadb_bootstrapped(self):
        state_dir = self.get_mariadb_state_dir()
        return os.path.isfile(state_dir + '/' + 'bootstrapped')

    def mark_mariadb_boostrap(self):
        state_dir = self.get_mariadb_state_dir()
        b_file = state_dir + '/' + 'bootstrapped'
        self.content_file(b_file, '', owner=os.getuid(), group=os.getgid())

    def do_mariadb_query_state(cname):
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

    def get_peer_info(self, mode):
        n = self.get_this_node()
        return n['peers']['mariadb'][mode]

    def get_cluster_info(self):
        port = 3306
        nodes = self.hosts_with_service('mariadb')
        cluster = []
        for n in nodes:
            node = self.get_node(n)
            hostname = node['inv']['hostname']
            addr = self.get_addr_for(node['inv'], 'database')
            cluster.append({'hostname': hostname, 'addr': addr,
                            'port': port})
        return cluster

    def populate_peer(self, nodes, modes):
        port = 3306
        if not self.peer_info:
            self.peer_info['cluster'] = self.get_cluster_info()

            balancer = self.get_balancer()
            if balancer:
                port = 13306

            # use different port with vip
            hostname = addr = conf.get_vip('internal')['domain_name']
            self.peer_info['client'] = {'hostname': hostname, 'addr': addr,
                                        'port': port}

        for n in nodes:
            node = self.get_node(n)
            peer_rec = node['peers'].setdefault('mariadb', {})
            if 'client' in modes:
                peer_rec['client'] = self.peer_info['client']
            if 'cluster' in modes:
                peer_rec['cluster'] = self.peer_info['cluster']

    def handle_schema(self, schema, user, passwd, pre_sync_script_dir=None):
        # BUG? two grant some cases makes mariadb not authentice non 'localhost'
        # users until restart , flush privileges does not helps
        # GRANT ALL PRIVILEGES ON {schema}.* TO '{user}'@'localhost' \
        # IDENTIFIED BY '{passwd}';
        sql = r"""CREATE SCHEMA IF NOT EXISTS {schema};
        GRANT ALL PRIVILEGES ON {schema}.* TO '{user}'@'%' \
        IDENTIFIED BY '{passwd}';
        SELECT IF(count(*) = 0, CONCAT('FREE','_FOR','_ALL'), 'FULL')
        FROM INFORMATION_SCHEMA.TABLES WHERE table_schema='{schema}';""".format(
            schema=schema, user=user,
            # $ for shell, the others for mysql
            passwd=passwd.replace('\\', '\\\\').replace("'", r"\'").replace('$', '\$')
        )
        retry = 1024  # wating for mariadb become ready
        while True:
            try:
                if pre_sync_script_dir:  # NOT TESTED
                    script = ("if mysql -u root <<EOF\n | grep FREE_FOR_ALL &&"
                              " [ -f {dir}/{schema}.sql] then\n{sql}\nEOF\n"
                              "mysql -u root <{dir}/{schema}.sql; fi".format(
                                dir=pre_sync_script_dir, schema=schema))
                else:
                    script = 'mysql -u root <<EOF\n{sql}\nEOF\n'.format(
                        sql=sql)
                break
            except:
                if retry:
                    time.sleep(0.2)
                    retry -= 1
                else:
                    raise
        # the merged version was too confusing to debug
        localsh.run(script)

    # split to multi node ? invidual callbacks ?
    # aggregatd operations ..
    # NOTE: this op used to be parallel
    def do_handle_schemas(cname, schemas):
        self = facility.get_component(cname)
        for s in schemas:
            self.handle_schema(*s)

    def register_user_with_schemas(self, user, schema_names):
        pwd = util.get_keymgr()(self.name, user)
        for sn in schema_names:
            self.schema_registry.append((sn, user, pwd))

    def db_url(self, db, user=None):
        pi = self.get_peer_info('client')
        host = pi['addr']
        port = pi['port']
        if user is None:
            user = db
            # utf8 is the default nowadays
            # TODO: source_ip
        pwd = urllib.parse.quote_plus(util.get_keymgr()(self.name, user))
        return 'mysql+pymysql://%s:%s@%s:%s/%s' % (user, pwd,
                                                   host, port, db)

    def compose(self):
        super(MariaDB, self).compose()
        h = self.hosts_with_service('mariadb')
        self.populate_peer(h, ['cluster'])
        ci = self.get_cluster_info()
        servers = []
        if len(h) > 1:
            check = ' check inter 3s on-marked-down shutdown-sessions port 9200'
        else:
            check = ''
        for i in ci:
            servers.append(' '.join((i['hostname'], i['addr'] + ':' + str(i['port']),
                           'backup' + check)))
        balancer = self.get_balancer()
        if balancer:
            if util.get_distro()['family'] == 'debian':
                # the galera packages does not have cluster checker
                # TODO: support mor mysql variants
                option = ['tcpka']
            else:
                option = ['tcpka', 'httpchk']
            balancer.add_listener('mariadb', {
                                 'bind': '*:13306',
                                 'stick': 'on dst',
                                 'stick-table': 'type ip size 1024',
                                 'option': option,
                                 'timeout': {'client': '128m',
                                             'server': '128m'},
                                 'server': servers})

        # clustercheckuser allowed from localhost only
        util.bless_with_principal(h, [(self.name, 'clustercheckuser')])
