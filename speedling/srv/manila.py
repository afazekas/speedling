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


def do_manila_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('manila')


m_srv = {'manila-data', 'manila-api', 'manila-scheduler', 'manila-share'}


# example: london named generic
# TODO is it fixed? https://bugzilla.redhat.com/show_bug.cgi?id=1324783
def etc_manila_manila_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url(),
                'enabled_share_backends': 'london',
                'default_share_type': 'default',
                'state_path': '/var/lib/manila',
                'rootwrap_config': '/etc/manila/rootwrap.conf'},
    'neutron': util.keystone_authtoken_section('neutron_for_manila'),
    'cinder': util.keystone_authtoken_section('cinder_for_manila'),
    'nova': util.keystone_authtoken_section('nova_for_manila'),
    'database': {'connection': mariadb.db_url('manila')},
    'keystone_authtoken': util.keystone_authtoken_section('manila'),
    'oslo_concurrency': {'lock_path': '$state_path/tmp'}
}


def manila_etccfg(services, global_service_union):
    usrgrp.group('manila')
    usrgrp.user('manila', 'manila')
    util.base_service_dirs('manila')
    cfgfile.ini_file_sync('/etc/manila/manila.conf',
                          etc_manila_manila_conf(),
                          owner='manila', group='manila')
    util.unit_file('openstack-manila-api',
                   '/usr/local/bin/manila-api --config-file /etc/manila/manila.conf --log-file /var/log/manila/api.log',
                   'manila')
    util.unit_file('openstack-manila-data',
                   '/usr/local/bin/manila-data --config-file /etc/manila/manila.conf --log-file /var/log/manila/data.log',
                   'manila')
    util.unit_file('openstack-manila-scheduler',
                   '/usr/local/bin/manila-scheduler--config-file /etc/manila/manila.conf --log-file /var/log/manila/scheduler.log',
                   'manila')
    util.unit_file('openstack-manila-share',
                   '/usr/local/bin/manila-share --config-file /etc/manila/manila.conf --log-file /var/log/manila/share.log',
                   'manila')


def do_local_manila_service_start():
    selected_services = inv.get_this_inv()['services']
    srvs = []
    for bar in m_srv:
        if bar in selected_services:
            srvs.append('openstack-' + bar + '.service')
    srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
    localsh.run('systemctl start %s' % (' '.join(srvs)))


# ( # the doc suggest it needs to happen before the manila-share start
#  source ~/admin-openrc.sh
#  manila type-create default True


#  # wouln not be better to create the keypair here ?
#  # DEFAULT/manila_service_keypair_name = manila-service
#  nova keypair-add manila-service --pub-key /etc/manila/ssh-key.pub
#  nova flavor-create manila-service-flavor 100 128 0 1 #[DEFAULT]service_instance_flavor_id
#
#  sudo systemctl start openstack-manila-share.service
# ) &


def task_manila_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)

    a = inv.hosts_with_service('manila-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = a.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_manila_db)

    # start services
    inv.do_do(inv.hosts_with_any_service(m_srv), do_local_manila_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def manila_pkgs():
    return set()


def register():
    component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'manila',
      'pkg_deps': manila_pkgs,
      'cfg_step': manila_etccfg,
      'goal': task_manila_steps
    }
    facility.register_component(component,
                                m_srv)

register()
