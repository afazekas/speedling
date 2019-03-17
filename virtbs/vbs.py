from __future__ import print_function

import argparse
import ast
import errno
import functools
import ipaddress
# is ujson still the fastest ?
# anyjson did not had dump
import json
import os
import os.path
import pwd
import sys
import uuid
import xml.etree.ElementTree as ET
from collections import defaultdict

import libvirt
import yaml

import __main__
# WARNING: improper shell escapes, evil guy can be evil!
from speedling import cfgfile
from speedling import fetch
from speedling import localsh
from speedling import netutils
from speedling import util
from virtbs import xmlvirt

try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote


try:
    import virtualbmc.manager as vbmc
    VIRTBMC_ENABLED = True
except ImportError:
    VIRTBMC_ENABLED = False


# TODO: init selinux perm
# TODO: create Makefile instead of setup.py
# NOTE: libvirt is not on pypi

# TODO kill ssh DNS , kill requiretty (both my image and cloud init)

SSH_PUBLIC_KEY_LIST_PATH_REL = '/id_rsa.pub'  # ro file, can have multiple keys

SSH_PRIVATE_KEY_PATH_REL = '/id_rsa'  # ro file


# WARN: muxes are different now
@functools.lru_cache()
def get_path(thing=''):
    paths = CONFIG.get('paths', {})
    root_path = paths.get('virtbs_root', '/srv/virtbs')
    if not thing:
        return root_path
    return os.path.join(root_path, thing)


# We had to limit the max number of machines in order
# fit into the port range
# assume max 16 slince with each max 255 host
def bmc_port(bslice, offset):
    base = int(CONFIG.get('address_pool', {}).get('bmc_port_base', 8192))
    return base + bslice * 256 + offset


def qcow2_to_raw(src, dst):
    localsh.run("qemu-img convert -f qcow2 -O raw -S 4k {src} {dst}".format(
        src=cmd_quote(src), dst=cmd_quote(dst)))


def image_info(img):
    jdata = localsh.ret("qemu-img info --output=json " + cmd_quote(img))
    return json.loads(jdata)


def get_virtual_size(img):
    i = image_info(img)
    return i['virtual-size']


def create_backed_qcow2(src, dst, size='10G', bfmt='raw'):
    # the args are not shell escaped
    if size:
        s = util.human_byte_to_int(size)
        image_size = get_virtual_size(src)
        if image_size > s:
            size = image_size
        localsh.run("qemu-img create -f qcow2 -o 'backing_fmt={bfmt},"
                    "backing_file={src}' '{dst}' '{size}'".format(
                        src=src, dst=dst, size=size, bfmt=bfmt))
    else:
        localsh.run("qemu-img create -f qcow2 -o 'backing_fmt={bfmt},"
                    "backing_file={src}' '{dst}'".format(
                        src=src, dst=dst, bfmt=bfmt))


# NOTE: It may default to sparse raw in the future
def create_empty_disk(dst, size, fmt='qcow2'):
    if fmt == 'raw':
        localsh.run("truncate -s {size} {dst}".format(
            fmt=fmt, dst=dst))
    else:
        localsh.run("qemu-img create -f {fmt} '{dst}' '{size}'".format(
            fmt=fmt, dst=dst, size=size))


# TODO: clean on exception
# TODO: add support for direct kernel/initrd inject boot
# TODO: add image creation step for fetching kernels
#       and initrd
def bootvm(conn, node):
    # base_image sparse raw
    # assume uuids not collideing! ;-)
    # TODO: assume clock and rnd gen does not works ;-)
    # extra disks are not formated!
    keys_path = get_path("keys")
    for disk in node['disks']:
        if 'image_slot' in disk and disk['image_slot']:
            create_backed_qcow2(
                base_image(disk['image_slot'],
                           disk.get('image_tag', 'default')),
                disk['path'],  disk['size'])
        else:
            create_empty_disk(disk['path'], disk['size'])

    if 'config_drive' in node:
        kpp = keys_path + SSH_PUBLIC_KEY_LIST_PATH_REL
        ssh_keys = open(kpp, 'r').readlines()
        ssh_keys = [line.strip() for line in ssh_keys
                    if not line.startswith('#')]
        create_cloud_config_image(node, ssh_keys)

    domain_xml = xmlvirt.generate_libvirt_dom_xml(node)
    dom = conn.defineXML(domain_xml)
    if dom is None:
        raise RuntimeError('Failed to define a domain from an XML definition')
    # NOTE: virtbmc destroys the vm on 'off' , so it has to be persistent
    # NOTE: Might be possible to create first then define
    if dom.create() < 0:
        raise ('Can not boot guest domain.')
    return dom


def files_to_iso(filemap, config_image):
    # filemap is target,source pairs
    pathspec = ' '.join(('='.join((target, source)).join(("'", "'"))
                         for (target, source) in filemap))
    # use real shell escape ? single=False
    localsh.run("mkisofs -graft-points -o '{config_image}' "
                "-V cidata -r -J --quiet {pathspec}".format(
                    pathspec=pathspec, config_image=config_image))


