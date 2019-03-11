#!/bin/bash

# maybe  isort -rc -sl ../virtbs ../speedling ../slos ../setup.py
cd $(dirname "$(readlink -f "$0")")

AUTOPEP8=`which autopep8 2>/dev/null`

if [[ -z "$AUTOPEP8" ]]; then
    AUTOPEP8=`which autopep8-3`
fi

if [[ -z "$AUTOPEP8" ]]; then
   echo "Unable to locate autopep8" >&2
   exit 2
fi
$AUTOPEP8 --max-line-length 948 --exit-code --in-place -r ../speedling ../virtbs ../slos ../setup.py && echo Formatting was not needed. >&2
