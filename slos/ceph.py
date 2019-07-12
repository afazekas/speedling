import logging
import os

import speedling.tasks
from speedling import cfgfile
from speedling import conf
from speedling import facility
from speedling import localsh
from speedling import util

LOG = logging.getLogger(__name__)


# TODO: add support for attribute_change
def task_handle_pools(self):
    facility.task_wants(self.task_handle_osds)
    inv_mons = self.hosts_with_service('ceph-mon')
    pooler = util.rand_pick(inv_mons)
    pools = set(('volumes', 'images', 'vms', 'gnocchi'))
    self.call_do(pooler, self.do_ensure_pools, c_kwargs={'pools': pools})


def task_handle_osds(self):
    fsid = self.get_fsid()
    facility.task_wants(self.task_ceph_mon)
    self.call_do(self.hosts_with_service('ceph-osd'),
                 self.do_ceph_deploy_all_in_one_demo, c_kwargs={'fsid': fsid})


def task_ceph_steps(self):
    self.create_ceph_state_dirs()  # move to mon or wait to mon
    self = facility.get_component('ceph')
    sub_tasks_to_wait = []
    facility.task_wants(self.task_ceph_mon)

    gconf = conf.get_global_config()
    all_services = gconf['global_service_flags']

    if 'ceph-mgr' in all_services:
        sub_tasks_to_wait.append(self.task_setup_mgr)
        facility.task_will_need(self.task_setup_mgr)

    facility.task_will_need(self.task_handle_pools)  # mon is crahsing if it called before we have any osd
    sub_tasks_to_wait.append(self.task_handle_pools)
    facility.task_wants(*sub_tasks_to_wait)
    # TODO: check syncronise with nova/cinder


def task_setup_mgr(self):
    inv_mons = self.hosts_with_service('ceph-mon')
    keyringer = util.rand_pick(inv_mons)
    inv_mgrs = self.hosts_with_service('ceph-mgr')
    msg_matrix = {}
    for m in inv_mgrs:
        i = self.get_node(m)['inv']
        host = i['hostname']
        properties = {'mon': 'allow profile mgr',
                      'osd': 'allow *',
                      'mds': 'allow *'}
        key = self.get_keyring(keyringer, 'mgr.' + host, properties)
        msg_matrix[m] = {'kwargs': {'key': key}}

    # assume all node remote, which can be wrong
    self.call_diff_args(msg_matrix, self.do_setup_key_and_mgr_start)


def task_key_glance(self):
    inv_mons = self.hosts_with_service('ceph-mon')
    keyringer = util.rand_pick(inv_mons)
    facility.task_wants(self.task_ceph_mon)
    key = self.get_keyring(keyringer,
                           'client.glance',
                           {'mon': 'allow r',
                            'osd': 'allow class-read object_prefix rbd_children, allow rwx pool=images'})
    self.call_do(self.hosts_with_service('glance-api'),
                 self.do_glance_keyring, c_kwargs={'key': key})
    facility.task_wants(self.task_handle_pools)


def task_key_for_cinder_nova(self):
    # based on current code both nova cinder wries the same file, it is not wanted
    used_by = {'cinder-volume', 'nova-compute'}  # TODO: use suffix names
    inv_mons = self.hosts_with_service('ceph-mon')
    keyringer = util.rand_pick(inv_mons)
    facility.task_wants(self.task_ceph_mon)
    key = self.get_keyring(keyringer,
                           'client.cinder',
                           {'mon': 'allow r',
                            'osd': 'allow class-read object_prefix rbd_children, allow rwx pool=volumes, allow rwx pool=vms, allow rx pool=images'})

    self.call_do(self.hosts_with_any_service(used_by),
                 self.do_cinder_nova_keyring, c_kwargs={'key': key})

    if 'nova-compute' in used_by:
        # facility.task_wants(speedling.srv.nova.task_libvirt)
        self.call_do(self.hosts_with_service('nova-compute'),
                     self.do_register_ceph_libvirt)
    facility.task_wants(self.task_handle_pools)