# dhcp 'request host-name' instead of meta-data ?
# the phone home feautere looks interesting
def create_cloud_config_image(node, ssh_keys):
    vm_uuid = node['vm_uuid']
    hostname = node['hostname']
    cd_path = get_path("cd")
    ci = '#cloud-config\n' + json.dumps({
         'users': [{'name': 'stack',
                    'ssh-authorized-keys': ssh_keys,
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'lock-passwd': True}]})
    cif = os.path.join(cd_path, 'user-data-' + vm_uuid)
    mif = os.path.join(cd_path, 'meta-data-' + vm_uuid)
    target = os.path.join(cd_path, vm_uuid + '.iso')
    mi = ("instance-id: {vm_uuid}\nhostname: {hostname}\n"
          "local-hostname: {hostname}\n").format(hostname=hostname,
                                                 vm_uuid=vm_uuid)

    cfgfile.put_to_file(cif, ci)
    cfgfile.put_to_file(mif, mi)
    file_map = [('user-data', cif), ('meta-data', mif)]
    files_to_iso(file_map, target)
    return target


def get_libvirt_conn():
    conn = libvirt.open('qemu:///system')
    if not conn:
        print('Failed to open connection to the hypervisor', file=sys.stderr)
        sys.exit(1)
    return conn


def bmc_creds(build_slice, offset):
    return {"pm_addr": get_host_ip(),
            "pm_type": "pxe_ipmitool",
            "pm_user": "admin",
            "pm_password": "password",
            "pm_port": str(bmc_port(build_slice, offset))}


def fork_no_wait(node):
    pid = os.fork()
    if not (pid):
        conn = get_libvirt_conn()
        bootvm(conn, node)
        if VIRTBMC_ENABLED:
            vbmc_manager = vbmc.VirtualBMCManager()
            vbmc_manager.add(username='admin',
                             password='password',
                             port=node['bmc_port'],
                             address='::',
                             domain_name=node['virt_domain_name'],
                             libvirt_uri='qemu:///system',
                             libvirt_sasl_username=None,
                             libvirt_sasl_password=None)
            vbmc_manager.start(node['virt_domain_name'])
        exit(0)
    return pid


# ~ 0.3 sec in waiting for libvirt
# fork helps, unless we wait for machined?
def boot_vms(nodes):
    boots = {}
    for node in nodes:
        # mechine migh have more args, like ip address in the future
        # which might not be need to be passed here

        # ensure image , allowing some parallel build/boot, but
        # the first node which requres the same image will do the build and
        # wait for the earlier node
        for disk in node['disks']:
            if 'image_slot' in disk and disk['image_slot']:
                base_image(disk['image_slot'], disk.get('image_tag', 'default'))

        pid = fork_no_wait(node)
        boots[pid] = node
    for p in boots.keys():
        (pid, status) = os.waitpid(p, 0)
        if status:  # not just the 8 bit exit code!
            print("Failed to boot pid: {pid}, status:"
                  " {status}, params: {params}".format(pid=pid, status=status,
                                                       params=node),
                  file=sys.stderr)


def _file_sha256_sum(for_sum):
    # compare this with some built in function
    return localsh.ret("sha256sum '{}'".format(
                       for_sum)).split(" ")[0]


# not genrally usable, yet!
# not fault and insane tolerant!
# not parallel safe!
def image_download(name, data, key=None, renew=False):
    build_id = str(uuid.uuid4())
    img_dir = get_image_data_dir(name)
    default_path = os.path.join(img_dir, 'default')

    if not renew:
        try:
            path = os.readlink(default_path)
            return (data['fmt'], path)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    D = data.copy()
    D['key'] = key
    url = data['url_pattern'].format(**D)
    fetch_path = get_path("downloads")
    download_path = os.path.join(fetch_path, os.path.basename(url))
    # WARNING: assumes unique names
    verified = False
    if os.path.exists(download_path) and 'sha256' in data:
        suma = _file_sha256_sum(download_path)
        if suma == data['sha256']:
            print('Alrady have the file at {}'.format(download_path))
            verified = True
        else:
            print('File at {} has incorrect sha256 sum, expected: {} '
                  'got: {}'.format(download_path, data['sha256'], suma))
    if not verified:
        print('Downloading {} ..'.format(url))  # log ?
        fetch.download_origin_or_mirror(url, download_path)
        if 'sha256' in data:
            suma = _file_sha256_sum(download_path)
            print('checksum {}'.format(suma))
            assert suma == data['sha256']
    work_file = os.path.join(img_dir, build_id)
    if 'compression' in data:
        print('Decompressing ..')
        if 'tar' not in data['compression']:
            # xz does sparse, others ?
            localsh.run(("{compression} --decompress "
                         "<'{download_path}' >'{work_file}'").format(
                compression=data['compression'],
                download_path=download_path, work_file=work_file))
        else:
            if 'file_in_tar_pattern' not in D:
                raise NotImplementedError('We are not gussing the filename in '
                                          'tar, use file_in_tar_pattern')
            in_file = data['file_in_tar_pattern'].format(**D)
            # assume tar autodectes the compression
            # no way specifiy the decompressed file final name !
            # not tested code path
            localsh.run(("cd '{fetch_path}'; tar -xf "
                         "'{download_path}' '{in_file}'; "
                         "cp --sparse=always '{in_file}' '{work_file}' "
                         "; rm '{in_file}'").format(
                        fetch_path=fetch_path, download_path=download_path,
                        in_file=in_file))
    else:
        localsh.run(("cp --sparse=always "
                     "'{download_path}' '{work_file}'").format(
            download_path=download_path, work_file=work_file))
    tmp_link = work_file + '-lnk'
    os.symlink(os.path.basename(work_file), tmp_link)
    os.rename(tmp_link, default_path)
    return (data['fmt'], work_file)


