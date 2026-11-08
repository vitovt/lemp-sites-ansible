# Read Update Release Notes

Use `wp-update` as root. Read release notes before an update when possible.

```sh
wp-update plan example.com --scope plugins --policy minor --changelog full
wp-update apply example.com --scope plugin:akismet --changelog brief
wp-update apply example.com --scope plugins --policy minor --changelog none
wp-update run --automatic --all-sites --changelog none
```

| Mode | Terminal output | Saved report after apply/run |
| --- | --- | --- |
| `brief` (default) | Up to five newest releases; up to two source lines per release, limited to 240 characters | Full available notes |
| `full` | All published entries in the selected range | Full available notes |
| `none` | No release notes; no source requests | None |

The selected range is `OLD < release <= NEW`. Numeric versions are compared as
numbers: `1.10` is newer than `1.9`. Prerelease or non-numeric ranges are not
interpreted. Their notes are reported as unavailable.

## Sources and limits

Plugin notes use the public WordPress.org plugin information API. The request
sends the plugin slug. It does not send site names, installed files, database
credentials, or backup credentials. The official API returns plugin readme
sections, including the changelog:
[WordPress plugin API](https://developer.wordpress.org/reference/functions/plugins_api/).

Only published version headings are selected. HTML is converted to plain text.
Script/style content and terminal control characters are removed. HTTP/HTTPS
links are preserved as text. No downloaded code is executed.

A source can omit old or intermediate releases. The report never promises a
complete release history. Missing range-boundary headings cause a `PARTIAL`
notice. An absent source, an unsupported format, or a network failure causes
`Changelog unavailable`. It does not change the update result.

Core updates show the official release archive link. Core release-note
aggregation is not implemented. Themes have no standard release-note source
in this helper. Private plugins can have no WordPress.org entry. Check the
author documentation in these cases.

Requests use HTTPS, reject redirects, and limit each response to 1 MiB. A request
uses an 8-second socket timeout and an elapsed-time check between reads. A blocked
read can extend the elapsed limit by up to the socket timeout. Multiple plugin
requests can add time to a run. Use `none` to avoid those requests.

## After an update

- Check the update result and Git commit ID first.
- Read the notes for successfully updated components only.
- Use `CHANGELOG FILE` to find the full private report in the site's `logs/`.

Reports use `wp-update-changelog-*.complete.txt` and mode `0600`. They are
outside `htdocs` and are not added to Git. `plan` does not write reports.
Fetch and write failures cannot turn a successful update into a failed update.
Notes are requested after component processing. A successful run removes its
maintenance gate before requesting notes. A failed run keeps its safety state.
An unsuccessful component is not described as successfully updated.

Release-note text is not sent to Telegram or syslog. Existing update status
logging and notifications remain separate. Standard backup, hold, Git, checksum,
and health checks remain in effect.

## Validation

Offline tests cover numeric ranges, missing boundaries, HTML parsing, control
characters, size/time limits, redirect rejection, all display modes, private
report files, and successful/failed updater integration. Test sources use
synthetic release notes. They do not contact production systems.

Live API access from this workstation was unavailable during development
(`URLError`). No server deployment or real WordPress update was performed.

Results: `tests/check.sh` passed 244 unit tests and all syntax checks. The
isolated feature source also passed 35 updater tests without unrelated local
translation changes. No live API or server integration result is claimed.
