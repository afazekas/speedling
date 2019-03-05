from __future__ import print_function
import libvirt
import re
import sys
import os
import os.path
import yaml
try:
    from shlex import quote as cmd_quote
except ImportError:
    from pipes import quote as cmd_quote

# WARNING: improper shell escapes, evil guy can be evil!
from speedling import localsh
from speedling import cfgfile
from speedling import netutils
from speedling import fetch
from speedling import util

import uuid
import errno
import pwd
import grp
import argparse

import __main__

try:
    import virtualbmc.manager as vbmc
    VIRTBMC_ENABLED = True
except ImportError:
    VIRTBMC_ENABLED = False

from collections import defaultdict
from collections import OrderedDict

import xml.etree.ElementTree as ET
# is ujson still the fastest ?
# anyjson did not had dump
import json


# TODO: init selinux perm
# TODO: create Makefile instead of setup.py
# NOTE: libvirt is not on pypi

# TODO kill ssh DNS , kill requiretty (both my image and cloud init)

DATA_PATH = '/srv/virtbs'
FETCH_PATH = os.path.join(DATA_PATH, 'downloads')
FLOATING_IMAGES = os.path.join(DATA_PATH, 'library')
BASE_IMG_PATH = os.path.join(DATA_PATH, '_base')  # raw, sparse

LIVE_ROOT_PATH = os.path.join(DATA_PATH, 'live')  # live root disks,

CONFIG_DRIVE_PATH = os.path.join(DATA_PATH, 'cd')

# unconfined_u:object_r:virt_log_t:s0
LOG_PATH = os.path.join(DATA_PATH, 'log')

KEYS_PATH = os.path.join(DATA_PATH, 'keys')

HOME_PATH = os.environ.get('HOME', '/root')

SSH_PUBLIC_KEY_LIST_PATH = KEYS_PATH + '/id_rsa.pub'  # ro file, can have multiple keys

SSH_PRIVATE_KEY_PATH = KEYS_PATH + '/id_rsa'  # ro file


IMAGE_OWNER = pwd.getpwnam('qemu').pw_uid
IMAGE_GROUP = grp.getgrnam('qemu').gr_gid

# it will be an option, default to 1
# valid from 0 .. F
# build-slice
# NOTE: maybe it can work up to 254 (FE) now
BUILD_SLICE = 0x1

# Do not let the virtbs machine on the same L2,
# with the same mac range without nat ;-)

# reserving 2^24 mac address
# 2 hexdigit, net offset
# 2 hexdig for slice_num +1
# 2 hexdif for 00 reserved, 01 router, others machine offsets
# and floating ips , FF reserved (brodcast)
MAC_PREFIX = '52:54:00'

# TODO: reserve /24 for other nets
# 00: mynet
# xx: other net (so we can have 255 net per slice)

# ip range / 16 , first 16 bit, 2^16 address
# last 16 bit is the same as wilt mac
IPV4_PREFIX = '172.16'

# This is just managemnt ip range,
# we are not allocating for the others

# TODO: allow to have more host per slice, on the
# cose of lest slices . ~ 0xF slice 0XFFF host
# maybe ot was the old plan I just forgotten ;-)

NETMASK = '255.255.255.0'

# We had to limit the max number of machines in order
# fit into the port range
# assume max 16 slince with eaxh max 255 host
BMC_PORT_BASE = 8192


def bmc_port(bslice, offset):
    return BMC_PORT_BASE + bslice * 256 + offset


def generate_iso_disk(image_file):
    return """<disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{image_file}' cache='unsafe'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>""".format(image_file=image_file)


# is the dev name respected today at least for pci rel addr ?
def generate_qcow2_disk(image_file, dev='vda'):
    return """<disk type='file' device='disk'>
      <driver name='qemu' cache='unsafe' type='qcow2'/>
      <source file='{image_file}'/>
      <target dev='{dev}' bus='virtio'/>
    </disk>""".format(image_file=image_file, dev=dev)


# expected to be defined and have bridge with the same name
def generate_net_dev(mac, brname='bs0mynet'):
    return """<interface type='bridge'>
      <source bridge='{brname}'/>
      <model type='virtio'/>
      <mac address='{mac}'/>
      </interface>""".format(mac=mac, brname=brname)


