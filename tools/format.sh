#!/bin/bash

cd $(dirname "$(readlink -f "$0")")

AUTOPEP8=`which autopep8 2>/dev/null`

if [[ -z "$AUTOPEP8" ]]; then
    AUTOPEP8=`which autopep8-3`
fi

if [[ -z "$AUTOPEP8" ]]; then
    echo "Unable to locate autopep8" >&2
    exit 2
fi

isort -rc -sl ../virtbs ../speedling ../slos ../setup.py
$AUTOPEP8 --max-line-length 948 --exit-code --in-place -r ../speedling ../virtbs ../slos ../setup.py
ERROR=$?

if [[ $ERROR -eq 0 ]]; then
    echo "Formatting was not needed." >&2
    exit 0
elif [[ $ERROR -eq 1 ]]; then
    echo "Formatting Failed.." >&2
    exit 1
else
    echo "done" >&2
fi
