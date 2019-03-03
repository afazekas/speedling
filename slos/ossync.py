#!/usr/bin/python
from keystoneclient.v3 import client as keystone_client
from keystoneauth1 import session as keystone_session

# TODO: the client or server become slower in the past
# years, time to reconsider the strategy

# keystone client depends on this library so it is not an extra
import requests.structures
import time
import logging
import os

LOG = logging.getLogger(__name__)
# TODO: flavors, aggregates, neutron extnet, demo nets, meybe image remote sync
# TODO: if password is not a string type, try to call the [0] with [1:]
# TODO: the dry_run MUST be verbose and descriptive, human friendly,
#        and working ;-), and read-only

# not possible to enable/disable anything, it is enabled or deleted
# not specified region, service, endpoint will be deleted
# region+type+name considered as primery key  for services even the api allows
# other possiblities and the region is not part of the service record
# region+type+name allows to have different desiption in different region
# description+type+name another candidate key


# some record can have extra attributes, this fact is ignored for now
# keystone may allow multiple endpoints with the same name+reg+srv, we don't

# valid interface names are only admin,public,internal

def endp_triple(url):
    return {'admin': url, 'public': url, 'internal': url}

# domain, projects, roles, groups are automatically created
# only assigments, and group membership, is removed automatically
# the user record (passowd) just updated when changed,
#    otherwise tokens got invaldated on password update
# project hierarchy not manged
# roleas are implicitly created, do not misstype them !

# roles can have extra paramaters like domain (can be null),
# we prented roles just have name and id

# {
# 'Default': { 'projects': {'services': {'description':
#                                       'service dummy project'}},
#    'groups': {'test_grp': { 'project_roles': {('Default','services') :
#         ['service']},
#                             'domain_roles':{'test_domain': ['admin']}}},
#                'users': {'test_user': { 'email': 'nova@foo',
#                                     'password': 'secret',
#                                     'project_roles':{('Default', 'services'):
#                                            ['service']},
#                                     'domain_roles':{'test_domain':
#                                            ['admin']},
#                                     'member_of' :{'test_domain':
#                                        ['foo_group']}}},
#          'description': "it's a domain"},
# 'test_domain': {'groups': {'foo_group': { 'roles': ['foo']}} }
#        }
#