def generate_dev_console(vm_uuid):
    return """<console type='file'>
      <source path='{path}/{vm_uuid}-console.log'/>
      <target type='serial' port='0'/>
    </console>""".format(vm_uuid=vm_uuid, path=LOG_PATH)


# TODO extra_devs arg net/disk/console array (ordered)
# TODO vnc has password arg
def generate_libvirt_dom_xml(vm_uuid, name, memory, vcpu, dev_txts):
    # No good reasion for using a real xml library for creating this part
    # at least for now
    # vm_uuid an uuid string
    # Memory integer KiB
    # vcpu integer number of vcpus
    # nets_xml_txt part for nets
    # disk_xml_part part for root, data, config drive
    dev_str = '\n'.join(dev_txts)
    return """<domain type='kvm'>
  <name>{name}</name>
  <memory unit='MiB'>{memory}</memory>
  <vcpu placement='static'>{vcpu}</vcpu>
  <sysinfo type='smbios'>
    <system>
      <entry name='product'>VirtBS Compute</entry>
      <entry name='uuid'>{vm_uuid}</entry>
    </system>
  </sysinfo>
  <os>
    <type arch='x86_64'>hvm</type>
    <boot dev='hd'/>
    <smbios mode='sysinfo'/>
  </os>
  <features>
    <acpi/>
    <apic/>
  </features>
  <cpu mode='host-passthrough'>
    <model fallback='allow'/>
  </cpu>
  <clock offset='utc'>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='hpet' present='no'/>
  </clock>
  <devices>
    {dev_str}
    <rng model='virtio'>
      <backend model='random'>/dev/urandom</backend>
    </rng>
    <controller type='usb' index='0'>
      <alias name='usb'/>
    </controller>
    <controller type='pci' index='0' model='pci-root'>
      <alias name='pci.0'/>
    </controller>
    <input type='tablet' bus='usb'>
      <alias name='input0'/>
      <address type='usb' bus='0' port='1'/>
    </input>
    <input type='mouse' bus='ps2'>
      <alias name='input1'/>
    </input>
    <input type='keyboard' bus='ps2'>
      <alias name='input2'/>
    </input>
    <graphics type='vnc' port='-1' autoport='yes'
              listen='0.0.0.0' keymap='en-us'/>
    <video>
      <model type='cirrus' vram='16384' heads='1' primary='yes'/>
      <alias name='video0'/>
    </video>
  </devices>
</domain>
""".format(vm_uuid=vm_uuid, memory=memory, vcpu=vcpu,
           dev_str=dev_str, name=name)
# TODO consolelog dir..

# TODO loop + allocator, updater ..
# in the example ip mac last 3 byte matches, it can be kept


def qcow2_to_raw(src, dst):
    localsh.run("qemu-img convert -f qcow2 -O raw -S 4k {src} {dst}".format(
        src=cmd_quote(src), dst=cmd_quote(dst)))
    os.chown(dst, IMAGE_OWNER, IMAGE_GROUP)


def image_info(img):
    jdata = localsh.ret("qemu-img info --output=json " + cmd_quote(img))
    return json.loads(jdata)


def get_virtual_size(img):
    i = image_info(img)
    return i['virtual-size']


RE_HUMAN_SIZE = re.compile('(\d+)(.*)')
UNITS = {'k': 1024, 'm': 2**20, 'g': 2**30, 't': 2**40, 'p': 2**50, 'e': 2**60}


def human_byte_to_int(human_str):
    s = human_str.strip()
    m = RE_HUMAN_SIZE.search(s)
    si = int(m.group(1))
    u = m.group(2).strip()[0].lower()
    return si * UNITS[u]


def create_backed_qcow2(src, dst, size='10G', bfmt='raw'):
    # the args are not shell escaped
    if size:
        s = human_byte_to_int(size)
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
    os.chown(dst, IMAGE_OWNER, IMAGE_GROUP)


# NOTE: It may default to sparse raw in the future
def create_empty_disk(dst, size, fmt='qcow2'):
    if fmt == 'raw':
        localsh.run("truncate -s {size} {dst}".format(
          fmt=fmt, dst=dst))
    else:
        localsh.run("qemu-img create -f {fmt} '{dst}' '{size}'".format(
          fmt=fmt, dst=dst, size=size))
        # to py ?
    os.chown(dst, IMAGE_OWNER, IMAGE_GROUP)


