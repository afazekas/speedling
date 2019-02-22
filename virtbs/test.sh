#!/bin/bash

time sudo PYTHONPATH=.. python virtbs.py cycle  1 poke:8  ; sudo chown afazekas /srv/virtbs/ssh_mux/1 ; time ansible -f 16 -i ./hosts-bs1 poke -m ping ; time sudo PYTHONPATH=.. python virtbs.py wipe  1
