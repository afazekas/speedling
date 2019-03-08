import ast
import collections
import contextlib
import re
import shlex

SECTION_NAME_RE = re.compile('\[(.*)\][ \t\r\n]+')


def _parse_value(v):
    try:
        return ast.literal_eval(v)
    except (ValueError, SyntaxError):
        return v.strip()


# not bug compatible,
# fun: "'foo'"
def parse_ansible_invetory_ini(source):
    host_vars = collections.defaultdict(dict)
    group_vars = collections.defaultdict(dict)
    host_in_group = collections.defaultdict(list)  # multi group member allowed
    inherit_from = collections.defaultdict(set)
    l = 42
    in_group = 'all'

    def child_porcessor():
        if not l.isspace():
            p = l.lstrip().split(' ', 1)[0]
            inherit_from[p].add(in_group)

    def group_processor():
        foo = shlex.split(l)  # without comments
        ll = len(foo)
        if not ll:
            if not l.isspace():
                raise Exception('Invalid Syntax3 :' + l)
        host = foo[0]
        host_in_group[in_group].append(host)
        if len(foo) > 1:
            # spaces not supported near =
            for kv in foo[1:]:
                parts = kv.split("=", 1)
                if len(parts) != 2:
                    raise Exception('Invalid Syntax2 :' + l)
                k = _parse_value(parts[0])
                v = _parse_value(parts[1])
                host_vars[host][k] = v

    def group_var_processor():
        parts = l.split("=", 1)
        if len(parts) != 2:
            if not l.isspace():
                raise Exception('Invalid Syntax:' + l)
        k = _parse_value(parts[0])
        v = _parse_value(parts[1].lstrip())
        group_vars[in_group][k] = v

    group_var_child = group_processor

    not_intersting_ch = {'\r', '\n', '#'}
    with contextlib.closing(open(source)) as f:
        while True:
            l = f.readline(131072)
            if l == '':
                break
            fc = l[0]
            if fc in not_intersting_ch:
                continue
            if fc == '[':
                m = SECTION_NAME_RE.match(l)
                current_section = m.group(1)
                parts = current_section.split(':', 1)
                if len(parts) == 1:
                    in_group = parts[0]
                    group_var_child = group_processor
                    continue
                else:
                    sub = parts[1]
                    in_group = parts[0]
                    if sub == 'children':
                        group_var_child = child_porcessor
                        continue
                    if sub == 'vars':
                        group_var_child = group_var_processor
                        continue
                    raise Exception('Dont know what to do with section: ' +
                                    current_section)
            group_var_child()

    return {
        'hosts': host_vars,
        'group_var': group_vars,
        'host_in_group': {k: set(v) for k, v in host_in_group.items()},
        'inherit_from': inherit_from
    }


# the dynamic invetory uses json format
def parse_ansible_invetory(source, use_yaml='auto'):
    if use_yaml == 'auto':
        # ansible doc mandatas to have extenson
        if source.endswith('.yaml') or source.endswith('.yml'):
            use_yaml = True
    if not use_yaml:
        import json  # any?
        stream = open(source, 'r')
        try:
            skel = json.load(stream)
            use_yaml = False
        except:
            if use_yaml == 'auto':
                use_yaml = True
    if use_yaml is True:
        import yaml  # no hard dep
        stream = open(source, 'r')
        skel = yaml.load(stream)

    host_vars = {}
    group_vars = collections.defaultdict(dict)
    host_in_group = collections.defaultdict(list)  # multi group member allowed
    inherit_from = collections.defaultdict(set)

    def handle_hosts(group, H):
        for host, var in H.items():
            host_in_group[host].append(group)
            if var:
                if host not in host_vars:
                    host_vars[host] = var
                else:
                    host_vars[host].update(var)

    def handle_group(group, G):
        if 'hosts' in G:
            handle_hosts(group, G['hosts'])
        if 'children' in G:
            for g, v in G['children'].items():
                handle_group(g, v)
                inherit_from[g].add(group)
        if 'vars' in G:
            group_vars[group].update(G['vars'])

    for g, val in skel.items():
        handle_group(g, val)

    return {
        'hosts': host_vars,
        'group_var': group_vars,
        'host_in_group': {k: set(v) for k, v in host_in_group.items()},
        'inherit_from': inherit_from
    }


if '__main__' == __name__:
    import tempfile
    import os
    test = """
foo bar="{'baz':42,'bar':{'egg':'ham'}}"
[all:vars]
sl_default_region = RegionOne
sl_try_dict = {'foo':1 }
[controller]
foo
[compute]
baz bar="{'baz':42,'bar':{'egg':'spam'}}"
[os:children]
controller
compute"""
    tmp = tempfile.mkstemp()
    f = os.fdopen(tmp[0], 'w')
    f.write(test)
    f.close()
    res = parse_ansible_invetory_ini(tmp[1])
    print(res)
    os.remove(tmp[1])
    test2 = """---
all:
   hosts:
      foo:
        baz: 42
        bar:
          egg: ham
   vars:
      sl_default_region: RegionOne
      sl_try_dict:
          foo: 1
   children:
     os:
      children:
        controller:
           hosts:
             foo:
        compute:
           baz:
             baz: 42
             bar:
               egg: spam
"""
    tmp = tempfile.mkstemp()
    f = os.fdopen(tmp[0], 'w')
    f.write(test2)
    f.close()
    res = parse_ansible_invetory(tmp[1], use_yaml=True)
    print(res)
    os.remove(tmp[1])
