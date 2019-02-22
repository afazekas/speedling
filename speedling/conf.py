import argparse
import sys

# merge with inv ?

ARGS = None


def construct_parser():
    parser = argparse.ArgumentParser(
                      description='Speedling openstack dev installler')
    parser.add_argument('-r', '--receiver',
                        help='Receiver mode act on remote host',
                        action='store_true')
    parser.add_argument('-i', '--identity',
                        help='The controller node act like the given node without remote access')
    parser.add_argument('-I', '--all-remote',
                        action='store_true',
                        help='The controller node does not ties to figure out his position')
    parser.add_argument('-s', '--state-dir',
                        default='./state',
                        help='Directory used for store deploy state used at reruns created at first run')
    parser.add_argument('--wipe',
                        action='store_true',
                        help='Used in case you want to manage a new deployment with the given state dir (delete old state recirsive)')
    parser.add_argument('-a', '--asset-dir',
                        default='./asset',
                        help='Extra deploy resources like patches, database snapshots..')
    parser.add_argument('-c', '--config',
                        help='acquire global conf and inventory',
                        default='examples/all_in_one.py')
    parser.add_argument('-o', '--config-option',
                        help='acquire global conf and inventory',
                        default='{"inventory":"speedling.ini"}')

    return parser


def _conf():
    global ARGS
    parser = construct_parser()
    ARGS = parser.parse_args(sys.argv[1:])


def get_args():
    # TODO: repalce func without if
    if not ARGS:
        _conf()
    return ARGS


GLOBAL_CONFIG_EXAMPLE = {'default_region': 'RegionOne',
                         'service_prefix': 'sl-',
                         'deployment_id': 42,
                         'allow_address_fallback': ['default_gw', 'sshed_address'],  # order matters
                         'deploymant_name': 'OpenStack',
                         'cinder_ceph_libvirt_secret_uuid': '457eb676-33da-42ec-9a8c-9293d545c337',  # move to ceph usage feature
                         'gnetworks': {'access': {'pools': {'defualt': {'subnet': '172.16.1.0/24', 'pourposes': {'sshnet', 'management'}}}}},
                         'vip': {'public': {'domain_name': '172.16.1.2', 'internal_address': '172.16.1.2'},
                                 'internal': {'domain_name': '172.16.1.2', 'internal_address': '172.16.1.2'}}}

GLOBAL_CONFIG = GLOBAL_CONFIG_EXAMPLE


# internal_address can be used in the hosts file,
# usable in case single address (floating) or anycast
# internal only vip may net need an domain name
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