def virt_costumize_script(image, script_file, args_str='', log_file=None):
    # NOTE: cannot specify a constant destination file :(
    # --copy-in {script_file}:/root/custumie.sh did not worked
    # LIBGUESTFS_BACKEND=direct , file permissionsss
    base_name = os.path.basename(script_file)
    cmd = '/root/' + base_name + ' ' + args_str
    (r, log) = localsh.run_log(("LIBGUESTFS_BACKEND=direct "
                                "virt-customize --verbose --add {image} "
                                "--memsize 1024 "
                                "--copy-in {script_file}:/root/ "
                                "--chmod 755:/root/{base_name} "
                                "--run-command {cmd} "
                                "--selinux-relabel ").format(
        image=cmd_quote(image), script_file=cmd_quote(script_file),
        base_name=cmd_quote(base_name),
        cmd=cmd_quote(cmd)))
    print(log)
    if log_file:
        f = open(log_file, "w")
        f.write(log)
        f.close()
    if r:
        raise Exception("virt_costumize Failed")


# incomplete, temporary
# these dics will be originated from some file and will be json friendy
def __filter_to_json(d):
    di = {}
    for k, v in d.items():
        if callable(v):
            if hasattr(v, '__name__'):
                di[k] = v.__name__
            else:
                di[k] = str(v)
    return di


def is_slot_exists(name):
    lib_dir = os.path.join(get_path("library"), name)
    return os.path.isdir(lib_dir)


def get_image_data_dir(name):
    lib_dir = os.path.join(get_path("library"), name)
    os.makedirs(lib_dir, exist_ok=True)
    return lib_dir


def image_virt_customize(name, data, image_tag=None, renew=False):
    # TODO: we do not really want to have _base_image
    # to convert to raw and move it to the _base and mage the gc more complex

    img_dir = get_image_data_dir(name)
    default_path = os.path.join(img_dir, 'default' if not image_tag
                                         else image_tag)
    if not renew and os.path.islink(default_path):
        real_path = os.path.realpath(default_path)
        return ('qcow2', real_path)

    location = base_image(data['base_slot'],
                          data.get('image_tag', 'default'))
    # TODO: same name uniquie magic
    # TODO: build lock
    # TODO: parallel safe build
    if not renew:
        try:
            path = os.readlink(default_path)
            return ('qcow2', path)
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    build_id = str(uuid.uuid4())
    build_image = os.path.join(img_dir, build_id)  # no suffix
    build_log = os.path.join(img_dir, build_id + '.log')
    create_backed_qcow2(location, build_image)  # TODO: add size parameter
    script = None
    script_desc = data.get('script', None)
    if 'here' in script_desc:
        script = script_desc['here']
    if 'file' in script_desc:
        if script:
            raise Exception('Declare only here or file not both')
        script = open(script_desc['file']).read()

    assert script  # TODO: raise a joke
    script_file = os.path.join(img_dir, build_id + '-build.sh')
    cfgfile.put_to_file(script_file, script)
    print('Customizing image for {} at {}'.format(name, build_image))
    args_str = data.get('script_arguments', '')
    virt_costumize_script(build_image, script_file, args_str, build_log)

    di = __filter_to_json(data)
    cfgfile.put_to_file(build_image + '-data.json', json.dumps(di))
    tmp_link = build_image + '-lnk'
    os.symlink(os.path.basename(build_image), tmp_link)
    os.rename(tmp_link, default_path)
    return ('qcow2', build_image)


# NOTE: we might need to handle more version key kind values
image_flow_table = {}


# returns back a sparse raw image either
# from _base or from the library
# Nobody allowed to modify these raw files,
# deleting can be ok in same cases
def base_image(image_type, image_tag='default'):
    # reqursively does the build steps to reach a valid image
    (fmt, image) = image_flow_table[image_type]['driver'](
        image_type,
        image_flow_table[image_type], image_tag)
    if fmt == 'qcow2':
        # WARNING: assumes globaly uniquie name
        base_file = os.path.basename(image)
        bpath = get_path("_base")
        base_path = os.path.join(bpath, base_file)
        if not os.path.isfile(base_path):
            qcow2_to_raw(image, base_path)
        return base_path
    assert fmt == 'raw'
    return image


def wipe_domain_by_uuid(UUID):
    con = get_libvirt_conn()
    dom = con.lookupByUUIDString(UUID)
    desc = dom.XMLDesc()
    root = ET.fromstring(desc)
    to_del = []
    live_path = get_path('live')
    cd_path = get_path('cd')
    for disk in root.find('devices').findall('disk'):
        filepath = disk.find('source').get('file')
        if filepath.startswith(live_path):
            to_del.append(filepath)
        elif filepath.startswith(cd_path):
            to_del.append(filepath)
        else:
            print("Unexpected disk location, skipping {}".format(filepath))

    # TODO: add better error handling and attempt these only when needed
    try:
        dom.destroy()
    except libvirt.libvirtError:
        pass  # assume turned off
    try:
        dom.undefine()
    except libvirt.libvirtError:
        pass  # assume transiant vm or concurrent delete
    for f in to_del:
        try:
            os.unlink(f)
        except Exception:
            print("Unable to delete: '{f}'".format(f=f))


