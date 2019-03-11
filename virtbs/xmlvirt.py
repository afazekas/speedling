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
def generate_net_dev(iface):
    return """<interface type='bridge'>
      <source bridge='{bridge}'/>
      <model type='virtio'/>
      <mac address='{mac}'/>
      </interface>""".format(**iface)


def generate_dev_console(vm_uuid, path):
    return """<console type='file'>
      <source path='{path}'/>
      <target type='serial' port='0'/>
    </console>""".format(vm_uuid=vm_uuid, path=path)


# TODO extra_devs arg net/disk/console array (ordered)
# TODO vnc has password arg
def generate_libvirt_dom_xml(node):
    # TODO: other arch
    # No good reasion for using a real xml library for creating this part
    # at least for now
    dev_txts = []
    if 'console_log' in node:
        dev_txts.append(generate_dev_console(node['vm_uuid'],
                                             node['console_log']))
    if 'config_drive' in node:
        dev_txts.append(generate_iso_disk(node['config_drive']))
    if 'interfaces' in node:
        for ifs in node['interfaces']:
            dev_txts.append(generate_net_dev(ifs))
    if 'disks' in node:
        counter = ord('a')
        for disk in node['disks']:
            dev = 'vd' + chr(counter)
            dev_txts.append(generate_qcow2_disk(disk['path'], dev))
            counter += 1

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
""".format(vm_uuid=node['vm_uuid'], memory=node['memory'], vcpu=node['vcpu'],
           dev_str=dev_str, name=node['virt_domain_name'])


def netxml_from(base):
    reservations = base.get('reservations', {})
    internet_access = base.get('internet_access', False)
    address_serving_4 = reservations.get(4, [])
    address_serving_6 = reservations.get(6, [])
    mtu = base.get('mtu', 9090)
    x = []
    x.append("""<network ipv6='yes'>
<name>{net_name}</name>"
<mtu size='{mtu}'/>
<bridge name='{net_name}' stp='off'/>""".format(net_name=base['name'], mtu=mtu))
    if internet_access:
        x.append("<forward mode='nat'/>")
    if address_serving_4:
        x.append("""<mac address='{mac}'/>
<ip address='{ip}' netmask='{mask}'>
<dhcp>""".format(mac=base['mac'], mask=base['ipv4_mask'],
                 ip=base['ipv4_address']))

        for entry in address_serving_4:
            s = "<host mac='{mac}' name='{name}' ip='{ip}'/>".format(**entry)
            x.append(s)
        x.append('</dhcp>\n</ip>')

    if address_serving_6:
        # dhcp_ip = base['ipv4_address']
        # dhcp6_ip = base['ipv6_address']
        x.append("""<mac address='{mac}'/>
<ip family="ipv6" address="{ip}" prefix="{prefix}"/>
<dhcp>""".format(mac=base['mac'], mask=base['ipv6_prefix'],
                 ip=base['ipv6_address']))

        for entry in address_serving_6:
            s = ("<host family='ipv6' mac='{mac}'"
                 " name='{name}' ip='{ip}'/>".format(**entry))
            x.append(s)
        x.append('</dhcp>\n</ip>')

    x.append('</network>')
    return '\n'.join(x)