# TODO: respect NETMASK
# offset 0 reserved
# offset 1 router ip
# max offset is reserved for  brodacast
def get_mac_for(build_slice, net_id, offset):
    return '{MAC_PREFIX}:{bslice:02x}:{net_id:02x}:{offset:02x}'.format(
            MAC_PREFIX=MAC_PREFIX,
            bslice=build_slice, net_id=net_id, offset=offset)


def get_ipv4_for(build_slice, net_id, offset):
    # net_id unused
    return '{IPV4_PREFIX}.{bslice}.{offset}'.format(
            IPV4_PREFIX=IPV4_PREFIX,
            bslice=build_slice, net_id=net_id, offset=offset)


# normally it is just for the machines to cumminicate with themself
# set to mtu to something high, and do not tell to the machines ;-)
def create_blank_net(conn, build_slice, net_id, name):
    mac = get_mac_for(build_slice, net_id, 1)
    net_name = 'bs{build_slice}{name}'.format(build_slice=build_slice,
                                              name=name)
    network = """<network>
    <name>{net_name}</name>
    <mtu size='9050'/>
    <bridge name='{net_name}' stp='off'/>
    <mac address='{mac}'/>
    </network>""".format(net_name=net_name,
                         mac=mac)
#  TODO: optinal transient   conn.networkCreateXML(network)
    net = conn.networkDefineXML(network)
    net.setAutostart(True)
    net.create()


def create_my_net(conn, build_slice, hosts):
    # TODO This net must support one vlan with mtu 9000
    net_name = 'bs{build_slice}mynet'.format(build_slice=build_slice)
    reser = []
    for data in hosts:
        offset = data['offset']
        if data.get('blank', False):  # blank machines, not managed by our dhcp
            continue
        mac = get_mac_for(build_slice, 0, offset)
        ip = get_ipv4_for(build_slice, 0, offset)

        name = data.get('hostname', None)
        if not name:
            group = data.get('hostgroup', 'host')
            name = '{}-{:02x}'.format(group, offset)

        reser.append(("<host mac='{mac}' "
                      "name='{name}' "
                      "ip='{ip}'/>'").format(ip=ip, name=name, mac=mac))

    reservation = '\n'.join(reser)
    mac = get_mac_for(build_slice, 0, 1)
    ip = get_ipv4_for(build_slice, 0, 1)
    network = """<network>
    <name>{net_name}</name>
    <forward mode='nat'/>
    <bridge name='{net_name}'/>
    <mac address='{mac}'/>
    <ip address='{ip}' netmask='{mask}'>
    <dhcp>
        {reservation}
    </dhcp>
    </ip>
    </network>""".format(net_name=net_name,
                         reservation=reservation, ip=ip,
                         mac=mac, mask=NETMASK)

    conn.networkCreateXML(network)


def virt_domain_name(build_slice, hostname):
    return 'bs{build_slice:x}-{name}'.format(
            build_slice=build_slice, name=hostname)