# remove machines and disks
# and nets
def wipe_slice(build_slice):
    conn = get_libvirt_conn()
    ss = 'bs{build_slice}'.format(
        build_slice=build_slice)

    # just delete everything starts with the uuid, instead of xml parse ?
    # the configdrive build files are not deleted yet
    for dom in conn.listAllDomains():
        name = dom.name()
        UUID = dom.UUIDString()  # the not string version is also str
        if name.startswith(ss):
            # TODO: Make it parallel,
            #  make sure it does not stalls at hign vm count
            wipe_domain_by_uuid(UUID)

    conn = get_libvirt_conn()
    for net in conn.listAllNetworks():
        if net.name().startswith(ss):
            try:
                net.destroy()
            except libvirt.libvirtError:
                net.undefine()
            else:
                net.undefine()
    # TODO: fork the vbmc handling
    if VIRTBMC_ENABLED:
        vbmc_manager = vbmc.VirtualBMCManager()
        # maybe just listing the .vbmc dir would be enough
        doms = vbmc_manager.list()
        if doms and isinstance(doms[0], int):
            doms = doms[1]  # lib difference hack
        for d in doms:
            if d['domain_name'].startswith(ss):
                if d['status'] == vbmc.RUNNING:
                    vbmc_manager.stop(d['domain_name'])
            vbmc_manager.delete(d['domain_name'])


def get_br_name(build_slice, net_name):
    return "bs{}{}".format(build_slice, net_name)


# by default without proxy command
def generate_ansible_inventory(nodes, common_opts, target_file):
    stream = open(target_file, 'w')
    group_members = defaultdict(list)
    stream.write('localhost ansible_connection=local\n')
    group_members['local'].append('localhost')
    for m in nodes:
        assert m['hostgroup'] not in {'local', 'virtbs'}
        group_members[m['hostgroup']].append(
            m['hostname'])
        group_members['virtbs'].append(m['hostname'])
        stream.write(m['hostname'])

        def param_write(key, value):
            stream.write(' ' +
                         ('='.join((key, value.join(("'", "'"))))))
        param_write('ansible_ssh_host', m['access_ip'])
        stream.write('\n')

    for group, memebers in group_members.items():
        stream.write('[%s]\n' % group)
        for m in memebers:
            stream.write('%s\n' % m)

    stream.write('[virtbs:vars]\n')
    for key, value in common_opts.items():
        stream.write(('='.join((key, value.join(("'", "'"))))) + '\n')
    stream.close()


# temporary way for testing speedling
def generate_ansible_inventory_speedling(machines, build_slice,
                                         common_opts, target_file):
    stream = open(target_file, 'w')
    group_members = defaultdict(list)
    stream.write('localhost ansible_connection=local\n')
    group_members['local'].append('localhost')
    for m in machines:
        mac = get_mac_for(build_slice, 0x0, m['offset'])
        networks = {'access': {'if_lookup': {'mac': mac},
                               'addresses': [m['access_ip']]}}
        assert m['hostgroup'] not in {'local', 'virtbs'}
        group_members[m['hostgroup']].append(
            m['hostname'])
        group_members['virtbs'].append(m['hostname'])
        stream.write(m['hostname'])

        def param_write(key, value):
            stream.write(' ' +
                         ('='.join((key, value.join(("\"'", "'\""))))))

        def param_write_eval(key, value):
            value = str(value)
            stream.write(' ' +
                         ('='.join((key, value.join(('"', '"'))))))

        param_write('ansible_ssh_host', m['access_ip'])
        param_write('sl_ssh_address', m['access_ip'])
        param_write_eval('sl_networks', networks)
        stream.write('\n')

    for group, memebers in group_members.items():
        stream.write('[%s]\n' % group)
        for m in memebers:
            stream.write('%s\n' % m)

    stream.write('[virtbs:vars]\n')
    for key, value in common_opts.items():
        stream.write(('='.join((key, value.join(("'", "'"))))) + '\n')
    stream.close()


NAME_TO_ID = {}


def assigne_net_names_to_id():
    counter = 1
    # 0 is reserved for specail lo usage
    for _, params in machine_types.items():
        if 'nets' in params:
            for net in params['nets']:
                if net not in NAME_TO_ID:
                    NAME_TO_ID[net] = counter
                    counter += 1


# nets not in this dict are not requisted
# nets with [] requested, but do not have any reservation
NAME_TO_RESERVATION = {}


def populate_reservations():
    nf = CONFIG.get('network_flags', {})
    ap = CONFIG.get('address_pool', {})
    default_4 = ap.get('ipv4_address_serving_default_enabled', False)
    for node in NODES:
        if node.get('disable_address_serving', False):
            continue
        if 'hostname' not in node:
            continue
        hostname = node['hostname']
        ifs = node.get('interfaces', [])
        for iface in ifs:
            net = iface.get('network', None)
            rese = NAME_TO_RESERVATION.setdefault(net, {4: [], 6: []})
            if net in nf:
                if not nf[net].get('address_serving_4', default_4):
                    continue
            else:
                continue
            rese[4].append({'mac': iface['mac'],
                            'ip': iface['ipv4_addr'], 'name': hostname})


