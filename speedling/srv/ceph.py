import os

from speedling import facility
from speedling import inv
from speedling import util
from speedling import conf
from osinsutils import localsh
from osinsutils import cfgfile
import speedling.srv.glance
import speedling.tasks


import logging

LOG = logging.getLogger(__name__)


# this is on the local node
def get_ceph_state_dir():
    args = conf.get_args()
    state_dir = args.state_dir
    return state_dir + '/ceph'


def create_ceph_state_dirs():
    state_dir = get_ceph_state_dir()
    cfgfile.ensure_path_exists(get_ceph_state_dir(),
                               owner=os.getuid(),
                               group=os.getgid())

    cfgfile.ensure_path_exists(state_dir + '/mon_bootstrap',
                               owner=os.getuid(),
                               group=os.getgid())


def is_mon_bootstrapped(mon_host):
    mon_state_dir = get_ceph_state_dir() + '/mon_bootstrap'
    return os.path.isfile(mon_state_dir + '/' + mon_host)


def touch(path):
    open(path, 'w').close()


def set_mon_bootsrapped(mon_host, done=True):
    mon_state_dir = get_ceph_state_dir() + '/mon_bootstrap'
    touch(mon_state_dir + '/' + mon_host)


def generate_fsid():
    # TODO randomize and/or add to the component
    return 'a7f64266-0894-4f1e-a635-d0aeaca0e993'  # from the doc


FSID = None


def get_fsid():
    global FSID
    if FSID:
        return FSID
    state_dir = get_ceph_state_dir()
    fsid_state = state_dir + '/fsid'
    if os.path.isfile(fsid_state):
        f = open(fsid_state, 'r')
        fsid = f.read().strip()
        f.close()
    else:
        fsid = generate_fsid()
        cfgfile.content_file(fsid_state, fsid,
                             owner=os.getuid(), group=os.getgid())
    FSID = fsid
    return fsid


def etc_ceph_ceph_conf(fsid, mons):
    # TODO remove the osd section, it is a dirtty _hack_ for
    # demo installing also on ext4

    # TODO add 'public network' and 'cluster network'

    ceph_config = {
        'global': {
            'fsid': fsid,
            'mon_initial_members': ','.join(m[0] for m in mons),
            'mon_host': ','.join(m[1] for m in mons),
            'auth_cluster_required': 'cephx',
            'auth_service_required': 'cephx',
            'auth_client_required': 'cephx',
            'filestore_xattr_use_omap': True,
        },
        'osd': {
            'osd_max_object_namespace_len': 64,  # just on ext4, use the normal one on xfs!
            'osd_max_object_name_len': 256,
            'journal': 128  # too small, non production !
        }
    }
    return ceph_config


# remote on only on of the the ceph mon only once
def initial_keyring():
    # admin key
    localsh.run("ceph-authtool --create-keyring /etc/ceph/ceph.client.admin.keyring --gen-key -n client.admin --set-uid=0 --cap mon 'allow *' --cap osd 'allow *' --cap mds 'allow'")
    admin_keyring = localsh.ret("cat /etc/ceph/ceph.client.admin.keyring")
    # NOTE: rgw mds also have bootstrap key
    # bootsrap keys usually used by only the tools, probably they not need to persist on non mon nodes
    localsh.run("""ceph-authtool --create-keyring /tmp/ceph.mon.keyring --gen-key -n mon. --cap mon 'allow *'
                   ceph-authtool --create-keyring /var/lib/ceph/bootstrap-osd/ceph.keyring --gen-key -n client.bootstrap-osd --cap mon 'profile bootstrap-osd'
                   ceph-authtool /tmp/ceph.mon.keyring --import-keyring /var/lib/ceph/bootstrap-osd/ceph.keyring
                   ceph-authtool /tmp/ceph.mon.keyring --import-keyring /etc/ceph/ceph.client.admin.keyring""")
    mon_bootstrap = localsh.ret("cat /tmp/ceph.mon.keyring")
    return (admin_keyring, mon_bootstrap)


# remote on mons before the first run
# the file has date we will see is mons mind if generate new on all node
def gen_monmap(mons, fsid):
    cluster_name = 'ceph'
    localsh.run("""monmaptool --create --fsid '{fsid}' /tmp/monmap && \
                   chown ceph /tmp/ceph.mon.keyring""".format(fsid=fsid))
    for name, ip in mons:
        localsh.run("""
            monmaptool --add {hostname} {ip} /tmp/monmap""".format(hostname=name,
                                                                   ip=ip))
        cfgfile.ensure_path_exists(
            '/var/lib/ceph/mon/{cluster_name}-{hostname}'.format(
                cluster_name=cluster_name, hostname=name),
            owner='ceph', group='ceph')


