#!/bin/bash

cd $(dirname "$(readlink -f "$0")")

pyflakes ../virtbs ../speedling ../slos ../setup.py