def create_networks(conn, build_slice):
    nf = CONFIG.get('network_flags', {})
    ap = CONFIG.get('address_pool', {})
    default_atype = ap.get('prefered_address', 'ipv6stateless')
    default_stateless = ap.get('ipv6_stateless_default_enabled', False)
    if default_atype == 'ipv4':
        default_atype = 'ipv6stateless'
    for net, rese in NAME_TO_RESERVATION.items():
        net_name = 'bs' + str(build_slice) + net
        net_opts = nf.get(net, {})

        net_id = NAME_TO_ID[net]
        address_serving_6 = net_opts.get('address_serving_6', default_stateless)
        atype = net_opts.get('prefered_address', 'ipv6stateless')
        if atype == 'ipv4':
            atype = 'ipv6stateless'
        base = {'name': net_name,
                'mac': get_mac_for(build_slice, net_id, 1),
                'ipv4_address': get_addr_for(build_slice, net_id, 1, atype='ipv4'),
                'ipv6_address': get_addr_for(build_slice, net_id, 1, atype=atype),
                'ipv4_mask': '255.255.255.0',  # TODO: calculate
                'internet_access': net_opts.get('internet_access', False),
                'address_serving_6': address_serving_6,
                'reservations': rese}
        net_xml = xmlvirt.netxml_from(base)
        print('Creating network: ' + net_name)
        net = conn.networkDefineXML(net_xml)
        net.setAutostart(True)
        net.create()


# offset 0 reserved
# offset 1 router ip
# max offset is reserved for  brodacast

MAC_START = None
MAC_SLICE_MUL = None
MAC_SUBNET_MUL = None

IPV4_START = None
IPV4_SLICE_MUL = None
IPV4_SUBNET_MUL = None
IPV4_NETMASK = None

IPV6_SLICE_MUL = None
IPV6_SUBNET_MUL = None
IPV6_PREFIX = 64

IPV6_LINK_LOCAL_START = ipaddress.IPv6Address('fe80::0')


def process_address_pool():
    global MAC_START, MAC_SLICE_MUL, MAC_SUBNET_MUL
    global IPV4_START, IPV4_SLICE_MUL, IPV4_NETMASK, IPV4_SUBNET_MUL
    global IPV6_START, IPV6_SLICE_MUL, IPV6_SUBNET_MUL, IPV6_PREFIX
    global IPV4_AS_IPV6_START

    addrs = CONFIG.get('address_pool', {})
    mac_start = addrs.get('mac_start', '52:54:00:00:00')
    MAC_START = int(mac_start.replace(':', ''), 16)
    # endian ?
    ipv4_start = addrs.get('ipv4_start', '172.16.0.0')
    IPV4_START = ipaddress.IPv4Address(ipv4_start)

    ipv6_start = addrs.get('ipv6_stateless_start', 'fd00:aaaa::0')
    IPV6_START = ipaddress.IPv6Address(ipv6_start)

    ipv4_slice_prefix = int(addrs.get('ipv4_slice_prefix', 20))
    ipv4_prefix = int(addrs.get('ipv4_subnet_prefix', 24))
    # NOTE: the config currently allows crazy shifting, however
    # the bit opts might be faster than a mul
    IPV4_SLICE_MUL = 2**(32 - ipv4_slice_prefix)
    IPV4_SUBNET_MUL = 2**(32 - ipv4_prefix)
    IPV4_NETMASK = str(ipaddress.IPv4Network('0.0.0.0/' + str(ipv4_prefix))
                       .netmask)

    MAC_SUBNET_MUL = IPV4_SUBNET_MUL
    mac_slice_extra_power = int(addrs.get('mac_slice_extra_power', 8))
    MAC_SLICE_MUL = 2**(32 - ipv4_slice_prefix + mac_slice_extra_power)

    IPV6_SLICE_MUL = 2**(128 - IPV6_PREFIX + (32 - ipv4_slice_prefix))
    IPV6_SUBNET_MUL = 2**(128 - IPV6_PREFIX)
    ipv4as6 = addrs.get('ipv6_from4_start', '0:0:0:0:0:ffff::0')
    IPV4_AS_IPV6_START = ipaddress.IPv6Address(ipv4as6)


def get_int_mac(build_slice, net_id, offset):
    return (MAC_START + build_slice*MAC_SLICE_MUL +
            MAC_SUBNET_MUL * net_id + offset)


def get_mac_for(build_slice, net_id, offset, delim=':'):
    mac_int = get_int_mac(build_slice, net_id, offset)
    mac_bytes = mac_int.to_bytes(6, byteorder='big')
    mac_str = delim.join("{:02x}".format(mac_bytes[i]) for i in range(0, 6))
    return mac_str


def get_ipv6_mac_based(build_slice, net_id, offset, base):
    mac_int = get_int_mac(build_slice, net_id, offset)
    mac_bytes = mac_int.to_bytes(6, byteorder='big')
    subnet_bytes = bytes((mac_bytes[0] ^ 2, *mac_bytes[1:3],
                          0xff, 0xfe, *mac_bytes[3:]))
    subn = int.from_bytes(subnet_bytes, byteorder='big', signed=False)
    rel = (build_slice*IPV6_SLICE_MUL + IPV6_SUBNET_MUL * net_id) + subn
    return str(base + rel)


def get_ipv6_ipv4_based(build_slice, net_id, offset):
    rel = build_slice*IPV4_SLICE_MUL + IPV4_SUBNET_MUL * net_id + offset
    ipv4_rel = int(IPV4_START + rel)
    return str(IPV4_AS_IPV6_START + ipv4_rel)


def get_addr_for(build_slice, net_id, offset, atype='ipv4'):
    if atype == 'ipv4':
        rel = build_slice*IPV4_SLICE_MUL + IPV4_SUBNET_MUL * net_id + offset
        return str(IPV4_START + rel)
    if atype == 'ipv6stateless':
        return get_ipv6_mac_based(build_slice, net_id, offset,
                                  IPV6_START)
    if atype == 'ipv6local':
        return get_ipv6_mac_based(build_slice, net_id, offset,
                                  IPV6_LINK_LOCAL_START)
    if atype == 'ipv4as6':
        return get_ipv6_ipv4_based(build_slice, net_id, offset)

    raise NotImplementedError("Not implemented ip version " + atype)


