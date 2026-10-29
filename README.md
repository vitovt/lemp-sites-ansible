# wp-hosting-ansible (v2)

Ansible для хостингу WordPress.
Ціль: Debian 13 (trixie) у непривілейованому LXC.
Весь вхідний трафік іде через Cloudflare.
Статус: усі ролі написані, lint чистий. Повний тест на тестовому CT ще не пройдено (див. «Тестовий запуск»).

## Вимоги

- Python 3 і venv у `.venv/` (не в git).
- Колекції з `requirements.yml`.
- Ansible підключається як `root` за SSH-ключем.

```sh
python3 -m venv .venv
.venv/bin/pip install ansible-core ansible-lint
.venv/bin/ansible-galaxy collection install -r requirements.yml
```

## Перевірка

Ці команди не підключаються до серверів:

```sh
.venv/bin/ansible-playbook -i inventories/prod/hosts.yml --syntax-check playbooks/site.yml
.venv/bin/ansible-lint
```

Перший запуск на сервері робіть з `--check --diff`.

## Playbooks

| Файл | Дія |
|---|---|
| `playbooks/site.yml` | Повна конфігурація хоста. |
| `playbooks/site-backup.yml` | Бекап зараз: один сайт (`-e wp_site_only=<domain>`) або все. |
| `playbooks/site-remove.yml` | Видалення сайту (спершу бекап і архів). |
| `playbooks/site-clone.yml` | Клон сайту A у сайт B. |

Один сайт:

```sh
ansible-playbook playbooks/site.yml --limit wp01 -e wp_site_only=jenet.info
```

Порядок ролей у `site.yml`:
common, telegram_alert, nginx, php, mariadb, valkey, certbot_cloudflare, site, backup_restic, wp_monitor.
Кожна роль має тег з тією ж назвою.

## Структура

| Шлях | Зміст |
|---|---|
| `inventories/prod/hosts.yml` | Хости. |
| `inventories/prod/host_vars/<host>/sites.yml` | Сайти хоста (`wp_sites`). |
| `inventories/test/` | Тестовий CT `wptest` (див. «Тестовий запуск»). |
| `tests/verify.sh` | Перевірки на тестовому CT. |
| `scripts/test-local.sh`, `tests/container/` | Необов'язковий локальний smoke-тест у docker. |
| `roles/` | Ролі. |
| `playbooks/` | Playbooks. |
| `scripts/site` | CLI для `wp_sites` (працює на ноутбуці адміна). |
| `secrets/` | Згенеровані паролі. Не в git. |

## Ролі

| Роль | Дія |
|---|---|
| `common` | Перевірка Debian 13, пакети, часовий пояс, unattended-upgrades, SSH. Перевірка ключа root. |
| `nginx` | Nginx, FastCGI cache, модуль cache-purge, Cloudflare real IP. |
| `php` | PHP з packages.sury.org (кілька версій), wp-cli 2.12.0. |
| `mariadb` | MariaDB 11.8, доступ тільки через socket. |
| `valkey` | Valkey замість Redis: тільки unix socket, ACL-користувач на сайт. |
| `site` | Користувачі, каталоги, БД, FPM pool, vhost, wp-config, cron. |
| `certbot_cloudflare` | Сертифікат Let's Encrypt на сайт (DNS-01 через Cloudflare). |
| `telegram_alert` | Скрипт `tg-alert`: Telegram + syslog. |
| `backup_restic` | Щоночі: дамп БД кожного сайту, знімок restic, ротація, check. |
| `wp_monitor` | Щоночі: checksums, git status, PHP в uploads, нові адміни, HTTP 200. |

Примітка про `valkey`:
роль раніше мала назву `redis`.
Debian 13 дає пакет `valkey-server`.
Valkey сумісний з протоколом Redis.
Тому прапорець сайту лишається `redis: true`.
Константи `WP_REDIS_*` теж лишаються.
Плагін Redis Object Cache працює з Valkey без змін.

## Сайт: каталоги і права

Модель прав: тільки групи. ACL не використовуємо.
Кожен сайт має три облікові записи:

- `<user>` — власник коду. Shell: `/usr/sbin/nologin`. Пароль заблоковано.
- `<user>-php` — PHP-FPM pool. Основна група: `<user>`.
- `www-data` (nginx) — член групи `<user>`. Читає статичні файли.

Власники сайтів не мають SFTP і SSH.
Код змінює тільки адмін (root, wp-cli, git).

Базовий каталог: `/srv/sites/<domain>/`.

