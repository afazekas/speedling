from speedling import util
from speedling import inv
from speedling import conf
from speedling import facility
from speedling import tasks
from speedling import gitutils

from osinsutils import cfgfile
from osinsutils import localsh
from osinsutils import usrgrp
from osinsutils import fetch


import speedling.srv.common
import logging


LOG = logging.getLogger(__name__)


img_url = 'http://download.cirros-cloud.net/0.4.0/cirros-0.4.0-x86_64-disk.img'
img_md5 = '443b7623e27ecf03dc9e01ee93f67afe'  # glance uses md5


def _file_md5_sum(for_sum):
    # compare this with some built in function
    return localsh.ret("md5sum '{}'".format(
                       for_sum)).split(" ")[0]


# TODO: bake cirros into the image
def do_fetch_image():
    comp = facility.get_component('tempest')
    tempest_git_dir = gitutils.component_git_dir(comp)
    image_file = tempest_git_dir + '/etc/cirros.img'
    fetch.download_origin_or_mirror(img_url, image_file)
    um = _file_md5_sum(image_file)
    assert um == img_md5  # TODO: raise real


def do_tempest_cfg(image_ref, image_ref_alt, public_network_id, min_compute_nodes=1):
    comp = facility.get_component('tempest')
    tempest_git_dir = gitutils.component_git_dir(comp)
    cfgfile.ensure_path_exists(tempest_git_dir, owner='stack', group='stack')

    cfg = gen_tempest_conf(image_ref, image_ref_alt, public_network_id, min_compute_nodes)
    cfgfile.ini_file_sync('/'.join((tempest_git_dir, 'etc', 'tempest.conf')),
                          cfg,
                          mode=0o755,
                          owner='stack', group='stack')


def do_network_id():
    net_uuid = localsh.ret(util.userrc_script('admin') +
                           "openstack network list --external --name public | awk '/ public / {print $2}'")
    return net_uuid.strip()


# TODO: have neutron part to store the public network id as a state file instead
network_id = None


def task_network_id():
    global network_id
    facility.task_wants(speedling.srv.neutron.task_neutron_steps,
                        speedling.srv.osclients.task_osclients_steps,
                        speedling.srv.keystone.step_keystone_ready)

    tempest_nodes = inv.hosts_with_component('tempest')
    netter = inv.rand_pick(tempest_nodes)
    r = inv.do_do(netter, do_network_id)
    network_id = r[next(iter(netter))]['return_value']
    assert network_id


def task_nova_flavors():
    facility.task_wants(speedling.srv.nova.task_nova_steps,
                        speedling.srv.osclients.task_osclients_steps,
                        speedling.srv.keystone.step_keystone_ready)
    inv.do_do(inv.rand_pick(inv.hosts_with_component('tempest')), do_ensure_flavors)


def task_tempest_steps():
    facility.task_will_need(speedling.srv.glance.task_glance_steps,
                            task_nova_flavors, task_network_id,
                            speedling.srv.osclients.task_osclients_steps)

    tempest_nodes = inv.hosts_with_component('tempest')
    inv.do_do(tempest_nodes, do_fetch_image)
    facility.task_wants(speedling.srv.glance.task_glance_steps,
                        speedling.srv.osclients.task_osclients_steps,
                        speedling.srv.keystone.step_keystone_ready)
    imager = inv.rand_pick(tempest_nodes)
    r = inv.do_do(imager, do_ensure_test_images)
    (image_ref, image_ref_alt) = r[next(iter(imager))]['return_value']
    facility.task_wants(task_network_id)
    inv.do_do(tempest_nodes, do_tempest_cfg, c_kwargs={'image_ref': image_ref,
                                                       'image_ref_alt': image_ref_alt,
                                                       'public_network_id': network_id,
                                                       'min_compute_nodes': len(inv.hosts_with_service('nova-compute'))})
    facility.task_wants(task_nova_flavors)


def do_ensure_test_images():
    # TODO: Do not duplicate images
    comp = facility.get_component('tempest')
    tempest_git_dir = gitutils.component_git_dir(comp)
    image_file = tempest_git_dir + '/etc/cirros.img'
    admin_snippet = util.userrc_script('admin')
    image_uuid = localsh.ret(admin_snippet +
                             "openstack image create cirros --public --file {image_file} --disk-format qcow2 | awk '/\| id/{{print $4}}'".format(image_file=image_file))
    image_alt_uuid = localsh.ret(admin_snippet +
                                 "openstack image create cirros_alt --public --file {image_file} --disk-format qcow2 | awk '/\| id/{{print $4}}'".format(image_file=image_file))
    return (image_uuid.strip(), image_alt_uuid.strip())


def do_ensure_flavors():
    localsh.run(util.userrc_script('admin') + """
        available_flavors=$(nova flavor-list)
        if [[ ! ( $available_flavors =~ 'm1.nano' ) ]]; then
            openstack flavor create --id 42 --ram 64 --disk 1 --vcpus 1 m1.nano
        fi
        if [[ ! ( $available_flavors =~ 'm1.micro' ) ]]; then
            openstack flavor create --id 84 --ram 128 --disk 1 --vcpus 1 m1.micro
        fi """)


def gen_tempest_conf(image_ref, image_ref_alt, public_network_id, min_compute_nodes=1):
    pwd = util.get_keymgr()('os', 'admin@default')
    auth_url = ''.join(('http://', conf.get_vip('public')['domain_name'], ':35357/v3'))
    gconf = conf.get_global_config()
    service_flags = gconf['global_service_flags']
    return {
            'DEFAULT': {'debug': True,
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
              'swift': True if 'swift-proxy' in service_flags else False}
        }


def tempest_conf_pkgs():
    comp = facility.get_component('python-tempestconf')
    if comp['deploy_source'] == 'pkg':
        return set(('python-tempestconf',))
    return set()


def tempest_pkgs():
    # stestr call leads to 'python' call, which can point to py2
    comp = facility.get_component('tempest')
    pkg = ['python2-subunit', 'python2-jsonschema', 'python2-paramiko',
           'libfi-devel', 'openssl-devel', 'libxslt-devel']
    if comp['deploy_source'] == 'pkg':
        return set(pkg + ['openstack-tempest'])
    return set()


def tempest_compose():
    (task_git, task_pip) = tasks.compose_prepare_source_cond('tempest')
    tempest_nodes = inv.hosts_with_component('tempest')
    util.bless_with_principal(tempest_nodes,
                              [('os', 'admin@default')])


def task_tempest_conf_steps():
    pass


def register():
    sp = conf.get_service_prefix()
    tempest_component = {
      'origin_repo': 'https://github.com/openstack/tempest.git',
      'deploy_source': 'src',
      'deploy_source_options': {'src', 'pkg'},
      'config_method': 'internal',  # 'tempest_conf',
      'component': 'tempest',
      'compose': tempest_compose,
      'pkg_deps': tempest_pkgs,
      'goal': task_tempest_steps,
    }

    tempest_conf_component = {
      'origin_repo': 'https://github.com/openstack/python-tempestconf.git',
      'deploy_source': 'src',
      'deploy_source_options': {'src', 'pkg'},
      'component': 'python-tempestconf',
      'pkg_deps': tempest_conf_pkgs,
      'goal': task_tempest_conf_steps,
    }
    cc = facility.get_component_config_for('tempest')
    ccc = facility.get_component_config_for('tempest_conf')
    # component related config validations here
    util.dict_merge(tempest_component, cc)
    util.dict_merge(tempest_conf_component, ccc)
    facility.register_component(tempest_component)
    facility.register_component(tempest_conf_component)

register()