def virt_domain_name(build_slice, hostname):
    return 'bs{build_slice:x}-{name}'.format(
        build_slice=build_slice, name=hostname)


def generate_node(build_slice, offset, machine_type_name):
    machine_type = machine_types[machine_type_name]
    vm_uuid = str(uuid.uuid4())
    hostname = '{}-{:02x}'.format(machine_type_name, offset)
    access_ip = None

    nf = CONFIG.get('network_flags', {})
    ap = CONFIG.get('address_pool', {})
    default_atype = ap.get('prefered_address', 'ipv4')
    # allways the first network is the one we want to ssh
    if 'nets' in machine_type:
        if machine_type['nets']:
            net = machine_type['nets'][0]
            atype = nf.get(net, {}).get('preferred_address', default_atype)
            access_ip = get_addr_for(build_slice,
                                     NAME_TO_ID[net], offset, atype=atype)

    config_drive_path = get_path('cd')
    console_log_path = get_path('log')
    node = {'console_log': os.path.join(console_log_path,
                                        vm_uuid + '-console.log'),
            'config_drive': os.path.join(config_drive_path, vm_uuid + '.iso'),
            'vm_uuid': vm_uuid,
            'vcpu': machine_type.get('vcpu', 1),
            'memory': machine_type.get('memory', 1024),
            'bmc_port': bmc_port(build_slice, offset),
            'hostname': hostname,
            'virt_domain_name': virt_domain_name(build_slice, hostname),
            'hostgroup': machine_type_name,
            'offset': offset,
            'build_slice': build_slice,
            'access_ip': access_ip,
            }
    if access_ip:
        node['access_ip_version'] = 4 if atype == 'ipv4' else 6
    node['interfaces'] = []
    nets = machine_type.get('nets', [])
    for net in nets:
        atype = nf.get(net, {}).get('preferred_address', default_atype)
        access_ip = get_addr_for(build_slice,
                                 NAME_TO_ID[net], offset, atype=atype)
        node['interfaces'].append({
            'bridge': get_br_name(build_slice, net),  # del
            'network': net,
            'mac': get_mac_for(build_slice, NAME_TO_ID[net], offset),
            'ip_version': 4 if atype == 'ipv4' else 6,
            'ipv4_addr': get_addr_for(build_slice,
                                      NAME_TO_ID[net], offset, atype='ipv4'),
            'access_ip': access_ip})
    dev = 'vda'
    # TODO: add (back) the ability to select older version from the slot
    disks = machine_type.get('disks', {})
    dlist = []
    for d, v in disks.items():
        v['name_ref'] = d
        dlist.append(v)
    dlist = sorted(dlist, key=lambda x: x['name_ref'])
    counter = ord('a')
    node['disks'] = []
    live_path = get_path('live')
    for d in dlist:
        dev = 'vd' + chr(counter)
        counter += 1
        node['disks'].append({'size': d.get('size', '10G'),
                              'image_slot': d.get('image_slot', None),
                              'path': os.path.join(
            live_path, '-'.join((vm_uuid, dev)))})
    return node


NODES = []


def generate_nodes(build_slice, request):
    offset = 2
    for mtype, num in request.items():
        for _ in range(num):
            NODES.append(generate_node(build_slice, offset, mtype))
            offset += 1
    return NODES


def generate_ipmi_instack(machines, build_slice, target_file):
    instack_nodes = []
    for n in machines:
        node = {}
        node['name'] = n['hostname']
        node['mac'] = [get_mac_for(build_slice, 0x0, n['offset'])]
        node['cpu'] = n['vcpu']
        node['memory'] = n['memory']
        node['disk'] = n['disk_size'] if 'disk_size' in n else '20G'
        node['arch'] = 'x86_64'  # TODO support emulted arch and other archs
        node.update(bmc_creds(build_slice, n['offset']))
        instack_nodes.append(node)
    instack_data = {'nodes':  instack_nodes}
    with open(target_file, 'w') as outfile:
        json.dump(instack_data, outfile, indent=4, sort_keys=True)


# ssh alias file
def generate_ssh_config(machines, common_opts, target_file):
    stream = open(target_file, 'w')
    priv_key = get_path('keys') + SSH_PRIVATE_KEY_PATH_REL
    for m in machines:
        stream.write(('Host {name}\n'
                      '    HostName {ip}\n'
                      '    User stack\n'
                      '    IdentityFile {id_file}\n').format(name=m['hostname'],
                                                             ip=m['access_ip'],
                                                             id_file=priv_key))
        for k, v in common_opts.items():
            stream.write('    %s %s\n' % (k, v))
    stream.close()


def get_username():
    return pwd.getpwuid(os.getuid())[0]


def get_host_ip():
    global get_host_ip
    host_ip = netutils.discover_default_route_src_addr()

    def get_hostip():
        return host_ip
    get_host_ip = get_hostip
    return host_ip


