"""Resolve and validate the same pool settings in preflight and site tasks."""
from collections.abc import Mapping
import re

from ansible.errors import AnsibleFilterError


DEFAULTS = dict(pm='ondemand', max_children=4, process_idle_timeout='30s',
                max_requests=500, start_servers=2, min_spare_servers=1,
                max_spare_servers=3)
LEGACY = dict(pm_max_children='max_children', pm_idle_timeout='process_idle_timeout',
              pm_max_requests='max_requests')
DYNAMIC = {'start_servers', 'min_spare_servers', 'max_spare_servers'}


def mapping(value, name):
    if not isinstance(value, Mapping):
        raise AnsibleFilterError(f'{name} must be a mapping')
    return dict(value)


def integer(value, minimum, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnsibleFilterError(f'{name} must be an integer >= {minimum}')


def resolve(site, server=None, legacy_defaults=None, cap=64):
    """Merge partial layers. Reject explicit mode-incompatible settings."""
    server = mapping({} if server is None else server, 'php_fpm_defaults')
    legacy_defaults = mapping({} if legacy_defaults is None else legacy_defaults, 'site_defaults')
    site = mapping(site, 'site settings')
    nested = mapping(site.get('php_fpm', {}), 'php_fpm')
    for name, layer in [('php_fpm_defaults', server), ('php_fpm', nested)]:
        unknown = set(layer) - set(DEFAULTS)
        if unknown:
            raise AnsibleFilterError(f'{name}: unknown keys {sorted(unknown)}')
    for flat, key in LEGACY.items():
        if flat in site and key in nested:
            raise AnsibleFilterError(f'Use either php_fpm.{key} or {flat}, not both')
    cfg = {**DEFAULTS, **server}
    cfg.update({key: legacy_defaults[flat] for flat, key in LEGACY.items() if flat in legacy_defaults})
    cfg.update(nested)
    cfg.update({key: site[flat] for flat, key in LEGACY.items() if flat in site})
    if not isinstance(cfg['pm'], str) or cfg['pm'] not in ('ondemand', 'dynamic'):
        raise AnsibleFilterError('php_fpm.pm must be ondemand or dynamic')
    integer(cap, 1, 'php_fpm_max_children_cap')
    integer(cfg['max_children'], 1, 'php_fpm.max_children')
    if cfg['max_children'] > cap:
        raise AnsibleFilterError(f'php_fpm.max_children exceeds php_fpm_max_children_cap={cap}')
    integer(cfg['max_requests'], 0, 'php_fpm.max_requests')
    # Built-in inactive settings are harmless. Explicit inactive settings are errors.
    explicit = set(server) | set(nested)
    explicit.update(key for flat, key in LEGACY.items() if flat in site)
    # Legacy role defaults contain an idle timeout for backward compatibility.
    # Validate its value for ondemand, but do not treat it as a site mode choice.
    if cfg['pm'] == 'ondemand':
        if explicit & DYNAMIC:
            raise AnsibleFilterError('ondemand does not accept start_servers or spare server settings')
        timeout = cfg['process_idle_timeout']
        if isinstance(timeout, bool) or not isinstance(timeout, (int, str)) or not re.fullmatch(r'[0-9]+[smhd]?', str(timeout)):
            raise AnsibleFilterError('php_fpm.process_idle_timeout must be nonnegative seconds or a duration such as 30s or 1m')
    else:
        if 'process_idle_timeout' in explicit:
            raise AnsibleFilterError('dynamic does not accept process_idle_timeout; remove the explicit idle timeout')
        for key in DYNAMIC:
            integer(cfg[key], 1, f'php_fpm.{key}')
        if not cfg['min_spare_servers'] <= cfg['start_servers'] <= cfg['max_spare_servers'] <= cfg['max_children']:
            raise AnsibleFilterError('Require min_spare_servers <= start_servers <= max_spare_servers <= max_children')
    return cfg


def validate(sites, server=None, legacy_defaults=None, cap=64, worker_est=150):
    errors = []
    try:
        integer(worker_est, 1, 'php_fpm_worker_est_mb')
        resolve({}, server, legacy_defaults, cap)
    except AnsibleFilterError as exc:
        errors.append(f'FPM defaults: {exc}')
    if isinstance(sites, Mapping):
        for domain, cfg in sites.items():
            try:
                resolve({} if cfg is None else cfg, server, legacy_defaults, cap)
            except AnsibleFilterError as exc:
                errors.append(f'{domain}: {exc}')
    return errors


def budget(sites, server=None, legacy_defaults=None, cap=64):
    return sum(resolve(cfg or {}, server, legacy_defaults, cap)['max_children'] for cfg in sites.values())


class FilterModule:
    def filters(self):
        return dict(php_fpm_resolve=resolve, php_fpm_errors=validate, php_fpm_workers=budget)
