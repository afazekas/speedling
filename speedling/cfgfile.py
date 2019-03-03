import contextlib
import errno
import re
import os
import stat
try:
    from collections import abc
except:
    import collections as abc
import pwd  # getent passwd
import grp  # getent groups
import numbers
import logging
import uuid

LOG = logging.getLogger(__name__)

# TODO: add full support for multine args, use a real tokenizer/parser

# Does not creates path
# Does not uses safe write, avoid ctrl+c !

# NOTE: '\r' handling is partial, the output can be inconsistent


def _key_value_to_str(key, value):
    if isinstance(value, str):  # basic string
        # tryng without quoting ..
        return key + ' = ' + value + '\n'
    if isinstance(value, abc.Iterable):  # multi value
        return ''.join((_key_value_to_str(key, i) for i in value))
    # not string, expecting boolean and number types
    return key + ' = ' + str(value) + '\n'


def _section_to_str(name, section_dict):
    keys = sorted(section_dict.keys())
    if name:
        sec_str = '\n[' + name + ']\n'
    else:
        sec_str = ''
    return sec_str + ''.join((_key_value_to_str(key,
                              section_dict[key]) for
                              key in keys))


# TODO: white space ini rules needs to be verified
SECTION_NAME_RE = re.compile('\[(.*)\][\r\n]+')
SECTION_ARG_ANCHOR = re.compile('#([^ =]+)[= ].*[\r\n]+')
VALID_OTHER_START = set(('\n', '\r'))

# TODO: create function variant which deals with the rpmnew


def ini_file_sync(target_path, *args, **kwargs):
    kwargs['template_file'] = target_path
    return ini_gen(target_path, *args, **kwargs)


#   ~500 cfg_file/sec/core  (3k line files)
# write in C ?
def ini_gen(target_path, paramters, owner='root', group='root', mode=0o640,
            acl=None, se_label=None, template_file=None, dry_run=False):
    if acl:
        raise NotImplementedError
    if dry_run:
        raise NotImplementedError
    if se_label:
        raise NotImplementedError

    # for early failure, put it into the front
    if isinstance(owner, numbers.Integral):
        uid = owner
    else:
        # cache ??
        uid = pwd.getpwnam(owner).pw_uid

    if isinstance(group, numbers.Integral):
        gid = group
    else:
        gid = grp.getgrnam(group).gr_gid

    target_exists = False

    try:
        with contextlib.closing(open(target_path)) as f:
            original_lines = f.readlines()
            target_exists = True
    except IOError as e:
        if e.errno == errno.ENOENT:
            original_lines = []
        else:
            raise  # reraise

    if target_exists:
        original_stat = os.lstat(target_path)
        if not stat.S_ISREG(original_stat[stat.ST_MODE]):
            raise  # illegal state, not supported, delete ?

    if template_file:
        if template_file != target_path:
            with contextlib.closing(open(template_file, 'r')) as f:
                template_file_content = f.readlines()
        else:
            template_file_content = list(original_lines)
    else:
        template_file_content = []

    filtered_template = []
    section_offset = {}  # last occurance
    arg_to_pos = {}  # insert after this offset

    # most frequent thing is the lines stars with '#'
    # most of these lines are not matching to the regex
    current_section = None
    for l in template_file_content:
            fc = l[0]
            if fc == '#':
                m = SECTION_ARG_ANCHOR.match(l)
                if (m):
                    k_t = (current_section, m.group(1))
                    arg_to_pos[k_t] = len(filtered_template)
                filtered_template.append(l)
                continue
            # remove all non section name or comment part
            if fc == '[':
                m = SECTION_NAME_RE.match(l)
                if not m:
                    continue
                current_section = m.group(1)
                section_offset[current_section] = len(filtered_template)
                filtered_template.append(l)
                continue
            if fc in VALID_OTHER_START:
                filtered_template.append(l)

    new_section = {}
    insert_after = []
    for section, keys in paramters.items():
        if section not in section_offset:
            new_section[section] = keys
            continue
        key_keys = sorted(keys.keys())
        for key in key_keys:
            pair = (section, key)
            ins_str = _key_value_to_str(key, keys[key])
            if pair in arg_to_pos:
                insert_after.append((arg_to_pos[pair],
                                    ins_str))
            else:
                insert_after.append((section_offset[section],
                                    ins_str))
    insert_after = sorted(insert_after, reverse=True)
    generated_lines = list(filtered_template)  # just rename?

    for pos, content in insert_after:
        generated_lines.insert(pos+1, content)

    def none_key_fn(item):
        if item:
            return item
        else:
            return ''
    sections = sorted(new_section.keys(), key=none_key_fn)
    try:
        for sec in sections:
            generated_lines.append(_section_to_str(sec, new_section[sec]))
    except:
        LOG.error('section: %s' % (sec))
        raise

    final_file = ''.join(generated_lines)
    # we should not have split it at read time
    original = ''.join(original_lines)
    changed = original != final_file or not target_exists
    if changed:
        f = None
        try:
            # TODO: rnd tmp name + move(rename)
            f = os.fdopen(os.open(target_path,
                                  os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                                  mode), 'w')
            f.write(final_file)
            original_stat = os.lstat(target_path)
            changed = True
        finally:
            if f:
                f.close()
    if (original_stat[stat.ST_UID] != uid or
            original_stat[stat.ST_GID] != gid):
        os.chown(target_path, uid, gid)
        changed = True
    if (stat.S_IMODE(original_stat[stat.ST_MODE]) != mode):
        os.chmod(target_path, mode)
        changed = True
    return changed


