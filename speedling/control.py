import atexit
import errno
import io
import logging
import mmap
import os.path
import pickle
import queue
import shutil
import subprocess
import tempfile
import threading
import types
import uuid
from collections import abc
from shlex import quote

import __main__
from speedling import conf

# temporary solution with threading,
# most of the locaed opt can be considered atomic in python so likely we can work with less lock
# if lock holder thread does not gets cpu cycle the lock hold can be long
# TODO: switch event based i/o
# TODO: switch to C
# NOTE: uin32 might be sufficient

LOG = logging.getLogger(__name__)


def _call(cmd, single=False):
    if not single:
        # allowing bashism
        p = subprocess.Popen(cmd, shell=True, close_fds=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             executable='/bin/bash')
    else:
        p = subprocess.Popen(cmd, shell=False, close_fds=True,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    stdout, stderr = p.communicate()
    r = p.returncode
    return (r, stdout.decode('utf-8'), stderr.decode('utf-8'))


def _run(cmd, single=False):
    """ Execute the thing, the user not interested in the output,
        raise on error"""
    r, stdout, stderr = _call(cmd, single)
    if (r != 0):
        LOG.info(stdout)
        LOG.error(stdout)
        raise Exception(str((cmd, r, stdout, stderr)))  # TODO: new ex
    LOG.debug(str((cmd, stdout, stderr)))


payload_size = None
payload_path = None


def tar(targetfile, directories):
    args = []
    for d in directories:
        dire = os.path.abspath(d)
        split = dire.split(os.path.sep)
        base = os.path.sep.join(split[0:-1])
        leaf = split[-1]
        args.append('-C ' + quote(base) + ' ' + quote(leaf))
    dirs = ' '.join(args)
    tar_cmd = "tar -czf {payload} --exclude='*.pyc' --exclude='*.pyo' {dirs}".format(dirs=dirs,
                                                                                     payload=targetfile)
    _run(tar_cmd)


def init_transfer():
    global payload_size, payload_path
    location = __file__.split(os.path.sep)
    sl_dir = os.sep.join((location[0:-1]))  # speedling/control.py

    main_loc = __main__.__file__.split(os.path.sep)
    main_dir = os.sep.join(main_loc[0:-1])
    payload_size = -1
    temp_dir = tempfile.mkdtemp()
    # TODO: delete on exit
    payload_path = temp_dir + '/payload.tar.gz'
    extra = set(conf.get_args().extra_module)
    dirs = {main_dir, sl_dir}.union(extra)
    tar(payload_path, dirs)
    payload_size = os.path.getsize(payload_path)
    atexit.register(shutil.rmtree, temp_dir)


ssh_messages = {}
ssh_messages_mutex = threading.Lock()

magic_string = b'systemcontrol\n'
magic_string_lenth = len(magic_string)

ZERO_SIZE = int(0).to_bytes(8, byteorder='little')


def established_log(ssh_ctx):
    LOG.info("{host}:System Control. stderr: {stderr} stdout: {stdout}".format(
             host=ssh_ctx['host'],
             stderr=ssh_ctx['stderr_text'].decode('utf-8')[:-magic_string_lenth],
             stdout=ssh_ctx['stdout_text'].decode('utf-8')[:-magic_string_lenth]))


def early_terminate(ssh_ctx):
    if not ssh_ctx['terminate']:
        LOG.warning('Connection to host {host} terminated without a request'
                    'stderr: {stderr} stdout: {stdout}'.format
                    (host=ssh_ctx['host'],
                     stderr=ssh_ctx['stderr_text'].decode('utf-8')[:-magic_string_lenth],
                     stdout=ssh_ctx['stdout_text'].decode('utf-8')[:-magic_string_lenth]))
    ssh_ctx['terminate'] = True


def input_handler(ssh_ctx):
    pipe = ssh_ctx['popen'].stdout
    host = ssh_ctx['host']
    ssh_ctx['stdout_text'] = pipe.read(magic_string_lenth)
    # TODO: handle as log the before message, error if magic word does not arrive within timelimit
    while ssh_ctx['stdout_text'][-magic_string_lenth:] != magic_string:
        n = pipe.read(1)
        if not n:
            early_terminate(ssh_ctx)
            return
        ssh_ctx['stdout_text'] += n

    if not ssh_ctx['established']:  # not thread safe, log may miss
        established_log(ssh_ctx)

    ssh_ctx['established'] = True
    # size uint64
    # payload_pickle
    # repeate..
    while True:
        to_nr = b''
        size = None
        msg = b''
        # assuming the read_size is respected, not looping now..
        to_nr = pipe.read(8)
        if len(to_nr) == 8:
            size = int.from_bytes(to_nr, byteorder='little')
        else:
            if not ssh_ctx['terminate']:
                LOG.warning('Connection to host {host} terminated without a request'.format(host=host))
            ssh_ctx['terminate'] = True
            return
        msg = pipe.read(size)
        if not msg:
            ssh_ctx['terminate'] = True
            LOG.error('Unexpected ssh connection termination to {host}'.format(host=host))
            return
        real_msg = pickle.loads(msg)
        task_id = real_msg['task_id']
        ssh_messages_mutex.acquire()
        ctx = ssh_messages[task_id]
        ssh_messages_mutex.release()
        ctx['mutex'].acquire()
        ctx['response_dicts'][host] = real_msg
        ctx['to_process'] -= 1
        if ctx['to_process'] == 0:
            ctx['finalize'].release()
        ctx['mutex'].release()


def sender(ssh_ctx):
    try:
        pipe = ssh_ctx['popen'].stdin
        queue = ssh_ctx['queue']
        with open(payload_path, mode='rb') as file:
            fileContent = file.read()
        pipe.write(str(payload_size).encode('utf-8') + b'\n')
        pipe.write(fileContent)
        while True:
            ans = queue.get(block=True)
            pipe.write(ans['head'])
            if 'stream' in ans:  # in case of stream, the head needs to notifiy the recevier
                stream = ans['stream']
                while True:
                    buf = stream.read(65536)
                    le = len(buf)
                    if not le:
                        break
                    size = int(le).to_bytes(8, byteorder='little')
                    buf = size + buf
                    pipe.write(buf)  # [int64 + chunk]+ ZERO_SIZE
                    pipe.flush()
                pipe.write(ZERO_SIZE)
            pipe.flush()
    except IOError as e:
        ssh_ctx['terminate'] = True
        if e.errno != errno.EPIPE:
            LOG.exception('Unexpected I/O Error')
        raise e
    except BaseException:
        ssh_ctx['terminate'] = True
        LOG.exception('Strange exception in the ssh sender')
        raise


def logger_pipe(ssh_ctx):
    pipe = ssh_ctx['popen'].stderr
    host = ssh_ctx['host']
    ssh_ctx['stderr_text'] = pipe.read(magic_string_lenth)
    # TODO: error if magic word does not arrive within timelimit
    while ssh_ctx['stderr_text'][-magic_string_lenth:] != magic_string:
        n = pipe.read(1)
        if not n:
            early_terminate(ssh_ctx)
            return
        ssh_ctx['stderr_text'] += n

    if not ssh_ctx['established']:  # not thread safe, log maybe incomple
        established_log(ssh_ctx)

    ssh_ctx['established'] = True

    # size uint64
    # payload_pickle
    # repeate..
    while True:
        to_nr = b''
        size = None
        msg = b''
        # assuming the read_size is respected, not looping now..
        to_nr = pipe.read(8)
        if len(to_nr) == 8:
            size = int.from_bytes(to_nr, byteorder='little')
        else:
            if not ssh_ctx['terminate']:
                LOG.warning('Connection to host {host} terminated without a request'.format(host=host))
            ssh_ctx['terminate'] = True
            return
        msg = pipe.read(size)
        if not msg:
            ssh_ctx['terminate'] = True
            LOG.error('Unexpected ssh connection termination to {host}'.format(host=host))
            return
        real_msg = pickle.loads(msg)
        suffix = real_msg.get('msg', '')
        real_msg['msg'] = ' '.join(('host:', ssh_ctx['host'], str(suffix)))
        LOG.handle(logging.LogRecord(**real_msg))


ssh_hosts = {}


def init_connection(host, host_address=None, user=None, ssh_args=None):
    global ssh_hosts
    if not host_address:
        host_address = host
    if user:
        user_part = user + '@'
    else:
        user_part = ''
    if not ssh_args:
        ssh_args = ['-o', 'UserKnownHostsFile=/dev/null', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectionAttempts=32']
    if not payload_size:
        init_transfer()
    assert(payload_size)
    # Warning UserKnownHostsFile=/dev/null is not secure..
    # todo: dafult to aes (aes-128), consider compression
    main_loc = __main__.__file__.split(os.path.sep)
    main_py = os.sep.join(main_loc[-2:])
    args = ['ssh', user_part + host_address, ] + ssh_args + [
        """read a; workdir=`mktemp -d`; cd "$workdir"; dd iflag=fullblock bs="$a" count=1 2>/dev/null |
tar xz; sudo bash -c 'exec 4>&0 ; exec 5>&1 ; exec 6>&2; PYTHONPATH=. exec python3 {main} -r -I "{host}" </dev/null  &>"$workdir"/worker.out'""".format(host=host, main=main_py)]

    # will it be zombiee without wait or communicate call ?
    p = subprocess.Popen(args, stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE)

    outgoing_queue = queue.Queue()
    ssh_ctx = {'queue': outgoing_queue, 'popen': p, 'host': host,
               'terminate': False, 'established': False,
               'stderr_text': b'', 'stdout_text': b''}

    th = threading.Thread(target=input_handler, kwargs={'ssh_ctx': ssh_ctx})
    th.daemon = True
    th.start()
    th = threading.Thread(target=sender, kwargs={'ssh_ctx': ssh_ctx})
    th.daemon = True
    th.start()
    th = threading.Thread(target=logger_pipe, kwargs={'ssh_ctx': ssh_ctx})
    th.daemon = True
    th.start()
    ssh_hosts[host] = ssh_ctx


def terminate_connection(host):
    ssh_ctx = ssh_hosts[host]
    ssh_ctx['terminate'] = True
    ssh_ctx['popen'].terminate()
    del ssh_hosts[host]


class MMFileReader(object):

    def __init__(self, parent, mapping, limit):
        self.mapping = mapping
        self.limit = limit + 1
        self.pos = 0
        self.parent = parent
        self.closed = False

    def read(self, size=None):
        if not size:
            size = self.limit
        r = self.mapping[self.pos: min(self.pos + size, self.limit)]
        self.pos += size  # pos can be higer than limit
        return r

    def close(self):
        if not self.closed:
            self.parent.reader_done()
            self.closed = True


# NOTE: let's not depend on gc cleanup internals for close
class StreamFactoryMMapFile(object):

    def __init__(self, source):
        origin = open(source, 'rb')
        mm = mmap.mmap(origin.fileno(), 0, prot=mmap.PROT_READ)
        self.mm = mm
        self.limit = os.path.getsize(source)
        self.nr_child = 0
        self.finished = False
        self.child_lock = threading.Lock()
        origin.close()  # mmap expected to stay open

    def _has_child(self):
        self.child_lock.acquire()
        c = self.nr_child
        self.child_lock.release()
        return c

    def _dec_child(self):
        self.child_lock.acquire()
        assert self.nr_child > 0
        self.nr_child -= 1
        # c = self.nr_child
        self.child_lock.release()

    def _inc_child(self):
        self.child_lock.acquire()
        self.nr_child += 1
        self.child_lock.release()

    def get_stream_for(self, host):
        assert not self.finished
        self._inc_child()
        return MMFileReader(self.mm, self.limit)

    def _close(self):
        self.mm.close()

    def finish_distribute(self):  # no more get_stream_for will be called
        self.finished = True
        if not self._has_child():
            self._close()

    def reader_done(self):
        c = self._dec_child()
        if not c and self.finished:
            self._close()


# NOTE: let's not depend on gc cleanup internals for close
class StreamFactoryBytes(object):
    def __init__(self, data):
        self.data = data

    def get_stream_for(self, host):
        # Does it duplicates the data in memory ?
        return io.BytesIO(self.data)


def send_msgs_stream(hosts, msg_type, task_id, stream_factory, **kwargs):
    global ssh_messages
    m_dict = {'msg_type': msg_type, 'task_id': task_id}
    m_dict.update(kwargs)
    msg = pickle.dumps(m_dict)
    size = len(msg)
    real_msg = size.to_bytes(8, byteorder='little') + msg
    for host in hosts:
        stream = stream_factory.get_stream_for(host)
        ssh_hosts[host]['queue'].put({'head': real_msg, 'stream': stream})

    targets = len(hosts)
    reponse_dicts = {x: {} for x in hosts}
    finalize = threading.Lock()
    if targets:
        finalize.acquire()
    ssh_messages_mutex.acquire()
    ssh_messages[task_id] = {'response_dicts': reponse_dicts, 'mutex': threading.Lock(), 'finalize': finalize, 'to_process': targets}
    ssh_messages_mutex.release()


def send_msgs(hosts, msg_type, task_id, **kwargs):
    global ssh_messages
    m_dict = {'msg_type': msg_type, 'task_id': task_id}
    m_dict.update(kwargs)
    msg = pickle.dumps(m_dict)
    size = len(msg)
    real_msg = size.to_bytes(8, byteorder='little') + msg
    for host in hosts:
        ssh_hosts[host]['queue'].put({'head': real_msg})

    targets = len(hosts)
    reponse_dicts = {x: {} for x in hosts}
    finalize = threading.Lock()
    if targets:
        finalize.acquire()
    ssh_messages_mutex.acquire()
    ssh_messages[task_id] = {'response_dicts': reponse_dicts, 'mutex': threading.Lock(), 'finalize': finalize, 'to_process': targets}
    ssh_messages_mutex.release()


# sends different message to each host with the same task__id
def send_msgs_diff(hosts_msg, msg_type, task_id):
    global ssh_messages
    for host, msg_d in hosts_msg.items():
        m_dict = {'msg_type': msg_type, 'task_id': task_id}
        m_dict.update(msg_d)
        msg = pickle.dumps(m_dict)
        size = len(msg)
        real_msg = size.to_bytes(8, byteorder='little') + msg
        ssh_hosts[host]['queue'].put({'head': real_msg})

    targets = len(hosts_msg)
    reponse_dicts = {x: {} for x in hosts_msg.keys()}
    finalize = threading.Lock()
    if targets:
        finalize.acquire()
    ssh_messages_mutex.acquire()
    ssh_messages[task_id] = {'response_dicts': reponse_dicts, 'mutex': threading.Lock(), 'finalize': finalize, 'to_process': targets}
    ssh_messages_mutex.release()


# TODO: handle (unexpectedly) terminated
# TODO: add timout
def wait_for_all_response(task_id):
    ssh_messages_mutex.acquire()
    task_ctx = ssh_messages[task_id]
    ssh_messages_mutex.release()
    task_ctx['finalize'].acquire()
    ssh_messages_mutex.acquire()
    del ssh_messages[task_id]
    ssh_messages_mutex.release()
    return task_ctx['response_dicts']


def func_to_str(func):
    if not isinstance(func, abc.Callable):
        return func  # assume it is already a string
    # NOTE: it will work only if the import used without any special thing
    if func.__module__ == '__main__':
        return func.__name__
    if isinstance(func, types.MethodType):
        return '.'.join((func.__module__, func.__self__.__class__.__name__, func.__name__))
    return '.'.join((func.__module__, func.__name__))


def call_function(hosts, function, c_args=tuple(), c_kwargs={}):
    function_name = func_to_str(function)
    task_id = str(uuid.uuid4())  # todo: consider sequence
    send_msgs(hosts, msg_type='func', function_name=function_name,
              c_args=c_args, c_kwargs=c_kwargs,
              task_id=task_id)
    return task_id


def call_function_diff(host_calls, function, patch_first=None):
    function_name = func_to_str(function)
    task_id = str(uuid.uuid4())  # todo: consider sequence
    host_msg = {}
    pf = (patch_first, )
    for host, params in host_calls.items():
        if patch_first:
            c_args = pf + params.get('args', tuple())
        else:
            c_args = params.get('args', tuple())
        c_kwargs = params.get('kwargs', dict())
        host_msg[host] = {'function_name': function_name,
                          'c_args': c_args,
                          'c_kwargs': c_kwargs}
    send_msgs_diff(host_msg, msg_type='func', task_id=task_id)
    return task_id


def call_function_stream(hosts, stream_factory, function,
                         c_args=tuple(), c_kwargs={}):
    function_name = func_to_str(function)
    task_id = str(uuid.uuid4())  # todo: consider sequence
    send_msgs_stream(hosts, msg_type='input_stream', task_id=task_id,
                     stream_factory=stream_factory, function_name=function_name,
                     c_args=c_args, c_kwargs=c_kwargs)
    return task_id


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO,
                        format='%(thread)d %(created)f %(levelname)s %(name)s %(message)s')
    init_connection('127.0.0.1')
    init_connection('127.0.0.2')
    init_connection('127.0.0.3')

    LOG.info('start')
    for a in range(1000):
        task_id = call_function(['127.0.0.1', '127.0.0.2', '127.0.0.3'], c_args=('abs', -42))
        wait_for_all_response(task_id)
    LOG.info('finish')