# this is also responsible for ceph.conf
def task_ceph_mon(self):
    fsid = self.get_fsid()
    facility.task_wants(speedling.tasks.task_hostname)
    inv_mons = self.hosts_with_service('ceph-mon')
    mon_host_ips = []
    for m in inv_mons:
        i = self.get_node(m)['inv']
        host = i['hostname']
        addr = self.get_addr_for(i, 'ceph_storage')
        mon_host_ips.append((host, addr))
    ceph_conf = self.etc_ceph_ceph_conf(fsid, mon_host_ips)
    state_dir = self.get_state_dir()
    ceph_conf_state = state_dir + '/ceph.conf'
    cfgfile.ini_file_sync(ceph_conf_state, ceph_conf,
                          owner=os.getuid(), group=os.getgid())

    inv_mons_boostrapped = {}
    # check is any mon bootstrapped already
    inv_mons_boostrapped = set([m for m in inv_mons if self.is_mon_bootstrapped(m)])
    ceph_conf_nodes = self.hosts_with_any_service(set.union(set(self.services.keys()), {'cinder-volume', 'nova-compute'}))
    self.call_do(ceph_conf_nodes, self.do_place_ceph_conf, c_kwargs={'fsid': fsid, 'mons': mon_host_ips})
    if not inv_mons_boostrapped:
        # all mon fresh
        LOG.info("Ceph mon fresh install")
        keyringer = util.rand_pick(inv_mons)
        ret = self.call_do(keyringer, self.initial_keyring)
        (admin_keyring, mon_bootstrap) = ret[next(iter(keyringer))]['return_value']
        self.file_plain(state_dir + '/ceph.client.admin.keyring', admin_keyring,
                        owner=os.getuid(), group=os.getgid())
        self.call_do(inv_mons, self.do_boostrap_mon, c_args=(mon_host_ips, fsid, mon_bootstrap, admin_keyring))
        for m in inv_mons:
            self.set_mon_bootsrapped(m)
    else:
        if (inv_mons_boostrapped == inv_mons):
            LOG.info("Ceph mons already bootstrapped")
        else:
            LOG.warn('Your montor topligy is moved, handler not implemented')


class Disk(facility.Component):
    # ceph __init__ may create it, but likely we will not use this class
    pass


def touch(path):
    open(path, 'w').close()


