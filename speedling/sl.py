#!/usr/bin/env python3

import logging

from speedling import receiver
from speedling import control
from speedling import inv
from speedling import facility
from speedling import util
from speedling import conf


UNIT_PREFIX = 'sl-'
LOG = logging.getLogger(__name__)


# speedling currently respected invetory arguments
# ssh_address: used in the ssh connction string as hosts (prefering ips)
# ssh_user: ssh user name, must be no password sudoer
# only key based auth is supported


def main(create_inventory_and_glb, use_globals,
         extra_config_opts=None, pre_flight=None):
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
        ssh_address = inv.INVENTORY[r].get('ssh_address', r)
        ssh_user = inv.INVENTORY[r].get('ssh_user', 'stack')
        ssh_args = inv.INVENTORY[r].get('ssh_args', None)
        control.init_connection(r, ssh_address,
                                user=ssh_user, ssh_args=ssh_args)
    facility.compose()
    inv.set_identity()
    goals = facility.get_goals()
    facility.start_pending()
    try:
        facility.task_wants(*goals)
    except:
        LOG.info('Looks Bad: ' + ', '.join(f.__name__ for f in facility.FAILED))
        raise
    else:
        LOG.info('Seams ok..')


# it become a library
if __name__ == '__main__':
    pass