def do_boostrap_mon(mons, fsid, mon_bootstrap_keyring, admin_keyring):
    cluster_name = 'ceph'
    # TODO: create/use file put
    cfgfile.content_file('/tmp/ceph.mon.keyring', mon_bootstrap_keyring,
                         owner='ceph')
    cfgfile.content_file('/etc/ceph/ceph.client.admin.keyring', admin_keyring)
    this_hostname = inv.get_this_inv()['hostname']

    gen_monmap(mons, fsid)
    localsh.run(("sudo -u ceph ceph-mon --mkfs -i {name} "
                 "--monmap /tmp/monmap "
                 "--keyring /tmp/ceph.mon.keyring").format(name=this_hostname))

    cfgfile.content_file('/var/lib/ceph/mon/{cluster_name}-{hostname}.done'.format(
                          cluster_name=cluster_name, hostname=this_hostname), '', owner='ceph')
    localsh.run(("systemctl enable ceph-mon@{host} && "
                 "systemctl start ceph-mon@{host}").format(host=this_hostname))


def do_place_ceph_conf(fsid, mons):
    ceph_config = etc_ceph_ceph_conf(fsid=fsid, mons=mons)
    cfgfile.ensure_path_exists('/etc/ceph', mode=0o755)  # all relaveant cli/srv nodes
    ceph_local_conf = '/etc/ceph/ceph.conf'
    cfgfile.ini_file_sync(ceph_local_conf, ceph_config, mode=0o644)


def fetch_key(user, properties=dict()):
    props = ' '.join(("{cap} '{rule}'".format(cap=k, rule=v) for (k, v) in properties.items()))
    return localsh.ret("ceph auth get-or-create {name} {props}".format(name=user, props=props))


def do_setup_key_and_mgr_start(key):
    this_hostname = inv.get_this_inv()['hostname']
    key_dir_path = '/var/lib/ceph/mgr/ceph-' + this_hostname
    key_path = key_dir_path + '/keyring'
    cfgfile.ensure_path_exists(key_dir_path, owner='ceph', group='ceph')
    cfgfile.content_file(key_path, key, owner='ceph', group='ceph')
    localsh.run("""
name={hostname}
ln -s /usr/lib/systemd/system/ceph-mgr@.service /etc/systemd/system/ceph-mgr@$name.service
systemctl start ceph-mgr@$name
""".format(hostname=this_hostname))


def get_keyring(node, user, properties):
    assert len(node) == 1
    c_kwargs = {'user': user,
                'properties': properties}
    ret = inv.do_do(node, fetch_key, c_kwargs=c_kwargs)
    return ret[next(iter(node))]['return_value']


def task_setup_mgr():
    inv_mons = inv.hosts_with_service('ceph-mon')
    keyringer = inv.rand_pick(inv_mons)
    inv_mgrs = inv.hosts_with_service('ceph-mgr')
    msg_matrix = {}
    for m in inv_mgrs:
        i = inv.get_node(m)['inv']
        host = i['hostname']
        properties = {'mon': 'allow profile mgr',
                      'osd': 'allow *',
                      'mds': 'allow *'}
        key = get_keyring(keyringer, 'mgr.' + host, properties)
        msg_matrix[m] = {'kwargs': {'key': key}}

    # assume all node remote, which can be wrong
    inv.do_diff(msg_matrix, do_setup_key_and_mgr_start)


def do_cinder_nova_keyring(key):
    target = '/etc/ceph/ceph.client.cinder.keyring'
    # TODO: insecure, use posix acl or shared group, but it should not be world readable
    # consider more keys ..
    try:
        cfgfile.content_file(target, key, owner='cinder', group='cinder', mode=0o644)
    except:
        cfgfile.content_file(target, key, owner='nova', group='nova', mode=0o644)


def task_key_for_cinder_nova():
    component = facility.get_component('ceph')
    used_by = set(component['used_by']).intersection({'cinder-volume', 'nova-compute'})
    inv_mons = inv.hosts_with_service('ceph-mon')
    keyringer = inv.rand_pick(inv_mons)
    key = get_keyring(keyringer,
                      'client.cinder',
                      {'mon': 'allow r',
                       'osd': 'allow class-read object_prefix rbd_children, allow rwx pool=volumes, allow rwx pool=vms, allow rx pool=images'})
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)  # this steps ensure service users

    inv.do_do(inv.hosts_with_any_service(used_by),
              do_cinder_nova_keyring, c_kwargs={'key': key})

    if 'nova-compute' in component['used_by']:
        facility.task_wants(speedling.srv.nova.task_libvirt)
        inv.do_do(inv.hosts_with_service('nova-compute'),
                  do_register_ceph_libvirt)


