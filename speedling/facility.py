import threading
import os.path
import time
import inspect
import logging
from collections import abc
from collections import defaultdict
from speedling import cfgfile
from speedling import localsh
from speedling import util
from speedling import conf
from speedling import inv
from speedling import pkgutils
from speedling import piputils
from speedling import gitutils
from copy import deepcopy

LOG = logging.getLogger(__name__)


# name: same as the key (auto populated)
# deploy_source pkg (deb/rpm/..), pypi (pip install ..), container
# deploy_mode: standalone (systemd service), mod_wsgi, uwsgi, container, nginx, ...
#              consider multiple httpd as well
# instance_name: httpd@instance_name
# 'compose': 'func_ptr to function executed before tasks but after inventory',
# 'goal', 'task for this service, it can be shared within the components, it may differ based on action
# actions: update, reconfigure, deploy, wipe, dry_reconfigure, dry_update
REGISTERED_SERVICES = {}
# TODO: the registered services has to provide the options only, we need to have configured service list
#      as well, which includes the selection and read by the service steps
REGISTERED_COMPONENTS = {}

UNIT_NAME_MAPPING = {}


class Component(object):

    default_component_config = {}
    default_deploy_source = 'src'
    default_deploy_mode = 'standalone'
    supported_deploy_mode = {'src', 'pkg', 'pip'}
    services = {}
    leaf = True
    final_task = None

    def extend_consumers(self, dependencies, prefix=tuple()):
        # supperted things: [{sname: section, component:Component}, ..] first is default
        # {sname: Component, sname2: Component2 } # unordered
        if isinstance(dependencies, abc.Mapping):
            for k, v in dependencies.items():
                if isinstance(v, Component):
                    consumed_as = v.consumers.setdefault(self, [])
                    consumed_as.append(prefix + tuple(k))
                if isinstance(v, abc.Mapping):
                    self.extend_consumers(v, prefix=prefix + tuple(k))
                if isinstance(v, abc.Iterable):
                    for d in v:
                        if isinstance(d, abc.Mapping):
                            if 'sname' in d:
                                consumed_as = d['component'].consumers.setdefault(self, [])
                                consumed_as.append(prefix + (d['sname'],))
                            else:
                                self.extend_consumers(d, prefix)
        # TODO: other cases

    def __init__(self, alias='', offset='', dependencies={}):
        """alias: The services from the nameless version will be refered ASIS,
                  The named versions will be suffixed @alias
                  like nova-api@RegionOne nova-api@RegionTWO
                  The component itself is referred as the lowercase leaf class name,
                  with the same suffix rules.
                  The alias is not for region name.

           offset: In may Cases a single component instance hadles all version
                   of the service on the same node, but in case the component
                   chooses to allow otherwise their name will include the offset
                   http@RegionOne@0 http@@0
           dependencies: Each component can have TYPE slots for other components
                   The relationship is the user defines the providers as dependency.
                   """
        # if your class just customization for a normally leaf class
        # you want to use the parent's name
        # I mean if a mariadb subclass just changes some minor way
        # you do not want every usage to be renamed, but if you start
        # installing postgres it should have a different name
        if self.leaf:
            self.short_name = self.__class__.__name__.lower()
        else:
            next_cls = super(self.__class__, self)
            while next_cls.leaf:
                next_cls = super(self.__class__, self)
            self.short_name = next_cls.__name__.lower()
            assert self.short_name != 'component'

        self.alias = alias
        self.offset = offset
        self.dependencies = dependencies
        self.consumers = {}
        self.extend_consumers(dependencies)
        if alias or offset:
            suffix = '@' + alias
        else:
            suffix = ''
        if offset:
            suffix += '@' + str(offset)
        self.name = self.short_name + suffix
        self.suffix = suffix
        register_component(self)
        self.changed = defaultdict(dict)
        # per instance lock
        nwc = util.lock_sigleton_call(self.have_content)
        self.have_content = nwc  # nwc.__get__(self, self)

    def bound_to_instance(self, gf):
        bounded = Task(gf, self)
        name = gf.__name__
        # bounded = gf.__get__(self, self)
        setattr(self, name, bounded)
        return bounded

    def get_component_config(self):
        """Without argument only allowed on managed nodes"""
        i = inv.get_this_inv()
        ccc = i.get('component_configs')
        cc = self.default_component_config.deepcopy()
        if ccc:
            util.dict_merge(cc, ccc.get(self.name, dict()))
        return cc

    def compose(self):
        """called at compose lifecycle if the component or service involved.
           Only called on the control node"""
        if self.final_task:
            add_goal(self.final_task)

    def node_compose(self):
        """The managed nodes call it for node base composition,
           for example required packages."""
        pkgutils.add_compose(self.get_node_packages())

    def get_final_task(task=None):
        """acquiring task which can be waited for.
           usually it is the last task the component made
           But some cases the component may intiate content fetch for after
           usage (like datafiles for test)
           if another task is better for the waiter,
           for wait he can request it"""
        pass

    # NOTE: interface will change
    def populate_peer_info_for(self, nodes=set(), mode=None, network='*'):
        """Used at compose phase for providing network connectivity information,
           for other nodes, the exact payload is not defined,
           The caller knows the calle implementation."""
        # NOTE: Might be used for firewall rule creation `hints`
        pass

    def populate_extra_cfg_for(self, nodes, component, cfg_extend):
        if isinstance(component, Component):
            componenet_name = component.name
        else:
            componenet_name = component
        for n in nodes:
            node = self.get_node(n)
            node['cfg_extend'].setdefault(componenet_name, {})
            cdict = node['cfg_extend'][componenet_name]
            util.dict_merge(cdict, cfg_extend)

    # NOTE: cache it ?
    def get_services_global_name(self):
        """ get service dict with naming rules applied"""
        if hasattr(self, '_get_services'):
            return self._get_services
        self._get_services = {k + self.suffix: v for (k, v) in self.services.items()}
        return self._get_services

    def call_do(self, hosts, the_do, c_args=tuple(), c_kwargs={}):
        real_args = (self.name, ) + c_args
        return inv.do_do(hosts, the_do, real_args, c_kwargs)

    def call_diff_args(self, matrix, do):
        return inv.do_diff(matrix, do, self.name)

    def distribute_as_file(self, *args, **kwargs):
        return inv.distribute_as_file(*args, **kwargs)

    def distribute_for_command(self, *args, **kwargs):
        return inv.distribute_for_command(*args, **kwargs)

    def hosts_with_any_service(self, services):
        return inv.hosts_with_any_service(set(s + self.suffix for s in services))

    def hosts_with_component(self, component):
        return inv.hosts_with_component(component + self.suffix)

    def get_state_dir(self, extra=''):
        return util.get_state_dir(os.path.sep.join((self.name, extra)))

    def hosts_with_service(self, service):
        return inv.hosts_with_service(service + self.suffix)

    def get_node(self, *args, **kwargs):
        return inv.get_node(*args, **kwargs)

    def get_this_node(self, *args, **kwargs):
        return inv.get_this_node(*args, **kwargs)

    def get_this_inv(self, *args, **kwargs):
        return inv.get_this_inv(*args, **kwargs)

    def get_addr_for(self, *args, **kwargs):
        return inv.get_addr_for(*args, **kwargs)

    def ini_file_sync(self, target_path, paramters, *args, **kwargs):
        node = self.get_this_node()
        cfg_extend = node['cfg_extend']
        node_inv = node['inv']
        # we might support callable instead of plain type
        if self.name in cfg_extend:
            comp_cfg_extend = cfg_extend[self.name]
            if target_path in comp_cfg_extend:
                util.dict_merge(paramters, comp_cfg_extend[target_path])  # modifies the original dict!
        extend_config = node_inv.get('components', {}).get(self.name, {}).get('extend_config', {})
        if target_path in extend_config:
            util.dict_merge(paramters, extend_config[target_path])  # modifies the original dict!

        self.changed['file'][target_path] = cfgfile.ini_file_sync(target_path, paramters,
                                                                  *args, **kwargs)

    def content_file(self, target_path, *args, **kwargs):
        self.changed['file'][target_path] = cfgfile.content_file(target_path,
                                                                 *args, **kwargs)

    def ensure_path_exists(self, link, *args, **kwargs):
        self.changed['file'][link] = cfgfile.ensure_path_exists(link, *args, **kwargs)

    def haproxy_file(self, target_path,  *args, **kwargs):
        self.changed['file'][target_path] = cfgfile.haproxy_file(target_path, *args, **kwargs)

    def rabbit_file(self, target_path,  *args, **kwargs):
        self.changed['file'][target_path] = cfgfile.rabbit_file(target_path, *args, **kwargs)

    def install_file(self, target_path,  *args, **kwargs):
        self.changed['file'][target_path] = cfgfile.install_file(target_path, *args, **kwargs)

    def ensure_sym_link(self,  target_path,  *args, **kwargs):
        self.changed['file'][target_path] = cfgfile.ensure_sym_link(target_path, *args, **kwargs)

    def etccfg_content(self, dry=None):
        """if your config can be done before any command call place it here,
           this stept meant to be /etc like changes  """

    def have_binaries(self):
        pkgutils.ensure_compose()

    def have_content(self):
        self.have_binaries()
        self.etccfg_content()

    def wait_for_components(self, *comps):
        task_wants(*(comp.final_task for comp in comps if comp.final_task), caller_name=self.__class__.__name__)

    def get_node_packages(self):
        """which packages the component needs int current node context"""
        return set()

    def filter_node_enabled_services(self, candidates):
        i = self.get_this_inv()
        services = i.get('services', set())
        return {c + self.suffix for c in candidates if c + self.suffix in services}

    def get_enabled_services_from_component(self):
        return self.filter_node_enabled_services(self.services.keys())

    def unit_name_mapping(style):
        return {}

    # TODO add default bounce condition and coordinated bounce


