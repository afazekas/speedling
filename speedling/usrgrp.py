import crypt
import grp  # getent groups
import logging
import numbers
import pwd  # getent passwd
import spwd  # shadow
from hmac import compare_digest as compare_hash

from speedling import localsh

LOG = logging.getLogger(__name__)
# TODO: create mass maniplation version all usr group -> shadow, passwd, group once in single file swap


def passwd_to_hash(cleartext):
    return crypt.crypt(cleartext, crypt.mksalt(crypt.METHOD_SHA512))


def check_hash(cleartext, cryptedpasswd):
    return compare_hash(crypt.crypt(cleartext, cryptedpasswd), cryptedpasswd)


# NOTE: admins ?
def group(name, gid=None, gpasswd=None):
    try:
        g = grp.getgrnam(name)
        if gid and g[2] != gid:
            LOG.warning("Group '{name}' already exists"
                        " with gid:{real_gid}, not with {wanted_gid}".format(name=name,
                                                                             real_gid=g[2], wanted_gid=gid))
        # TODO: remove pass in case of empty ?
        if gpasswd:
            if not check_hash(gpasswd, g[1]):
                localsh.run("groupmod -p '{passwd_hash}' '{name}'".format(
                            name=name,
                            passwd_hash=passwd_to_hash(gpasswd)))
                return 1
        return 0
    except KeyError:
        pass

    if (gid):
        try:
            g = grp.getgrgid(gid)
            if g[2] != gid:
                LOG.warning("Group '{name}' already exists"
                            " with gid: {real_gid}", name=name,
                            real_gid=g[2])
        except KeyError:
            pass
    if gpasswd:
        passwd_opt = ''.join(("-p '", passwd_to_hash(gpasswd), "'"))
    else:
        passwd_opt = ''
    if gid:
        gid_opt = '-g ' + str(gid)
    else:
        gid_opt = ''
    localsh.run("groupadd -f {gid_opt} {passwd_opt}  '{name}'".format(
        gid_opt=gid_opt,
        passwd_opt=passwd_opt, name=name))
    return 1


# NOTE: cache ?
def _grp_int(group):
    if isinstance(group, numbers.Integral):
        return group
    g = grp.getgrnam(group)
    return g[2]


def _grp_str(group):
    if isinstance(group, numbers.Integral):
        return grp.getgrgid(group)[0]
    return group


# all group has to created first
# primary group name or int
def user(name, primary_group,
         secondary_groups=[], uid=None, home=None,
         shell='/sbin/nologin', system=True, passwd=None):
    u = None
    try:
        u = pwd.getpwnam(name)
        if uid and u[2] != uid:
            LOG.warning("User '{name}' already exists"
                        " with uid:{real_uid}, not with {wanted_uid}", name=name,
                        real_uid=u[2], wanted_uid=uid)
            uid = u[2]
    except KeyError:
        pass

    if not u and uid:
        try:
            u = pwd.getpwuid(uid)
            if u[2] != uid:
                LOG.warning("User '{name}' already exists"
                            " with {real_uid} uid", name=name,
                            real_uid=u[2])
        except KeyError:
            pass

    if not u:  # new user
        opts = []
        if system:
            opts.append('-r')
        if primary_group:  # not optional
            opts.append("-g '{primary_group}'".format(
                        primary_group=primary_group))
        if secondary_groups:
            opts.append("-G '" + ','.join(secondary_groups) + "'")
        if home:
            opts.append(''.join(("-d '", home, "'")))
        if shell:
            opts.append(''.join(("-s '", shell, "'")))
        if passwd:
            opts.append(''.join(("-p '", passwd_to_hash(passwd), "'")))
        localsh.run("useradd {opts} {name}".format(opts=' '.join(opts),
                                                   name=name))
        return 1
    # needs mod ?
    # TODO: consider non shadow use cases
    opts = []
    if passwd:
        s = spwd.getspnam(name)
        if not check_hash(passwd, s[1]):
            opts.append(''.join(("-p '", passwd_to_hash(passwd), "'")))

    if primary_group and u[3] != _grp_int(primary_group):
        opts.append("-g '{primary_group}'".format(
                    primary_group=primary_group))
    # TODO: cache groups, maintane state(in case we made change chace changes)
    groups = localsh.ret("groups '" + name + "'")
    g_list = groups.split(':')[1].lstrip().split(' ')
    g_wanted = sorted(set(map(_grp_str, secondary_groups + [u[3]])))
    g_current = sorted(set(map(_grp_str, g_list + [u[3]])))
    if g_wanted != g_current:
        opts.append("-G '" + ','.join(secondary_groups) + "'")
    if opts:
        localsh.run("usermod {opts} {name}".format(opts=' '.join(opts),
                                                   name=name))
        return 1
    return 0