def do_glance_keyring(key):
    target = '/etc/ceph/ceph.client.glance.keyring'
    cfgfile.content_file(target, key, owner='glance', group='glance', mode=0o640)


def task_key_glance():
    component = facility.get_component('ceph')
    glance_on_ceph = 'glance-api' in component['used_by']
    if not glance_on_ceph:
        return
    inv_mons = inv.hosts_with_service('ceph-mon')
    keyringer = inv.rand_pick(inv_mons)
    key = get_keyring(keyringer,
                      'client.glance',
                      {'mon': 'allow r',
                       'osd': 'allow class-read object_prefix rbd_children, allow rwx pool=images'})
    facility.task_wants(speedling.tasks.task_cfg_etccfg_steps)  # this steps ensure service users
    inv.do_do(inv.hosts_with_service('glance-api'),
              do_glance_keyring, c_kwargs={'key': key})


# Ceph nowadays likes hostnames in the mon.conf
# Non prod!
def do_ceph_deploy_all_in_one_demo(fsid):
    # we must not regenerate the fsid ever !
    # TODO single persist
    # TODO randomize
    # TODO reentrant / idempontetn ..
    cluster_name = 'ceph'
    # local not mounted, not formated osd on the root disk , just for demo !!!
    # OSD ID number is integer, should be continius allocation
    cfgfile.ensure_path_exists('/srv/ceph', owner='ceph', group='ceph', mode=0o755)
    cfgfile.ensure_path_exists('/srv/ceph/osd_0', owner='ceph', group='ceph')
    # NOTE: ceph-disk is deprecated
    localsh.run(("ceph-disk prepare --cluster '{cluster_name}' "
                 " --cluster-uuid '{fsid}'  /srv/ceph/osd_0 ").format(
                    cluster_name=cluster_name, fsid=fsid))
    localsh.run("""retry=0; while ! ceph-disk activate /srv/ceph/osd_0; do
                   ((retry++))
                   if [[ retry -ge 5 ]]; then
                      break
                   fi
                   sleep 0.1
                   done
                """)


# it is a possible combination to use swift as a backup service
# ceph auth get-or-create client.cinder-backup mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=backups' | tee /etc/ceph/ceph.client.cinder-backup.keyring
# chown cinder:cinder /etc/ceph/ceph.client.cinder-backup.keyring
#    gconf = conf.get_global_config()
#    if {'gnocchi', 'gnocchi-metricd'}.intersection(gconf['global_service_flags']):
#       localsh.run("""
# ceph auth get-or-create client.gnocchi mon "allow r" osd "allow class-read object_prefix rbd_children, allow rwx pool=gnocchi" | tee /etc/ceph/ceph.client.gnocchi.keyring
# chown gnocchi:gnocchi /etc/ceph/ceph.client.gnocchi.keyring""")


def do_register_ceph_libvirt():
    # after libvirt is up:
    localsh.run("""virsh --connect qemu:///system secret-define --file <(cat <<EOF
<secret ephemeral='no' private='no'>
  <uuid>457eb676-33da-42ec-9a8c-9293d545c337</uuid>
  <usage type='ceph'>
    <name>client.cinder secret</name>
  </usage>
</secret>
EOF
)""")

    # The secret can be defined as an user, but the value needs the be set as system otherwise it is not visible for nova
    # we might need 2 keys on n-cpu nodes, one for `volumes` and one for `vms`.
    localsh.run('virsh --connect qemu:///system secret-set-value --secret 457eb676-33da-42ec-9a8c-9293d545c337 --base64 $(ceph-authtool  /etc/ceph/ceph.client.cinder.keyring -p -n client.cinder)')


