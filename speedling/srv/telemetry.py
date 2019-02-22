from speedling import util
from speedling import inv
from speedling import sl
from speedling import conf
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

# WARNING NOT FINISHED, INCOMPLET, INCORRECT


def aodh_etccfg(services, global_service_union):
        usrgrp.group('aodh', 989)
        usrgrp.user('aodh', 991)
        util.base_service_dirs('aodh')

        # only if mod_wsgi mode, make it optional
        cfgfile.ensure_path_exists('/var/www/cgi-bin/aodh', mode=0o755)
        cfgfile.ensure_sym_link('/var/www/cgi-bin/aodh/app', '/usr/lib/python2.7/site-packages/aodh/api/app.wsgi')
        cfgfile.content_file('/etc/httpd/conf.d/wsgi-aodh.conf',
                             etc_httpd_conf_d_wsgi_aodh_conf(),
                             mode=0o644)

        cfgfile.ini_file_sync('/etc/aodh/aodh.conf',
                              etc_aodh_aodh_conf(),
                              owner='aodh', group='aodh')


# TODO: to use keystone
# iniset /etc/gnocchi/api-paste.ini pipeline:main pipeline gnocchi+auth
def gnocchi_etccfg(services, global_service_union):
        usrgrp.group('gnocchi', 991)
        usrgrp.user('gnocchi', 994)
        util.base_service_dirs('gnocchi')
        cfgfile.ini_file_sync('/etc/gnocchi/gnocchi.conf',
                              etc_gnocchi_gnocchi_conf(),
                              owner='gnocchi', group='gnocchi')

        # only if mod_wsgi mode, make it optional
        cfgfile.ensure_path_exists('/var/www/cgi-bin/gnocchi', mode=0o755)
        cfgfile.ensure_sym_link('/var/www/cgi-bin/gnocchi/app', '/usr/lib/python2.7/site-packages/gnocchi/rest/app.wsgi')
        cfgfile.content_file('/etc/httpd/conf.d/wsgi-gnocchi.conf',
                             etc_httpd_conf_d_wsgi_gnocchi_conf(),
                             mode=0o644)

        util.unit_file('gnocchi-metricd',
                       '/usr/local/bin/gnocchi-metricd --log-file /var/log/gnocchi/metricd.log',
                       'gnocchi')
        util.unit_file('gnocchi-api',
                       '/usr/local/bin/gnocchi-api --log-file /var/log/gnocchi/api.log',
                       'gnocchi')
        util.unit_file('gnocchi-statsd',
                       '/usr/local/bin/gnocchi-statsd --log-file /var/log/gnocchi/statsd.log',
                       'gnocchi')


def ceilometer_etccfg(services, global_service_union):
    usrgrp.group('ceilometer', 166)
    usrgrp.user('ceilometer', 166)
    util.base_service_dirs('ceilometer')
    cfgfile.ini_file_sync('/etc/ceilometer/ceilometer.conf',
                          etc_ceilometer_ceilometer_conf(),
                          owner='ceilometer', group='ceilometer')
    util.unit_file('openstack-ceilometer-central',
                   '/usr/local/bin/ceilometer-polling --polling-namespaces central --logfile /var/log/ceilometer/central.log',
                   'ceilometer')
    util.unit_file('openstack-ceilometer-central',
                   '/usr/local/bin/ceilometer-polling --logfile /var/log/ceilometer/polling.log',
                   'ceilometer')
    util.unit_file('openstack-ceilometer-compute',
                   '/usr/local/bin/ceilometer-polling --polling-namespaces compute --logfile /var/log/ceilometer/compute.log',
                   'ceilometer')
    util.unit_file('openstack-ceilometer-ipmi',
                   '/usr/local/bin/ceilometer-polling --polling-namespaces ipmi --logfile /var/log/ceilometer/ipmi.log',
                   'ceilometer')


def etc_gnocchi_gnocchi_conf(): return {
    'DEFAULT': {'debug': True,
                'transport_url': rabbitmq.transport_url()},
    'api': {'auth_mode': 'keystone'},
    'keystone_authtoken': util.keystone_authtoken_section('gnocchi_auth'),
    'storage': {'ceph_keyring': '/etc/ceph/ceph.client.gnocchi.keyring',
                'ceph_username': 'gnocchi',
                'driver': 'ceph'},
    'indexer': {'url': mariadb.db_url('gnocchi')}
}


def etc_aodh_aodh_conf():
    redis_url = 'redis://' + conf.get_vip('internal')['domain_name'] + ':6379'
    return {
        'DEFAULT': {'debug': True,
                    'transport_url': rabbitmq.transport_url()},
        'coordination': {'backend_url': redis_url},
        'keystone_authtoken': util.keystone_authtoken_section('aodh_auth'),
        'service_credentials': util.keystone_authtoken_section('aodh'),
        'database': {'connection': mariadb.db_url('aodh'),
                     'alarm_history_time_to_live': 2592000}
    }


