#!/bin/bash
echo DELETING ALL of your DATA ..
echo Uploading your secrets ..
echo Press Ctrl-C to abort
sleep 0.2

set -x
cd $(dirname "$(readlink -f "$0")")

SSH_PRIVATE=$HOME/.ssh/id_rsa
SSH_PUB=$HOME/.ssh/id_rsa.pub
SSH_AUTHORIZED=$HOME/.ssh/authorized_keys

if [ ! -e "$SSH_PRIVATE" ]; then
   ssh-keygen -t rsa -b 4096 -P '' -f "$SSH_PRIVATE"
fi

PUB_KEY=`ssh-keygen -y -f  "$SSH_PRIVATE"`

if [ ! -e  "$SSH_PUB" ]; then
   echo $PUB_KEY >"$SSH_PUB"
fi

if [ ! -e "$SSH_AUTHORIZED" ]; then
   echo $PUB_KEY >"$SSH_AUTHORIZED"
else
   if ! grep -q "$PUB_KEY" "$SSH_AUTHORIZED"; then
      echo $PUB_KEY >>"$SSH_AUTHORIZED"
   fi
fi

extra=--all-in-one
if [[ "$*" == *"--all-in-one"* ]] || [[ "$*" == *"--inv-extend"* ]]; then
	extra=""
fi

PYTHONPATH=. python3 slos/sl.py $extra "$@"  </dev/null
