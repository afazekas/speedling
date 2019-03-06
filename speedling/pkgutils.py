import threading
import logging
from speedling import localsh
from speedling import conf
from speedling import util

LOG = logging.getLogger(__name__)
PKG_COMPOSOTION = set()

# fantasy_gourp something like gentoo package classes
# fantasy_element pkg name in one distro
# Since these are fantasy names they will remain static even all
# distro renames it's packages

# The \\default used of sub distro distinguisher not specified or when the subdistro is unknown

# Layers Familiy redhat, debian, suse ..
# Variant Fedora, RHEL, ubuntu, opensuse
# version '18.4' , '29', '8'
# \\default used as a fallback
# This dict not expected grove or change at runtime
# Some cases multiple packages required to do the same thing

PKG_MAPPING = {'srv-http\\apache-httpd': {'redhat': {'\\default': ['httpd'],
                                                     'fedora': {'\\default': []}},  # just an example
                                          '\\default': ['apache2']},
               'srv-sql\\mariadb-galera': {'debian': {'\\default': ['galera-3', 'mariadb-server']},
                                           'suse': {'\\default': ['mariadb-galera', 'mariadb-server']},
                                           'redhat': {'\\default': ['mariadb-server-galera']}},
               'srv-ldap\\openldap': {'debian': {'\\default': ['slapd']},
                                      'suse': {'\\default': ['openldap2']},
                                      'redhat': {'\\default': ['openldap']}},
               'dev-http\\apache-httpd': {'redhat': {'\\default': ['httpd-devel']},
                                          'suse': {'\\default': ['apache2-devel']},
                                          'debian': {'\\default': ['apache2-dev']}},
               'lib-dev\\python3': {'debian': {'\\default': ['python3-dev']},
                                    '\\default': ['python3-devel']},
               'lib-dev\\python2': {'debian': {'\\default': ['python2-dev']},
                                    'suse': {'\\default': ['python-devel']},
                                    'redhat': {'\\default': ['python2-devel']}},
               'lib-dev\\xslt': {'debian': {'\\default': ['libxslt-dev']},
                                 '\\default': ['libxslt-devel']},
               'lib-dev\\ffi': {'debian': {'\\default': ['libffi-dev']},
                                '\\default': ['libffi-devel']},
               'lib-dev\\mariadb': {'debian': {'\\default': ['libmariadb-dev']},
                                    'suse': {'\\default': ['libmariadb-devel']},
                                    'redhat': {'\\default': ['mariadb-devel']}},
               'lib-dev\\openssl': {'debian': {'\\default': ['libssl-dev']},
                                    '\\default': ['openssl-devel']},
               'lib-dev\\erasurecode': {'debian': {'\\default': ['liberasurecode-dev']},
                                        '\\default': ['liberasurecode-devel']},
               'lib-py3\\openldap': {'debian': {'\\default': ['python3-ldap']},
                                     '\\default': ['python3-openldap']},
               'srv-rsync\\rsyncd': {'redhat': {'\\default': ['rsync-daemon']},
                                     '\\default': ['rsync']},
               'lib-dev\\openldap': {'redhat': {'\\default': ['openldap-devel']},
                                     'suse': {'\\default': ['openldap2-devel']},
                                     'debian': {'\\default': ['libldap2-dev']}},
               'lib-http-py3\\mod_wsgi': {'redhat': {'\\default': ['python3-mod_wsgi']},
                                          'suse': {'\\default': ['apache2-mod-wsgi-py3']},
                                          'debian': {'\\default': ['libapache2-mod-wsgi-py3']}},
               'lib-py3\\pymemcached': {'redhat': {'\\default': ['python3-memcached']},
                                        'suse': {'\\default': ['python3-python-memcached']},
                                        'debian': {'\\default': ['python3-memcache']}},
               'lib-py2\\subunit': {'redhat': {'\\default': ['python2-subunit']},
                                    '\\default': ['python-subunit']},
               'lib-py2\\jsonschema': {'redhat': {'\\default': ['python2-jsonschema']},
                                       '\\default': ['python-jsonschema']},
               'lib-py2\\paramiko': {'debian': {'\\default': ['python-paramiko']},
                                     '\\default': ['python2-paramiko']},
               'lib-py3\\pymysql': {'redhat': {'\\default': ['python3-PyMySQL']},
                                    'suse': {'\\default': ['python3-python-memcached']},
                                    'debian': {'\\default': ['python3-pymysql']}},
               'lib-py2\\keystonemiddleware': {'redhat': {'\\default': ['python2-keystonemiddleware']},
                                               '\\default': ['python-keystonemiddleware']},
               'util-cli\\pcs': {'suse': {'\\default': ['pacemaker-cli']},
                                 '\\default': ['pcs']},
               'lib-py3\\libvirt': {'suse': {'\\default': ['python3-libvirt-python']},
                                    '\\default': ['python3-libvirt']},
               'util-lang\\gcc-g++': {'debian': {'\\default': ['g++']},
                                      '\\default': ['gcc-c++']},
               'lib-py3\\libgusetfs': {'debian': {'\\default': ['python3-guestfs']},
                                       '\\default': ['python3-libguestfs']},
               'util-cli\\libvirt': {'debian': {'\\default': ['libvirt-clients']},
                                     '\\default': ['libvirt-client']},
               'srv-virt\\libvirt': {'debian': {'\\default': ['libvirt-daemon-system']},
                                     '\\default': ['libvirt']},
               'util-cli\\conntrack': {'debian': {'\\default': ['conntrack']},
                                       'suse': {'\\default': ['libvirt-daemon']},
                                       '\\default': ['libvirt']},
               'util-cli\\qemu-img': {'debian': {'\\default': ['qemu']},
                                      '\\default': ['qemu-img']},
               'util-cli\\iputils': {'debian': {'\\default': ['iputils-arping', 'iputils-ping']},
                                     '\\default': ['iputils']},
               'lib-py3\\libguestfs': {'debian': {'\\default': ['python3-guestfs']},
                                       '\\default': ['python3-libguestfs']},
               'cli-py2\\pip': {'debian': {'\\default': ['python-pip']},
                                '\\default': ['python2-pip']},
               'lib-py3\\pyxattr': {'debian': {'\\default': ['python-pyxattr']},
                                    'redhat': {'\\default': ['pyxattr']},
                                    'suse': {'\\default': ['python-xattr']}},
               'srv-radosgw\\ceph': {'debian': {'\\default': ['radosgw']},
                                     '\\default': ['ceph-radosgw']},
               'srv-ovs\\switch': {'debian': {'\\default': ['openvswitch-switch']},
                                   '\\default': ['openvswitch']}}


