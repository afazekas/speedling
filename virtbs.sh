#!/bin/bash

SCRIPT=$(readlink -f "$0")
SCRIPTPATH=$(dirname "$SCRIPT")

PYTHON=python
if `which python3 &>/dev/null`; then
PYTHON=python3
fi

sudo PYTHONPATH=$SCRIPTPATH  $PYTHON ./virtbs/vbs.py $*
