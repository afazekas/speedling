from speedling import util
from speedling import inv
from speedling import sl
from speedling.srv import rabbitmq
from speedling.srv import mariadb

import __main__
from speedling import facility
from speedling import gitutils
from speedling import tasks

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp

import speedling.srv.common
from osinsutils import glb
import logging

LOG = logging.getLogger(__name__)

# WARNING NOT TESTED


def do_local_heat_service_start():
    selected_services = inv.get_this_inv()['services']
    srvs = []
    if 'heat-api' in selected_services:
        srvs.append('openstack-heat-api.service')

    if 'heat-api-cfn' in selected_services:
        srvs.append('openstack-heat-api-cfn.service')

    if 'heat-engine' in selected_services:
        srvs.append('openstack-heat-engine.service')

    if 'heat-api-cloudwatch' in selected_services:
        srvs.append('heat-api-cloudwatch.service')

    srvs = [sl.UNIT_PREFIX + x for x in srvs]
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def do_heat_db():
    # TODO: change the function to near db and near key parts
    tasks.db_sync('heat')


# TODO: auth_encryption_key  minimum size req check
# TODO: make the key mgr able to generate 32ch random things for shared things
def shared_key_gen():
    return '12345678901234567890123456789012'  # WRONG


# NOTE: 'heat_watch_server_url':"http://${ENDPOINT_IP}:8003" # will be removed,
# https://etherpad.openstack.org/p/YVR-heat-liberty-deprecation
# wait condition, wouln't be better to use ip even if we have a domian name ?
def etc_heat_heat_conf():
    keymgr = util.get_keymgr()
    return {
        'DEFAULT': {'debug': True,
                    'transport_url': rabbitmq.transport_url(),
                    'region_name_for_services': glb.region_name,
                    'auth_encryption_key':  shared_key_gen(),  # util.keymgr('shared', 'heat'),
                    'heat_waitcondition_server_url':  "http://" + glb.public_vip_domain_name + ":8000/v1/waitcondition",  # must reachbe by the guest vms
                    'heat_metadata_server_url': 'http://' + glb.public_vip_domain_name + ':8000',
                    'stack_user_domain_name': 'heat',
                    'stack_domain_admin': 'heat_domain_admin',
                    'stack_domain_admin_password': keymgr('os', 'heat_domain_admin@heat')},
        'database': {'connection': mariadb.db_url('heat')},
        'keystone_authtoken': util.keystone_authtoken_section('heat'),
        'cache': {'enabled': True,
                  'backend': "dogpile.cache.memory"},
        'trustee': {'auth_url': 'http://' + glb.public_vip_domain_name + ':35357',
                    'user_domain_id': 'default',
                    'username': 'heat_trusted',
                    'password':  keymgr('os', 'heat_trusted@default'),
                    'auth_plugin': 'password'},
        'clients_keystone': {'auth_uri': 'http://' + glb.public_vip_domain_name + ':5000'},
        'ec2authtoken': {'auth_uri': 'http://' + glb.public_vip_domain_name + ':5000/v2.0'}
    }


h_srv = {'heat-api', 'heat-api-cfn', 'heat-engine', 'heat-api-cloudwatch'}


def do_extra_role():
    localsh.run('source /root/admin-openrc.sh; openstack role create heat_stack_user || openstack role show heat_stack_user')


def task_heat_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready)
    tasks.prepare_source_cond('heat')
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    heats = inv.hosts_with_service('heat-api')
    mysqls = inv.hosts_with_service('mariadb')
    tgt = heats.intersection(mysqls)
    assert tgt
    inv.do_do(inv.rand_pick(tgt), do_heat_db)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)
    inv.do_do(inv.rand_pick(tgt), do_extra_role)
    inv.do_do(inv.hosts_with_any_service(h_srv), do_local_heat_service_start)

    # orphaned roles not supported by the usermanager, this command is slow!


def heat_etccfg(services, global_service_union):
        usrgrp.group('heat', 187)
        usrgrp.user('heat', 187)
        util.base_service_dirs('heat')
        comp = facility.get_component('heat')
        heat_git_dir = gitutils.component_git_dir(comp)
        cfgfile.install_file('/etc/heat/api-paste.ini',
                             '/'.join((heat_git_dir,
                                      'etc/heat/api-paste.ini')),
                             mode=0o644,
                             owner='heat', group='heat')
        cfgfile.install_file('/etc/heat/policy.json',
                             '/'.join((heat_git_dir,
                                       'etc/heat/policy.json')),
                             mode=0o644,
                             owner='heat', group='heat')
        cfgfile.ini_file_sync('/etc/heat/heat.conf',
                              etc_heat_heat_conf(),
                              owner='heat', group='heat')
        util.unit_file('openstck-heat-api-cfn',
                       '/usr/local/bin/heat-api-cfn --config-file /etc/heat/heat.conf ',
                       'heat')
        util.unit_file('openstck-heat-engine',
                       '/usr/local/bin/heat-engine --config-file /etc/heat/heat.conf ',
                       'heat')
        util.unit_file('openstck-heat-api',
                       '/usr/local/bin/heat-api --config-file /etc/heat/heat.conf ',
                       'heat')


def heat_pkgs():
    return set()


def register():
    heat_component = {
      'deploy_source': 'git',
      'deploy_mode': 'standalone',
      'component': 'heat',
      'pkg_deps': heat_pkgs,
      'cfg_step': heat_etccfg,
      'goal': task_heat_steps
    }
    facility.register_component(heat_component, h_srv)

register()