class LoadBalancer(Component):
    # TODO: make LB abstract to support LB != HAProxy
    # NOTE: theoretically httpd can do ballancing, but ..
    pass


class SQLDB(Component):
    # Consider postgres
    def db_url(*args, **kwargs):
        raise NotImplementedError

    # provides basic db access to the listed schemas
    def register_user_with_schemas(self, user, schema_names):
        raise NotImplementedError


class Messaging(Component):

    def get_transport_url(self):
        raise NotImplementedError

    # scoping are very implementation specific
    def register_user(self, user):
        raise NotImplementedError


class VirtDriver(Component):
    pass


# TODO: convince the localsh to have retry
def do_retrycmd_after_content(cname, cmd):
    self = get_component(cname)
    self.have_content()
    retry = 30
    while True:
        try:
            localsh.run(cmd)
        except:
            if retry == 0:
                raise
        else:
            break

        time.sleep(0.2)
        retry -= 1


class OpenStack(Component):
    # TODO: place hare some config possibility like worker number strategy
    python_version = 3
    regions = ['RegionOne']  # move vips to region, keystone may register himself to multiple, but others should use single (per instance)

    def get_node_packages(self):
        pkgs = super(OpenStack, self).get_node_packages()
        if self.deploy_source == 'src':
            pypkg = 'lib-dev\\python' + str(self.python_version)
            pippkg = 'python3-pip'
            if self.python_version == 2:
                pippkg = 'cli-py2\\pip'
            pkgs.update({pippkg, 'git', pypkg, 'util-cli\\gcc-g++',
                         'lib-dev\\ffi', 'lib-dev\\xslt', 'lib-dev\\openssl',
                         'lib-py3\\pymysql'})
        return pkgs

    # overrides
    def have_content(self):
        self.have_binaries()  # -devel
        gconf = conf.get_global_config()
        need_git = gconf.get('use_git', True)  # switch these if you do have good image
        need_pip = gconf.get('use_pip', True)
        if need_git:
            gitutils.process_component_repo(self)
        if need_pip:
            if self.python_version != 2:
                piputils.setup_develop(self)
            else:
                piputils.setup_develop2(self)
        self.etccfg_content()