| Шлях | Власник | Режим |
|---|---|---|
| `.` | root:`<user>` | 0750 |
| `htdocs` | `<user>`:`<user>` | 2750 |
| `htdocs/wp-content` | `<user>`:`<user>` | 2750 |
| `htdocs/wp-content/{uploads,cache,upgrade}` | `<user>`:`<user>` | 2770 |
| `htdocs/wp-config.php` | `<user>`:`<user>` | 0640 |
| `conf`, `conf/nginx` | root:root | 0755 |
| `logs` | `<user>`:`<user>` | 2770 |
| `tmp` | `<user>-php`:`<user>` | 0700 |
| `backup` | root:root | 0700 |

PHP читає код, але не може його змінити.
PHP пише тільки в `uploads`, `cache`, `upgrade`, `logs`, `tmp`.
Nginx забороняє виконання PHP у `uploads`, `cache`, `upgrade`.
Nginx не віддає `wp-config.php` і dot-файли.

Скинути права одного сайту:

```sh
ansible-playbook playbooks/site.yml -t site,site_fix_perms -e wp_site_only=<domain>
```

Скрипт на сервері: `/usr/local/sbin/site-fix-perms <domain>`.
Код: каталоги 2750, файли 0640.
Каталоги для запису: каталоги 2770, файли 0660.

## База даних

Роль створює БД `db_name` (utf8mb4, utf8mb4_unicode_ci).
Роль створює користувача `db_name@localhost` з правами `ALL` на цю БД.
Пароль: 32 символи, у файлі `secrets/<host>/db/<domain>.pass`.
Lookup `password` створює файл при першому запуску.
Не видаляйте цей файл. Інакше пароль у БД зміниться.
У GRANT `_` — це шаблон LIKE. Роль екранує його (`` `test\_local`.* ``).

## wp-config.php

Роль створює `wp-config.php` тільки один раз (`force: false`).
Роль не змінює вже наявний файл.
Salts: 8 випадкових значень по 64 символи.
Файл має ці налаштування:

- `DB_HOST` — `localhost:<mariadb socket>`.
- `DISABLE_WP_CRON` — true (див. «Cron»).
- `DISALLOW_FILE_EDIT`, `DISALLOW_FILE_MODS` — true.
- `AUTOMATIC_UPDATER_DISABLED` — true.
- `WP_REDIS_SCHEME` — `unix`, `WP_REDIS_PATH` — socket Valkey.
- `WP_REDIS_PREFIX` — `<user>:`.
- `WP_REDIS_USERNAME`, `WP_REDIS_PASSWORD` (`[user, pass]`), `WP_REDIS_SELECTIVE_FLUSH`.

Оновлення ядра і плагінів робить адмін через wp-cli:

```sh
runuser -u <user> -- php<версія> /usr/local/bin/wp --path=/srv/sites/<domain>/htdocs plugin update --all
```

Використовуйте ту версію PHP, що й pool сайту.

## Cron

WP-Cron вимкнено.
Системний cron запускає `wp cron event run --due-now` кожні 5 хвилин.
Користувач: `<user>-php`. Файл: `/etc/cron.d/wp-<user>`.
Лог: `logs/cron.log`. Logrotate: `/etc/logrotate.d/wp-<user>`.

## Git-знімок коду

`/usr/local/sbin/wp-git-commit <domain> [повідомлення]` робить commit `htdocs` у локальний git.
Remote немає. `uploads`, `cache`, `upgrade` не входять у git.
Робіть знімок перед і після оновлень.

## FastCGI cache і Nginx Helper

Nginx кешує сторінки для гостей (зона `WORDPRESS`).
Ключ: `$scheme$request_method$host$request_uri`. Levels: `1:2`.
Це значення за замовчуванням у плагіні Nginx Helper.
Не змінюйте ключ. Інакше purge не знайде файли.

Налаштування плагіна (Settings → Nginx Helper):

| Параметр | Значення |
|---|---|
| Enable Purge | так |
| Caching Method | nginx Fastcgi cache |
| Purge Method | Using a GET request to PURGE/url |
| Purge when post/page is modified | так |
| Purge when comment is approved/deleted | так |

Не використовуйте метод «Delete local server cache files».
Причина: nginx створює каталоги кешу з правами 0700 і файли з правами 0600.
Власник — `www-data`. PHP працює як `<user>-php`.
PHP не може видалити ці файли без ACL. ACL ми не використовуємо.
Див. github.com/rtCamp/nginx-helper/issues/259.

Як працює purge:

1. Пакет `libnginx-mod-http-cache-purge` дає директиву `fastcgi_cache_purge`.
2. Vhost має `location ~ ^/purge(/.*)$`.
3. Цей location приймає запити тільки з `127.0.0.1` і `::1`.
4. Роль додає в `/etc/hosts` рядок `127.0.0.1 <domain> <aliases>  # wp-site <domain>`.
5. Плагін робить запит `https://<domain>/purge/<url>`. Запит іде на локальний nginx, а не на Cloudflare.

Proxmox змінює тільки свій блок у `/etc/hosts` контейнера. Наші рядки лишаються.
Обмеження: плагін може перевіряти TLS-сертифікат.
Тоді зі snakeoil-сертифікатом purge не працює. Потрібен сертифікат Let's Encrypt.
Перевірте purge після першого запуску.

## Valkey і Redis Object Cache

Плагін: Redis Object Cache (Till Krüss).
Плагін бере налаштування з `WP_REDIS_*` у `wp-config.php`.
Socket: `/run/valkey/valkey-server.sock`.
Кожен сайт має свій префікс ключів (`<user>:`).
`port 0`: TCP вимкнено. Socket `770`, група `valkey`.
Користувач `default` вимкнено. Адмін: `admin`, пароль у `/root/.valkey-admin.pass`.
Кожен сайт: ACL-користувач `<user>`, ключі тільки `<user>:*`, без `@admin` і `@dangerous`.
Пароль: `secrets/<host>/valkey/<domain>.pass`.
`<user>-php` і `<user>` входять у групу `valkey`.
Зміна ACL (новий сайт) перезапускає Valkey. Кеш усіх сайтів очищається.

## Let's Encrypt (certbot_cloudflare)

Пакети Debian: `certbot`, `python3-certbot-dns-cloudflare`.
Токен Cloudflare (Zone:DNS:Edit): `secrets/cloudflare.ini` → `/etc/letsencrypt/cloudflare.ini` (0600).
Немає файлу або `certbot_cloudflare_enabled: false` — роль пропускається, сайти лишаються на snakeoil.
Один сертифікат на сайт: `--cert-name <domain>`, імена = domain + aliases.
Роль запитує сертифікат, якщо його немає або змінився список імен.
Оновлення: `certbot.timer` з пакета. Deploy hook: `nginx -t` і `systemctl reload nginx`.
Vhost переходить на сертифікат LE під час того ж запуску (роль `site` іде після).

## Telegram (telegram_alert)

`/usr/local/sbin/tg-alert "текст"` або `... | tg-alert`.
Завжди пише в syslog (`journalctl -t tg-alert`).
Токен і chat id: `secrets/telegram.env` → `/etc/tg-alert.env` (0600). Без файлу — тільки syslog.

## Бекап (backup_restic)

Репозиторій restic: `/mnt/backups/restic`. `/mnt/backups` має бути точкою монтування
(інакше роль зупиняється; `backup_require_mount: false` — тільки для тестів).
Пароль репозиторію: `secrets/<host>/restic.pass`. Без нього бекап не прочитати. Зберігайте копію.
Таймер `wp-backup.timer` (03:15) запускає `/usr/local/sbin/wp-backup`:

1. `mariadb-dump --single-transaction` кожного сайту → `/srv/sites/<d>/backup/db.sql.gz` (перевірка gzip і «Dump completed»).
2. `restic backup` `/srv/sites`, `/etc/nginx`, `/etc/php`, `/etc/mysql`, `/etc/letsencrypt` (без `tmp` і `cache`).
3. `forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune`, потім `check`.
4. Помилка → `tg-alert`.

`wp-backup <domain>` — один сайт зараз (тег `site:<domain>`, без forget).
Відновлення: `set -a; . /etc/restic/env; set +a; restic snapshots; restic restore <id> --target /root/restore`.

## Моніторинг (wp_monitor)

Таймер `wp-monitor.timer` (04:30) запускає `/usr/local/sbin/wp-monitor`. Для кожного сайту:
`wp core verify-checksums`, `wp plugin verify-checksums --all` (тільки плагіни з wordpress.org),
`git status --porcelain` у `htdocs` (перший запуск робить baseline), PHP-файли в `uploads`/`cache`/`upgrade`,
нові адміністратори проти `/var/lib/wp-monitor/<domain>.admins`, HTTP 200 головної через 127.0.0.1.
Одне спільне повідомлення `tg-alert`, тільки коли є проблема.
Прийняти зміни: код — `wp-git-commit <domain>`, адміни — `wp-monitor --ack <domain>`.
Сайт без WordPress пропускається.

## Видалення і клон

```sh
scripts/site remove wp01 old.example.com        # питає домен ще раз
scripts/site clone wp01 example.com staging.example.com
```

