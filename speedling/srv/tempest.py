from speedling import util
from speedling import conf
from speedling import facility
from speedling import gitutils

from osinsutils import localsh
from osinsutils import fetch

import logging


LOG = logging.getLogger(__name__)


img_url = 'http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img'
img_md5 = '443b7623e27ecf03dc9e01ee93f67afe'  # glance uses md5


def _file_md5_sum(for_sum):
    # compare its speed with some built in function
    return localsh.ret("md5sum '{}'".format(
                       for_sum)).split(" ")[0]


def task_network_id(self):
    self.wait_for_components(self.neutron, self.osclient, self.keystone)
    tempest_nodes = self.hosts_with_component('tempest')
    netter = util.rand_pick(tempest_nodes)
    r = self.call_do(netter, self.do_network_id)
    self.network_id = r[next(iter(netter))]['return_value']
    assert self.network_id


def task_nova_flavors(self):
    self.wait_for_components(self.neutron, self.osclient, self.keystone)
    self.call_do(util.rand_pick(self.hosts_with_component('tempest')), self.do_ensure_flavors)


def task_tempest_steps(self):
    tempest_nodes = self.hosts_with_component('tempest')
    self.call_do(tempest_nodes, self.do_fetch_image)

    self.wait_for_components(self.glance, self.osclient, self.keystone)
    imager = util.rand_pick(tempest_nodes)
    r = self.call_do(imager, self.do_ensure_test_images)
    (image_ref, image_ref_alt) = r[next(iter(imager))]['return_value']
    facility.task_wants(self.task_network_id)
    self.call_do(tempest_nodes, self.do_tempest_cfg, c_kwargs={'image_ref': image_ref,
                                                               'image_ref_alt': image_ref_alt,
                                                               'public_network_id': self.network_id,
                                                               'min_compute_nodes': len(self.hosts_with_service('nova-compute'))})
    facility.task_wants(self.task_nova_flavors)