class InterfaceDriver(Component):
    pass


# TODO: the ciken egg problem can be solvad as egg chickin as well, it will be less confusing
class StorageBackend(Component):
    def get_glance_conf_extend(self, sname):
        """provides full config dict, the name is the section name and ':type' glance expects
           The response should be repetable, if backand may store as a state."""
        return {'/etc/glance/glance-api.conf': {}}

    def get_cinder_conf_extend(self, sname):
        """provides full config dict, the name is the section name cinder expects
           The response should be repetable, if backand may store as a state."""
        return {'/etc/cinder/cinder.conf': {}}

    def get_nova_conf_extend(self):
        return {'/etc/nova/nova.conf': {}}

    def get_waits_for_nova_task(self):
        return {self.final_task}

    def get_waits_for_glance_task(self):
        return {self.final_task}

    def get_waits_for_cinder_task(self):
        return {self.final_task}

    def compose(self):
        super(StorageBackend, self).compose()
        for comp, consumed_ases in self.consumers.items():
            if comp.short_name == 'glance':  # The StorageBackend class should not in this file
                for consumed_as in consumed_ases:
                    cfg_extend = self.get_glance_conf_extend(consumed_as[-1])
                    g_api_nodes = comp.hosts_with_service('glance-api')
                    self.populate_extra_cfg_for(g_api_nodes, comp, cfg_extend)
                continue
            if comp.short_name == 'nova':  # The StorageBackend class should not in this file
                for consumed_as in consumed_ases:
                    cfg_extend = self.get_nova_conf_extend()
                    g_api_nodes = comp.hosts_with_service('nova-compute')
                    self.populate_extra_cfg_for(g_api_nodes, comp, cfg_extend)
                continue
            if comp.short_name == 'cinder':  # The StorageBackend class should not in this file
                for consumed_as in consumed_ases:
                    cfg_extend = self.get_cinder_conf_extend(consumed_as[-1])
                    g_api_nodes = comp.hosts_with_service('cinder-volume')
                    self.populate_extra_cfg_for(g_api_nodes, comp, cfg_extend)