# TODO: clean on exception
# TODO: add support for direct kernel/initrd inject boot
# TODO: add image creation step for fetching kernels
#       and initrd
def bootvm(conn, base_image, hostname, build_slice, offset,
           memory=8192,
           vcpu=4,
           disk_size='20G',
           extra_disks=(),
           extra_net_mac_brs=(), **irrelevant):
    # base_image sparse raw
    # assume uuids not collideing! ;-)
    # TODO: assume clock and rnd gen does not works ;-)
    # extra disks are not formated!
    vm_uuid = str(uuid.uuid4())

    net_name = get_br_name(build_slice, 'mynet')
    virt_dom_name = virt_domain_name(build_slice, hostname)
    # TODO: do it before
    cfgfile.ensure_path_exists(LIVE_ROOT_PATH, owner=IMAGE_OWNER,
                               group=IMAGE_GROUP, mode=0o755)
    root_image = os.path.join(LIVE_ROOT_PATH, vm_uuid + '-vda')
    mac = get_mac_for(build_slice, 0x0, offset)
    nets_xml_txt = generate_net_dev(mac, net_name)
    if base_image:
        create_backed_qcow2(base_image, root_image, disk_size)
    else:
        create_empty_disk(root_image, disk_size)
    disk_xml_txt = generate_qcow2_disk(root_image)

    console_xml_txt = generate_dev_console(vm_uuid)
    devs = [nets_xml_txt, disk_xml_txt, console_xml_txt]
    if base_image:  # blank images does not gets config drive as well
        ssh_keys = open(SSH_PUBLIC_KEY_LIST_PATH, 'r').readlines()
        ssh_keys = [line.strip() for line in ssh_keys
                    if not line.startswith('#')]
        iso = create_cloud_config_image(ssh_keys, vm_uuid, hostname)
        devs.append(generate_iso_disk(iso))
    offset = 1
    for size in extra_disks:
        dev = 'vd' + chr(ord('a') + offset)
        disk_path = os.path.join(LIVE_ROOT_PATH, '-'.join((vm_uuid, dev)))
        create_empty_disk(disk_path, size)
        devs.append(generate_qcow2_disk(disk_path, dev=dev))
        offset += 1
    # add slice prefix ??
    for e_mac, e_br_name in extra_net_mac_brs:
        devs.append(generate_net_dev(e_mac, e_br_name))

    domain_xml = generate_libvirt_dom_xml(vm_uuid,
                                          virt_dom_name,
                                          memory,
                                          vcpu, devs, )
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
def create_cloud_config_image(ssh_keys, vm_uuid, hostname):
    ci = '#cloud-config\n' + json.dumps({
         'users': [{'name': 'stack',
                    'ssh-authorized-keys': ssh_keys,
                    'sudo': 'ALL=(ALL) NOPASSWD:ALL',
                    'lock-passwd': True}]})
    cif = os.path.join(CONFIG_DRIVE_PATH, 'user-data-' + vm_uuid)
    mif = os.path.join(CONFIG_DRIVE_PATH, 'meta-data-' + vm_uuid)
    target = os.path.join(CONFIG_DRIVE_PATH, vm_uuid + '.iso')
    mi = ("instance-id: {vm_uuid}\nhostname: {hostname}\n"
          "local-hostname: {hostname}\n").format(hostname=hostname,
                                                 vm_uuid=vm_uuid)

    cfgfile.content_file(cif, ci)
    cfgfile.content_file(mif, mi)
    file_map = [('user-data', cif), ('meta-data', mif)]
    files_to_iso(file_map, target)
    return target


def get_libvirt_conn():
    conn = libvirt.open(None)
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


def fork_no_wait(*args, **kwargs):
    pid = os.fork()
    if not (pid):
        conn = get_libvirt_conn()
        bootvm(conn, *args, **kwargs)
        domain_name = virt_domain_name(kwargs['build_slice'],
                                       kwargs['hostname'])
        if VIRTBMC_ENABLED:
            vbmc_manager = vbmc.VirtualBMCManager()
            vbmc_manager.add(username='admin',
                             password='password',
                             port=bmc_port(kwargs['build_slice'], kwargs['offset']),
                             address='::',
                             domain_name=domain_name,
                             libvirt_uri='qemu:///system',
                             libvirt_sasl_username=None,
                             libvirt_sasl_password=None)
            vbmc_manager.start(domain_name)
        exit(0)
    return pid


# ~ 0.3 sec in waiting for libvirt
# fork helps, unless we wait for machined?
def boot_vms(machines, build_slice):
    boots = {}
    for machine in machines:
        # mechine migh have more arge, like ip address in the future
        # which might not need to passed here
        pid = fork_no_wait(build_slice=build_slice, **machine)
        boots[pid] = machine
    for p in boots.keys():
        (pid, status) = os.waitpid(p, 0)
        if status:  # not just the 8 bit exit code!
            print("Failed to boot pid: {pid}, status:"
                  " {status}, params: {params}".format(pid=pid, status=status,
                                                       params=machine),
                  file=sys.stderr)


# class or not to class this is the question ;-) ,not
# looks like it will be enough complex to switch to classes ,and to some `real`
# db  (leveldb ?)
def get_image_data_dir(libname):
    lib_dir = os.path.join(FLOATING_IMAGES, libname)
    cfgfile.ensure_path_exists(lib_dir, owner=IMAGE_OWNER,
                               group=IMAGE_GROUP, mode=0o755)
    return lib_dir


