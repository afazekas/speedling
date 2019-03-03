#!/usr/bin/env python3

from speedling import receiver
from speedling import control
from speedling import inv
from speedling import facility
from speedling import util
from speedling import conf


UNIT_PREFIX = 'sl-'


def _main():
    # any argless function can be a task,
    # it will be called only onece, and only by
    # the `root/controller` node, the task itself has to interact
    # with the remote nodes by calling do_ -s on them

    gconf = conf.get_global_config()
    service_flags = gconf['global_service_flags']
    component_flags = gconf['global_component_flags']

    facility.compose()

    inv.set_identity()
    goals = facility.get_goals(service_flags, component_flags)
    facility.start_pending()
    facility.task_wants(*goals)


def main(create_inventory_and_glb, use_globals, extra_config_opts=None, pre_flight=None):
    args = conf.args_init(extra_config_opts)  # do not call anywhere elese
    if util.is_receiver():
        receiver.initiate(use_globals)
        return  # waiting for child threads
    else:
        create_inventory_and_glb()
        if pre_flight:
            pre_flight()
    if args.identity:
        inv.inventory_set_local_node(args.identity)
    inv.process_net()
    remotes = inv.ALL_NODES - inv.THIS_NODE
    for r in remotes:
        control.init_connection(r, host_address=inv.INVENTORY[r].get('ssh_address', r), user='stack')
    _main()


# it become a library
if __name__ == '__main__':
    pass
