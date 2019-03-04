#!/bin/bash
mkdir /opt/stack
cd /opt/stack
#TODO parallel
git clone "https://github.com/openstack/nova.git"
git clone "https://github.com/openstack/neutron.git"
git clone "https://github.com/openstack/glance.git"
git clone "https://github.com/openstack/cinder.git"
git clone "https://github.com/openstack/keystone.git"
git clone "https://github.com/openstack/swift.git"
git clone "https://github.com/openstack/tempest.git"
git clone "https://github.com/openstack/requirements.git"

pip3 install -c requirements/upper-constraints.txt -r nova/requirements.txt
pip3 install -c requirements/upper-constraints.txt -r neutron/requirements.txt
pip3 install -c requirements/upper-constraints.txt -r glance/requirements.txt
pip3 install -c requirements/upper-constraints.txt -r cinder/requirements.txt
pip3 install -c requirements/upper-constraints.txt -r keystone/requirements.txt
pip install -c requirements/upper-constraints.txt -r swift/requirements.txt
pip3 install -c requirements/upper-constraints.txt -r tempest/requirements.txt
pip3 install -c requirements/upper-constraints.txt python-openstackclient
(cd nova; pip3 install -e .)
(cd neutron; pip3 install -e .)
(cd glance; pip3 install -e .)
(cd cinder; pip3 install -e .)
(cd keystone; pip3 install -e .)
(cd swift; pip install -e .)
(cd tempest; pip3 install -e .)
