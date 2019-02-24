from speedling import inv
from speedling import facility
from speedling import util
from speedling import piputils
from speedling import gitutils
from speedling import conf
from osinsutils import localsh
from osinsutils import cfgfile

import logging
import time


LOG = logging.getLogger(__name__)


def do_hostname(hosts):
    this_inv = inv.get_this_inv()
    hostname = this_inv['hostname']

    localsh.run("hostnamectl set-hostname '%s'" % (hostname))
    cfgfile.content_file('/etc/hosts', hosts, mode=0o644)


def task_hostname():
    hosts_l = []
    hosts_l.append("127.0.0.1  localhost.localdomain localhost")
    hosts_l.append("::1  localhost6.localdomain6 localhost6")
    for n, node in inv.INVENTORY.items():
        addr = inv.get_addr_for(node, 'ssh')
        name = node['hostname']
        hosts_l.append(addr + " " + name)
    hosts_l.append('')
    hosts_str = '\n'.join(hosts_l)
    inv.do_do(inv.ALL_NODES, do_hostname, c_kwargs={'hosts': hosts_str})


# NOTE: pass only name to less wire traffic
def do_process_repo(component_name):
    comp = facility.get_component(component_name)
    gitutils.procoss_component_repo(comp)


def do_process_pip(component_name):
    comp = facility.get_component(component_name)
    piputils.setup_develop(comp)


# swift is the only pip2 user
def do_process_pip2(component_name):
    comp = facility.get_component(component_name)
    piputils.setup_develop2(comp)


# TODO: etccfg steps hasto depend on gits,
# regular goal has to depend on pip
# make it a compose friendly
def compose_prepare_source(name, pip2=False):

    gconf = conf.get_global_config()
    need_repos = gconf.get('use_git', True)
    need_setup = gconf.get('use_pip', True)
    need_pkgs = gconf.get('use_pkg', True)

    def component_git_repo():
        h = inv.hosts_with_component(name)
        inv.do_do(h, do_process_repo, c_args=(name,))

    def component_pip():
        needs = []
        if need_repos:
            needs.append(component_git_repo)
        if need_pkgs:
            needs.append(task_pkg_install)
        facility.task_wants(*needs)
        h = inv.hosts_with_component(name)
        if pip2:
            inv.do_do(h, do_process_pip2, c_args=(name,))
        else:
            inv.do_do(h, do_process_pip, c_args=(name,))

    component_git_repo.__name__ = name + '_git_repo'
    task_git = component_git_repo
    component_pip.__name__ = name + '_pip'
    task_pip = component_pip

    comp = facility.get_component(name)
    if need_setup:
        facility.task_add_wants(comp['goal'], task_pip)
    if need_repos:
        facility.task_add_wants(comp['goal'], task_git)
        facility.task_add_wants(task_cfg_etccfg_steps, task_git)
    return (task_git, task_pip)


def compose_prepare_source_cond(name, pip2=False):
    comp = facility.get_component(name)
    if 'deploy_source' in comp and comp['deploy_source'] == 'src':
        return compose_prepare_source(name, pip2)
    return (None, None)


# TODO: create variant for service dicts, witch component lookup
def local_os_service_start_by_component(*args):
    to_start = []
    for component in args:
        comp = facility.get_component(component)
        selected_services = inv.get_this_inv()['services']
        managed_services = comp.get('services', None)
        if not managed_services or not selected_services:
            return
        ds = comp['deploy_source']
        relevant_services = selected_services.intersection(set(managed_services.keys()))
        for s in relevant_services:
            service = managed_services[s]
            if service['deploy_mode'] == 'standalone':
                to_start.append(comp['services'][s]['unit_name'][ds])
    localsh.run('systemctl start %s' % (' '.join(to_start)))


# TODO: select db part node, which may got the default scheme
#       use the same node for all DB admin step (they know how to do admin login)
#       the schema steps will be scheduled to `random` api nodes
def do_handle_schema(schema, user, passwd, pre_sync_script_dir=None):

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


def do_synccmd(sync_cmd):
    retry = 30
    try:
        localsh.run(sync_cmd)
    except:
        if retry == 0:
            raise
        time.sleep(0.2)
        retry -= 1
        LOG.debug(('Sync did not succuded after multiple attempt with: %s' % sync_cmd))


def subtask_db_sync(speaker, schema, sync_cmd=None,
                    schema_user=None, schema_passwd=None,
                    pre_sync_script_dir=None):
    if not schema_user:
        schema_user = schema
    if not schema_passwd:
        schema_passwd = util.get_keymgr()('mysql', schema_user)
    # expected to give back the same node in single run
    db_speaker = set((next(iter(inv.hosts_with_service('mariadb'))),))
    inv.do_do(db_speaker, do_handle_schema, c_kwargs={'schema': schema,
                                                      'user': schema_user,
                                                      'passwd': schema_passwd,
                                                      'pre_sync_script_dir': pre_sync_script_dir})
    inv.do_do(inv.rand_pick(speaker), do_synccmd, c_kwargs={'sync_cmd': sync_cmd})


