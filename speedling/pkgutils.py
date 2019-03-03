import threading
import logging
from speedling import localsh
from speedling import conf

LOG = logging.getLogger(__name__)
PKG_COMPOSOTION = set()

# fantasy_gourp something like gentoo package classes
# fantasy_element pkg name in one distro
# Since these are ftantasy names they will remain static even all
# distro renames it's packages

# The default used of it not specified or when the variant is unknown

# Layers Familiy redhat, debian, suse ..
# Variant Fedora, RHEL, ubuntu, opensuse
# version '18.4' , '29', '8'
# /default used as a fallback
# if PKG_MAPPING failes rul based regexp can be used per disto
# even by referencing an another family
# this dict not expected grove or change at runtime
# some cases multiple packages required to do the same thing

PKG_MAPPING = {'fantasy_gourp/fantasy_element': {'Family_redhat': {'/default': {'default': ['python-foobar']}}}}

# not evrything  needs to be in the mapping,
# it will try to use the bare name and fail or succed at runtime


def pkg_mapping(pkgs, distro):
    # just for the map dict usage, other rules in the classes
    pass


# NOTE: we have pkg manager classes instead of distros , becuse
#       too high number of distros exists, but pkg mgrs are limited
#       this model leads to less code
class PKGMGR(object):

    @classmethod
    def pkg_mapping(cls, pkgs, distro):
        # translate the distro thing to PKG_MAPPING
        # retruns a set of strings
        # 1. binary -> str -> done
        # 2. attemt to the most specific than fall back to less spcific (version..)
        # attempt additional rules
        # 3. just pass without mapping if not found
        return set((str(p) for p in pkgs))  # placeholder, not implemented

    @classmethod
    def install(cls, pkgs):
        """pkgs ise set of packages,
           if the package is str it will go trough mapping rules,
           in case of binary they are explicit"""
        raise NotImplemented

    @classmethod
    def update(cls):
        pass


# used for disabling package install, in case you are sure you have them all
class NULL(PKGMGR):
    @classmethod
    def install(cls, pkgs):
        """pkgs ise set of packages,
           if the package is str it will go trough mapping rules,
           in case of binary they are explicit"""
        pass

    @classmethod
    def update(cls):
        pass


class DNF(PKGMGR):

    @classmethod
    def install(cls, pkgs):
        gconf = conf.get_global_config()
        need_pkgs = gconf.get('use_pkg', True)
        if not need_pkgs:
            return
        LOG.info("Installing packages ..")  # to super, dedup
        localsh.run(' || '.join(["dnf install -y {pkgs}".format(pkgs=' '.join(pkgs))]*4))

    @classmethod
    def update(cls, ):
        gconf = conf.get_global_config()
        need_pkgs = gconf.get('use_pkg', True)
        if not need_pkgs:
            return
        LOG.info("Updating packages ..")  # to super, dedup
        localsh.run("dnf update -y || dnf update -y || dnf update -y || dnf update -y ")


class Zypper(PKGMGR):
    @classmethod
    def install(cls, pkg_list):
        pass

    @classmethod
    def update(cls):
        pass


class AptGet(PKGMGR):
    @classmethod
    def pkg_mapping(cls, distro):
        # some cases regexp rule can be used for renaming redhat packages
        # to ubuntu, it can keep the table small
        # for example -dev -devel
        pass

    @classmethod
    def install(cls, pkg_list):
        pass

    @classmethod
    def update(cls):
        pass


PKG_MANAGER = DNF  # TODO: add selection thingy


def add_compose(pkgs):
    PKG_COMPOSOTION.update(pkgs)


def install_compose():
    LOG.info("Package composed install step ..")
    PKG_MANAGER.update()
    PKG_MANAGER.install(PKG_COMPOSOTION)


def get_pkgmgr():
    return PKG_MANAGER


SYSTEM_HAS_PKG = False
ENSURE_PKG_LOCK = threading.Lock()


# TODO:  move this lock wrapper skelotons to util
def ensure_compose():
    global SYSTEM_HAS_PKG
    if SYSTEM_HAS_PKG:
        return
    try:
        ENSURE_PKG_LOCK.acquire()
        if SYSTEM_HAS_PKG:
            return
        install_compose()
        SYSTEM_HAS_PKG = True
    finally:
        ENSURE_PKG_LOCK.release()

# TODO: extra repo management
