# Развёртывание на Debian 12: Supabase или PostgreSQL 17

Команды выполнять от `root` на чистом сервере. Требуются DNS A-запись домена, открытые 80/443 и исходящий HTTPS к PyPI, npm и Open-Meteo. Backend слушает только `127.0.0.1:8000`; nginx публикует его под `/api/`. Секреты не помещаются в Git или клиентскую сборку. Варианты БД отличаются только `DATABASE_URL`.

## 1. Пакеты и код

```bash
apt update
apt install -y git curl ca-certificates python3 python3-venv python3-pip nginx certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource_setup.sh
bash /tmp/nodesource_setup.sh
apt install -y nodejs
python3 --version; node --version; npm --version
install -d /opt/healthcore
git clone --branch dev https://github.com/BAITC-Hacks/hack-ed58da2d-health-core.git /opt/healthcore/src
id healthcore || useradd --system --home /opt/healthcore/api --shell /usr/sbin/nologin healthcore
install -d -o healthcore -g healthcore /opt/healthcore/api /opt/healthcore/api/data /opt/healthcore/api/models /opt/healthcore/api/TZ
install -d /opt/healthcore/ui
cp -a /opt/healthcore/src/backend /opt/healthcore/api/
cp /opt/healthcore/src/requirements.txt /opt/healthcore/api/
chown -R healthcore:healthcore /opt/healthcore/api
python3 -m venv /opt/healthcore/api/.venv
/opt/healthcore/api/.venv/bin/python -m pip install --upgrade pip
/opt/healthcore/api/.venv/bin/python -m pip install -r /opt/healthcore/api/requirements.txt
```

Репозиторий закрыт: получите доступ от организатора или перенесите архив **того же коммита** из выданного репозитория в `/opt/healthcore/src`. Конкурсные CSV и документы входят в закрытый Git-репозиторий. Для автоматического обучения скопируйте `TZ/` в `/opt/healthcore/api/TZ` до первого запуска; для проверки установки без них доступен генератор синтетических CSV в шаге 3.

## 2. Выбор базы данных

### A. Supabase PostgreSQL

Создайте проект в Supabase. В **Connect → Connection string** выберите Direct connection при наличии IPv6 или **Session pooler, порт 5432** для IPv4. URL вида `postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/postgres?sslmode=require` поместите только в `/opt/healthcore/api/.env`. Пароль URL-кодируется. API key Supabase не нужен. Команда `init-db` создаёт таблицы внутри **существующей** БД; облачный проект она не создаёт. Таблицы `public` получают RLS без политик `anon`/`authenticated`: браузер работает только через backend.

Session Pooler имеет ограничение числа постоянных соединений. По умолчанию API использует пул из двух соединений без дополнительных (`DB_POOL_SIZE=2`, `DB_MAX_OVERFLOW=0`). Учитывайте также локальный API, CLI и другие сервисы, подключённые к тому же проекту.

### B. Собственный PostgreSQL 17

