import argparse
import sys

# merge with inv ?

ARGS = None


def construct_parser(*extras):
    parser = argparse.ArgumentParser(
                      description='Speedling openstack dev installler')
    parser.add_argument('-r', '--receiver',
                        help='Receiver mode act on remote host',
                        action='store_true')
    # -I causes some trouble ATM, so it might be removed
    parser.add_argument('-I', '--identity',
                        help='The controller node act like the given node without remote access')
    parser.add_argument('-s', '--state-dir',
                        default='./state',
                        help='Directory used for store deploy state used at reruns created at first run')
    parser.add_argument('-a', '--asset-dir',
                        default='./asset',
                        help='Extra deploy resources like patches, database snapshots..')
    parser.add_argument('-e', '--extra-module', action='append', type=list,
                        default=[],
                        help='Extra module directories to transfer. pyo, pyc excluded')
    parser.add_argument('--dont-touch-pkgs',
                        action='store_true',
                        help='skip all calls to the pkg manger, '
                             'use it if you are using machines with preisntalled packages')
    parser.add_argument('--dont-touch-pypi',
                        action='store_true',
                        help='skip all calls for pypi content, '
                             'use it if you are using machines with preisntalled pips')
    parser.add_argument('--dont-touch-src-repos',
                        action='store_true',
                        help='Use when your CI manages the srouce reposities')

    parser.add_argument('-A', '--alrady-have-everything',
                        help="Implies --dont-touch-src-repos, --dont-touch-pypi, --dont-touch-pkgs",
                        action='store_true')

    for extra in extras:
        extra(parser)

    return parser


GLOBAL_CONFIG_EXAMPLE = {'allow_address_fallback': ['default_gw', 'sshed_address'],  # order matters
                         'cinder_ceph_libvirt_secret_uuid': '457eb676-33da-42ec-9a8c-9293d545c337'}  # move to ceph usage feature

GLOBAL_CONFIG = GLOBAL_CONFIG_EXAMPLE


def args_init(extras=tuple()):
    global ARGS
    parser = construct_parser(extras)
    ARGS = parser.parse_args(sys.argv[1:])
    # TODO: rename these to match cfg option
    if ARGS.dont_touch_pkgs or ARGS.alrady_have_everything:
        GLOBAL_CONFIG['use_pkg'] = False
    if ARGS.dont_touch_pypi or ARGS.alrady_have_everything:
        GLOBAL_CONFIG['use_pip'] = False
    if ARGS.dont_touch_src_repos or ARGS.alrady_have_everything:
        GLOBAL_CONFIG['use_git'] = False

    return ARGS


def get_args():
    return ARGS


# internal_address can be used in the hosts file,
# usable in case single address (floating) or anycast
# internal only vip may net need an domain name
# THIS WILL BE REMOVED
def get_vip(vip):
    return GLOBAL_CONFIG['vip'][vip]


def get_global_config():
    return GLOBAL_CONFIG


def get_service_prefix():
    return GLOBAL_CONFIG.get('service_prefix', 'sl-')


def get_default_region():
    return GLOBAL_CONFIG.get('default_region', 'RegionOne')


# pourposes:
#   sshnet: used for connecting to machine from the managment (mybe via proxy)
#   management: used for BMC
#   imaging: used for image download for imageing the machine itself
#   tunneling: used for inter machine tunneling traffic
#   public_listen: used for services provided to external (LB front)
#   internal_listen: used for listening for internal use (usually LB connects to it)
#   replication: used for ceph/swift/... data replication
#   storage: storage storage_network for clients (ceph osd)
#   backing_object: network between object frontend and backend nodes
#                   swift-proxy -> swift-object
#                   ceph object translator services -> rados

def define_netowork(name, **kwargs):
    pass


def get_global_nets():
    if 'networks' in GLOBAL_CONFIG:
        return GLOBAL_CONFIG.get('networks')
    else:
        d = dict()
        GLOBAL_CONFIG['networks'] = d
        return d
