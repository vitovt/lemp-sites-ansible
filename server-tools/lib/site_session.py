"""Trusted identity and environment for local site-owner sessions.

The launcher changes credentials. The shell never changes credentials.
All paths stay absolute so a future jail can provide the same internal layout.
"""
import grp
import ctypes
import os
from pathlib import Path
import pwd
import re
import stat
import sys

from wp_hosting import DOMAIN, NAME, RESERVED, parse_env

SITES = Path('/srv/sites')
TOOLS = Path('/usr/local/lib/wp-hosting')
SHELL = '/usr/local/sbin/site-shell'
BASE_PATH = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
PATH = str(TOOLS / 'bin/site-session') + ':' + BASE_PATH


def trusted(path, *, directory=False):
    path = Path(path)
    for ancestor in [*reversed(path.parents), path]:
        info = ancestor.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Untrusted root-owned path: ' + str(ancestor))
    info = path.lstat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError('Wrong path type: ' + str(path))
    return path


def facts(domain):
    if not DOMAIN.fullmatch(domain):
        raise ValueError('Invalid site domain')
    base = trusted(SITES / domain, directory=True)
    path = trusted(base / 'conf/site.env')
    # Trusted parents cannot be renamed or written by the site owner.
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
            raise ValueError('Unsafe site facts')
        # Parse literal key/value facts, never source shell code or execute PHP.
        with os.fdopen(os.dup(fd)) as stream:
            values = parse_env(stream.read())
    finally:
        os.close(fd)
    if values.get('DOMAIN') != domain or not NAME.fullmatch(values.get('SITE_USER', '')) or values['SITE_USER'] in RESERVED:
        raise ValueError('Invalid site identity')
    if not re.fullmatch(r'[1-9][0-9]*\.[0-9]+', values.get('PHP_VERSION', '')):
        raise ValueError('Invalid PHP branch')
    account = pwd.getpwnam(values['SITE_USER'])
    group = grp.getgrgid(account.pw_gid)
    if account.pw_uid == 0 or account.pw_gid == 0 or account.pw_dir != str(base) or group.gr_name != values['SITE_USER']:
        raise ValueError('Site account does not match the trusted facts')
    groups = {account.pw_gid}
    if values.get('REDIS', 'false') not in ('true', 'false'):
        raise ValueError('Invalid Redis flag')
    if values.get('REDIS') == 'true':
        valkey = grp.getgrnam('valkey')
        if valkey.gr_gid == 0:
            raise ValueError('Unsafe Valkey group')
        groups.add(valkey.gr_gid)
    return {**values, 'base': base, 'domain': domain, 'account': account, 'groups': sorted(groups)}


