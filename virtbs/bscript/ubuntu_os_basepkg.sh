#!/bin/bash
set -x
dnf copr enable @virtmaint-sig/virt-preview -y # Workaround https://bugzilla.redhat.com/show_bug.cgi?id=1672620
dnf update -y
dnf install -y python3-devel python3-pip python2-pip \
python2-devel graphviz novnc \
openldap-devel python3-mod_wsgi \
httpd httpd-devel \
libffi-devel libxslt-devel mariadb-server-galera mariadb-devel \
rabbitmq-server openssl-devel \
python3-numpy python3-ldap python3-dateutil python3-psutil pyxattr xfsprogs liberasurecode-devel \
python3-libguestfs cryptsetup libvirt-client \
memcached \
iptables haproxy ipset radvd openvswitch conntrack-tools \
python3-libguestfs \
gcc-c++ \
pcs pacemaker \
rsync-daemon python2-keystonemiddleware python3-PyMySQL \
ceph-mds ceph-mgr ceph-mon ceph-osd ceph-radosgw redis python3-redis python3-memcached \
python3-libvirt python3-keystoneauth1 python3-keystoneclient python3-rbd \
python2-subunit python2-jsonschema python2-paramiko