class Tempest(facility.OpenStack):
    origin_repo = 'https://github.com/openstack/tempest.git'
    deploy_source = 'src'
    deploy_source_options = {'src', 'pkg'}

    def __init__(self, *args, **kwargs):
        super(Tempest, self).__init__(*args, **kwargs)
        self.final_task = self.bound_to_instance(task_tempest_steps)
        self.bound_to_instance(task_nova_flavors)
        self.bound_to_instance(task_network_id)
        self.network_id = None
        self.keystone = self.dependencies['keystone']
        self.neutron = self.dependencies['neutron']
        self.nova = self.dependencies['nova']
        self.glance = self.dependencies['glance']
        self.osclient = self.dependencies['osclient']

    # TODO: bake cirros into the image
    def do_fetch_image(cname):
        self = facility.get_component(cname)
        tempest_git_dir = gitutils.component_git_dir(self)
        self.have_content()
        image_file = tempest_git_dir + '/etc/cirros.img'
        fetch.download_origin_or_mirror(img_url, image_file)
        um = _file_md5_sum(image_file)
        assert um == img_md5  # TODO: raise real

    def do_tempest_cfg(cname, image_ref, image_ref_alt, public_network_id, min_compute_nodes=1):
        self = facility.get_component(cname)
        tempest_git_dir = gitutils.component_git_dir(self)
        self.ensure_path_exists(tempest_git_dir, owner='stack', group='stack')

        cfg = self.gen_tempest_conf(image_ref, image_ref_alt, public_network_id, min_compute_nodes)
        self.ini_file_sync('/'.join((tempest_git_dir, 'etc', 'tempest.conf')),
                           cfg,
                           mode=0o755,
                           owner='stack', group='stack')

    def do_network_id(cname):
        net_uuid = localsh.ret(util.userrc_script('admin') +
                               "openstack network list --external --name public | awk '/ public / {print $2}'")
        return net_uuid.strip()

    # TODO: have neutron part to store the public network id as a state file instead

    def do_ensure_test_images(cname):
        # TODO: Do not duplicate images
        self = facility.get_component(cname)
        self.have_content()
        tempest_git_dir = gitutils.component_git_dir(self)
        image_file = tempest_git_dir + '/etc/cirros.img'
        admin_snippet = util.userrc_script('admin')
        image_uuid = localsh.ret(admin_snippet +
                                 "openstack image create cirros --public --file {image_file} --disk-format qcow2 | awk '/\| id/{{print $4}}'".format(image_file=image_file))
        image_alt_uuid = localsh.ret(admin_snippet +
                                     "openstack image create cirros_alt --public --file {image_file} --disk-format qcow2 | awk '/\| id/{{print $4}}'".format(image_file=image_file))
        return (image_uuid.strip(), image_alt_uuid.strip())

    def do_ensure_flavors(cname):
        localsh.run(util.userrc_script('admin') + """
            available_flavors=$(nova flavor-list)
            retry=30
            while ! available_flavors=$(nova flavor-list) ; do
                ((retry--))
                if [[ retry == 0 ]]; then
                break;
            fi
            done

            if [[ ! ( $available_flavors =~ 'm1.nano' ) ]]; then
                openstack flavor create --id 42 --ram 64 --disk 1 --vcpus 1 m1.nano
            fi
            if [[ ! ( $available_flavors =~ 'm1.micro' ) ]]; then
                openstack flavor create --id 84 --ram 128 --disk 1 --vcpus 1 m1.micro
            fi """)

    def gen_tempest_conf(self, image_ref, image_ref_alt, public_network_id, min_compute_nodes=1):
        pwd = util.get_keymgr()(self.keystone.name, 'admin@default')
        auth_url = ''.join(('http://', conf.get_vip('public')['domain_name'], ':35357/v3'))
        gconf = conf.get_global_config()
        service_flags = gconf['global_service_flags']
        return {'DEFAULT': {'debug': True,
                            'log_file': 'tempest.log'},
                'auth': {'tempest_roles': 'user',
                         'admin_username': 'admin',
                         'admin_project_name': 'admin',
                         'admin_domain_name': 'Default',
                         'admin_password': pwd},
                'compute': {'flavor_ref': 42,
                            'flavor_ref_alt': 84,
                            'image_ref': image_ref,
                            'image_ref_alt': image_ref_alt,
                            'min_compute_nodes': min_compute_nodes,
                            'max_microversion': 'latest'},
                'compute-feature-enabled': {'attach_encrypted_volume': False},
                'network': {'floating_network_name': 'public',
                            'public_network_id': public_network_id},
                'scenario': {'img_dir': 'etc',
                             'img_file': 'cirros.img'},
                'validation': {'image_ssh_user': 'cirros'},
                'object-storage': {'reseller_admin_role': 'admin',
                                   'operator_role':  'user'},
                'oslo-concurrency': {'lock_path': '/tmp'},
                'image': {'image_path': img_url, 'http_image': img_url},
                'identity': {'uri': auth_url,
                             'uri_v3': auth_url},
                'volume': {'storage_protocol': 'ceph',
                           'max_microversion': 'latest'},
                'service_available': {
                  'horizon': True if 'horizon' in service_flags else False,
                  'cinder': True if 'cinder-api' in service_flags else False,
                  'nova': True if 'nova-api' in service_flags else False,
                  'neutron': True if 'neutron-server' in service_flags else False,
                  'glance': True if 'glance-api' in service_flags else False,
                  'heat': True if 'heat-api' in service_flags else False,
                  'ironic': True if 'ironic-api' in service_flags else False,
                  'zaqar': True if 'zaqar' in service_flags else False,
                  'swift': True if 'swift-proxy' in service_flags else False}}

    def get_node_packages(self):
        pkgs = super(Tempest, self).get_node_packages()
        pkgs.update({'python2-subunit', 'python2-jsonschema', 'python2-paramiko',
                     'libffi-devel', 'openssl-devel', 'libxslt-devel'})
        if self.deploy_source == 'pkg':
            pkgs.update({'openstack-tempest'})
        return pkgs

    def compose(self):
        super(Tempest, self).compose()
        tempest_nodes = self.hosts_with_component('tempest')
        util.bless_with_principal(tempest_nodes,
                                  [(self.keystone.name, 'admin@default')])
