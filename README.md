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
| `playbooks/doctor.yml` | Тільки preflight-перевірки, без змін. |
| `playbooks/verify.yml` | Перевірка всіх хостів і сайтів (PASS/FAIL), без змін. |
| `playbooks/site-backup.yml` | Бекап зараз: один сайт (`-e wp_site_only=<domain>`) або все. |
| `playbooks/site-remove.yml` | Видалення сайту (спершу бекап і архів). |
| `playbooks/site-clone.yml` | Клон сайту A у сайт B. |
| `playbooks/site-repair-core.yml` | Звіт і ремонт ядра WordPress одного сайту після алерту цілісності. |

Один сайт:

```sh
ansible-playbook playbooks/site.yml --limit wp01 -e wp_site_only=jenet.info
```

Порядок ролей у `site.yml`:
preflight, common, telegram_alert, nginx, php, mariadb, valkey, certbot_cloudflare, site, backup_restic, wp_monitor.
Кожна роль має тег з тією ж назвою.

## Preflight і doctor

Роль `preflight` іде першою в `site.yml` (тег `always`) і нічого не змінює.
Усі помилки збираються в один список, і play зупиняється до будь-якої зміни.
`playbooks/doctor.yml` запускає ті самі перевірки окремо.

- `wp_sites`: формат домену і aliases, невідомі ключі (опечатки), `php_version` вигляду `8.3`,
  `user`/`db_name` (`[a-z][a-z0-9_]*`, user до 28 символів), булеві прапорці,
  `table_prefix`; дублікати користувачів, БД, доменів і aliases між сайтами.
- Розбіжність з хостом: `user` або `db_name` у `sites.yml` не збігається з `conf/site.env`
  існуючого сайту (перейменування створило б другий набір).
- Хост: Debian 13; не встановлено `apache2` (`preflight_forbidden_packages`); порти 80/443
  не зайняті іншою програмою, ніж nginx; вільне місце (`preflight_min_free_mb`: `/` 2 ГіБ,
  `/srv/sites` 2 ГіБ, `/var` 1 ГіБ); RAM < 1 ГіБ — тільки попередження.

```sh
ansible-playbook playbooks/doctor.yml --limit wp01
```

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
| `preflight` | Перевірки без змін: `wp_sites`, apache2, порти, місце, ОС. |
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

Новий сайт (ще немає `conf/site.env`): якщо в `htdocs` є файли, але немає WordPress
(`wp-includes/version.php`), роль зупиняється до створення користувачів. Дозволено: порожній
або відсутній `htdocs`, тільки `wp-config.php`/`.git`/порожні каталоги, або повне дерево WordPress.
Свідомо лишити чужі файли: `-e site_docroot_force=true`.

Свіжий WordPress (рідко: зазвичай сайти переносяться) — тільки за запитом, за замовчуванням вимкнено:
`scripts/site add <host> <domain> ... --install-wp` (або `-e site_wp_install=true -e wp_site_only=<d>`),
чи ключ сайту `wp_install: true`. Працює, лише поки немає `wp-includes/version.php`:
`wp core download --skip-content` (md5 перевіряє wp-cli), `verify-checksums`, `wp core install`
(якщо БД ще порожня), тема за замовчуванням, `site-fix-perms`, git baseline.
Адмін: `wp_admin_user` (типово `wpadmin`), `wp_admin_email` (типово `admin@<domain>`), пароль —
`secrets/<host>/wp-admin/<domain>.pass` (передається через stdin, не видно в `ps`).
`--skip-content`, бо повний пакет wp-cli 2.12 розпаковує з tar.gz і обрізає шляхи до 100 символів
(у WP 7.1 бракує 25 файлів `wp-includes/php-ai-client`).

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
runuser -u <user> -- env HOME=/tmp php<версія> /usr/local/bin/wp --path=/srv/sites/<domain>/htdocs plugin update --all
wp-git-commit <domain> "plugin update"
```

Використовуйте ту версію PHP, що й pool сайту. `HOME=/tmp`: домашній каталог власника — корінь сайту, він не має права запису.
`DISALLOW_FILE_MODS` блокує тільки адмінку: wp-cli від власника ставить і оновлює плагіни, теми і ядро
(перевірено: `plugin install/update`, `theme install`, `core update --version=6.8 --force` і назад на 7.1.4, `core verify-checksums`).

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
Обмеження (перевірено на тестовій VM): плагін перевіряє TLS-сертифікат.
Зі snakeoil purge не працює (`cURL error 60` у `uploads/nginx-helper/nginx.log`), кеш живе до 30 хв.
Потрібен сертифікат Let's Encrypt. Ручний `curl -k https://<d>/purge/` з хоста працює, ззовні — 403.

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

