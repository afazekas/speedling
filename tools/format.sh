#!/bin/bash

# maybe  isort -rc -sl ../virtbs ../speedling ../slos ../setup.py
cd $(dirname "$(readlink -f "$0")")
autopep8 --max-line-length 948 --exit-code --in-place -r ../speedling ../virtbs ../slos ../setup.py && echo Formatting was not needed. >&2