def _file_sha256_sum(for_sum):
    # compare this with some built in function
    return localsh.ret("sha256sum '{}'".format(
                       for_sum)).split(" ")[0]


# not genrally usable, yet!
# not fault and insane tolerant!
# not parallel safe!
def image_download(name, data, key=None, renew=False):
    build_id = str(uuid.uuid4())
    if not key and 'version_key' in data:
        key = data['version_key']
    img_dir = get_image_data_dir(name)
    default_path = os.path.join(img_dir, 'default' if not key
                                else 'default-' + key)

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
    download_path = os.path.join(FETCH_PATH, os.path.basename(url))
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
            localsh.run(("cd '{FETCH_PATH}'; tar -xf "
                        "'{download_path}' '{in_file}'; "
                         "cp --sparse=always '{in_file}' '{work_file}' "
                         "; rm '{in_file}'").format(
                        FETCH_PATH=FETCH_PATH, download_path=download_path,
                        in_file=in_file))
    else:
        localsh.run(("cp --sparse=always "
                    "'{download_path}' '{work_file}'").format(
                download_path=download_path, work_file=work_file))
    tmp_link = work_file + '-lnk'
    os.symlink(work_file, tmp_link)
    os.rename(tmp_link, default_path)
    return (data['fmt'], work_file)


# TODO: replace arg str to array , use proper escape
def virt_costumize_script(image, script_file, args_str, log_file=None):
    # NOTE: cannot specify a constant destination file :(
    # --copy-in {script_file}:/root/custumie.sh did not worked
    # LIBGUESTFS_BACKEND=direct , file permissionsss
    base_name = os.path.basename(script_file)
    (r, log) = localsh.run_log(("LIBGUESTFS_BACKEND=direct "
                                "virt-customize --verbose --add {image} "
                                "--memsize 1024 "
                                "--copy-in {script_file}:/root/ "
                                "--chmod 755:/root/{base_name} "
                                "--run-command '/root/{base_name} {args_str}' "
                                "--selinux-relabel ").format(
                                image=image, script_file=script_file,
                                base_name=base_name,
                                args_str=args_str))
    print(log)
    if log_file:
        f = open(log_file, "w")
        f.write(log)
        f.close()
    if r:
        raise Exception("virt_costumize Failed")


# incomplete, temporary
# these dics will be originated from file and will be json friendy
def __filter_to_json(d):
    di = {}
    for k, v in d.items():
        if callable(v):
            if hasattr(v, '__name__'):
                di[k] = v.__name__
            else:
                di[k] = str(v)
    return di


def image_virt_customize(name, data, key=None, renew=False):
    # TODO: we do not really want to have _base_image
    # to convert to raw and move it to the _base and mage the gc more complex
    location = base_image(data['base_slot'],
                          data.get('base_version_key', None))
    # TODO: same name uniquie magic
    # TODO: build lock
    # TODO: parallel safe build
    img_dir = get_image_data_dir(name)
    default_path = os.path.join(img_dir, 'default' if not key
                                         else 'default-' + key)
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
    cfgfile.content_file(script_file, script)
    args = ""
    if key:
        args = '--key ' + key
    print('Customizing image for {} at {}'.format(name, build_image))
    virt_costumize_script(build_image, script_file, args, build_log)

    di = __filter_to_json(data)
    cfgfile.content_file(build_image + '-data.json', json.dumps(di))
    tmp_link = build_image + '-lnk'
    os.symlink(build_image, tmp_link)
    os.rename(tmp_link, default_path)
    return ('qcow2', build_image)


# NOTE: we might need to handle more version key kind values
image_flow_table = {}


