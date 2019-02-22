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

osinstutils
-----------

This the incubator of library which eventually be preinstalled
on the images, but for now it is transfered with speedling.
It contains `lower` level config management helpers,
ATM stiff moving in/out from other directories in the repo.

virtbs
------

Virt build slices, a simple script for managing virtual machines and images
on a single big machine. The only way to configure it is changing the source ATM.
It requires root privileges to create bridges (libvirt) and virtual machines.

.. code:: bash

   ./virtbs.sh wipe 1 # deletes everything in slice1 (prefixed with bs1)
   ./virtbs.sh cycle 1 controller:1 # recreate (destroy + build) one controller node
   ./virtbs.sh cycle 1 controller:1,compute:1 # recreate (destroy + build) one controller and compute node


speedling
---------
It copies itself to remote machines and receiving and processing commands
from the origin node.

The execution is parallel, maintained by task threads.
Most component has it own thread, some cases waits *wants* other threads to complete
before continuing it's task.

The receiver nodes expected to have limited view of the Univers,
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
    ./test_aio.sh  # caretes virtual machine, uses sudo
    ssh -F sshconf-bs1 f29-dev-02
    source state/admin-openrc.sh
    openstack image list
    cd /opt/stack/tempest/
    stestr run minim

Basic flow if you are logged into a throw away test machine

.. code:: bash

    sudo useradd -s /bin/bash -d /opt/stack -m stack
    echo "stack ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/stack
    sudo su - stack
    git clone https://github.com/afazekas/speedling
    cd speedling
    ./stack.sh
    source state/admin-openrc.sh
    openstack image list
    cd /opt/stack/tempest/
    stestr run minim
