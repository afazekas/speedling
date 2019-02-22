from speedling import util
from speedling import inv
from speedling import sl
import __main__
from speedling import facility
from speedling.srv import rabbitmq
from speedling.srv import mariadb

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
from osinsutils import glb
import logging

LOG = logging.getLogger(__name__)

# WARNING NOT FINISHED


def etc_ironic_ironic_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url(),
                'enabled_drivers': 'fake, pxe_ipmitool'},  # WARN fake
    'database': {'connection': mariadb.db_url('ironic')},
    'keystone_authtoken': util.keystone_authtoken_section('ironic_auth'),
}


def ironic_etccfg(services, global_service_union):
    usrgrp.group('ironic')
    usrgrp.user('ironic', 'ironic')
    util.base_service_dirs('ironic')
    cfgfile.ini_file_sync('/etc/ironic/ironic.conf', etc_ironic_ironic_conf(),
                          owner='ironic', group='ironic')
    util.unit_file('openstck-ironic-api',
                   '/usr/local/bin/ironic-api',
                   'ironic')
    util.unit_file('openstck-ironic-conductor',
                   '/usr/local/bin/ironic-conductor',
                   'ironic')


def do_local_ironic_service_start():
    selected_services = inv.get_this_inv()['services']

    srvs = []
    if 'ironic-api' in selected_services:
        srvs.append('openstack-ironic-api.service')

    if 'ironic-conductor' in selected_services:
        srvs.append('openstack-ironic-conductor.service')

    srvs = [sl.UNIT_PREFIX + x for x in srvs]
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def do_ironic_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('ironic')


i_srv = {'ironic-api', 'ironic-conductor'}


def task_ironic_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    irons = inv.hosts_with_service('ironic-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = irons.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_ironic_db)
    inv.do_do(inv.hosts_with_any_service(i_srv), do_local_ironic_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def ironic_pkgs():
    return {'ipmitool', 'iptables', 'ipxe-bootimgs', 'gnupg', 'libguestfs',
            'libguestfs-tools', 'libvirt', 'libvirt-python', 'qemu-system-x86',
            'net-tools', 'openssh-clients', 'openvswitch', 'sgabios',
            'shellinabox', 'syslinux', 'tftp-server', 'xinetd',
            'squashfs-tools', 'libvirt-devel', 'socat', 'ipxe-roms-qemu', 'jq'}


def register():
    ironic_component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'ironic',
      'pkg_deps': ironic_pkgs,
      'cfg_step': ironic_etccfg,
      'goal': task_ironic_steps
    }
    facility.register_component(ironic_component, i_srv)

register()