Увімкнення object cache: `wp redis enable` (і кнопка Enable в адмінці) не працює —
плагін (3.0.0) спершу робить `FLUSHDB`, а ACL сайту це забороняє (`@dangerous`).
Скопіюйте drop-in як власник сайту:
```sh
runuser -u <user> -- cp <htdocs>/wp-content/plugins/redis-cache/includes/object-cache.php <htdocs>/wp-content/
runuser -u <user> -- env HOME=/tmp php<ver> /usr/local/bin/wp --path=<htdocs> redis status   # Connected
```
Далі `wp cache flush` працює вибірково (тільки `<user>:*`). Перевірено: чужі ключі,
`FLUSHDB`, `FLUSHALL`, `KEYS`, `CONFIG` — NOPERM.

## Let's Encrypt (certbot_cloudflare)

Пакети Debian: `certbot`, `python3-certbot-dns-cloudflare`.
Токен Cloudflare (Zone:DNS:Edit): `secrets/cloudflare.ini` → `/etc/letsencrypt/cloudflare.ini` (0600).
Немає файлу або `certbot_cloudflare_enabled: false` — роль пропускається, сайти лишаються на snakeoil.
Один сертифікат на сайт: `--cert-name <domain>`, імена = domain + aliases.
Роль запитує сертифікат, якщо його немає або змінився список імен.
Оновлення: `certbot.timer` з пакета. Deploy hook: `nginx -t` і `systemctl reload nginx`.
Vhost переходить на сертифікат LE під час того ж запуску (роль `site` іде після).

