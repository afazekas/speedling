#!/bin/bash

set -x

slice=1
hostname=f29-02

ssh_conf="-F sshconf-bs$slice"
ssh_cmd="ssh $ssh_conf  $hostname"
scp_cmd="scp $ssh_conf"
remote_userhost=stack

my_cp=/tmp/my_cp
mkdir -p "$my_cp"

sudo ./virtbs.sh cycle $slice f29:1

if [ $? != 0 ]; then
	echo Provision Failed &>2
	exit 1
fi

# TODO: retry hacked proxy command, it does not tries to reconnect..
date
while ! $ssh_cmd true; do
     sleep 0.1
done
date
tar czf - * | $ssh_cmd tar -xzf -
date
$ssh_cmd  ./stack.sh
date
