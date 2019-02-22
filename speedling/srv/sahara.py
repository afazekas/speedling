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


def do_sahara_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('cinder')


def etc_sahara_sahara_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url()},
    'database': {'connection': mariadb.db_url('sahara')},
    'keystone_authtoken': util.keystone_authtoken_section('sahara_auth'),
}


def sahara_etccfg(services, global_service_union):
    usrgrp.group('sahara')
    usrgrp.user('sahara', 'sahara')
    util.base_service_dirs('sahara')
    cfgfile.ini_file_sync('/etc/sahara/sahara.conf',
                          etc_sahara_sahara_conf(),
                          owner='sahara', group='sahara')
    util.unit_file('openstack-sahara-api',
                   '/usr/local/bin/sahara-api --config-file /etc/sahara/sahara.conf',
                   'sahara')
    util.unit_file('openstack-sahara-engine',
                   '/usr/local/bin/sahara-engine --config-file /etc/sahara/sahara.conf',
                   'sahara')

s_srv = {'sahara-api', 'sahara-engine'}


def task_sahara_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)

    sh = inv.hosts_with_service('sahara-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = sh.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_sahara_db)

    # start services
    inv.do_do(inv.hosts_with_any_service(s_srv), do_local_sahara_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)


def do_local_sahara_service_start():
    selected_services = inv.get_this_inv()['services']

    for bar in s_srv:
        if bar in selected_services:
            srvs.append('openstack-' + bar + '.service')
    srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def sahara_pkgs():
    return set()


def register():
    component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'sahara',
      'pkg_deps': sahara_pkgs,
      'cfg_step': sahara_etccfg,
      'goal': task_sahara_steps
    }
    facility.register_component(component,
                                s_srv)

register()
