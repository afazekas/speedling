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


def do_cinder_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('mistral')


m_srv = {'mistral-api', 'mistral-engine'}


def local_mistral_service_start():
    selected_services = inv.get_this_inv()['services']

    srvs = []
    for bar in m_srv:
        if bar in selected_services:
            srvs.append('openstack-' + bar + '.service')
    srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def etc_mistral_mistral_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url()},
    'database': {'connection': mariadb.db_url('mistral')},
    'keystone_authtoken': util.keystone_authtoken_section('mistral'),
}


def task_mistral_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    db_sync('mistral')
    local_mistral_service_start()
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def mistral_etccfg(services, global_service_union):
    usrgrp.group('mistral')
    usrgrp.user('mistral', 'mistral')
    util.base_service_dirs('mistral')
    cfgfile.ini_file_sync('/etc/mistral/mistral.conf',
                          etc_mistral_mistral_conf(),
                          owner='mistral', group='mistral')


def task_cinder_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)

    m = inv.hosts_with_service('mistral-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = m.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_mistral_db)

    # start services
    inv.do_do(inv.hosts_with_any_service(m_srv), do_local_mistral_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def mistral_pkgs():
    return set()


def register():
    component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'mistral',
      'pkg_deps': mistral_pkgs,
      'cfg_step': mistral_etccfg,
      'goal': task_mistral_steps
    }
    facility.register_component(component,
                                m_srv)

register()
