#!/usr/bin/python
"""Own only the maintenance marker for a fresh WordPress installation."""
import os
from pathlib import Path
import stat

CONTENT = b'fresh WordPress installation\n'


def manage(path, *, release=False, check=False):
    path = Path(path)
    parent = path.parent.stat()
    if path.parent.is_symlink() or parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
        raise ValueError('The installation gate needs a trusted parent directory')
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        if release:
            return False
        if check:
            return True
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        except FileExistsError:
            return manage(path, release=release, check=check)
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        return True
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1 or info.st_mode & 0o022:
            raise ValueError('Unsafe installation gate')
        if stream.read(len(CONTENT) + 1) != CONTENT:
            raise ValueError('Another operation owns the maintenance gate; inspect it before installation')
        if release:
            if not check:
                path.unlink()
            return True
        changed = stat.S_IMODE(info.st_mode) != 0o644
        if changed and not check:
            os.fchmod(stream.fileno(), 0o644)
        return changed


def main():
    from ansible.module_utils.basic import AnsibleModule
    module = AnsibleModule(argument_spec=dict(path=dict(required=True, type='path'),
        state=dict(choices=['present', 'absent'], default='present')), supports_check_mode=True)
    try:
        changed = manage(module.params['path'], release=module.params['state'] == 'absent', check=module.check_mode)
        module.exit_json(changed=changed)
    except (OSError, ValueError) as exc:
        module.fail_json(msg=str(exc))


if __name__ == '__main__':
    main()