class Ceph(facility.StorageBackend):
    origin_repo = 'https://github.com/ceph/ceph'
    deploy_source = 'pkg'
    services = {'ceph-osd': {'deploy_mode': 'standalone'},
                'ceph-mon': {'deploy_mode': 'standalone'},
                'ceph-mds': {'deploy_mode': 'standalone'},
                'ceph-mgr': {'deploy_mode': 'standalone'},
                'ceph-radosgw': {'deploy_mode': 'standalone'}}

    def __init__(self, *args, **kwargs):
        super(Ceph, self).__init__(**kwargs)
        self.final_task = self.bound_to_instance(task_ceph_steps)
        [self.bound_to_instance(f) for f in [task_key_for_cinder_nova, task_setup_mgr,
                                             task_handle_pools, task_key_glance,
                                             task_ceph_mon, task_handle_osds]]
        self.peer_info = {}
        self.pool_registry = {}
        self.access_registry = {}
        self.fsid = None

    def create_ceph_state_dirs(self, ):
        state_dir = self.get_state_dir()
        self.file_path(self.get_state_dir(),
                       owner=os.getuid(),
                       group=os.getgid())

        self.file_path(state_dir + '/mon_bootstrap',
                       owner=os.getuid(),
                       group=os.getgid())

    def is_mon_bootstrapped(self, mon_host):
        mon_state_dir = self.get_state_dir() + '/mon_bootstrap'
        return os.path.isfile(mon_state_dir + '/' + mon_host)

    def set_mon_bootsrapped(self, mon_host, done=True):
        mon_state_dir = self.get_state_dir() + '/mon_bootstrap'
        touch(mon_state_dir + '/' + mon_host)

    def generate_fsid(self):
        # TODO randomize and/or add to the component
        return 'a7f64266-0894-4f1e-a635-d0aeaca0e993'  # from the doc

    def get_fsid(self):
        fsid = self.fsid
        if fsid:
            return fsid
        state_dir = self.get_state_dir()
        fsid_state = state_dir + '/fsid'
        if os.path.isfile(fsid_state):
            f = open(fsid_state, 'r')
            fsid = f.read().strip()
            f.close()
        else:
            fsid = self.generate_fsid()
            self.file_plain(fsid_state, fsid,
                            owner=os.getuid(), group=os.getgid())
            self.fsid = fsid
        return fsid

    def etc_ceph_ceph_conf(self, fsid, mons):
        # TODO remove the osd section, it is a dirtty _hack_ for
        # demo installing also on ext4

        # TODO add 'public network' and 'cluster network'

        ceph_config = {
            'global': {
                'fsid': fsid,
                'mon_initial_members': ','.join(m[0] for m in mons),
                'mon_host': ','.join("v2:" + m[1] + ":3300/0" for m in mons),
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
    def initial_keyring(self):
        # admin key
        localsh.run("ceph-authtool --create-keyring /etc/ceph/ceph.client.admin.keyring --gen-key -n client.admin --cap mon 'allow *' --cap osd 'allow *' --cap mds 'allow'")
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
    def gen_monmap(self, mons, fsid):
        cluster_name = 'ceph'
        localsh.run("""monmaptool --create --fsid '{fsid}' /tmp/monmap && \
                       chown ceph /tmp/ceph.mon.keyring""".format(fsid=fsid))
        for name, ip in mons:
            localsh.run("""
                monmaptool --add {hostname} v2:{ip}:3300/0 /tmp/monmap""".format(hostname=name,
                                                                                 ip=ip))
            self.file_path(
                '/var/lib/ceph/mon/{cluster_name}-{hostname}'.format(
                    cluster_name=cluster_name, hostname=name),
                owner='ceph', group='ceph')

    def do_boostrap_mon(cname, mons, fsid, mon_bootstrap_keyring, admin_keyring):
        self = facility.get_component(cname)
        cluster_name = 'ceph'
        # TODO: create/use file put
        self.file_plain('/tmp/ceph.mon.keyring', mon_bootstrap_keyring,
                        owner='ceph')
        self.file_plain('/etc/ceph/ceph.client.admin.keyring', admin_keyring)
        this_hostname = self.get_this_node()['inv']['hostname']

        self.gen_monmap(mons, fsid)
        localsh.run(("sudo -u ceph ceph-mon --mkfs -i {name} "
                     "--monmap /tmp/monmap "
                     "--keyring /tmp/ceph.mon.keyring").format(name=this_hostname))

        self.file_plain('/var/lib/ceph/mon/{cluster_name}-{hostname}.done'.format(
            cluster_name=cluster_name, hostname=this_hostname), '', owner='ceph')
        localsh.run(("systemctl enable ceph-mon@{host} && "
                     "systemctl start ceph-mon@{host}").format(host=this_hostname))

    def do_place_ceph_conf(cname, fsid, mons):
        self = facility.get_component(cname)
        self.have_content()
        self = facility.get_component(cname)
        ceph_config = self.etc_ceph_ceph_conf(fsid=fsid, mons=mons)
        self.file_path('/etc/ceph', mode=0o755)  # all relaveant cli/srv nodes
        ceph_local_conf = '/etc/ceph/' + self.name + '.conf'
        self.file_ini(ceph_local_conf, ceph_config, mode=0o644)

    def fetch_key(self, user, properties=dict()):
        props = ' '.join(("{cap} '{rule}'".format(cap=k, rule=v) for (k, v) in properties.items()))
        return localsh.ret("ceph auth get-or-create {name} {props}".format(name=user, props=props))

    def do_setup_key_and_mgr_start(cname, key):
        self = facility.get_component(cname)
        this_hostname = self.get_this_inv()['hostname']
        key_dir_path = '/var/lib/ceph/mgr/ceph-' + this_hostname
        key_path = key_dir_path + '/keyring'
        self.file_path(key_dir_path, owner='ceph', group='ceph')
        self.file_plain(key_path, key, owner='ceph', group='ceph')
        localsh.run("""
name={hostname}
ln -s /usr/lib/systemd/system/ceph-mgr@.service /etc/systemd/system/ceph-mgr@$name.service
systemctl start ceph-mgr@$name
    """.format(hostname=this_hostname))

    def get_keyring(self, node, user, properties):
        assert len(node) == 1
        c_kwargs = {'user': user,
                    'properties': properties}
        ret = self.call_do(node, self.fetch_key, c_kwargs=c_kwargs)
        return ret[next(iter(node))]['return_value']

    def do_cinder_nova_keyring(cname, key):
        self = facility.get_component(cname)
        target = '/etc/ceph/ceph.client.cinder.keyring'
        # TODO: insecure, use posix acl or shared group, but it should not be world readable
        # consider more keys ..
        # we need to call to local cinder/nova to have_content before
        self.file_plain(target, key, owner='root', group='root', mode=0o644)

    def do_glance_keyring(cname, key):
        self = facility.get_component(cname)
        target = '/etc/ceph/ceph.client.glance.keyring'
        self.file_plain(target, key, owner='glance', group='glance', mode=0o640)

    # Ceph nowadays likes hostnames in the mon.conf
    # Non prod!
    def do_ceph_deploy_all_in_one_demo(cname, fsid):
        self = facility.get_component(cname)
        # we must not regenerate the fsid ever !
        # TODO single persist
        # TODO randomize
        # TODO reentrant / idempontetn ..
        cluster_name = 'ceph'
        # local not mounted, not formated osd on the root disk , just for demo !!!
        # OSD ID number is integer, should be a continius allocation
        osd_num = 0
        self.file_path('/var/lib/ceph/osd/ceph-{osd_num}'.format(osd_num=osd_num), owner='ceph', group='ceph', mode=0o755)
        localsh.run("ceph osd create")   # this allocates a new osd number, first run it is 0
        localsh.run("ceph-osd -i {osd_num} --mkfs --mkkey --no-mon-config  --setuser ceph --setgroup ceph".format(osd_num=osd_num))
        localsh.run("ceph auth add osd.{osd_num} osd 'allow *' mon 'allow rwx' -i /var/lib/ceph/osd/ceph-{osd_num}/keyring".format(osd_num=osd_num))
        localsh.run("systemctl enable ceph-osd@{osd_num} && systemctl start ceph-osd@{osd_num}".format(osd_num=osd_num))

    # it is a possible combination to use swift as a backup service
    # ceph auth get-or-create client.cinder-backup mon 'allow r' osd 'allow class-read object_prefix rbd_children, allow rwx pool=backups' | tee /etc/ceph/ceph.client.cinder-backup.keyring
    # chown cinder:cinder /etc/ceph/ceph.client.cinder-backup.keyring
    #    gconf = conf.get_global_config()
    #    if {'gnocchi', 'gnocchi-metricd'}.intersection(gconf['global_service_flags']):
    #       localsh.run("""
    # ceph auth get-or-create client.gnocchi mon "allow r" osd "allow class-read object_prefix rbd_children, allow rwx pool=gnocchi" | tee /etc/ceph/ceph.client.gnocchi.keyring
    # chown gnocchi:gnocchi /etc/ceph/ceph.client.gnocchi.keyring""")

    def do_register_ceph_libvirt(self, ):
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

    # pools can be created bfore osd, auth key can be created for non exisitng pool
    def do_ensure_pools(self, pools):
        existing = localsh.ret("ceph osd lspools").strip()
        # input '1 volumes,2 images,3 vms,4 gnocchi,'
        pairs = existing.split(',')
        # TODO: duplicate ?
        pool_defined = set((pair.split(' ')[1] for pair in pairs if pair))
        missing_pools = pools - pool_defined
        for pool in missing_pools:
            localsh.run("ceph osd pool create {pool} 16 && ceph osd pool set {pool} size 1; ceph osd pool application enable {pool} rbd".format(pool=pool))

    def get_node_packages(self):
        pkgs = super(Ceph, self).get_node_packages()
        pkgs.update({'ceph-mds', 'ceph-mgr', 'ceph-mon',
                     'ceph-osd', 'srv-radosgw\\ceph'})
        return pkgs

    def get_glance_conf_extend(self, sname):
        (name, stype) = sname.split(':')
        # stype supposed to be rbd
        new_section = {name: {'rbd_store_pool': 'images',
                              'rbd_store_user': 'glance',
                              'rbd_store_ceph_conf': '/etc/ceph/' + self.name + '.conf'}}

        return {'/etc/glance/glance-api.conf': new_section}

    def get_waits_for_nova_task(self):
        return {self.task_key_for_cinder_nova}

    def get_waits_for_glance_task(self):
        return {self.task_key_glance}

    def get_waits_for_cinder_task(self):
        return {self.task_key_for_cinder_nova}