def default_packages(distro, distro_version):
    # NOTE: fedora rsync-daemon not just rsync

    # I am expecting fully poppulated images
    # it just makes sure it is ok
    # TODO:add other ditros
    # TODO: split to per component
    # TODO: add option for pkg/pip
    # TODO: add option for containers
    return set(['python3-devel',
                'python2-devel', 'graphviz', 'novnc', 'openldap-devel', 'python3-mod_wsgi',
                'httpd', 'libffi-devel', 'libxslt-devel', 'mariadb-server', 'mariadb-devel', 'galera',
                'httpd-devel', 'rabbitmq-server', 'openssl-devel',
                'python3-numpy', 'python3-ldap', 'python3-dateutil', 'python3-psutil', 'pyxattr', 'xfsprogs', 'liberasurecode-devel',
                'python3-libguestfs', 'cryptsetup', 'libvirt-client',
                'memcached',
                'iptables', 'haproxy', 'ipset', 'radvd', 'openvswitch', 'conntrack-tools',
                'pcp-system-tools',
                'python3-libguestfs',
                'gcc-c++', 'pcs', 'pacemaker',
                'rsync-daemon', 'python2-keystonemiddleware', 'python3-PyMySQL',
                'ceph-mds', 'ceph-mgr', 'ceph-mon', 'ceph-osd', 'ceph-radosgw', 'redis python3-redis', 'python3-memcached',
                'python3-libvirt', 'python3-keystoneauth1', 'python3-keystoneclient', 'python3-rbd',
                'python2-subunit', 'python2-jsonschema', 'python2-paramiko'])
    # rsyslog, os-net-config, jq ..


# NOTE: use_pkg=False can skip this step
def do_pkg_fetch():
    pkgs = default_packages('fedora', '29')
    inv_entry = inv.get_this_inv()
    comp = set(inv_entry.get('components', tuple()))
    func_set = set()
    for c in comp:
        component = facility.get_component(c)
        f = component.get('pkg_deps', None)
        if f:
            func_set.add(f)

    selected_services = inv_entry['services']
    for srv in selected_services:
        try:
            s = facility.get_service_by_name(srv)
            f = s['component'].get('pkg_deps', None)
            if f:
                func_set.add(f)
        except Exception:
            # TODO: remove excption, let it fail, preferably earlier
            LOG.warn('Service "{srv}" is not a registered service'.format(srv=srv))
    u = set.union(*[f() for f in func_set])
    LOG.info("Installing packages ..")
    localsh.run("yum update -y || yum update -y || yum update -y || yum update -y ")
    localsh.run(' || '.join(["yum install -y {pkgs}".format(pkgs=' '.join(pkgs.union(u)))]*4))


def task_establish_repos():
    # rdo_repos()
    pass


def task_pkg_install():
    # facility.task_wants(task_establish_repos)
    gconf = conf.get_global_config()
    need_pkgs = gconf.get('use_pkg', True)
    if need_pkgs:
        inv.do_do(inv.ALL_NODES, do_pkg_fetch)


# seams cheaper to have one task for all etc like cfg steps,
# than managing many small functions, even tough it could be paralell op with multi functions
# 'service_union' union of all services from all hosts,
# in order to know for example do we have lbaas anywhere
# globale feature flag for example: 'neutron-fwaas'

def do_local_etccfg_steps():
    host_record = inv.get_this_inv()
    services = host_record.get('services', set())

    cfgfile.ensure_path_exists('/srv', mode=0o755)

    steps = facility.get_cfg_steps(services)
    for step in steps:
        # TODO: do not pass args they can get it..
        step(services=services)

    localsh.run('systemctl daemon-reload')


def task_cfg_etccfg_steps():
    facility.task_wants(task_pkg_install)
    assert inv.ALL_NODES
    inv.do_do(inv.ALL_NODES, do_local_etccfg_steps)


def do_dummy_netconfig():
    localsh.run('systemctl start openvswitch.service')

    # TODO switch to os-net-config
    # wait (no --no-wait)
    localsh.run('ovs-vsctl --may-exist add-br br-ex')

    # add ip to external bridge instead of adding a phyisical if
    localsh.run("""
    ifconfig br-ex 192.0.2.1
    ip link set br-ex up
    ROUTE_TO_INTERNET=$(ip route get 8.8.8.8)
    OBOUND_DEV=$(echo ${ROUTE_TO_INTERNET#*dev} | awk '{print $1}')
    iptables -t nat -A POSTROUTING -o $OBOUND_DEV -j MASQUERADE
    tee /proc/sys/net/ipv4/ip_forward <<<1 >/dev/null
    """)


def task_net_config():
    # This is temporary here, normally it should do interface persistent config
    facility.task_wants(task_pkg_install)
    inv.do_do(inv.hosts_with_service('neutron-l3-agent'),
              do_dummy_netconfig)