def process_request(build_slice, request):
    assigne_net_names_to_id()
    generate_nodes(build_slice, request)
    populate_reservations()

    wipe_slice(build_slice)
    conn = get_libvirt_conn()
    create_networks(conn, build_slice)
    boot_vms(NODES)
    # TODO persist pattern mux
    vbs_root = get_path()
    ssh_mux_base_path = os.path.join(vbs_root, 'ssh_mux', str(build_slice))
    os.makedirs(ssh_mux_base_path, exist_ok=True)
    os.chmod(ssh_mux_base_path, 0o1777)
    # switch to key value to be ssh conf friendly
    # TODO: move to some cfg file
    ssh_general_options = {'ForwardAgent': 'yes',
                           'ServerAliveInterval': 30,
                           'ServerAliveCountMax': 5,
                           'ConnectionAttempts': 32,
                           'ControlMaster': 'auto',
                           'ControlPersist': '15m',
                           'ControlPath':  ssh_mux_base_path + '/%r@%h:%p',
                           'StrictHostKeyChecking': 'no',
                           'UserKnownHostsFile': '/dev/null'}
    ansible_ssh_common_args = ' '.join('-o %s=%s' % (k, v)
                                       for k, v in ssh_general_options.items())
    # TODO: create variant with proxy command
    generate_ansible_inventory(NODES, {
        'ansible_ssh_user': 'stack',
        'ansible_ssh_common_args': ansible_ssh_common_args},
        'hosts-bs' + str(build_slice))

    generate_ansible_inventory_speedling(NODES, build_slice, {
        'ansible_ssh_user': 'stack',
        'ansible_ssh_common_args': ansible_ssh_common_args},
        'sl-hosts-bs' + str(build_slice))

    generate_ssh_config(NODES, ssh_general_options,
                        'sshconf-bs' + str(build_slice))

    # TODO: try to gen remote files, which respects the source's
    # ssh options regarding the the proxy server (host_ip)
    host_ip = get_host_ip()
    l_user = get_username()
    ssh_general_options['ProxyCommand'] = ('ssh -o StrictHostKeyChecking=no '
                                           '-o UserKnownHostsFile=/dev/null '
                                           '-o ConnectionAttempts=32 '
                                           ' -W %h:%p {usr}@{hst}').format(
                                               hst=host_ip, usr=l_user)
    del ssh_general_options['ControlPath']  # assume the remote has a better
    generate_ansible_inventory(NODES, {
        'ansible_ssh_user': 'stack',
        'ansible_ssh_common_args': ansible_ssh_common_args},
        'hosts-remote-bs' + str(build_slice))

    generate_ssh_config(NODES, ssh_general_options,
                        'sshconf-remote-bs' + str(build_slice))
    generate_ipmi_instack(NODES, build_slice,
                          'instackenv-' + str(build_slice) + '.json')
    # TODO: create instack.json from the blank nodes,


def queury_non_root():
    non_root = os.environ.get('SUDO_USER', 'root').strip()
    if non_root and non_root != 'root':
        return non_root
    try:
        non_root = localsh.ret('logname').strip()
        if non_root and non_root != 'root':
            return non_root
    except Exception:
        pass
    try:
        localsh.ret("who am i | awk '{print $1}'").strip()
        if non_root and non_root != 'root':
            return non_root
    except Exception:
        pass


def create_workspace():
    # consider adding other groups
    root = get_path() + os.path.sep
    dirs = ['downloads', 'library', '_base',
            'live', 'cd', 'log', 'keys']
    for d in dirs:
        os.makedirs(root + d, exist_ok=True)

    # is priv key exists
    base_key_path = get_path("keys")
    priv_key = base_key_path + SSH_PRIVATE_KEY_PATH_REL
    pub_keys = base_key_path + SSH_PUBLIC_KEY_LIST_PATH_REL
    if not os.path.isfile(priv_key):
        localsh.run("ssh-keygen -t rsa -b 4096 -P '' -f '{path}'".format(
            path=priv_key))
    if not os.path.isfile(pub_keys):
        localsh.run("ssh-keygen -y -f '{private'} > '{public}'".format(
            private=priv_key,
            public=pub_keys))
    non_root = queury_non_root()
    if non_root:
        localsh.run("chown {non_root} '{priv}'".format(non_root=non_root,
                                                       priv=priv_key))


# TODO: cli, some argparse thing ..
# virtbs wipe <slice_num>
# virtbs cycle <slice_num> type1:num1,type2:num2,type3:num3 --ssh_file <path>
# virtbs gc base [not_ref_for_days_float]
# virtbs gc images [not_ref_for_days_float]
# an image is referenced if one of its child referenced as well
# virtbs gc all [not_ref_for_days_float]
# not_ref_for_days_float defaults to 14 (or cfg value)

# provider for example centos_cloud_iamge
# --image_key provider:key --image-key provider2:key2

# TODO: create backref_dir <uuid_rf> , with back ref symbolink links
# (or use db)

# virtbs renew provider
# virtbs renew provider:key


image_download.flow_exportd = True
image_virt_customize.flow_exportd = True


class MyAppend(argparse.Action):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reset_dest = False

    def __call__(self, parser, namespace, values, option_string=None):
        if not self.reset_dest:
            setattr(namespace, self.dest, [])
            self.reset_dest = True
        getattr(namespace, self.dest).append(values)


def tag_image(slot, ref, new_refs):
    assert is_slot_exists(slot)
    dire = get_image_data_dir(slot)
    src = os.path.realpath(os.path.join(dire, ref))
    src = os.path.basename(src)
    for dst in new_refs:
        if os.sep in dst:
            print("/ is not allowed in tags", file=sys.stderr)
            sys.exit(4)
        cfgfile.ensure_sym_link(os.path.join(dire, dst), src)