# default_alg: instead of version_key execute the named function
def base_image(image_type, version_key=None):
    # reqursively does the build steps to reach a valid image
    (fmt, image) = image_flow_table[image_type]['driver'](
                                                image_type,
                                                image_flow_table[image_type],
                                                version_key)
    if fmt == 'qcow2':
        # WARNING: assumes globaly uniquie name
        base_file = os.path.basename(image)
        base_path = os.path.join(BASE_IMG_PATH, base_file)
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
    for disk in root.find('devices').findall('disk'):
        filepath = disk.find('source').get('file')
        if filepath.startswith(LIVE_ROOT_PATH):
            to_del.append(filepath)
        elif filepath.startswith(CONFIG_DRIVE_PATH):
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
        except:
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
            # TODO: Make it parallel, make sure it does not stalls at hign vm count
            wipe_domain_by_uuid(UUID)

    conn = get_libvirt_conn()
    for net in conn.listAllNetworks():
        if net.name().startswith(ss):
            try:
                net.destroy()
                net.undefine()
            except libvirt.libvirtError as e:
                print(net.name() + str(e))
    # TODO: fork the vbmc handling
    if VIRTBMC_ENABLED:
        vbmc_manager = vbmc.VirtualBMCManager()
        doms = vbmc_manager.list()  # maybe just listing the .vbmc dir would be enough
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
def generate_ansible_inventory(machines, common_opts, target_file):
    stream = open(target_file, 'w')
    group_members = defaultdict(list)
    stream.write('localhost ansible_connection=local\n')
    group_members['local'].append('localhost')
    for m in machines:
        if not m['blank']:
            assert m['inventory_group'] not in {'local', 'virtbs'}
            group_members[m['inventory_group']].append(
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
    #    print(machines)
    stream = open(target_file, 'w')
    group_members = defaultdict(list)
    stream.write('localhost ansible_connection=local\n')
    group_members['local'].append('localhost')
    for m in machines:
        mac = get_mac_for(build_slice, 0x0, m['offset'])
        # net_name = get_br_name(build_slice, 'mynet')
        networks = {'access': {'if_lookup': {'mac': mac}, 'addresses': [m['access_ip']]}}
        if m['extra_net_mac_brs']:
            (emac, ebr) = m['extra_net_mac_brs'][0]
            addr = get_ipv4_for(build_slice, 1, m['offset'])  # 1 is incorrect, registry must be more visible
            networks['extra'] = {'if_lookup': {'mac': emac}, 'addresses': [addr]}
        if not m['blank']:
            assert m['inventory_group'] not in {'local', 'virtbs'}
            group_members[m['inventory_group']].append(
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
    for m in machines:
        stream.write(('Host {name}\n'
                      '    HostName {ip}\n'
                      '    User stack\n'
                      '    IdentityFile {id_file}\n').format(name=m['hostname'],
                                                             ip=m['access_ip'],
                                                             id_file=SSH_PRIVATE_KEY_PATH))
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


def process_request(build_slice, machine_types, request):
    net_offset_high = 1
    net_reserved = {}  # {'mynet': 0}
    offset = 2
    unrolled = []
    for group, num in request.items():
        for _ in range(num):
            machine = machine_types[group].copy()  # deep ?
            unrolled.append(machine)
            machine['hostgroup'] = group
            # without bs
            # with bigger mask we will have more digits
            machine['hostname'] = '{}-{:02x}'.format(group, offset)
            machine['offset'] = offset
            machine['extra_net_mac_brs'] = []
            for net in machine.get('extra_nets', []):
                assert net != 'mynet'
                if net in net_reserved:
                    net_offset = net_reserved[net]
                else:
                    net_reserved[net] = net_offset = net_offset_high
                    net_offset_high += 1

                e_mac = get_mac_for(build_slice, net_offset, offset)
                e_br = get_br_name(build_slice, net)
                machine['extra_net_mac_brs'].append((e_mac, e_br))
            if 'base_image' not in machine:
                if 'image' in machine:
                    image_type = machine['image']
                    vk = machine.get('version_key', None)
                    machine['base_image'] = base_image(image_type,
                                                       version_key=vk)
                elif not machine.get('blank', False):
                    raise Exception('Non balnk machine without base_image or '
                                    'image ')
                else:
                    machine['base_image'] = None
            if 'blank' not in machine:
                machine['blank'] = False
            machine['access_ip'] = get_ipv4_for(build_slice, 0, offset)
            if 'inventory_group' not in machine:
                machine['inventory_group'] = machine['hostgroup']
            offset += 1
    wipe_slice(build_slice)
    conn = get_libvirt_conn()
    create_my_net(conn, build_slice, unrolled)
    for name, net_id in net_reserved.items():
        create_blank_net(conn, build_slice, net_id, name)
    boot_vms(unrolled, build_slice)
    # TODO persist pattern mux
    ssh_mux_base_path = os.path.join(DATA_PATH, 'ssh_mux', str(build_slice))
    cfgfile.ensure_path_exists(ssh_mux_base_path, owner=IMAGE_OWNER,
                               group=IMAGE_GROUP, mode=0o1777)
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
    generate_ansible_inventory(unrolled, {
                        'ansible_ssh_user': 'stack',
                        'ansible_ssh_common_args': ansible_ssh_common_args},
                               'hosts-bs' + str(build_slice))

    generate_ansible_inventory_speedling(unrolled, build_slice, {
                        'ansible_ssh_user': 'stack',
                        'ansible_ssh_common_args': ansible_ssh_common_args},
                               'sl-hosts-bs' + str(build_slice))

    generate_ssh_config(unrolled, ssh_general_options,
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
    generate_ansible_inventory(unrolled, {
                        'ansible_ssh_user': 'stack',
                        'ansible_ssh_common_args': ansible_ssh_common_args},
                               'hosts-remote-bs' + str(build_slice))

    generate_ssh_config(unrolled, ssh_general_options,
                        'sshconf-remote-bs' + str(build_slice))
    generate_ipmi_instack(unrolled, build_slice,
                          'instackenv-' + str(build_slice) + '.json')
    # TODO: create instack.json from the blank nodes,


# process_request(1,  machine_types, request)
def queury_non_root():
    non_root = os.environ.get('SUDO_USER', 'root').strip()
    if non_root and non_root != 'root':
        return non_root
    try:
        non_root = localsh.ret('logname').strip()
        if non_root and non_root != 'root':
            return non_root
    except:
        pass
    try:
        localsh.ret("who am i | awk '{print $1}'").strip()
        if non_root and non_root != 'root':
            return non_root
    except:
        pass


def create_workspace():
    # consider adding other groups
    dirs = [DATA_PATH, FETCH_PATH, FLOATING_IMAGES, BASE_IMG_PATH,
            LIVE_ROOT_PATH, CONFIG_DRIVE_PATH, LOG_PATH, KEYS_PATH]
    for d in dirs:
        cfgfile.ensure_path_exists(d, owner=IMAGE_OWNER,
                                   group=IMAGE_GROUP, mode=0o755)

    # is priv key exists
    if not os.path.isfile(SSH_PRIVATE_KEY_PATH):
        localsh.run("ssh-keygen -t rsa -b 4096 -P '' -f '{path}'".format(path=SSH_PRIVATE_KEY_PATH))
    if not os.path.isfile(SSH_PUBLIC_KEY_LIST_PATH):
        localsh.run("ssh-keygen -y -f '{private'} > '{public}'".format(private=SSH_PRIVATE_KEY_PATH,
                    public=SSH_PUBLIC_KEY_LIST_PATH))
    non_root = queury_non_root()
    if non_root:
        localsh.run("chown {non_root} '{priv}'".format(non_root=non_root,
                                                       priv=SSH_PRIVATE_KEY_PATH))


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


def gen_parser():
    parser = argparse.ArgumentParser(
                      description='Virt Build Slices the VM manager')
    parser.add_argument('-s', '--slice',
                        help='Receiver mode act on remote host',
                        type=int,
                        default=1)
    parser.add_argument('-c', '--config',
                        help='Receiver mode act on remote host',
                        default="virtbs/config.yaml")
    sps = parser.add_subparsers(help='sub-command help', dest='command', required=True)
    sps.add_parser('wipe', help='Destroys the slice')
    cycle_parser = sps.add_parser('cycle', help='Destroys the  slice, and creates a new one')
    cycle_parser.add_argument('matrix', help="',' sperated list of machine matrxes from the config file")
    cycle_parser.add_argument('topology', help=" machin_type:nr_instances, ..")
    return parser


def main():
    global image_flow_table
    global machine_types
    parser = gen_parser()
    args = parser.parse_args(sys.argv[1:])
    config = yaml.load(open(args.config))
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
    elif args.command == 'cycle':
        to_mul = args.matrix.split(',')
        machine_types = config['machine_matrix'][to_mul[0]]
        for mul in to_mul[1:]:
            util.dict_merge(machine_types, config['machine_matrix'][mul])

        request = OrderedDict((a, int(b)) for (a, b)
                              in (l.split(':') for l in args.topology.split(',')))
        process_request(build_slice,  machine_types, request)


if __name__ == '__main__':
    main()
