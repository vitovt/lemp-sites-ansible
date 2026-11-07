#!/usr/bin/python
"""Check static WordPress settings without executing application code."""
import re
from pathlib import Path


def constant(text, name):
    pattern = r"define\s*\(\s*(['\"])" + re.escape(name) + r"\1\s*,\s*(.*?)\s*\)\s*;"
    values = re.findall(pattern, text, re.S)
    if len(values) != 1:
        raise ValueError(f'{name}: require one static definition')
    value = values[0][1].strip()
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    if re.fullmatch(r"'[^'\\]*'", value) or re.fullmatch(r'"[^"\\$]*"', value):
        return value[1:-1]
    raise ValueError(f'{name}: dynamic values are not supported; review the migration')


def validate(text, database, password, socket):
    for name, expected in [('DB_NAME', database), ('DB_USER', database), ('DB_PASSWORD', password)]:
        if constant(text, name) != expected:
            raise ValueError(f'{name}: config and inventory secrets do not match')
    if constant(text, 'DB_HOST') not in ('localhost', f'localhost:{socket}'):
        raise ValueError('DB_HOST: require the managed local database socket')
    for name in ('DISABLE_WP_CRON', 'DISALLOW_FILE_EDIT', 'DISALLOW_FILE_MODS', 'AUTOMATIC_UPDATER_DISABLED'):
        if constant(text, name) is not True:
            raise ValueError(f'{name}: require true before import')


def main():
    from ansible.module_utils.basic import AnsibleModule
    module = AnsibleModule(argument_spec=dict(path=dict(required=True), database=dict(required=True),
        password=dict(required=True, no_log=True), socket=dict(default='/run/mysqld/mysqld.sock')),
        supports_check_mode=True)
    try:
        path = Path(module.params['path'])
        if path.exists():
            if path.is_symlink():
                raise ValueError('wp-config.php must not be a symlink')
            validate(path.read_text(), module.params['database'], module.params['password'], module.params['socket'])
        module.exit_json(changed=False)
    except (ValueError, OSError) as exc:
        module.fail_json(msg=str(exc))


if __name__ == '__main__':
    main()
