

def etc_zaqar_zaqar_conf(): return {
    'DEFAULT': {'debug': True,
                'unreliable': True,  # TODO: set it to False
                'admin_mode': True,
                'auth_strategy': 'keystone',
                'pooling': True},
    'drivers': {'message_store': 'mongodb'},
    'drivers:transport:websocket': {'bind': netdriver['tunnel_ip'],
                                    'port': 9000},
    'keystone_authtoken': keystone_authtoken_section('zaqar_auth'),
    'drivers:message_store:mongodb': {'uri': 'mongodb://localhost:27017/zaqar',
                                      'database': 'zaqar'},
    'drivers:management_store:mongodb': {'uri': 'mongodb://localhost:27017/zaqar',
                                         'database': 'zaqar'},
    'pooling:catalog': {'enable_virtual_pool': True}
}


def etc_uwsgi_d_zaqar_conf():
    return {'uwsgi': {
                             'http-socket': netdriver['tunnel_ip'] + ':8888',
                             'harakiri': 60,
                             'processes': 1,
                             'threads': 4,
                             'wsgi-file': '/usr/lib/python2.7/site-packages/zaqar/transport/wsgi/app.py',
                             'callable': 'app',
                             'master': 'true',
                             'add-header': "Connection: close",
                             'plugins': 'python'}}


def zaqar_etccfg():
        usrgrp.group('zaqar')
        usrgrp.user('zaqar', 'zaqar')
        util.base_service_dirs('zaqar')

        cfgfile.ensure_path_exists('/etc/zaqar',
                                   owner='zaqar', group='zaqar')
        cfgfile.ensure_path_exists('/var/log/zaqar',
                                   owner='zaqar', group='zaqar')
        cfgfile.ini_file_sync('/etc/zaqar/zaqar.conf',
                              etc_zaqar_zaqar_conf(),
                              owner='zaqar', group='zaqar')
        util.unit_file('openstack-zaqar',
                       '/usr/local/bin/zeqar-server',
                       'zaqar')
        # only if uwsgi mode
        cfgfile.ensure_path_exists('/etc/uwsgi.d')
        cfgfile.ini_file_sync('/etc/uwsgi.d/zaqar.ini',
                              etc_uwsgi_d_zaqar_conf(),
                              owner='zaqar', group='zaqar')

# mongo zaqar --eval '
# db = db.getSiblingDB("zaqar");
# db.createUser({user: "zaqar",
# pwd: "ZAQAR_DBPASS",
# roles: [ "readWrite", "dbAdmin" ]})'

# mongo zaqar_mgmt --eval '
# db = db.getSiblingDB("zaqar_mgmt");
# db.createUser({user: "zaqar",
# pwd: "ZAQAR_DBPASS",
# roles: [ "readWrite", "dbAdmin" ]})'