def has_capabilities():
    # No /proc mount is required inside a future jail. Ambient capabilities
    # require both permitted and inheritable capabilities, checked here.
    class Header(ctypes.Structure):
        _fields_ = [('version', ctypes.c_uint32), ('pid', ctypes.c_int)]
    class Data(ctypes.Structure):
        _fields_ = [('effective', ctypes.c_uint32), ('permitted', ctypes.c_uint32), ('inheritable', ctypes.c_uint32)]
    header = Header(0x20080522, 0)
    data = (Data * 2)()
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.capget(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot read process capabilities')
    return any(item.effective or item.permitted or item.inheritable for item in data)


def current_site():
    uid = os.getuid()
    if uid == 0 or os.getresuid() != (uid, uid, uid):
        raise PermissionError('Run as an unprivileged site owner; use jump-in SITE from root')
    account = pwd.getpwuid(uid)
    home = Path(account.pw_dir)
    if home.parent != SITES or not DOMAIN.fullmatch(home.name):
        raise PermissionError('This UID is not a managed site owner')
    s = facts(home.name)
    if s['account'].pw_uid != uid or s['account'].pw_name != account.pw_name:
        raise PermissionError('Only the code owner can open this site environment')
    if os.getresgid() != (account.pw_gid,) * 3 or set(os.getgroups()) != set(s['groups']):
        raise PermissionError('Unexpected groups; enter through jump-in SITE')
    if has_capabilities():
        raise PermissionError('A site session must not have process capabilities')
    return s


def owner_directory(path, s):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != s['account'].pw_uid or info.st_gid != s['account'].pw_gid:
        raise ValueError('Expected a directory owned by the site: ' + str(path))
    return path


def docroot(s):
    return owner_directory(s['base'] / 'htdocs', s)


def environment(s):
    term = os.environ.get('TERM', 'dumb')
    if not re.fullmatch(r'[a-zA-Z0-9+_.-]{1,80}', term):
        term = 'dumb'
    return {'HOME': str(s['base'] / 'owner-tmp'), 'SHELL': '/bin/bash', 'USER': s['SITE_USER'], 'LOGNAME': s['SITE_USER'],
            'PATH': PATH, 'TERM': term, 'LANG': 'C.UTF-8',
            'SITE_DOMAIN': s['domain'], 'SITE_USER': s['SITE_USER'], 'SITE_BASE': str(s['base']),
            'SITE_PHP': s['PHP_VERSION'], 'WP_CLI_CACHE_DIR': str(s['base'] / 'owner-tmp/wp-cli-cache'),
            'GIT_CONFIG_COUNT': '2', 'GIT_CONFIG_KEY_0': 'user.name', 'GIT_CONFIG_VALUE_0': 'Site operator',
            'GIT_CONFIG_KEY_1': 'user.email', 'GIT_CONFIG_VALUE_1': 'operator@localhost'}


def php_path(s):
    path = trusted('/usr/bin/php' + s['PHP_VERSION'])
    if not os.access(path, os.X_OK):
        raise ValueError('The site PHP command is not executable')
    return str(path)


def close_inherited_fds():
    # Linux close_range needs no /proc mount and also closes descriptors above
    # a caller-lowered RLIMIT_NOFILE. Keep only the terminal standard streams.
    libc = ctypes.CDLL(None, use_errno=True)
    close_range = getattr(libc, 'close_range', None)
    if close_range is None:
        raise OSError('The system needs close_range support for a safe site session')
    close_range.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    close_range.restype = ctypes.c_int
    if close_range(3, 0xffffffff, 0) != 0:
        raise OSError(ctypes.get_errno(), 'Cannot close inherited file descriptors')


def enter(domain):
    if os.getresuid() != (0, 0, 0):
        raise PermissionError('jump-in must run as root')
    if not sys.stdin.isatty() or not sys.stdout.isatty() or not sys.stderr.isatty():
        raise ValueError('jump-in needs an interactive terminal on stdin, stdout and stderr')
    s = facts(domain)
    root = docroot(s)
    shell = trusted(TOOLS / 'bin/site-shell')
    trusted('/usr/bin/setpriv')
    trusted('/usr/sbin/runuser')
    os.chdir(root)
    # A separate PTY prevents owner code from injecting into the root terminal.
    # Explicit -g/-G replace root and unexpected NSS groups before setpriv clears caps.
    command = ['/usr/sbin/runuser', '--pty', '--user=' + s['SITE_USER'], '--group=' + s['SITE_USER']]
    command += ['--supp-group=' + grp.getgrgid(gid).gr_name for gid in s['groups']]
    command += ['--', '/usr/bin/setpriv', '--inh-caps=-all', '--ambient-caps=-all', '--', str(shell)]
    env = environment(s)
    env['PATH'] = BASE_PATH  # Privileged terminal setup must use only system commands.
    close_inherited_fds()
    os.execve(command[0], command, env)


def shell():
    s = current_site()
    if not sys.stdin.isatty() or not sys.stdout.isatty() or not sys.stderr.isatty():
        raise ValueError('site-shell needs an interactive terminal; command and SSH modes are not supported')
    os.umask(0o027)
    root = docroot(s)
    home = owner_directory(s['base'] / 'owner-tmp', s)
    if stat.S_IMODE(home.stat().st_mode) != 0o700:
        raise ValueError('owner-tmp must have mode 0700')
    cache = home / 'wp-cli-cache'
    cache.mkdir(mode=0o700, exist_ok=True)
    owner_directory(cache, s)
    if stat.S_IMODE(cache.stat().st_mode) != 0o700:
        # Older WP-CLI runs can create a 0755 cache under the private owner home.
        # Tighten only this verified owner directory, without rewriting its files.
        cache.chmod(0o700)
    history = home / '.bash_history'
    fd = os.open(history, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('History must be a private regular file owned by this user')
    finally:
        os.close(fd)
    profile = trusted(TOOLS / 'lib/site-shell.bashrc')
    trusted(TOOLS / 'lib/site-completion.bash')
    trusted(TOOLS / 'bin/site-session', directory=True)
    php_path(s)
    env = environment(s)
    env['HISTFILE'] = str(history)
    os.chdir(root)
    close_inherited_fds()
    os.execve('/bin/bash', ['bash', '--noprofile', '--rcfile', str(profile), '-i'], env)


def cli(action, argv):
    try:
        if action == 'jump-in' and os.getresuid() != (0, 0, 0):
            raise PermissionError('jump-in must run as root')
        if argv == ['--help']:
            print('Usage: jump-in SITE (root only)' if action == 'jump-in' else
                  'Usage: site-shell\nOpen an interactive environment for the current site-owner UID.\nArguments, -c, login-shell mode and SSH commands are not supported.')
            return 0
        if action == 'jump-in':
            if len(argv) != 1 or argv[0].startswith('-'):
                raise ValueError('Usage: jump-in SITE')
            enter(argv[0])
        else:
            if argv:
                raise ValueError('site-shell accepts no arguments; -c, login-shell and SSH modes are not supported')
            shell()
        return 0
    except (ValueError, PermissionError, OSError, KeyError) as exc:
        print(action + ': ' + str(exc), file=sys.stderr)
        return 2