# coordination backend_url "redis://${PRIVATE_SERVICE_IP}:6379"
# store_events False ??
# dispatcher_gnocchi ulr option ?
def etc_ceilometer_ceilometer_conf():
    redis_url = 'redis://' + conf.get_vip('internal')['domain_name'] + ':6379'
    return {
            'DEFAULT': {'debug': True,
                        'transport_url': rabbitmq.transport_url(),
                        'event_dispatchers': "",  # TODO use es
                        'meter_dispatchers': 'gnocchi'},
            'database': {'connection': mariadb.db_url('ceilometer')},
            'keystone_authtoken': util.keystone_authtoken_section('ceilometer_auth'),
            'service_credentials': util.keystone_authtoken_section('ceilometer'),
            'compute': {'workload_partitioning': True},
            'notifications': {'workload_partitioning': True,
                              'workers': 3},
            'coordination': {'backend_url': redis_url},
            'collector':  {'workers': 3,
                           'batch_timeout': 5,  # max delay 5 sec
                           'batch_size': 50},
            'dispatcher_gnocchi': {'archive_policy': 'high'},
            'publisher': {'telemetry_secret': util.get_keymgr()('shared', 'telemetery')}
        }


# TODO: check best practices with directory usage
def etc_httpd_conf_d_wsgi_gnocchi_conf():
    return """Listen 8041
  <VirtualHost *:8041>
  DocumentRoot "/var/www/cgi-bin/gnocchi"

  <Directory "/var/www/cgi-bin/gnocchi">
    Options Indexes FollowSymLinks MultiViews
    AllowOverride None
    Require all granted
  </Directory>

  ErrorLog "/var/log/httpd/gnocchi_wsgi_error.log"
  ServerSignature Off
  CustomLog "/var/log/httpd/gnocchi_wsgi_access.log" combined
  SetEnvIf X-Forwarded-Proto https HTTPS=1
  WSGIApplicationGroup %{GLOBAL}
  WSGIDaemonProcess gnocchi group=gnocchi processes=3 threads=3 user=gnocchi
  WSGIProcessGroup gnocchi
  WSGIScriptAlias / "/var/www/cgi-bin/gnocchi/app"
</VirtualHost>
"""


# TODO: check best practices with directory usage
def etc_httpd_conf_d_wsgi_aodh_conf():
    return """Listen 8042
<VirtualHost *:8042>
  DocumentRoot "/var/www/cgi-bin/aodh"

  <Directory "/var/www/cgi-bin/aodh">
    Options Indexes FollowSymLinks MultiViews
    AllowOverride None
    Require all granted
  </Directory>

  ErrorLog "/var/log/httpd/aodh_wsgi_error.log"
  ServerSignature Off
  CustomLog "/var/log/httpd/aodh_wsgi_access.log" combined
  SetEnvIf X-Forwarded-Proto https HTTPS=1
  WSGIApplicationGroup %{GLOBAL}
  WSGIDaemonProcess aodh display-name=aodh_wsgi group=aodh processes=3 threads=2 user=aodh
  WSGIProcessGroup aodh
  WSGIScriptAlias / "/var/www/cgi-bin/aodh/app"
</VirtualHost>
"""


def local_aodh_service_start():
    srvs = []
    if 'aodh-evaluator' in selected_services:
        srvs.append('openstack-aodh-evaluator.service')

    if 'aodh-notifier' in selected_services:
        srvs.append('openstack-aodh-notifier.service')

    if 'aodh-listener' in selected_services:
        srvs.append('openstack-aodh-listener.service')

    srvs = [sl.UNIT_PREFIX + x for x in srvs]
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def task_aodh_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, task_redis_steps, speedling.srv.keystone.step_keystone_ready)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    db_sync('aodh')
    local_aodh_service_start()
    facility.task_wants(speedling.srv.keystone.step_keystone_ready, task_redis_steps)


def local_gnocchi_service_start():
    if 'gnocchi-metricd' in selected_services:
        srvs['openstack-gnocchi-metricd.service']
        srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
        localsh.run('systemctl start %s' % (' '.join(srvs)))


def task_gnocchi_steps():
    facility.task_will_need(speedling.srv.rabbitmq.task_rabbit_steps, speedling.srv.keystone.step_keystone_ready, ceph_steps)
    facility.task_wants(speedling.srv.mariadb.task_mariadb_steps)
    db_sync('gnocchi')  # partial
    facility.task_wants(ceph_steps)
    localsh.run('su -s /bin/sh -c "gnocchi-upgrade" gnocchi')  # full
    facility.task_wants(speedling.srv.keystone.step_keystone_ready)
    # archive policy could be configured here


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


c_srv = 'ceilometer-api', 'ceilometer-notification', 'ceilometer-central', 'ceilometer-collector', 'ceilometer-compute'


def local_ceilometer_service_start():
    selected_services = inv.get_this_inv()['services']

    srvs = []
    for bar in c_srv:
        if bar in selected_services:
            srvs.append('openstack-' + bar + '.service')
    srvs = [sl.UNIT_PREFIX + x for x in srvs]  # TODO: move to helper
    localsh.run('systemctl start %s' % (' '.join(srvs)))


def task_ceilometer_steps():
    facility.task_will_need(task_redis_steps)
    inv.do_do(inv.hosts_with_any_service(c_srv), do_local_ceilometer_service_start)
    facility.task_wants(speedling.srv.keystone.step_keystone_ready, task_redis_steps)


def register():
    pass

register()