def tagging(args):
    slot_name = args.slot_name
    origin = args.source
    tags = args.tags
    try:
        tags.remove(origin)
    except ValueError:
        pass
    if not tags:
        print("No distingueshed tag", file=sys.stderr)
        sys.exit(2)
    if not is_slot_exists(slot_name):
        print('Slot: "' + slot_name + '"does not exists', file=sys.stderr)
        sys.exit(3)
    tag_image(slot_name, origin, tags)


def gen_parser():
    parser = argparse.ArgumentParser(
        description='Virt Build Slices the VM manager')
    parser.add_argument('-s', '--slice',
                        help='Slice number to use',
                        type=int,
                        default=1)
    parser.add_argument('-c', '--config',
                        help=('Configuration file, multiple can be specified'
                              'the override each other in the specified order'),
                        action=MyAppend,
                        default=["virtbs/config.yaml"])
    parser.add_argument('-e', '--extra-string',
                        help='Overrides a configuration parameter '
                        ' with the string argument for'
                        ' example conf.machine_types.compute.foo=bar',
                        action='append',
                        default=[])
    parser.add_argument('-E', '--extra-literal',
                        help=('Overrides a configuration parameter '
                              ' with a python literal for example:'
                              ' -E conf.machine_types.compute.memory=42 OR'
                              ' -E conf.machine_types.compute.list=[42,13]'),
                        action='append',
                        default=[])
    # required=True not in py3.6
    sps = parser.add_subparsers(help='sub-command help', dest='command')
    sps.add_parser('wipe', help='Destroys the slice')
    cycle_parser = sps.add_parser('cycle',
                                  help='Destroys the slice, and creates a new one')
    cycle_parser.add_argument('matrix',
                              help="',' sperated list of machine matrixes from the config file")
    cycle_parser.add_argument('topology',
                              help=" machine_type:nr_instances, ..")

    tag_parser = sps.add_parser('tag', help='add tag(s) to an image in a given image slot')
    tag_parser.add_argument('slot_name',
                            help='The slot name from the flow table')
    tag_parser.add_argument('-s', '--source',
                            help='Original tag or image id',
                            default='default')
    tag_parser.add_argument('-r', '--recursive', help="Not Implemented,"
                            "also tag the pranet images",
                            action='store_true')
    tag_parser.add_argument('tags',
                            help='list of tags to be used,'
                            'the old tag will be overriden',
                            nargs='+')
    tagactive = sps.add_parser('tagactive', help='add tag(s) to an image(s) active in the slice (NotImplemented)')
    tagactive.add_argument('-r', '--recursive', help="Not Implemented,"
                           "also tag the pranet images")
    tagactive.add_argument('tags',
                           help='list of tags to be used,'
                           'the old tag will be overriden',
                           nargs='+')

    return parser


CONFIG = {}


def main():
    global CONFIG
    global image_flow_table
    global machine_types
    parser = gen_parser()
    args = parser.parse_args(sys.argv[1:])
    config = CONFIG = yaml.load(open(args.config[0]))
    for extra_cfg in args.config[1:]:
        extra = yaml.load(open(extra_cfg))
        util.dict_merge(config, extra)

    extra_mtype = {}
    for e in args.extra_string:
        (idenf, argstr) = e.split('=', 1)
        path = idenf.split('.')
        assert len(path) > 1
        if path[0] == 'conf':
            cfg = config
            for p in path[1:-1]:
                cfg = cfg.setdefault(p, {})
            cfg[path[-1]] = argstr
        elif path[0] == 'mtype':
            emtype = extra_mtype
            for p in path[1:-1]:
                emtype = emtype.setdefault(p, {})
            emtype[path[-1]] = argstr
        else:
            print('extras must start with conf. or mtype.', file=sys.stderr)
            sys.exit(2)

    for e in args.extra_literal:
        (idenf, argstr) = e.split('=', 1)
        path = idenf.split('.')
        assert len(path) > 1
        if path[0] == 'conf':
            cfg = config
            for p in path[1:-1]:
                cfg = cfg.setdefault(p, {})
            cfg[path[-1]] = ast.literal_eval(argstr)
        elif path[0] == 'mtype':
            emtype = extra_mtype
            for p in path[1:-1]:
                emtype = emtype.setdefault(p, {})
            emtype[path[-1]] = ast.literal_eval(argstr)
        else:
            print('extras must start with conf. or mtype.', file=sys.stderr)
            sys.exit(2)

    image_flow_table = config['image_flow']

    for rec, val in image_flow_table.items():
        driver = getattr(__main__, val['driver'])
        if not hasattr(driver, 'flow_exportd'):
            raise Exception('The driver ({}) not labled with '
                            ' "flow_exportd"'.format(val['driver']))
        val['driver'] = driver

    create_workspace()
    build_slice = args.slice
    if args.command == 'wipe':
        wipe_slice(build_slice)
    elif args.command == 'tag':
        tagging(args)
    elif args.command == 'cycle':
        to_mul = args.matrix.split(',')
        machine_types = config['machine_matrix'][to_mul[0]]
        for mul in to_mul[1:]:
            util.dict_merge(machine_types, config['machine_matrix'][mul])
        util.dict_merge(machine_types, extra_mtype)
        request = dict((a, int(b)) for (a, b)
                       in (l.split(':') for l in args.topology.split(',')))
        process_address_pool()
        process_request(build_slice, request)
    else:
        raise Exception('Valid commands are: wipe, cycle')


if __name__ == '__main__':
    main()
