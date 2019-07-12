speedling
=========

The not slow way to get OpenStack dev/test environment.


Warning
=======

The project is not ready for use,
it does not have real user interface and may damage your computer
or kill kittens.
You have been warned !


Is speedling fast?
==================
On a purpose build image at can do the job around 1~2 minute,
A reusable image can be build around 10~15 minute.

It does not do everything in random order,
so the flow is still human understandable and debugable.

In case you need to manage a lot of test node you might
want to increase the maximum file descriptors.
Order of magnitude ~5 per manged node used
(3 counted for ssh per managed node and consider some file opens).


Parts
=====


virtbs
------

Virt build slices, a simple script for managing virtual machines and images
on a single strong machine while using consistent network addresses.
It requires libvirt system privileges in order to create bridges.
Typically enough to add your user to the libvirt group.

.. code:: bash

   ./virtbs.sh --slice 1 wipe # deletes everything in slice1 (prefixed with bs1)
   ./virtbs.sh --slice 1 cycle roles,fedora controller:1 # recreate (destroy + build) one controller node
   ./virtbs.sh --slice 1 cycle roles,fedora controller:1,worker:1 # recreate (destroy + build) one controller and compute node


slos
----

speedling openstack, the actual payload code
which does the job.


speedling
---------
It copies itself to remote machines and receiving and processing commands
from the origin node.

The execution is parallel, maintained by task threads.
Most component has it own thread, some cases waits *wants* other threads to complete
before continuing it's task.

The receiver nodes expected to have limited view of the Universe,
they are not knowing every other nodes and/or every credentials.

The *task_* prefixed functions expected to call *do_* prefixed functions on the
remote nodes.
I single task does not expected to operate two different *do_* at the same time,
but it can call a *do_* on all nodes at the same time.
Any exception will abort the controller, however it does not interrupts the
other running tasks immediately.

The thing is under refactoring do not expect stable api/config way anytime soon.

Basic flow with local vm creation:

.. code:: bash

    git clone https://github.com/afazekas/speedling
    cd speedling
    ./prepare_host.sh  # only first time for installing dependencies
    ./test_aio.sh  # creates virtual machine and libvirt bridge networks
    ssh -F sshconf-bs1 controller-02
    source state/admin-openrc.sh
    openstack image list
    cd /opt/stack/tempest/
    stestr run minim

Basic flow if you are logged into a throw away test machine

.. code:: bash

    sudo useradd -s /bin/bash -m stack
    echo "stack ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/stack
    sudo su - stack
    git clone https://github.com/afazekas/speedling
    cd speedling
    ./stack.sh
    source state/admin-openrc.sh
    openstack image list
    cd /opt/stack/tempest/
    stestr run minim