# this is also responsible for ceph.conf
def task_ceph_mon():
    component = facility.get_component('ceph')
    fsid = get_fsid()
    facility.task_wants(speedling.tasks.task_pkg_install, speedling.tasks.task_hostname)
    inv_mons = inv.hosts_with_service('ceph-mon')
    mon_host_ips = []
    for m in inv_mons:
        i = inv.get_node(m)['inv']
        host = i['hostname']
        addr = inv.get_addr_for(i, 'ceph_storage')
        mon_host_ips.append((host, addr))
    ceph_conf = etc_ceph_ceph_conf(fsid, mon_host_ips)
    state_dir = get_ceph_state_dir()
    ceph_conf_state = state_dir + '/ceph.conf'
    cfgfile.ini_file_sync(ceph_conf_state, ceph_conf,
                          owner=os.getuid(), group=os.getgid())

    inv_mons_boostrapped = {}
    # check is any mon bootstrapped already
    inv_mons_boostrapped = set([m for m in inv_mons if is_mon_bootstrapped(m)])
    ceph_conf_nodes = inv.hosts_with_any_service(set.union(set(component['services'].keys()), set(component['used_by'])))
    inv.do_do(ceph_conf_nodes, do_place_ceph_conf, c_kwargs={'fsid': fsid, 'mons': mon_host_ips})
    if not inv_mons_boostrapped:
        # all mon fresh
        LOG.info("Ceph mon fresh install")
        keyringer = inv.rand_pick(inv_mons)
        ret = inv.do_do(keyringer, initial_keyring)
        (admin_keyring, mon_bootstrap) = ret[next(iter(keyringer))]['return_value']
        cfgfile.content_file(state_dir + '/ceph.client.admin.keyring', admin_keyring,
                             owner=os.getuid(), group=os.getgid())
        inv.do_do(inv_mons, do_boostrap_mon, c_args=(mon_host_ips, fsid, mon_bootstrap, admin_keyring))
        for m in inv_mons:
            set_mon_bootsrapped(m)
    else:
        if (inv_mons_boostrapped == inv_mons):
            LOG.info("Ceph mons already bootstrapped")
        else:
            LOG.warn('Your montor topligy is moved, handler not implemented')


# pools can be created bfore osd, auth key can be created for non exisitng pool
def do_ensure_pools(pools):
    existing = localsh.ret("ceph osd lspools").strip()
    # input '1 volumes,2 images,3 vms,4 gnocchi,'
    pairs = existing.split(',')
    # TODO: duplicate ?
    pool_defined = set((pair.split(' ')[1] for pair in pairs if pair))
    missing_pools = pools - pool_defined
    for pool in missing_pools:
        localsh.run("ceph osd pool create {pool} 16 && ceph osd pool set {pool} size 1; ceph osd pool application enable {pool} rbd".format(pool=pool))


# TODO: add support for attribute_change
def task_handle_pools():
    inv_mons = inv.hosts_with_service('ceph-mon')
    pooler = inv.rand_pick(inv_mons)
    pools = set(('volumes', 'images', 'vms', 'gnocchi'))
    inv.do_do(pooler, do_ensure_pools, c_kwargs={'pools': pools})


def task_ceph_steps():
    create_ceph_state_dirs()  # move to mon or wait to mon
    component = facility.get_component('ceph')
    fsid = get_fsid()
    sub_tasks_to_wait = []
    facility.task_wants(task_ceph_mon)

    gconf = conf.get_global_config()
    all_services = gconf['global_service_flags']

    if 'ceph-mgr' in all_services:
        sub_tasks_to_wait.append(task_setup_mgr)
        facility.task_will_need(task_setup_mgr)

    inv.do_do(inv.hosts_with_service('ceph-osd'),
              do_ceph_deploy_all_in_one_demo, c_kwargs={'fsid': fsid})
    facility.task_will_need(task_handle_pools)  # mon is crahsing if it called before we have any osd
    sub_tasks_to_wait.append(task_handle_pools)
    if set(component['used_by']).intersection({'cinder-volume', 'nova-compute'}):
        sub_tasks_to_wait.append(task_key_for_cinder_nova)
        facility.task_will_need(task_key_for_cinder_nova)
    sub_tasks_to_wait.append(task_key_glance)
    facility.task_wants(*sub_tasks_to_wait)


def ceph_pkgs():
    return {'ceph-mds', 'ceph-mgr', 'ceph-mon', 'ceph-osd', 'ceph-radosgw'}


def register():
    ceph = {'component': 'ceph',
            'origin_repo': 'https://github.com/ceph/ceph',
            'deploy_source': 'pkg',
            'services': {'ceph-osd': {'deploy_mode': 'standalone'},
                         'ceph-mon': {'deploy_mode': 'standalone'},
                         'ceph-mds': {'deploy_mode': 'standalone'},
                         'ceph-mgr': {'deploy_mode': 'standalone'},
                         'ceph-radosgw': {'deploy_mode': 'standalone'}},
            'pkg_deps': ceph_pkgs,
            'used_by': ['nova-compute', 'glance-api', 'cinder-volume'],
            'goal': task_ceph_steps}
    cc = facility.get_component_config_for('ceph')
    util.dict_merge(ceph, cc)
    facility.register_component(ceph)


register()
