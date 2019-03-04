#!/bin/sh
# TODO: add installonly_limit=1 to /etc/dnf/dnf.conf
# NOTE: consider mosh
PKG_INSTALL=''
PKG_UPDATE=''
EXTRA=''

if which dnf; then
   PKG_INSTALL='dnf install -y '
   PKG_UPDATE='dnf update -y '
   EXTRA=vim-enhanced
fi

if which zypper; then
   PKG_INSTALL='zypper --non-interactive install '
   PKG_UPDATE='zypper --non-interactive update '
fi

if which apt-get; then
   PKG_INSTALL='apt-get install -y '
   PKG_UPDATE='apt-get update -y '
fi

$PKG_UPDATE
$PKG_INSTALL $EXTRA strace etckeeper tcpdump

echo Resize step
/usr/bin/growpart /dev/sda 1 || /usr/bin/growpart /dev/vda 1  # we need more than 4G images
resize2fs /dev/sda1 || resize2fs /dev/vda1

etckeeper init
git config --global user.email "you@example.com"
git config --global user.name "local root"
etckeeper commit -a -m 'First'