`site-remove.yml` (`-e wp_site_only=<d> -e confirm=<d>`): `wp-backup <d>`, архів
`/mnt/backups/removed/<d>-<час>.tar.gz`, потім видаляє vhost, pool (зупиняє FPM без pools),
cron, logrotate, рядок у `/etc/hosts`, БД і користувача БД, ACL Valkey (`ACL LOAD`, без перезапуску),
сертифікат LE, користувачів, групу, каталог. `scripts/site` потім видаляє запис із `sites.yml`.
Паролі в `secrets/` лишаються.

`site-clone.yml` (`-e clone_from=A -e clone_to=B`): B має вже існувати (`scripts/site clone` додає і створює).
rsync `htdocs` (без `wp-config.php`, `.git`, `cache`), дамп БД A → БД B, `table_prefix`,
`wp search-replace //A //B` (і JSON-варіант), `site-fix-perms`, `wp cache flush`, git baseline.
Якщо в B вже є WordPress — потрібен `-e confirm=B`. Адміни копіюються: потім `wp-monitor --ack B`.

## Сайти і scripts/site

Сайти описані в `inventories/prod/host_vars/<host>/sites.yml` (`wp_sites`).
Значення за замовчуванням: `roles/site/defaults/main.yml` (`site_defaults`).
`scripts/site` працює тільки з локальними файлами.

```sh
scripts/site add wp01 example.com example 8.3 --dry-run
scripts/site info wp01
SITE_INVENTORY=inventories/test scripts/site info wptest
```

## Секрети

Каталог `secrets/` не входить у git.
Роль створює паролі в `secrets/<host>/...` при першому запуску.
Зберігайте копію `secrets/` у безпечному місці.

## Тестовий запуск

Ціль: тестовий CT `wptest` (Debian 13 LXC). Ніколи не prod.
`inventories/test`: сайти `test.local` (PHP 8.4) і `legacy.local` (PHP 7.4),
certbot вимкнено, `backup_require_mount: false`, nginx приймає будь-якого клієнта,
свої секрети в `secrets/test/`.

1. Впишіть адресу в `inventories/test/hosts.yml` (`ansible_host`, за потреби ProxyJump через ax102).
2. Два запуски. Другий має дати `changed=0` (або поясніть кожну зміну):
   ```sh
   .venv/bin/ansible-playbook -i inventories/test/hosts.yml playbooks/site.yml | tee /tmp/run1.log
   .venv/bin/ansible-playbook -i inventories/test/hosts.yml playbooks/site.yml | tee /tmp/run2.log
   ```
3. Перевірки на CT (ставить WordPress у тестові сайти):
   ```sh
   scp tests/verify.sh root@<wptest>:/root/ && ssh root@<wptest> bash /root/verify.sh
   ```
   Скрипт перевіряє: `nginx -t`; обидва FPM; MariaDB, Valkey, таймери; HTTP 200 обох сайтів;
   PHP в `uploads` не виконується; `limit_req` для `wp-login.php`; Valkey з `<user>-php`;
   `wp-monitor` чистий, після `stray.php` — помилка і запис `tg-alert` у journal;
   `wp-backup <d>`, повний `wp-backup`, `restic snapshots`.
4. Клон і видалення (з ноутбука):
   ```sh
   SITE_INVENTORY=inventories/test scripts/site clone wptest test.local clone.local
   ssh root@<wptest> 'curl -sk --resolve clone.local:443:127.0.0.1 https://clone.local/ | grep -c clone.local'
   SITE_INVENTORY=inventories/test scripts/site remove wptest clone.local
   ssh root@<wptest> 'ls /mnt/backups/removed; id clone_local; ls /srv/sites'
   ```
5. Вручну: `ansible-playbook ... -t site,site_fix_perms -e wp_site_only=test.local`; `systemctl list-timers`.
6. Не покрито тестом: справжній LE/Cloudflare, відправка в Telegram, точка монтування бекапу.

Необов'язково: `scripts/test-local.sh up|run|verify|down` — те саме в локальному docker-контейнері.

## Відкриті питання

- Дзеркало бекапу на Storage Box (restic copy / rclone).
- `WP_REDIS_SELECTIVE_FLUSH` у документації плагіна позначено «unsupported».
- Valkey перезапускається при зміні ACL (новий сайт) — кеш усіх сайтів очищається.
- Purge Nginx Helper зі snakeoil: перевірити після LE.
- CF-Connecting-IP від проксі хоста, `ansible.utils`/netaddr, винятки для коду з правом запису.