Официальный [PostgreSQL Apt Repository](https://www.postgresql.org/download/linux/debian/) поддерживает Debian 12. Установите пакет 17 и создайте отдельную роль:

```bash
apt install -y postgresql-common
install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc
printf 'Types: deb\nURIs: https://apt.postgresql.org/pub/repos/apt\nSuites: bookworm-pgdg\nComponents: main\nSigned-By: /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc\n' > /etc/apt/sources.list.d/pgdg.sources
apt update
apt install -y postgresql-17
systemctl enable --now postgresql
runuser -u postgres -- createuser --pwprompt healthcore
runuser -u postgres -- createdb --owner=healthcore healthcore
```

`createuser` попросит придумать пароль; не берите его из примеров. Пример URL: `postgresql+psycopg://healthcore:URL_ENCODED_PASSWORD@127.0.0.1:5432/healthcore`. Не открывайте порт 5432 в интернет. Для выделенной инсталляции проверьте `listen_addresses = 'localhost'` в `postgresql.conf` и парольное локальное правило в `pg_hba.conf`; после изменения перезапустите PostgreSQL. При подключении через loopback `sslmode=require` не нужен.

### Общая конфигурация

```bash
cp /opt/healthcore/src/deploy/server.env.example /opt/healthcore/api/.env
chmod 640 /opt/healthcore/api/.env
chown healthcore:healthcore /opt/healthcore/api/.env
```

Отредактируйте `.env`: задайте `DATABASE_URL` выбранного варианта. Ключ оператора не используется: все операции записи открыты и на публичном сайте. Если заданы и `SUPABASE_DATABASE_URL`, и `DATABASE_URL`, первая имеет приоритет; при выборе собственного PostgreSQL удалите `SUPABASE_DATABASE_URL`. `MODEL_DIR=/opt/healthcore/api/models`, `DB_INIT_ON_STARTUP=false` после явного `init-db`, `AUTO_BOOTSTRAP=true`, `TZ_DIR=/opt/healthcore/api/TZ`.

## 3. Схема, данные и модель

```bash
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli init-db'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m unittest discover -s backend/tests -v'
```

`init-db` повторяемо создаёт шесть таблиц, индексы, ограничения и две турбины; **не** меняет столбцы уже существующих таблиц.

Для конкурсного первого запуска скопируйте оба исходных CSV из клонированной `TZ/` в `/opt/healthcore/api/TZ` **до** запуска сервиса:

```bash
cp /opt/healthcore/src/TZ/*.csv /opt/healthcore/api/TZ/
chown healthcore:healthcore /opt/healthcore/api/TZ/*.csv
chmod 640 /opt/healthcore/api/TZ/*.csv
```

Имена должны заканчиваться на `turbine 1.csv` и `turbine 2.csv`. Сервис при первом старте сам проверит оба файла, импортирует исходные строки в PostgreSQL и обучит модель с отсечением 31.01.2026. Повторный старт при уже сохранённой ревизии не повторяет импорт и обучение. Если CSV отсутствуют или некорректны, API продолжит работать, но `/readiness` и интерфейс покажут причину; после исправления файлов перезапустите сервис.

Для автономного smoke test с явно синтетическими данными:

```bash
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.generate_demo_csv data/demo'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli import-csv 1 data/demo/demo-turbine-1.csv'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli import-csv 2 data/demo/demo-turbine-2.csv'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli train'
```

Для конкурсных результатов ручной импорт и обучение не нужны: при наличии файлов в `TZ_DIR` их выполнит сервис. Если Supabase уже содержит измерения и модель, автоматический bootstrap пропустит импорт и обучение. Метрики корневого README относятся **только** к конкурсным данным; синтетика — проверка запуска.

## 4. API, frontend и HTTPS

```bash
cp /opt/healthcore/src/deploy/healthcore-api.service /etc/systemd/system/healthcore-api.service
systemctl daemon-reload
systemctl enable --now healthcore-api
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/readiness
cd /opt/healthcore/src/frontend
npm ci
VITE_API_URL=/api npm run build
cp -a dist/. /opt/healthcore/ui/
cp /opt/healthcore/src/deploy/nginx-healthcore.conf /etc/nginx/sites-available/healthcore.ych.kz
ln -s /etc/nginx/sites-available/healthcore.ych.kz /etc/nginx/sites-enabled/healthcore.ych.kz
nginx -t && systemctl reload nginx
certbot --nginx -d healthcore.ych.kz
```

Если symlink уже существует, не создавайте его повторно. Перед заменой действующего сайта сохраните конфигурацию и файлы. Шаблон nginx содержит HTTP-блок; Certbot добавляет HTTPS и сертификат. Порт 8000 наружу не открывайте. Для другого домена замените `server_name` и аргумент Certbot.

Проверка: `curl -fsS https://healthcore.ych.kz/api/health` возвращает `"status":"ok"` и `"database_backend":"postgresql"`; `/api/readiness` после фоновой подготовки возвращает `"status":"ready"` и `"can_forecast":true`; `curl -I https://healthcore.ych.kz/` — `200`. Пока идут импорт и обучение, `/readiness` возвращает `importing`/`training`; сайт показывает тот же статус и блокирует новый прогноз. В браузере выберите турбину/ревизию и создайте прогноз; загрузка CSV, создание турбины и переобучение доступны без ключа. Без ключа Google Maps встроенная карта не загружается, но координаты и ссылки доступны в списке.

## 5. Обновление

Сначала сохраните резервные копии конфигурации, кода и БД. Затем `git -C /opt/healthcore/src pull --ff-only`, повторите копирование `backend/`, установку `requirements.txt`, `init-db`, `npm ci`, сборку UI и `systemctl restart healthcore-api`. Проверьте `journalctl -u healthcore-api -n 100 --no-pager` и health endpoint. Если изменилась структура таблиц, одного `init-db` недостаточно — нужна миграция до переключения сервиса. Не удаляйте старую БД или данные при обновлении.
