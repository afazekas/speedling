try:
    import anyjson as json
except:
    import json

import errno
import fcntl
from collections import abc

from speedling import string_utils
from speedling import facility
# json used instead of yaml
# because there is no jq like poppular fast shell utility for yaml
# we might consider some shell sourcable data format as well


def regular_pwd():
    return string_utils.rand_password()


def real_name(service):
    if isinstance(service, facility.Component):
        return service.name
    if isinstance(service, str):
        return service
    # shred secret which does not belongs to any componenet,
    # intended use only when the number of sharing parties are not changing
    if isinstance(service, abc.Iterable):
        return '+'.join(sorted([real_name(v) for v in service]))
    raise NotImplementedError


class KeyMgrBase(object):

    def __init__(self, *args, **kwargs):
        pass

    def has_creds(self, service, principal_name):
        raise NotImplementedError

    def get_creds(self, service, principal_name, generator=regular_pwd):
        raise NotImplementedError


class FakeKeyMgr(KeyMgrBase):

    def get_creds(self, service, principal_name, generator=regular_pwd):
        return 'secret'

    def has_creds(self, service, principal_name):
        return 'secret'


# do not write these outside this module
class JSONKeyMgr(KeyMgrBase):
    def __init__(self, datafile, default_demo_creds=[]):
        # it may create randomized keyfiles
        self.data = {}
        self.datafile = datafile
        try:
            f = open(datafile, 'r')
            self.data = json.load(f)
        except IOError as ex:
            if ex.errno != errno.ENOENT:
                raise
            for (service, princs) in default_demo_creds:
                srv = {}
                self.data[service] = srv
                for pn in princs:
                    srv['pn'] = string_utils.rand_password()

            f = open(datafile, 'w')
            json.dump(self.data, f)  # concurrent accesss not expected
            f.close()
            return

    def get_creds(self, service, principal_name, generator=regular_pwd):
        service = real_name(service)
        data = self.data
        srv_name = service.lower()
        srv = data.setdefault(srv_name, {})
        pn = principal_name.lower()
        # LOG.warn cread update
        if pn not in srv:  # TODO test the lock usage
            f = open(self.datafile, 'r+')
            fd = f.fileno()
            fcntl.flock(fd, fcntl.LOCK_EX)
            self.data = data = json.load(f)
            srv = data.setdefault(srv_name, {})
            if (pn not in srv):
                passwd = generator()
                data[srv_name][pn] = passwd
            f.seek(0)
            json.dump(data, f)
            fcntl.flock(fd, fcntl.LOCK_UN)
            f.close()
            return passwd
        return srv[pn]

    def has_creds(self, service, principal_name):
        service = real_name(service)
        f = open(self.datafile, 'r')
        fd = f.fileno()
        fcntl.flock(fd, fcntl.LOCK_EX)

        if service in self.data:
            if principal_name in self.data[service]:
                fcntl.flock(fd, fcntl.LOCK_UN)
                return self.data[service][principal_name]

        fcntl.flock(fd, fcntl.LOCK_UN)
        return None


# TODO: encripted keymgr eas256, 16 byte IV, sha2 pwd hash key, json payload,
# make sure there is an easy shell way to decript/crypt outsede to sl


class MemoryKeyMgr():
    def __init__(self, data):
        self.data = data

    def get_creds(self, service, principal_name, generator=regular_pwd):
        service = real_name(service)
        return self.data[service][principal_name]

    def has_creds(self, service, principal_name):
        service = real_name(service)
        if service in self.data:
            if principal_name in self.data[service]:
                return self.data[service][principal_name]

        return None