ENSURE_COMPOSE_LOCK = threading.Lock()


def do_node_generic_system():
    pkgutils.ensure_compose()


# Deprecated for external use
def _register_services(srvs):
    if isinstance(srvs, abc.Mapping):
        for n, srv in srvs.items():
            # TODO: add name in the component loop
            srv['name'] = n
            REGISTERED_SERVICES[n] = srv
    else:  # list
        # TODO: delete the list way
        for srv in srvs:
            assert srv['name'] not in REGISTERED_SERVICES
            REGISTERED_SERVICES[srv['name']] = srv


def get_service_by_name(name):
    return REGISTERED_SERVICES[name]


def register_component(component):
    assert component.name not in REGISTERED_COMPONENTS
    REGISTERED_COMPONENTS[component.name] = component
    srvs = component.get_services_global_name()
    if not srvs:
        return

    for s, d in srvs.items():
        d['component'] = component
    _register_services(srvs)


def get_component(component):
    return REGISTERED_COMPONENTS[component]


GOALS = set()


def add_goals(goals):
    GOALS.update(set(goals))


def add_goal(goal):
    GOALS.add(goal)


def get_goals():
    return GOALS


# cache
def get_local_active_services():
    host_record = inv.get_this_inv()
    services = host_record.get('services', set())
    srvs = {}
    for s in services:
        if s not in REGISTERED_SERVICES:
            LOG.warning("Unknown service '{}'".format(s))
        else:
            srvs[s] = REGISTERED_SERVICES[s]
    return srvs


# cache
def get_local_active_components():
    host_record = inv.get_this_inv()
    services = get_local_active_services()
    components = host_record.get('extra_components', set())
    comps = set()
    for c in components:
        comp = REGISTERED_COMPONENTS.get(c, None)
        if not comp:
            LOG.warning("Unknown component '{}'".format(c))
        else:
            comps.add(comp)
    for s, r in services.items():
        c = r.get('component', None)
        if c:
            comps.add(c)
    return comps


def compose():
    for c in REGISTERED_COMPONENTS.values():
        c.compose()

task_sync_mutex = threading.Lock()
pending = set()


def task_add_wants(task, *wants):
    if hasattr(task, 'wants'):
        task.wants += wants
    else:
        task.wants = wants


def _taskify(*args):
    task_sync_mutex.acquire()
    for task in args:
        if not hasattr(task, 'thr'):
            task.failed = False

            def helper_func():
                t = task
                start = time.time()

                def _finish_log():
                    try:
                        if hasattr(t, 'wants'):  # exrta deps as task attibute
                            task_wants(*t.wants, caller_name=t.__name__)
                        t()
                    except:
                        t.failed = True
                        LOG.error(t.__name__ + ' failed in ' + str(time.time() - start) + 's (waits included)')
                        raise
                    LOG.info(t.__name__ + ' finished in ' + str(time.time() - start) + 's (waits included)')
                    task_sync_mutex.acquire()
                    pending.remove(t)
                    task_sync_mutex.release()
                return _finish_log

            task.thr = threading.Thread(target=helper_func())
            pending.add(task)
            task.thr.start()
    task_sync_mutex.release()
    return [tsk for tsk in args if tsk.thr.is_alive()]


def log_pending():
        task_sync_mutex.acquire()
        LOG.info('Pending tasks:' + ', '.join((tsk.__name__ for tsk in pending)))
        task_sync_mutex.release()


def start_pending():
    def pending_task():
        while True:
            time.sleep(15)
            log_pending()
    t = threading.Thread(target=pending_task)
    t.setDaemon(True)
    t.start()


def task_will_need(*args):
    return _taskify(*args)


# methods cannot have extra attributes so you either use a class or a closure
# to duplicate tasks, BTW function copy also possible

class Task(object):

    def __init__(self, fn, ctx):
        self.fn = fn
        self.ctx = ctx
        self.__name__ = fn.__name__

    def __call__(self, *args, **kwargs):
        self.fn(self.ctx, *args, **kwargs)


def task_wants(*args, caller_name=None):
    wait_for = _taskify(*args)
    wait_for_names = [tsk.__name__ for tsk in wait_for]
    if not caller_name:
        curframe = inspect.currentframe()
        calframe = inspect.getouterframes(curframe, 2)
        caller_name = calframe[1][3]
    if wait_for:
        LOG.info('%s is waiting for: %s' % (caller_name, str(wait_for_names)))
    for wf in wait_for:
        wf.thr.join()
    for task in args:  # late fail, we do not want to interrupt the world
        if task.failed:
            raise Exception('Aborting %s because %s failed' % (caller_name, task.__name__))
