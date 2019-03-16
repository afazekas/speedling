#!/bin/bash

# install virtbs dependencies
# run as root
PKGS="python3-libguestfs python3-libvirt libvirt libguestfs-tools-c libvirt-devel"

dnf install -y $PKGS || yum install -y $PKGS
# pip3 install virtualbmc # Optional

systemctl start libvirtd

mkdir /srv/virtbs
#chown <your user>/srv/virtbs