# TODO: early blow up on undef but ref resource like grp (member)
def user_dom_sync(auth, user_domain, dry_run=False, endpoint_override=None):
    start = time.time()
    sess = keystone_session.Session(auth=auth)
    keystone = keystone_client.Client(session=sess,
                                      endpoint_override=endpoint_override)

    # The keystone remembers to the names, but in compares it is
    # case insensitive
    domain_to_id = requests.structures.CaseInsensitiveDict()
    # TODO: create tuple friendly insensitive dict,
    # or add lower attr to the thing we are adding
    domain_project_to_id = dict()  # lc enfornced on all attribute
    domain_group_to_id = dict()
    role_to_id = requests.structures.CaseInsensitiveDict()

    def get_role_id(role):
        if role not in role_to_id:
            r = keystone.roles.create(role)
            role_to_id[role] = r.id
            return r.id
        return role_to_id[role]

    # We do not expect high number of roles, so list all
    # BTW, failed to find the right api call to just see one named role
    initial_roles = keystone.roles.list()
    for role in initial_roles:
        role_to_id[role.name] = role.id

    for domain_name, domain in user_domain.items():
        dom_lc_name = domain_name.lower()
        # case insestive name
        assert domain_name not in domain_to_id
        dl = keystone.domains.list(name=domain_name)
        l = len(dl)
        desc = domain.get('Description', '')
        assert l < 2
        if l == 1:
            dom = dl[0]
            if dom.description != desc or dom.name != domain_name:  # case
                keystone.domains.update(dom.id,
                                        name=domain_name,
                                        description=desc)
        else:
            dom = keystone.domains.create(name=domain_name, description=desc)
        dom_id = dom.id
        domain_to_id[dom_lc_name] = dom_id
        if 'projects' in domain:
            for project_name, project in domain['projects'].items():
                # TODO: parent_id respect
                desc = project.get('description', '')
                lc_proj_name = project_name.lower()
                assert (dom_lc_name, lc_proj_name) not in domain_project_to_id
                pl = keystone.projects.list(domain=dom_id, name=project_name)
                l = len(pl)
                assert l < 2
                if l == 0:
                    p = keystone.projects.create(project_name,
                                                 dom_id,
                                                 description=desc)
                else:
                    p = pl[0]
                    if p.description != desc or p.name != project_name:
                        keystone.projects.update(p.id, name=project_name,
                                                 domain=dom_id,
                                                 description=desc)
                domain_project_to_id[(dom_lc_name, lc_proj_name)] = p.id

        if 'groups' in domain:
            # TODO: group roles , after _all_ project is ready
            # (after this loop)
            desc = project.get('description', '')
            for group_name, group in domain['groups'].items():
                desc = group.get('description', '')
                lc_grp_name = group_name.lower()
                assert (dom_lc_name, lc_grp_name) not in domain_group_to_id
                gl = keystone.groups.list(domain=dom_id, name=group_name)
                l = len(gl)
                assert l < 2
                if l == 0:
                    g = keystone.groups.create(group_name,
                                               dom_id,
                                               description=desc)
                else:
                    g = gl[0]
                    if g.description != desc or g.name != group_name:
                        keystone.groups.update(g.id, name=group_name,
                                               domain=dom_id,
                                               description=desc)
                domain_group_to_id[(dom_lc_name, lc_grp_name)] = g.id

        if 'users' not in domain:
            domain['users'] = {}  # changes original arg
        # ensure we have all rule id before switching to parallel
        # ensure we have all main keys
        role_set = set(())
        for user_name, user in domain['users'].items():
            if 'project_roles' not in user:
                user['project_roles'] = {}
            else:
                for role_list in list(user['project_roles'].values()):
                    role_set |= set(role_list)
            if 'domain_roles' not in user:
                user['domain_roles'] = {}
            else:
                for role_list in list(user['domain_roles'].values()):
                    role_set |= set(role_list)
            if 'member_of' not in user:
                user['member_of'] = {}
        for role in role_set:
            get_role_id(role)

    # TODO: group roles
    processes = []
    for domain_name, domain in user_domain.items():
        dom_id = domain_to_id[domain_name]
        jobs = list(domain['users'].items())
        l = len(jobs)
        # creating new fork for each 4 user
        # this was the easiest to add without huge reformating
        f = l
        while (f > 0):
            s = f - 4
            if s < 0:
                s = 0
            items = jobs[s:f]
            f = s
            p = os.fork()
            if not p:
                sess = keystone_session.Session(auth=auth)
                e = endpoint_override
                keystone = keystone_client.Client(session=sess,
                                                  endpoint_override=e)
                for user_name, user in items:
                    ul = keystone.users.list(domain=dom_id, name=user_name)
                    l = len(ul)
                    assert l < 2
                    user_rec = {}

                    relevant_args = ('email', 'description', 'password')
                    for arg in relevant_args:
                        user_rec[arg] = user.get(arg, None)
                    if 'default_project' in user:
                        default_project = user['default_project']
                        dpid = domain_project_to_id[(
                            default_project[0].lower(),
                            default_project[1].lower())]
                        user_rec['default_project'] = dpid
                    else:
                        user_rec['default_project'] = None
                    if 'enabled' not in user:
                        user_rec['enabled'] = True

                    existing_project_roles = {}
                    existing_domain_roles = {}
                    member_of = set()

                    if l == 0:
                        # TODO: check is the inherited roles have any relavant
                        # effect
                        u = keystone.users.create(user_name,
                                                  domain=dom_id,
                                                  **user_rec)
                        u_id = u.id
                    else:
                        u = ul[0]
                        u_id = u.id
                        u_email = u.email if hasattr(u, 'email') else None
                        u_desc = (u.description
                                  if hasattr(u, 'description') else None)
                        u_default_project = (u.default_project
                                             if hasattr(u, 'default_project')
                                             else None)
                        if (u.name != user_name or
                            u_email != user_rec['email'] or
                            u_desc != user_rec.get('description') or
                            u.enabled != user_rec.get('enabled') or
                                u_default_project != user_rec[
                                    'default_project']):
                            u = keystone.users.update(u.id, name=user_name,
                                                      **user_rec)
                        assigments = keystone.role_assignments.list(user=u.id)
                        for a in assigments:
                            # strange looking api reponse ..
                            role_id = a.role['id']
                            if 'project' in a.scope:
                                project_id = a.scope['project']['id']
                                if project_id not in existing_project_roles:
                                    existing_project_roles[
                                        project_id] = set((role_id,))
                                else:
                                    existing_project_roles[
                                        project_id].add(role_id)
                                continue
                            if 'domain' in a.scope:
                                d_id = a.scope['domain']['id']
                                if d_id not in existing_domain_roles:
                                    existing_domain_roles[
                                        d_id] = set((role_id,))
                                else:
                                    existing_domain_roles[d_id].add(role_id)
                        grps = keystone.groups.list(user=u_id)
                        for grp in grps:
                            member_of.add(grp.id)

                    # update grp member
                    target_groups = set()
                    for dom, groups in user['member_of'].items():
                        l_d = dom.lower()
                        for group in groups:
                            target_groups.add(
                                domain_group_to_id[(l_d, group.lower())])

                    grp_mem_del = member_of - target_groups
                    grp_mem_add = target_groups - member_of
                    for grp in grp_mem_del:
                        keystone.users.remove_from_group(u_id, grp)
                    for grp in grp_mem_add:
                        keystone.users.add_to_group(u_id, grp)

                    target_domain_roles = {}
                    target_project_roles = {}

                    for dom, roles in user['domain_roles'].items():
                        d_roles = set()
                        for role in roles:
                            d_roles.add(get_role_id(role))
                        target_domain_roles[
                            domain_to_id[dom.lower()]] = d_roles

                    for (dom, proj), roles in list(user['project_roles'].items()):
                        p_roles = set()
                        for role in roles:
                            p_roles.add(get_role_id(role))
                        proj_id = domain_project_to_id[(dom.lower(),
                                                        proj.lower())]
                        target_project_roles[proj_id] = p_roles

                    related_projects = set(target_project_roles.keys())
                    related_projects |= set(existing_project_roles.keys())
                    related_domains = set(target_domain_roles.keys())
                    related_domains |= set(existing_domain_roles.keys())

                    # update project roles
                    for proj_id in related_projects:
                        target_roles = target_project_roles.get(proj_id, set())
                        existing_roles = existing_project_roles.get(proj_id,
                                                                    set())
                        roles_to_add = target_roles - existing_roles
                        roles_to_del = existing_roles - target_roles
                        for role_id in roles_to_del:
                            keystone.roles.revoke(user=u_id, project=proj_id,
                                                  role=role_id)
                        for role_id in roles_to_add:
                            keystone.roles.grant(user=u_id, project=proj_id,
                                                 role=role_id)

                    # update domain roles
                    for d_id in related_domains:
                        target_roles = target_domain_roles.get(d_id, set())
                        existing_roles = existing_domain_roles.get(d_id,
                                                                   set())
                        roles_to_add = target_roles - existing_roles
                        roles_to_del = existing_roles - target_roles
                        for role_id in roles_to_del:
                            keystone.roles.revoke(user=u_id, domain=d_id,
                                                  role=role_id)
                        for role_id in roles_to_add:
                            keystone.roles.grant(user=u_id, domain=d_id,
                                                 role=role_id)

                    # validate password if not new user
                    # TODO
                os._exit(0)
            processes.append(p)
            if (len(processes) > 32):
                p = processes.pop(0)
                assert (p, 0) == os.waitpid(p, 0)
    for p in processes:
        assert (p, 0) == os.waitpid(p, 0)
    LOG.info("Managing keystone accounts took: %f" % (time.time() - start))