def lookup_pkg(distro, sub_dict):
    if distro:
        if distro[0] in sub_dict:
            lpkg = lookup_pkg(distro[1:], sub_dict[distro[0]])
            if lpkg is not None:
                return lpkg
        else:
            if '\\default' in sub_dict:
                return sub_dict['\\default']
    return None


def pkg_mapping(pkgs, distro):
    # just for the map dict usage, other rules in the classes
    # distro must be an ordered dict
    distro = list(distro.values())
    pkg_real_list = []
    for pkg in pkgs:
        if '\\' in pkg:
            lpkg = lookup_pkg(distro, PKG_MAPPING[pkg])
            assert lpkg is not None
            pkg_real_list += lpkg
        else:
            pkg_real_list.append(pkg)

    return set(pkg_real_list)


PKG_MGR_STR = None


def detect_pkg_mgr():
    global PKG_MGR_STR
    if PKG_MGR_STR is not None:
        return PKG_MGR_STR

    pkg_mgr = localsh.ret("""
if which zypper &>/dev/null; then
   echo zypper
elif [ -e /etc/redhat-release ]; then
   echo dnf
else
   echo apt-get
fi
""")
    return pkg_mgr.strip()

PKG_MANAGER = None


def get_pkgmgr():
    global PKG_MANAGER
    if PKG_MANAGER is not None:
        return PKG_MANAGER
    pkg_mgr = detect_pkg_mgr()
    gconf = conf.get_global_config()
    if not gconf.get('use_pkg', True):
        PKG_MANAGER = NULL
    elif pkg_mgr == 'dnf':
        PKG_MANAGER = DNF
    elif pkg_mgr == 'zypper':
        PKG_MANAGER = Zypper
    elif pkg_mgr == 'apt-get':
        PKG_MANAGER = AptGet
    else:
        raise Exception('Unable to figure out the package manager: {}'.format(pkg_mgr))
    return PKG_MANAGER


# NOTE: we have pkg manager classes instead of distros , becuse
#       too high number of distros exists, but pkg mgrs are limited
#       this model leads to less code
class PKGMGR(object):
    install_cmd = None
    update_cmd = None

    @classmethod
    def pkg_mapping(cls, pkgs, distro=None):
        if distro is None:
            distro = util.get_distro()
        return pkg_mapping(pkgs, distro)

    @classmethod
    def install(cls, pkgs):
        retry = 5
        LOG.info("Installing packages ..")  # to super, dedup
        pkgs = cls.pkg_mapping(pkgs)
        try:
            localsh.run(cls.install_cmd + ' '.join(pkgs))
        except:
            retry -= 1
            if not retry:
                raise

    @classmethod
    def update(cls):
        retry = 5
        LOG.info("Updating packages ..")  # to super, dedup
        try:
            localsh.run(cls.update_cmd)
        except:
            retry -= 1
            if not retry:
                raise


# used for disabling package install, in case you are sure you have them all
class NULL(PKGMGR):
    @classmethod
    def install(cls, pkgs):
        """pkgs ise set of packages,
           if the package is str it will go trough mapping rules,
           in case of binary they are explicit"""
        pass

    @classmethod
    def update(cls):
        pass


class DNF(PKGMGR):
    install_cmd = 'dnf install -y '
    update_cmd = 'dnf update -y '


class Zypper(PKGMGR):
    install_cmd = 'zypper --non-interactive install --auto-agree-with-licenses --no-recommends '
    update_cmd = 'zypper --non-interactive update '


class AptGet(PKGMGR):
    install_cmd = 'apt-get install -y '
    update_cmd = 'apt-get update -y '


def add_compose(pkgs):
    PKG_COMPOSOTION.update(pkgs)


def install_compose():
    LOG.info("Package composed install step ..")
    pkgmgr = get_pkgmgr()
    pkgmgr.update()
    pkgmgr.install(PKG_COMPOSOTION)


SYSTEM_HAS_PKG = False
ENSURE_PKG_LOCK = threading.Lock()


# TODO:  move this lock wrapper skelotons to util
def ensure_compose():
    global SYSTEM_HAS_PKG
    if SYSTEM_HAS_PKG:
        return
    try:
        ENSURE_PKG_LOCK.acquire()
        if SYSTEM_HAS_PKG:
            return
        install_compose()
        SYSTEM_HAS_PKG = True
    finally:
        ENSURE_PKG_LOCK.release()

# TODO: extra repo management