# '/etc' -> ''
# '/etc/foo' -> '/etc'
def _dir_part(path):
    pa = path.split(os.path.sep)
    return os.path.sep.join(pa[:-1])


# excepting abs path for link
def ensure_sym_link(link, target):
    assert link[0] == '/'
    original_target = None
    new = False
    try:
        original_target = os.readlink(link)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise  # likely not symbolic link but exists
        new = True
    if new:
        os.symlink(target, link)
        return True
    if target == original_target:
        return False
    # short enough, long enough, random enough, str
    # existance check ?
    tmplnk = os.path.join(_dir_part(link), str(uuid.uuid4()))
    os.symlink(target, tmplnk)
    os.rename(tmplnk, link)
    return True


def ensure_path_exists(target_path, mode=0o750, owner='root', group='root',
                       acl=None, se_label=None, dry_run=False):
    if acl:
        raise NotImplementedError
    if dry_run:
        raise NotImplementedError
    if se_label:
        raise NotImplementedError

    # for early failure, put it into the front
    # NOTE: repted code, function ?
    if isinstance(owner, numbers.Integral):
        uid = owner
    else:
        uid = pwd.getpwnam(owner).pw_uid

    if isinstance(group, numbers.Integral):
        gid = group
    else:
        gid = grp.getgrnam(group).gr_gid

    changed = False
    try:
        os.makedirs(target_path, mode)
        changed = True
    except OSError as exc:
        if exc.errno != errno.EEXIST:
            raise
    original_stat = os.lstat(target_path)
    # repeated pattern , function ?
    if (original_stat[stat.ST_UID] != uid or
       original_stat[stat.ST_GID] != gid):
        os.chown(target_path, uid, gid)
        changed = True
    if (stat.S_IMODE(original_stat[stat.ST_MODE]) != mode):
        os.chmod(target_path, mode)

    return changed


def content_file(target_path, content,
                 owner='root', group='root', mode=0o640,
                 acl=None, se_label=None, dry_run=False):
    if acl:
        raise NotImplementedError
    if dry_run:
        raise NotImplementedError
    if se_label:
        raise NotImplementedError

    # for early failure, put it into the front
    if isinstance(owner, numbers.Integral):
        uid = owner
    else:
        # cache ??
        uid = pwd.getpwnam(owner).pw_uid

    if isinstance(group, numbers.Integral):
        gid = group
    else:
        gid = grp.getgrnam(group).gr_gid

    target_exists = False

    try:
        with contextlib.closing(open(target_path)) as f:
            original_content = f.read()
            target_exists = True
    except IOError as e:
        if e.errno != errno.ENOENT:
            raise  # reraise

    if target_exists:
        original_stat = os.lstat(target_path)
        if not stat.S_ISREG(original_stat[stat.ST_MODE]):
            raise  # illegal state, not supported, delete ?
        changed = content != original_content
    else:
        changed = True

    if changed:
        f = None
        try:
            # TODO: rnd tmp name + move(rename)
            f = os.fdopen(os.open(target_path,
                                  os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                                  mode), 'w')
            f.write(content)
            original_stat = os.lstat(target_path)
            changed = True
        finally:
            if f:
                f.close()

    if (original_stat[stat.ST_UID] != uid or
            original_stat[stat.ST_GID] != gid):
        os.chown(target_path, uid, gid)
        changed = True
    if (stat.S_IMODE(original_stat[stat.ST_MODE]) != mode):
        os.chmod(target_path, mode)
        changed = True
    return changed