# These alg will blow up if anybody changes anything in parallel
# assuming nobody else manipulating these things at the same time

# NOTE: ~ 2.08 sec full, ~ 0.1 sec verify, without multiprocessing
# TODO: something increased the time above 7 sec ??
#       session was created outside, is it really 5 sec ?
def endpoint_sync(auth, regions, endpoint_override=None, dry_run=False):
    # TODO: figure out one valid endpoint_override from the regions
    # TODO: why python-request has the idea he needs to lookup the .netrc file
    # on every request
    start = time.time()
    session = keystone_session.Session(auth=auth)
    keystone = keystone_client.Client(session=session,
                                      endpoint_override=endpoint_override)

    changed = False
    existing_regions = keystone.regions.list()
    existing_services = keystone.services.list()
    existing_endpoints = keystone.endpoints.list()

    final_endpoint_dict = {}
    final_srv_set = set()
    for reg_name, reg in regions.items():
        if 'parent_region_id' not in reg:
            reg['parent_region_id'] = None
        if (reg['parent_region_id'] is not None and
                reg['parent_region_id'] not in regions):
            raise  # ref undef region
        region_uniq_sanity_check = []
        if 'services' in reg:
            for srv in reg['services']:
                if 'name' not in srv:
                    raise
                if 'type' not in srv:
                    raise
                if 'description' not in srv:
                    srv['description'] = None  # edit origin allowed
                key_tup = (srv['name'], srv['type'])
                if key_tup in region_uniq_sanity_check:
                    raise
                region_uniq_sanity_check.append(key_tup)

                srv_set_key = (srv['name'], srv['type'],
                               srv['description'])
                if srv_set_key not in final_srv_set:
                    srv_rec = (srv['name'], srv['type'], srv['description'])
                    final_srv_set.add(srv_set_key)

                if srv_set_key not in final_endpoint_dict:
                    final_endpoint_dict[srv_set_key] = {}
                if 'endpoints' in srv:
                    for enp_name, enp_value in srv['endpoints'].items():
                        rec = (reg_name, enp_name, enp_value)
                        if reg_name not in final_endpoint_dict[srv_set_key]:
                            final_endpoint_dict[srv_set_key] = {reg_name:
                                                                [rec]}
                        else:
                            k = srv_set_key
                            final_endpoint_dict[k][reg_name].append(rec)

    existing_srv_dict = dict()
    srv_dedup_list = dict()
    is_srv_desc_diff_only = dict()
    srv_endp = dict()  # of lists
    for srv in existing_services:
        srv_set_key = (srv.name, srv.type,
                       srv.description if hasattr(srv, 'description')
                       else None)
        if srv_set_key in srv_dedup_list:
            srv_dedup_list[srv_set_key].append(srv)
        else:
            srv_dedup_list[srv_set_key] = [srv]

        srv_set_only_key = (srv.name, srv.type)
        if srv_set_only_key in is_srv_desc_diff_only:
            is_srv_desc_diff_only[srv_set_only_key] = srv
        else:
            is_srv_desc_diff_only[srv_set_only_key] = False

        existing_srv_dict[srv.id] = srv

    endp_dict = dict()  # faster del by id
    for endp in existing_endpoints:
        sid = endp.service_id
        if sid not in srv_endp:
            srv_endp[sid] = []
        srv_endp[sid].append(endp)
        endp_dict[endp.id] = endp

    srv_dict = {}  # deduplicated, using new dict to be less confusing
    for key, srvL in srv_dedup_list.items():
        if len(srvL) > 0:
            # possilbe it has 0 end, and not in the srv_endp
            def nr_endps(srv):
                if srv.id not in srv_endp:  # consider poppulating it
                    return 0
                return len(srv_endp[srv.id])
            ordered = sorted(srvL, key=nr_endps, reverse=True)
            for srv in ordered[1:]:
                if srv.id in srv_endp:
                    enpL = srv_endp[srv.id]
                    for enp in enpL:
                        if dry_run:
                            return True
                        keystone.endpoints.delete(enp.id)
                        del endp_dict[endp.id]
                        changed = True
                    del srv_endp[srv.id]
                if dry_run:
                    return True
                keystone.services.delete(srv.id)
                changed = True
            srv_dict[key] = ordered[0]
        else:
            srv_dict[key] = srvL[0]

    # srv name,type,desc is now uniq

    # srv_dedup_list invalid, use srv_dict
    # exisitng endpint / service list invalid
    # srv_endp has already delted records

    # creating missing services
    for (srv_name, srv_type, srv_desc) in final_srv_set:
        srv_key = (srv_name, srv_type, srv_desc)
        if srv_key not in srv_dict:
            srv_sh_key = (srv_name, srv_type)
            if dry_run:
                return True
            if (srv_sh_key in is_srv_desc_diff_only and
                    is_srv_desc_diff_only[srv_sh_key]):
                srv = is_srv_desc_diff_only[srv_sh_key]
                del srv_dict[(srv_name, srv_type, srv.description)]
                srv.description = srv_desc
                srv.enabled = True
                n = keystone.services.update(srv,
                                             name=srv_name,
                                             type=srv_type,
                                             enabled=True,
                                             description=srv_desc)
                srv_dict[srv_key] = n
            else:
                n = keystone.services.create(name=srv_name,
                                             type=srv_type,
                                             enabled=True,
                                             description=srv_desc)
                srv_dict[srv_key] = n
                srv_endp[n.id] = []
            changed = True

    # creating missing regions, update desc , update parent
    reg_dict = {}
    for reg in existing_regions:
        reg_dict[reg.id] = reg
    reg_to_del = reg_dict.copy()
    regs_to_process = regions.copy()  # simpler then constructing a tree
    tmp_stack = []
    while regs_to_process:
        (name, reg) = regs_to_process.popitem()
        while (reg['parent_region_id'] is not None and
               reg['parent_region_id'] not in reg_dict):
            tmp_stack.append((name, reg))
            (name, reg) = (reg['parent_region_id'],
                           regs_to_process.pop(reg['parent_region_id']))
            # circular graph loop ?
            if len(tmp_stack) > 64:
                raise  # too long or loop
        tmp_stack.append((name, reg))

        while tmp_stack:
            (name, reg) = tmp_stack.pop()  # root region(s) first
            reg_to_del.pop(name, None)
            if 'description' not in reg:
                reg['description'] = ''
            # if you write none to keystone you get ''
            if reg['description'] == None:
                reg['description'] = ''
            if name not in reg_dict:
                if dry_run:
                    return True
                prid = reg['parent_region_id']
                r = keystone.regions.create(name,
                                            description=reg['description'],
                                            enabled=True,
                                            parent_region=prid)
                reg_dict[r.id] = r
                changed = True
                continue
            existing = reg_dict[name]
            if (existing.parent_region_id != reg['parent_region_id'] or
               existing.description != reg['description']):
                if dry_run:
                    return True
                keystone.regions.update(name,
                                        description=reg['description'],
                                        enabled=True,
                                        parent_region=reg['parent_region_id'])
                changed = True
    # creating missing endpoints
    # TODO: jump to multithread api calls for endp

    srv_enp_dictL = {}

    # constructing after srv desc changes  (list ref used)
    for enp in list(endp_dict.values()):
        srv = existing_srv_dict[enp.service_id]
        srv_set_key = (srv.name, srv.type,
                       srv.description if hasattr(srv, 'description')
                       else None)
        if srv_set_key not in srv_enp_dictL:
            srv_enp_dictL[srv_set_key] = {enp.region: {enp.id: enp}}
        else:
            srv_enp_dictL[srv_set_key][enp.region][enp.id] = enp

    all_srv = set(srv_enp_dictL.keys())
    all_srv.update(list(final_endpoint_dict.keys()))
    for srv_key in all_srv:
        if srv_key not in final_endpoint_dict:  # del all
            if dry_run:
                return True
            # duplicate delete required
            for reg, enp in srv_enp_dictL[srv_key].items():
                for enp_id in list(enp.keys()):
                    keystone.endpoints.delete(enp_id)
            changed = True
            continue
        if srv_key not in srv_enp_dictL:  # create all
            if dry_run:
                return True
            srv = srv_dict[srv_key]
            srv_id = srv.id
            for reg, enps in final_endpoint_dict[srv_key].items():
                for (reg_name, enp_interface, enp_value) in enps:
                    keystone.endpoints.create(srv_id, enp_value, enp_interface,
                                              region=reg_name, enabled=True)
            changed = True
            continue
        for reg, enp in srv_enp_dictL[srv_key].items():  # duplicate delete
            enp_to_delete = enp.copy()
            if reg in final_endpoint_dict[srv_key]:
                regl = final_endpoint_dict[srv_key][reg]
                for (reg_name, enp_interface, enp_value) in regl:
                    assert reg_name == reg
                    cands = []
                    match = None
                    for enp in list(srv_enp_dictL[srv_key][reg].values()):
                        if enp.interface == enp_interface:
                            cands.append(enp)
                            if enp.url == enp_value:
                                match = enp
                    if not match and dry_run:
                        return True
                    if cands:
                        if match:
                            # 1 keep others will be deleted
                            del enp_to_delete[match.id]
                            continue
                        # just url change in one all others will be deleted
                        del enp_to_delete[cands[0].id]
                        keystone.endpoints.update(enp.id, enp.service_id,
                                                  enp_value, region=reg_name,
                                                  enabled=True)
                    else:
                        srv = srv_dict[srv_key]
                        keystone.endpoints.create(srv.id, enp_value,
                                                  enp_interface,
                                                  region=reg_name,
                                                  enabled=True)
                    changed = True
            del_ids = list(enp_to_delete.keys())
            if del_ids and dry_run:
                return True
            for enp_id in del_ids:
                keystone.endpoints.delete(enp_id)
                changed = True

    # delete extra services
    for srv_key, srv_rec in srv_dict.items():
        if srv_key not in final_srv_set:
            if dry_run:
                return True
            keystone.services.delete(srv_rec.id)
            changed = True
    # delete extra regions

    if reg_to_del:
        if dry_run:
            return True
        for reg_name in list(reg_to_del.keys()):
            keystone.regions.delete(reg_name)
        changed = True

    LOG.info("Managing keystone endpoints took: %f" % (time.time() - start))
    return changed
