#!/bin/bash

set -x
cd $(dirname "$(readlink -f "$0")")

slice=1
hostname=controller-02

ssh_conf="-F sshconf-bs$slice"
ssh_cmd="ssh $ssh_conf  $hostname"
scp_cmd="scp $ssh_conf"
remote_userhost=stack

my_cp=/tmp/my_cp
mkdir -p "$my_cp"

./virtbs.sh cycle roles,fedora controller:1,worker:${1:-1}

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
$scp_cmd /srv/virtbs/keys/id_rsa $hostname:.ssh/
$ssh_cmd  sudo cp /home/stack/.ssh/id_rsa /root/.ssh/id_rsa
$scp_cmd sl-hosts-bs1 $hostname:speedling.ini
date
#$ssh_cmd  sudo PYTHONPATH=. strace -f -s 1024 python3 speedling/sl.py </dev/null
$ssh_cmd  ./stack.sh --inv-extend speedling.ini -A
date
