import threading
import time
import inspect
import logging
from collections import abc
from collections import defaultdict
from speedling import util


LOG = logging.getLogger(__name__)


def add_pkgs():
    pass


def add_pip_pkgs():
    pass

# name: same as the key (auto populated)
# deploy_source pkg (deb/rpm/..), pypi (pip install ..), container
# deploy_mode: standalone (systemd service), mod_wsgi, uwsgi, container, nginx, ...
#              consider multiple httpd as well
# instance_name: httpd@instance_name
# 'compose': 'func_ptr to function executed before tasks but after inventory',
# 'goal', 'task for this service, it can be shared within the components, it may differ based on action
# actions: update, reconfiguiry, deploy, wipe, dry_reconfigure, dry_update
REGISTERED_SERVICES = {}
# TODO: the registered servies has to provide the options only, we need to have configured service list
#      as well, which includes the selection and read by the service steps
REGISTERED_COMPONENTS = {}

COMPONENT_CONFIG = defaultdict(dict)


def get_component_config_for(component_name):
    return COMPONENT_CONFIG[component_name]


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


def validate_common(component):
    deploy_source = component['deploy_source']
    if 'deploy_source_options' in component:
        # use some funny exception, it can be opt out
        assert deploy_source in component['deploy_source_options']


def get_service_by_name(name):
    return REGISTERED_SERVICES[name]


def register_component(component):
    REGISTERED_COMPONENTS[component['component']] = component
    srvs = component.get('services', None)
    if not srvs:
        return

    for s, d in srvs.items():
        d['component'] = component
    _register_services(srvs)
    validate_common(component)


def get_component(component):
    return REGISTERED_COMPONENTS[component]


def get_goals(srvs, component_flags):
    empty = dict()  # this case should be asserted earlier
    r = set()
    for s in srvs:
        service = REGISTERED_SERVICES.get(s, empty)
        g = service.get('goal', None)
        if g:
            r.add(g)
        comp = service.get('component', empty)
        if isinstance(comp, abc.Mapping):
            g = comp.get('goal', None)
            if g:
                r.add(g)
    for c in component_flags:
        comp = get_component(c)
        g = comp.get('goal', None)
        if g:
            r.add(g)
    return r


def get_cfg_steps(srvs):
    empty = dict()  # this case should be asserted earlier
    r = set()
    for s in srvs:
        service = REGISTERED_SERVICES.get(s, empty)
        g = service.get('cfg_step', None)
        if g:
            r.add(g)
        comp = service.get('component', empty)
        if isinstance(comp, abc.Mapping):
            g = comp.get('cfg_step', None)
            if g:
                r.add(g)
    return r


def get_compose(srvs, component_flags):
    empty = dict()  # this case should be asserted earlier
    r = set()
    for s in srvs:
        service = REGISTERED_SERVICES.get(s, empty)
        g = service.get('compose', None)
        if g:
            r.add(g)
        comp = service.get('component', empty)
        if isinstance(comp, abc.Mapping):
            g = comp.get('compose', None)
            if g:
                r.add(g)
    for c in component_flags:
        comp = get_component(c)
        g = comp.get('compose', None)
        if g:
            r.add(g)
    return r


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


def endp_triple(url):
    return {'admin': url, 'public': url, 'internal': url}


REGISTERED_ENDPOINTS = {}


def _access_region(region):
    if region in REGISTERED_ENDPOINTS:
        r_dict = REGISTERED_ENDPOINTS[region]
    else:
        r_dict = {}
        REGISTERED_ENDPOINTS[region] = r_dict
    return r_dict


def set_parent_region(region, parent):
    r = _access_region(region)
    _access_region(parent)
    r['parent_region_id'] = parent


def set_region_description(region, description):
    r = _access_region(region)
    r['description'] = description


def _access_services(region):
    if 'services' in region:
        return region['services']
    services = []
    region['services'] = services
    return services


def _find_named_service(srvs, name):
    # warning linear search
    for d in srvs:
        if d['name'] == name:
            return d


def register_endpoints(region, name, etype, description, eps):
    r = _access_region(region)
    srvs = _access_services(r)
    # handle name as primary key
    s = _find_named_service(srvs, name)
    if s:
        LOG.warning("Redeclaring {name} service in the {region}".format(name=name, region=region))
    else:
        s = {'name': name}
        srvs.append(s)
    s['type'] = etype
    s['description'] = description
    s['endpoints'] = eps


def register_endpoint_tri(region, name, etype, description, url_base):
    eps = endp_triple(url_base)
    register_endpoints(region, name, etype, description, eps)


def regions_endpoinds():
    return REGISTERED_ENDPOINTS

# TODO: not all service requires admin role, fix it,
# the auth named ones does not expected to be used in place
# where admin ness is really needed
# the cross service user usually requires admin ness

# `the admin` user was created by the kystone-manage bootstrap


# consider the Default domain always existing
REGISTERED_USER_DOM = {'Default': {}}


# domain name here case sensitive, but may not be in keystone
def register_domain(name):
    if name in REGISTERED_USER_DOM:
        return REGISTERED_USER_DOM[name]
    d = {}
    REGISTERED_USER_DOM[name] = d
    return d


def register_group_in_domain(domain, group):
    raise NotImplementedError


# it is also lookup thing, description applied from the first call
def register_project_in_domain(domain, name, description=None):
    dom = register_domain(domain)
    if 'projects' not in dom:
        projects = {}
        dom['projects'] = projects
    else:
        projects = dom['projects']
    if name not in projects:
        if description:
            p = {'description': description}
        else:
            p = {}
        projects[name] = p
        return p
    return projects[name]


def register_user_in_domain(domain, user, password, project_roles, email=None):
    dom = register_domain(domain)
    if 'users' not in dom:
        users = {}
        dom['users'] = users
    else:
        users = dom['users']
    u = {'name': user, 'password': password, 'project_roles': project_roles}
    if email:
        u['email'] = email
    users[user] = u


# users just for token verify
def register_auth_user(user, password=None):
    keymgr = util.get_keymgr()
    if not password:
        password = keymgr('os', user + '@default')
    register_project_in_domain('Default', 'service', 'dummy service project')
    # TODO: try with 'service' role
    register_user_in_domain(domain='Default', user=user, password=password,
                            project_roles={('Default', 'service'): ['admin']})


def register_service_admin_user(user, password=None):
    keymgr = util.get_keymgr()
    if not password:
        password = keymgr('os', user + '@default')
    register_project_in_domain('Default', 'service', 'dummy service project')
    register_user_in_domain(domain='Default', user=user, password=password,
                            project_roles={('Default', 'service'): ['admin']})


def service_user_dom():
    return REGISTERED_USER_DOM
