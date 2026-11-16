#!/usr/bin/python3
"""Record root-owned provenance before creating a site's Unix accounts."""
import json
import grp
import os
from pathlib import Path
import pwd
import re
import stat
from ansible.module_utils.basic import AnsibleModule

ROOT = Path('/var/lib/wp-hosting/provision')


def account(name):
    try:
        return pwd.getpwnam(name)
    except KeyError:
        return None


def intent(domain, user, site, check=False):
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]+', domain) or not re.fullmatch(r'[a-z][a-z0-9_]*', user):
        raise ValueError('Invalid provisioning identity')
    expected = {'version': 1, 'domain': domain, 'user': user, 'path': site}
    marker = ROOT / (domain + '.json')
    for parent in [ROOT, *ROOT.parents]:
        if parent.is_symlink():
            raise ValueError('Provisioning marker directory must not contain symlinks')
    if ROOT.exists():
        info = ROOT.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Provisioning marker directory must be root-owned without group/other writes')
    exists = marker.exists() or marker.is_symlink()
    if exists:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unsafe provisioning marker')
        if json.loads(marker.read_text()) != expected:
            raise ValueError('Provisioning identity differs from the recorded intent; restore the original inventory before retrying')
    env = Path(site) / 'conf/site.env'
    if env.is_symlink():
        raise ValueError('Refuse a symlink at site.env')
    if env.is_file():
        info = env.lstat()
        conf = env.parent.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISDIR(conf.st_mode) or conf.st_uid != 0 or conf.st_mode & 0o022:
            raise ValueError('site.env and its conf directory must be root-owned without group/other writes')
        if exists and not check:
            marker.unlink()
        return exists, False
    for name in (user, user + '-php'):
        entry = account(name)
        if entry and not exists:
            raise ValueError(f'{domain}: account {name} exists but site.env and provisioning intent are missing. '
                             f'Inspect getent passwd {name} and {site}/conf; explicit recovery is required. Do not create an empty site.env or delete the account.')
        if entry:
            try:
                group = grp.getgrnam(user)
            except KeyError as exc:
                raise ValueError(f'{domain}: existing account has no matching site group') from exc
            if entry.pw_gid != group.gr_gid:
                raise ValueError(f'{domain}: account {name} has a different primary group')
        if entry and (entry.pw_uid == 0 or entry.pw_dir != site or entry.pw_shell != '/usr/sbin/nologin'):
            raise ValueError(f'{domain}: existing account {name} does not match the provisioning intent')
    if not exists and not check:
        ROOT.mkdir(parents=True, mode=0o700, exist_ok=True)
        descriptor = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(expected, stream)
            stream.flush()
            os.fsync(stream.fileno())
    return not exists, True


def main():
    module = AnsibleModule(argument_spec=dict(domain=dict(required=True), user=dict(required=True), site=dict(required=True)), supports_check_mode=True)
    try:
        changed, resume = intent(**module.params, check=module.check_mode)
        module.exit_json(changed=changed, resume=resume)
    except Exception as exc:
        module.fail_json(msg=str(exc))


if __name__ == '__main__':
    main()
