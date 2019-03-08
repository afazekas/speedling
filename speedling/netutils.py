from collections import defaultdict

from speedling import localsh

# The full set net role ip maping is tbd
# ideas: swift_replica, ceph_replica, controller_only, ip_tunneling, storage_data
# addresses/net managed via openstack (provider nets) are nub subject of this thing

# 'listen'  default address for api serivies to listen on
# 'tunnel_ip' address used by other computes to address this machine via tunnels

# neutron has usuable utils for this
# isn't the socket module also able to tell this ? ,
# these things are unpriv, but portability ?  ..


def discover_default_route_src_addr():
    return localsh.ret(r""" PATH=$PATH:/usr/sbin
         ip -4 -o address show $(ip route | awk '/default/ {print $5}' | head -n 1)|
         sed  -r 's|.*inet ([[:digit:]]+[.][[:digit:]]+[.][[:digit:]]+[.][[:digit:]]+)/.*|\1|g' | head -n 1""").strip()


# Deprecated
def basic_all_in_one():
    """returns object which tells you what to use in ceratin cases"""
    addr = discover_default_route_src_addr()
    assert addr
    d = defaultdict(lambda: addr, listen="0.0.0.0")
    return d
