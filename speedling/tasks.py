from speedling import inv
from speedling import facility
from osinsutils import localsh
from osinsutils import cfgfile

import logging


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


# TODO: create variant for service dicts, witch component lookup
def local_os_service_start_by_component(*args):
    to_start = []
    for comp in args:
        enabled = comp.get_enabled_services_from_component()
        ds = comp.deploy_source
        for s in enabled:
            service = comp.services[s]
            if service['deploy_mode'] == 'standalone':  # TODO make soure the options can be different for component instance
                to_start.append(service['unit_name'][ds])  # TODO: handle offset
    localsh.run('systemctl start %s' % (' '.join(to_start)))


def task_generic_system():
    # ensures all node compose finishes
    # It will include systemd file/config mangement, but not service state
    # it will include network interface managemnt
    # packages / selinux , # nodes individially can sync on sub parts
    # it may be just an indicator at the end
    inv.do_do(inv.ALL_NODES, facility.do_generic_system)