Помилка запиту не зупиняє play. Сайт лишається на поточному сертифікаті (новий сайт — snakeoil).
Роль показує WARNING з останніми рядками certbot і DNS-перевіркою кожного імені через
`dig @1.1.1.1`: зона, чи NS у Cloudflare, A-записи. Потім надсилає `tg-alert`.
Невдалий запит не рахується як `changed`. Наступний запуск пробує знову (ліміт LE: 5 невдалих
перевірок на ім'я за годину). Після виправлення DNS або токена:
`ansible-playbook playbooks/site.yml -t certbot_cloudflare,site -e wp_site_only=<d>`.

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

Уся логіка перевірок — в одному скрипті `/usr/local/sbin/wp-monitor` на сервері.
Таймер `wp-monitor.timer` (04:30) запускає його вночі: одне спільне повідомлення `tg-alert`,
тільки коли є проблема. Вивід — рядки `PASS|FAIL|SKIP <сайт> <перевірка>[: деталі]`, код 1 при FAIL.

| Перевірка | Що робить |
|---|---|
| `host_services`, `disk_space` | nginx, mariadb, valkey-server активні, `nginx -t`; вільно ≥ 10% на `/`, `/srv/sites`, `/var` |
| `db_connect` | WordPress дістає свою БД (`SELECT 1` через `$wpdb`) |
| `http_home` | головна через 127.0.0.1 → 200 |
| `fpm_pool` | php-fpm активний, сокет пулу є, запит повз кеш → 200 |
| `cert_expiry` | LE-сертифікат віддає nginx і він дійсний > 14 днів; без LE (snakeoil) — SKIP |
| `valkey` | для `redis: true`: SET/GET/DEL з ACL-користувачем сайту |
| `core_checksums` | `wp core verify-checksums --include-root`: змінені/відсутні файли ядра, зайві файли в `wp-admin/`, `wp-includes/`, зайві PHP у корені |
| `plugin_checksums` | `wp plugin verify-checksums --all` (тільки плагіни з wordpress.org) |
| `stray_php` | PHP прямо в `wp-content` (крім drop-in: `object-cache.php` тощо), приховані `.*.php` і `.user.ini` будь-де |
| `php_in_writable` | `*.php`/`.phtml`/`.phar` в `uploads`, `cache`, `upgrade` (крім `index.php` «Silence is golden») |
| `git_status` | незакомічені/нові файли в `htdocs` |
| `admin_users` | список адміністраторів (id + login) проти `/var/lib/wp-monitor/<domain>.admins`: «added evil (id 7) removed admin (id 1)». Після алерту baseline оновлюється (наступної ночі тиша) |
| `content_spike` | більше `monitor_content_max` (ключ сайту, типово 10, `0` = вимк.) нових записів будь-якого типу за 24 год. Не рахуються: revision, nav_menu_item, auto-draft, customize_changeset, oembed_cache, user_request, замовлення WooCommerce (`wp_monitor_content_exclude_types`), медіа (`wp_monitor_content_attachments: true` — рахувати) |

```sh
wp-monitor --check [domain]    # лише читання: без алерту, без запису baseline
wp-monitor --ack <domain>      # прийняти поточний список адмінів без алерту
wp-monitor --files <domain>    # перевірки цілісності + рядки FILE <шлях> (для site-repair-core)
wp-git-commit <domain> "опис"  # прийняти зміни коду (git baseline)
```

Пороги й перелік перевірок: `roles/wp_monitor/defaults/main.yml` → `/etc/wp-monitor.conf`.
Сайт без WordPress пропускається (SKIP).

### Перевірка на вимогу (verify.yml)

```sh
ansible-playbook playbooks/verify.yml [--limit wp01] [-e wp_site_only=example.com]
```

Тонкий playbook без власної логіки перевірок сайтів: запускає `wp-monitor --check` на кожному хості
(ті самі перевірки, що й уночі; без алерту і без запису baseline). Додає лише те, що знає тільки
Ansible — сервер проти інвентаря: права DB-користувача (тільки GRANT ALL на свою БД + USAGE), пул FPM
`/etc/php/<ver>/fpm/pool.d/<user>.conf`, vhost у `sites-enabled`, а також зайве на сервері, чого
немає в `wp_sites` (пули, vhost, каталоги `/srv/sites/*`, DB-користувачі). Будь-який FAIL — хост
падає з переліком.

### Ремонт ядра (site-repair-core.yml)

Нічого не видаляє і не комітить. Без `confirm` — тільки звіт (`wp-monitor --files`).

```sh
ansible-playbook playbooks/site-repair-core.yml --limit wp01 -e repair_site=example.com
# та сама версія ядра поверх (wp core download --force --skip-content; wp-content не чіпає), site-fix-perms:
ansible-playbook playbooks/site-repair-core.yml --limit wp01 -e repair_site=example.com -e confirm=example.com
# + перенести зайві/підозрілі PHP-файли в /srv/sites/<d>/quarantine/<час UTC>/ (root, 0700):
... -e confirm=example.com -e quarantine=true
```

Потім: `git status` у `htdocs` (від власника), `wp-git-commit <d> "core repair"`.
Плагіни з поганими checksums — перевстановити (`wp plugin install <slug> --force`).

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
scripts/site add wp01 new.example.com - 8.3 --install-wp   # зі свіжим WordPress
scripts/site info wp01
SITE_INVENTORY=inventories/test scripts/site info wptest
```

## Секрети

Каталог `secrets/` не входить у git.
Роль створює паролі в `secrets/<host>/...` при першому запуску.
Зберігайте копію `secrets/` у безпечному місці.

## Тестовий запуск

Ціль: тестова VM `wptest` (ans01-presta, Debian 13, KVM за NAT, тільки IPv4). Ніколи не prod.
`inventories/test`: сайти `test1.example` (PHP 8.4, alias `www.`) і `test2.example` (PHP 8.3),
certbot вимкнено (snakeoil), `backup_require_mount: false`, без Telegram (алерти тільки в syslog),
nginx приймає будь-якого клієнта, свої секрети в `secrets/test/`.
Root-входу немає: `ansible_user: vitovt`, `ansible_become: true` (sudo без пароля).
Prod лишається на root по ключу.

1. Адреса і порт уже в `inventories/test/hosts.yml`. Змінні середовища:
   ```sh
   export PATH=$PWD/.venv/bin:$PATH SITE_INVENTORY=inventories/test
   ```
2. Два запуски. Другий має дати `changed=0`:
   ```sh
   ansible-playbook -i inventories/test/hosts.yml playbooks/site.yml | tee /tmp/run1.log
   ansible-playbook -i inventories/test/hosts.yml playbooks/site.yml | tee /tmp/run2.log
   ```
3. Перевірки на VM (ставить WordPress у тестові сайти, `[site]` за замовчуванням `test1.example`):
   ```sh
   scp -P 22241 tests/verify.sh vitovt@<vm>: && ssh -p 22241 vitovt@<vm> sudo bash verify.sh
   ```
   Скрипт перевіряє: `nginx -t`; FPM, MariaDB, Valkey, таймери; для кожного сайту HTTP 200,
   редирект http→https, PHP в `uploads` не виконується; `limit_req` для `wp-login.php`;
   Valkey з `<user>-php`; `wp-monitor` чистий, після `stray.php` — помилка і запис `tg-alert`
   у syslog; `wp-backup <d>`, повний `wp-backup`, `restic snapshots`.
   Зовнішній 443 на VM не проброшено: усі HTTP-перевірки йдуть на самій VM через
   `curl -k --resolve <d>:443:127.0.0.1`.
4. Додавання, клон і видалення (з ноутбука):
   ```sh
   scripts/site add wptest test3.example - 8.4
   scripts/site clone wptest test1.example test4.example
   ssh ... "sudo curl -sk --resolve test4.example:443:127.0.0.1 'https://test4.example/?x=1' | grep -c test4.example"
   scripts/site remove wptest test4.example      # питає домен; архів у /mnt/backups/removed
   ```
   Після `wp-cli`-змін (плагіни, ядро) `wp-monitor` скаржиться на git status, поки не
   виконати `wp-git-commit <d>` — так і задумано.
5. Відновлення з restic (на VM, root): `set -a; . /etc/restic/env; set +a`,
   `restic restore latest --tag site:<d> --target /root/r`. Дамп має `CREATE DATABASE`/`USE`
   (`--databases`): у чужу БД — тільки після `sed -E '/^(CREATE DATABASE|USE )/d'`,
   інакше він перезапише робочу БД сайту.
6. Вручну: `ansible-playbook ... -t site,site_fix_perms -e wp_site_only=test1.example`; `systemctl list-timers`.
7. Не покрито тестом: справжній LE/Cloudflare, відправка в Telegram, точка монтування бекапу,
   IPv6, особливості LXC.
   Застереження VM: cloud-init (`manage_etc_hosts`) переписує `/etc/hosts` при перезавантаженні —
   рядки сайтів (для purge) зникнуть до наступного запуску `site.yml`.

Необов'язково: `scripts/test-local.sh up|run|verify|down` — те саме в локальному docker-контейнері.

## Відкриті питання

- Дзеркало бекапу на Storage Box (restic copy / rclone).
- `WP_REDIS_SELECTIVE_FLUSH` у документації плагіна позначено «unsupported».
- Valkey перезапускається при зміні ACL (новий сайт) — кеш усіх сайтів очищається.
- Purge Nginx Helper: перевірити з LE на prod (зі snakeoil не працює, див. вище).
- CF-Connecting-IP від проксі хоста, `ansible.utils`/netaddr, винятки для коду з правом запису.
