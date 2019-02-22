#!/usr/bin/env python3
import os
import queue
import threading
import select
import logging
import traceback
import pickle

# TODO: move the dependent function elsewhere
import pwd
import grp
import numbers

INPUTFD = 4
OUTPUTFD = 5
LOGFD = 6


response_queue = queue.Queue()
use_globals = globals()


class stream_reader(object):

    def __init__(self, readerfd):
        self.readerfd = readerfd
        self.chunk_remainig = 0
        self.eof = False

    def read(self, size=65536):
        # retunr with '' if done
        if self.eof or not size:
            return b''
        # <int64size><data><int64size><data><0size_end>
        if self.chunk_remainig > 0:
            buf = os.read(self.readerfd, self.chunk_remainig)
            l = len(buf)
            self.chunk_remainig -= l
            return buf
        next_size = read_ll(self.readerfd)
        if not next_size:
            self.eof = True
            return b''

        buf = os.read(self.readerfd, min(next_size, size))
        l = len(buf)
        self.chunk_remainig = next_size - l
        return buf


# test only
def file_writer(stream, path, owner='root', group='root', mode=0o640):
    f = os.fdopen(os.open(path,
                  os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                  mode), 'wb')
    # for early failure, put it into the front
    if isinstance(owner, numbers.Integral):
        uid = owner
    else:
        uid = pwd.getpwnam(owner).pw_uid

    if isinstance(group, numbers.Integral):
        gid = group
    else:
        gid = grp.getgrnam(group).gr_gid

    os.chown(path, uid, gid)
    while True:
        buf = stream.read()
        if buf:
            f.write(buf)
        else:
            f.close()
            return


def task_exec(task_id, msg_type, **kwargs):
    return_value = None
    loc = locals()
    if msg_type == 'func':
        # advantage, pickle dedup happens on the line, dict made str only here
        kwargs['code'] = """
args = {args}
kwargs = {kwargs}
return_value = {function_name}(*args, **kwargs)""".format(function_name=kwargs['function_name'],
                                                          args=str(kwargs.get('c_args', tuple())),
                                                          kwargs=str(kwargs.get('c_kwargs', {})))
        msg_type = 'code'

    if msg_type == 'input_stream':
        # advantage, pickle dedup happens on the line, dict made str only here
        kwargs['code'] = """
args = {args}
kwargs = {kwargs}
_i_stream = speedling.receiver.stream_reader(speedling.receiver.INPUTFD)
return_value = {function_name}(_i_stream, *args, **kwargs)
if _i_stream.read():
   while _i_stream.read():
      pass
   raise Exception('Stream was not fully consumed')
""".format(function_name=kwargs['function_name'],
           args=str(kwargs.get('c_args', tuple())),
           kwargs=str(kwargs.get('c_kwargs', {})))
        msg_type = 'code'

    if msg_type == 'code':
        try:
            exec(kwargs['code'], use_globals, loc)
        except BaseException:
            D = {'status': 1, 'task_id': task_id, 'err': traceback.format_exc()}
            response_queue.put(D)
            return D
        D = {'status': 0, 'task_id': task_id, 'return_value': loc['return_value']}
        response_queue.put(D)
        return D

    raise NotImplementedError


terminate = False


# allways handling the single opened stream
def read_ll(fd):
    to_nr = b''
    to_read = 8
    while True:
        c = os.read(fd, to_read)
        if not len(c):
            os.close(fd)
            print('Receving socket closed')
            return
        to_nr += c
        if len(to_nr) == 8:
            return int.from_bytes(to_nr, byteorder='little')
        else:
            to_read = 8 - len(to_nr)


def input_handler():
    global terminate
    # size uint64
    # payload_pickle
    # repeate..
    while True:
        msg = b''
        to_read = read_ll(INPUTFD)
        if not to_read:
            terminate = True
            return
        while to_read:
            chunk = os.read(INPUTFD, to_read)
            l = len(chunk)
            if not l:
                os.close(INPUTFD)
                print('Receving socket closed unexpectedly')
                terminate = True
                return
            to_read -= len(chunk)
            msg += chunk
        real_msg = pickle.loads(msg)
        if 'stream' not in real_msg['msg_type']:
            task = threading.Thread(target=task_exec, kwargs=real_msg)
            task.start()
        else:  # single threaded mode, keeping the input fd
            task_exec(**real_msg)


def writeall(fd, buf):
    to_write = len(buf)
    written = 0
    while to_write > written:
        w = os.write(fd, buf[written:])
        if not w:
            print('Sending socket closed unexpectedly')
            os.close(fd)
            raise Exception('Terminate')
        written += w


def answer_handler():
    global terminate
    p = select.poll()
    p.register(OUTPUTFD, select.POLLERR | select.POLLHUP | select.POLLNVAL)
    while True:
        try:
            ans = response_queue.get(block=True, timeout=5)
            ans = pickle.dumps(ans)
            size = len(ans).to_bytes(8, byteorder='little')
            writeall(OUTPUTFD, size)
            writeall(OUTPUTFD, ans)
        except queue.Empty:
            if terminate:
                print("Aborting output thread..")
                break
            ev = p.poll(0)
            if ev:
                terminate = True
                print("Output target stopped listening")
                break


class SSHLogHandler(logging.Handler):
    def emit(self, record):
        if hasattr(self, 'broken'):
            return
        # get the contructor args
        # TODO: py3 sinfo
        r = {'name': record.name,
             'level': record.levelno,
             'pathname': record.pathname,
             'lineno': record.lineno,
             'msg': record.msg,
             'args': record.args,
             'exc_info': record.exc_info,
             'func': record.funcName}
        # TODO: handle log.exception
        msg = pickle.dumps(r)
        size = len(msg).to_bytes(8, byteorder='little')
        real_msg = size + msg
        self.acquire()
        try:
            writeall(LOGFD, real_msg)  # may block until it is readed
        except Exception:
            self.broken = True
            print((traceback.format_exc()))
        finally:
            self.release()


def initiate(globals_to_use):
    global use_globals
    use_globals = globals_to_use
    os.write(OUTPUTFD, b'systemcontrol\n')
    os.write(LOGFD, b'systemcontrol\n')
    answer_thread = threading.Thread(target=answer_handler)
    answer_thread.start()

    input_thread = threading.Thread(target=input_handler)
    input_thread.daemon = True
    input_thread.start()

    rl = logging.getLogger('')
    sshh = SSHLogHandler()
    sshh.setLevel(1)
    rl.addHandler(sshh)