# installs small local file
def install_file(target_path, source_path,
                 owner='root', group='root', mode=0o640,
                 acl=None, se_label=None, dry_run=False):
    with open(source_path) as f:
        content = f.read()
    return content_file(target_path, content,
                        owner, group, mode,
                        acl, se_label, dry_run)


# erlang mapping is not used in the config,
# so the dict will be list of tupples
# The single quoted literal is almost the same as the not quoted one
def _logical_repr_to_str(part, nest=0):
    if isinstance(part, abc.Mapping):
        # list with tuppes
        s = '['
        commas = len(part) - 1
        for k, v in part.items():
            s += '{' + k + ','
            if nest == 0:
                s += '\n' + ' ' * (nest + 1)
            s += _logical_repr_to_str(v, nest=nest+1)
            s += '}'
            if commas:
                commas -= 1
                s += ','
            s += '\n' + (' ' * nest)
        s += ']'
        return s
    if isinstance(part, list):
        s = '['
        s += ','.join(_logical_repr_to_str(l, nest=nest+1) for l in part)
        s += ']'
        return s

    if isinstance(part, tuple):
        s = '{'
        s += ','.join(_logical_repr_to_str(l, nest=nest+1) for l in part)
        s += '}'
        return s

    if isinstance(part, str):
        return "'" + part + "'"
    if isinstance(part, bytes):
        return '"' + str(part) + '"'
    if part is True:
        return 'true'
    if part is False:
        return 'fales'
    return str(part)


def logical_repr_to_str(part):
    return _logical_repr_to_str(part) + '\n.'


def rabbit_file(target_path, part,
                owner='rabbitmq', group='rabbitmq', mode=0o640,
                acl=None, se_label=None, dry_run=False):
    return content_file(target_path, logical_repr_to_str(part),
                        owner, group, mode,
                        acl, se_label, dry_run)


def hacfg_section_lines(element, prefix):
    lines = []
    if isinstance(element, abc.Mapping):
        keys = sorted(element.keys())
        for k in keys:
            p = prefix + k + ' '
            lines += hacfg_section_lines(element[k], p)
        return lines
    if isinstance(element, str):  # iteable not list/tuple
        lines.append(prefix + element)
        return lines
    if isinstance(element, abc.Iterable):
        p = prefix + ' '
        for l in element:
            lines += hacfg_section_lines(l, p)
        return lines
    lines.append(prefix + str(element))
    return lines


def haproxy_to_str(cfg):
    cfg_cpy = cfg.copy()
    lines = []
    if 'global' in cfg:
        lines.append('global')
        lines += hacfg_section_lines(cfg_cpy['global'],
                                     prefix='    ')
        del cfg_cpy['global']
    if 'defaults' in cfg:
        lines.append('defaults')
        lines += hacfg_section_lines(cfg_cpy['defaults'],
                                     prefix='    ')
        del cfg_cpy['defaults']
    keys = sorted(cfg_cpy.keys())
    for k in keys:
        for name, data in cfg_cpy[k].items():
            lines.append(k + ' ' + name)
            lines += hacfg_section_lines(data, prefix='    ')
    lines.append('')
    return '\n'.join(lines)


def haproxy_file(target_path, part,
                 owner='root', group='root', mode=0o640,
                 acl=None, se_label=None, dry_run=False):
    return content_file(target_path, haproxy_to_str(part),
                        owner, group, mode,
                        acl, se_label, dry_run)
