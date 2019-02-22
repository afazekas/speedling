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

# WARNING NOT TESTED, INCOMPLTE!!


def do_designate_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('designate')


def task_designate_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    ds = inv.hosts_with_service('designate-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = ds.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_designate_db)
    inv.do_do(inv.hosts_with_any_service(d_srv), do_local_designate_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)

    # orphaned roles not supported by the usermanager, this command is slow!

d_srv = {'designate-api', 'designate-central', 'designate-zone-manager',
         'designate-mdns', 'designate-pool-manager', 'designate-sink',
         'designate-producer', 'designate-agent'}


def local_designate_service_start():
    selected_services = inv.get_this_inv()['services']
    srvs = []
    for bar in d_srv:
        if bar in selected_services:
            srvs.append(bar + '.service')
    srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
    localsh.run('systemctl start %s' % (' '.join(srvs)))


# this dns things will be handled specially,
# now it is the all-in-one thing .

# does it needs id attribute ?, default_pool_id usage ?
# ns_record will be part of the SOA, so it should by the FQDN of this end by '.'
# TODO use yaml.dump
def etc_designate_pools_yaml():
    return """---
- name: default
  description: DevStack BIND Pool
  attributes: {}

  ns_records:
    - hostname: ns1.devstack.org.
      priority: 1

  nameservers:
    - host: 127.0.0.1
      port: 53

  targets:
    - type: bind9
      description: BIND Instance

      masters:
        - host: 127.0.0.1
          port: 5354

      options:
        host: 127.0.0.1
        port: 5322
        rndc_host: 127.0.0.1
        rndc_port: 953
        rndc_config_file: /etc/named/rndc.conf
        rndc_key_file: /etc/named/rndc.key"""


# # Newer designate, using the pools.yaml
# sudo su -s /bin/sh -c "designate-manage pool update" designate

# NOTE transport_url might be a not supported option
# consider: 'oslo_messaging_rabbit'
def etc_designate_designate_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url()},
    'storage:sqlalchemy': {'connection': mariadb.db_url('designate')},
    'keystone_authtoken': util.keystone_authtoken_section('designate'),
}


def designate_etccfg(services, global_service_union):
    usrgrp.group('designate')
    usrgrp.user('designate', 'designate')
    util.base_service_dirs('designate')
    cfgfile.ini_file_sync('/etc/designate/designate.conf',
                          etc_designate_designate_conf(),
                          owner='designate', group='designate')
    util.unit_file('openstck-designate-worker',
                   '/usr/local/bin/designate-worker --config-file /etc/designate/designate.conf --log-file /var/log/designate/worker.log',
                   'designate')
    util.unit_file('openstck-designate-api',
                   '/usr/local/bin/designate-api --config-file /etc/designate/designate.conf --log-file /var/log/designate/api.log',
                   'designate')
    util.unit_file('openstck-designate-agent',
                   '/usr/local/bin/designate-agent --config-file /etc/designate/designate.conf --log-file /var/log/designate/agent.log',
                   'designate')
    util.unit_file('openstck-designate-central',
                   '/usr/local/bin/designate-central --config-file /etc/designate/designate.conf --log-file /var/log/designate/central.log',
                   'designate')
    util.unit_file('openstck-designate-mdns',
                   '/usr/local/bin/designate-mdns --config-file /etc/designate/designate.conf --log-file /var/log/designate/mdns.log',
                   'designate')
    util.unit_file('openstck-designate-pool-manager',
                   '/usr/local/bin/designate-pool-manager --config-file /etc/designate/designate.conf --log-file /var/log/designate/pool-manager.log',
                   'designate')
    util.unit_file('openstck-designate-producer',
                   '/usr/local/bin/designate-producer --config-file /etc/designate/designate.conf --log-file /var/log/designate/producer.log',
                   'designate')
    util.unit_file('openstck-designate-sink',
                   '/usr/local/bin/designate-sink --config-file /etc/designate/designate.conf --log-file /var/log/designate/sink.log',
                   'designate')
    util.unit_file('openstck-designate-zone-manager',
                   '/usr/local/bin/designate-zone-manager --config-file /etc/designate/designate.conf --log-file /var/log/designate/zone-manager.log',
                   'designate')


def designate_pkgs():
    return set()


def register():
    heat_component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'designate',
      'pkg_deps': designate_pkgs,
      'cfg_step': designate_etccfg,
      'goal': task_designate_steps
    }
    facility.register_component(designate_component, d_srv)

# register()
