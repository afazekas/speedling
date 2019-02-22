import subprocess
import logging
import threading
import time

LOG = logging.getLogger(__name__)

# TODO: nicer logging
# TODO: nicer exception

# NOTE: shlex on single ?

# We should default to the not shell version
# looks like it can be the more frequent case,
# when I have time to reformat the command


def feeder_thread(pipe, stream):
    while True:
        buf = stream.read()
        if not buf:
            pipe.flush()
            pipe.close()
            return
        pipe.write(buf)  # BrokenPipeError can be raised


def call(cmd, single=False, merge_output=False, input_stream=None):
    start = time.time()
    if merge_output:
        serr = subprocess.STDOUT
    else:
        serr = subprocess.PIPE
    if input_stream:
        stdin = subprocess.PIPE
    else:
        stdin = None
    if not single:
        # allowing bashism
        p = subprocess.Popen(cmd, shell=True, close_fds=True,
                             stdout=subprocess.PIPE, stderr=serr,
                             executable='/bin/bash', stdin=stdin)
    else:
        p = subprocess.Popen(cmd, shell=False, close_fds=True,
                             stdout=subprocess.PIPE, stderr=serr, stdin=stdin)
    if stdin:
        th = threading.Thread(target=feeder_thread, kwargs={'pipe': p.stdin,
                                                            'stream': input_stream})
        th.setDaemon(True)
        th.start()
        p.wait()  # communicate cutted the input pipe, the below is not good for large input
        return (0, p.stdout.read(), p.stderr.read())

    stdout, stderr = p.communicate()
    r = p.returncode
    if stdin:
        th.join()
    delta = time.time() - start
    if delta > 1:
        LOG.info('Long running command delta: %f (%s)', delta, cmd)
    return (r, stdout, stderr)


def ret(cmd, single=False, binary=False):
    """
        Execute the thing, the user interested in the stdout only.
        raise on error
        single : True, do not invoke a shell, just execute the command
    """
    r, stdout, stderr = call(cmd, single)

    if not binary:
        u_stdout = stdout.decode('utf-8')

    u_stderr = stderr.decode('utf-8')
    if (r != 0):
        if not binary:
            LOG.info(u_stdout)
        LOG.error(u_stderr)
        raise Exception(str((cmd, r, u_stderr)))  # TODO: new ex
    if u_stderr:
        LOG.info(u_stderr)
    if not binary:
        return u_stdout
    else:
        return stdout


def run(cmd, single=False):
    """ Execute the thing, the user not interested in the output,
        raise on error"""
    r, stdout, stderr = call(cmd, single)
    u_stdout = stdout.decode('utf-8')
    u_stderr = stderr.decode('utf-8')
    if (r != 0):
        LOG.info(cmd)
        LOG.info(u_stdout)
        LOG.error(u_stdout)
        raise Exception(str((cmd, r, u_stdout, u_stderr)))  # TODO: new ex
    LOG.debug(str((cmd, u_stdout, u_stderr)))


def run_stream_in(stream, cmd, single=False):
    """ Execute the thing, the user not interested in the output,
        but it has binary input stream
        raise on error"""
    r, stdout, stderr = call(cmd, single, input_stream=stream)
    u_stdout = stdout.decode('utf-8')
    u_stderr = stderr.decode('utf-8')
    if (r != 0):
        LOG.info(cmd)
        LOG.info(u_stdout)
        LOG.error(u_stdout)
        raise Exception(str((cmd, r, u_stdout, u_stderr)))  # TODO: new ex
    LOG.debug(str((cmd, u_stdout, u_stderr)))


def run_log(cmd, single=False):
    """  Execute the thing, the user not interested in the output for logging"""
    r, stdout, stderr = call(cmd, single, merge_output=True)
    u_stdout = stdout.decode('utf-8')
    if stderr:
        u_stderr = stderr.decode('utf-8') + '\n'
    else:
        u_stderr = ''
    return (r, """{cmd}\nreturned_with:{ret}\n{u_stdout}{u_stderr}""".format(
              cmd=cmd, ret=r, u_stdout=u_stdout, u_stderr=u_stderr))


def test(cmd, single=False):
    """ Execute the thing, the user not interested in the output,
        return true on 0, false otherwise"""
    r, stdout, stderr = call(cmd, single)
    u_stdout = stdout.decode('utf-8')
    u_stderr = stderr.decode('utf-8')
    LOG.debug(str((cmd, u_stdout, u_stderr)))
    return bool(not r)
