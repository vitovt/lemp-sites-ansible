# secrets/

This directory is on the control machine only. Git ignores everything here
except this README and `*.example` files. Keep files at mode 0600.

| File | Who creates it | Used by |
|---|---|---|
| `cloudflare.ini` | You, from `cloudflare.ini.example` | certbot_cloudflare (DNS-01) |
| `telegram.env` | You, from `telegram.env.example` (optional) | telegram_alert (tg-alert) |
| `<host>/db/<domain>.pass` | Ansible (`password` lookup) | site |
| `<host>/valkey/<domain>.pass` | Ansible (`password` lookup) | valkey, site |
| `<host>/valkey/admin.pass` | Ansible (`password` lookup) | valkey (admin user for root) |
| `<host>/restic.pass` | Ansible (`password` lookup) | backup_restic |

Back up this directory separately (for example in a password manager).
Without `restic.pass` the backups cannot be restored.
