"""Print safe, actionable advice without accepting or changing server state."""
import sys
import argparse
import re


def hint(tool, reason='', *, stream=None):
    # Do not repeat argv, environment, subprocess output, or credential values.
    # Reason is used for classification only and is never printed here.
    output = stream if stream is not None else sys.stderr
    advice = []
    text = str(reason).lower()
    if any(word in text for word in ('lock', 'another hosting operation')):
        advice.append('Check systemctl status wp-backup.service and ps aux. Wait for the active operation. Do not delete a lock file to bypass it.')
    if any(word in text for word in ('permission', 'root', 'owner', 'symlink', 'unsafe')):
        advice.append('Check the required user, ownership, and paths. Keep root-owned facts and secrets private. Do not use chmod 777 or bypass link checks.')
    if any(word in text for word in ('snapshot', 'restic', 'repository', 'backup', 'manifest', 'dump')):
        advice.append('Run wp-backup-manager status and wp-backup-manager list. Check the configured repository, backup mount, free space, and credential-file permissions. Do not edit repository files.')
    if tool in ('lemp', 'site'):
        advice.append('On the controller, run scripts/' + tool + ' --help. Check inventories/SERVER/settings.yml and sites.yml. Run scripts/lemp SERVER doctor. Replace SERVER with the selected inventory name.')
        advice.append('For an Ansible failure, read the failed task above. Check SSH access and dependencies. Review the cause before retrying; do not bypass a failed safety check.')
    elif tool == 'wp-backup-manager':
        advice.append('Run wp-backup-manager --help for command examples. Review the failed task log and wp-backup-manager workspaces. Keep failed data until you inspect it; recovery must not replace live data automatically.')
    elif tool == 'wp-backup':
        advice.append('Run wp-backup --help. Check wp-backup-manager status and journalctl -u wp-backup.service. Fix the reported repository, database, or manifest problem before retrying. Use extraction to review recovery data before a live restore.')
    elif tool in ('jump-in', 'site-shell'):
        advice.append('As root, use jump-in SITE with an installed site domain. site-shell accepts no arguments and runs only as the code owner. Check conf/site.env and htdocs. Do not enable SSH or change nologin to fix local entry.')
    elif tool in ('site-wp', 'site-php', 'site-check', 'site-policy', 'wp-site', 'jump-in-help'):
        advice.append('Inside jump-in SITE, run jump-in-help --all for commands. Use site-check for diagnostics and site-policy show for local policy. As root, use wp-site --help. Review checksum differences before accepting a policy exception.')
        if tool in ('site-wp', 'site-php'):
            advice.append('Check the site PHP version, command arguments, database access, and network. Use site-wp --help or site-php --help. Do not switch to root to bypass site permissions.')
    elif tool == 'site-fix-perms':
        advice.append('As root, run site-fix-perms SITE --check. Review the listed paths. After review, run site-fix-perms SITE to repair managed permissions, then repeat --check. Investigate links before removing them.')
    elif tool == 'wp-git-commit':
        advice.append('Run jump-in SITE, then git status --short and git diff. Review ignored files and secrets before any commit. wp-git-commit SITE stages all eligible changes; use git add -- PATH and git commit for selected reviewed paths.')
    elif tool == 'wp-monitor':
        advice.append('Run wp-monitor --check SITE for a read-only report. For plugin details, run wp-site check SITE plugins. Review Git with jump-in SITE, then git status --short and git diff. Do not accept unknown changes.')
        advice.append('After you verify intentional administrator changes, run wp-monitor --ack SITE. This accepts the administrator list, not code or checksum differences. Check Nginx/PHP-FPM logs for service failures.')
    elif tool == 'tg-alert':
        advice.append('Check the private /etc/tg-alert.env file: TG_BOT_TOKEN and TG_CHAT_ID. Check DNS/HTTPS access to api.telegram.org and journalctl -t tg-alert. Never print the token or use a token URL in shell history.')
    elif tool == 'valkey-acl-sync':
        advice.append('Review valkey-acl-sync --help. Check systemctl status valkey, the Unix socket, and the private ACL/admin files. Reapply the reviewed Ansible configuration; do not print credentials.')
    else:
        advice.append('Check command help, permissions, free space, and the operation log. Identify the cause before retrying.')
    for line in advice:
        print('HINT ' + line, file=output)


def run_cli(tool, callback, *, failure_code=1):
    """Preserve exit codes; add guidance to failures, never to cancellation."""
    try:
        result = callback()
    except KeyboardInterrupt:
        print('Cancelled.', file=sys.stderr)
        return 130
    except SystemExit as exc:
        if exc.code:
            hint(tool)
        raise
    except Exception as exc:
        # Exception text can contain secrets (URLs, DB errors, arbitrary argv).
        detail = str(exc) if isinstance(exc, (ValueError, OSError, KeyError, RuntimeError)) else type(exc).__name__ + '; inspect the operation log'
        detail = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', detail)[:500]
        print('FAIL ' + tool + ': ' + detail, file=sys.stderr)
        hint(tool, str(exc))
        return failure_code
    if result == 130:
        return result
    if result:
        print(f'FAIL {tool}: command returned exit code {result}.', file=sys.stderr)
        hint(tool)
    return result


class HintParser(argparse.ArgumentParser):
    def error(self, message):
        hint(self.prog.rsplit('/', 1)[-1], message)
        super().error(message)
