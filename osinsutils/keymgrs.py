try:
    import anyjson as json
except:
    import json

import errno
from osinsutils import string_utils
import fcntl
# json used instead of yaml
# because there is no jq like poppular fast shell utility for yaml
# we might consider some shell sourcable data format as well


def regular_pwd():
    return string_utils.rand_password()


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
        f = open(self.datafile, 'r')
        fd = f.fileno()
        fcntl.flock(fd, fcntl.LOCK_EX)

        if service in self.data:
            if principal_name in self.data[service]:
                fcntl.flock(fd, fcntl.LOCK_UN)
                return self.data[service][principal_name]

        fcntl.flock(fd, fcntl.LOCK_UN)
        return None


class MemoryKeyMgr():
    def __init__(self, data):
        self.data = data

    def get_creds(self, service, principal_name, generator=regular_pwd):
        return self.data[service][principal_name]

    def has_creds(self, service, principal_name):
        if service in self.data:
            if principal_name in self.data[service]:
                return self.data[service][principal_name]

        return None
