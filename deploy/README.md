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
install -d -o healthcore -g healthcore /opt/healthcore/api /opt/healthcore/api/data /opt/healthcore/api/models
install -d /opt/healthcore/ui
cp -a /opt/healthcore/src/backend /opt/healthcore/api/
cp /opt/healthcore/src/requirements.txt /opt/healthcore/api/
chown -R healthcore:healthcore /opt/healthcore/api
python3 -m venv /opt/healthcore/api/.venv
/opt/healthcore/api/.venv/bin/python -m pip install --upgrade pip
/opt/healthcore/api/.venv/bin/python -m pip install -r /opt/healthcore/api/requirements.txt
```

Если репозиторий закрыт, получите доступ от организатора или перенесите архив **того же коммита** из выданного репозитория в `/opt/healthcore/src`. Конкурсные CSV в Git не входят. Для проверки установки без них доступен генератор синтетических CSV в шаге 3.

## 2. Выбор базы данных

### A. Supabase PostgreSQL

Создайте проект в Supabase. В **Connect → Connection string** выберите Direct connection при наличии IPv6 или **Session pooler, порт 5432** для IPv4. URL вида `postgresql+psycopg://USER:URL_ENCODED_PASSWORD@HOST:5432/postgres?sslmode=require` поместите только в `/opt/healthcore/api/.env`. Пароль URL-кодируется. API key Supabase не нужен. Команда `init-db` создаёт таблицы внутри **существующей** БД; облачный проект она не создаёт. Таблицы `public` получают RLS без политик `anon`/`authenticated`: браузер работает только через backend.

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
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Отредактируйте `.env`: задайте `DATABASE_URL` выбранного варианта и вставьте случайный результат последней команды в `API_WRITE_KEY`. Ключ нужен для `POST` (турбины, CSV, обучение, расчёт); вводится в верхней панели сайта и хранится только в текущей вкладке. **Не** помещайте его в `VITE_*`, Git или публичный URL. Если заданы и `SUPABASE_DATABASE_URL`, и `DATABASE_URL`, первая имеет приоритет; при выборе собственного PostgreSQL удалите `SUPABASE_DATABASE_URL`. `MODEL_DIR=/opt/healthcore/api/models`, `DB_INIT_ON_STARTUP=false` после явного `init-db`. Для локального теста `API_WRITE_KEY` можно оставить пустым, но не для публичного сайта.

## 3. Схема, данные и модель

```bash
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli init-db'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m unittest discover -s backend/tests -v'
```

`init-db` повторяемо создаёт шесть таблиц, индексы, ограничения и две турбины; **не** меняет столбцы уже существующих таблиц. Для автономного smoke test с явно синтетическими данными:

```bash
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.generate_demo_csv data/demo'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli import-csv 1 data/demo/demo-turbine-1.csv'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli import-csv 2 data/demo/demo-turbine-2.csv'
runuser -u healthcore -- sh -c 'cd /opt/healthcore/api && .venv/bin/python -m backend.cli train'
```

Для конкурсных результатов вместо этого импортируйте два предоставленных CSV для ID 1 и 2 и обучите ревизию с нужным отсечением. Если Supabase уже содержит измерения и модели, импорт/обучение не повторяйте. Метрики корневого README относятся **только** к конкурсным данным; синтетика — проверка запуска.

## 4. API, frontend и HTTPS

```bash
cp /opt/healthcore/src/deploy/healthcore-api.service /etc/systemd/system/healthcore-api.service
systemctl daemon-reload
systemctl enable --now healthcore-api
curl -fsS http://127.0.0.1:8000/health
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

Проверка: `curl -fsS https://healthcore.ych.kz/api/health` возвращает `"status":"ok"` и `"database_backend":"postgresql"`; `curl -I https://healthcore.ych.kz/` — `200`. В браузере выберите турбину/ревизию и сохранённый прогноз; для новых операций введите ключ оператора. `POST /api/turbines` без ключа возвращает `401`. Без ключа Google Maps встроенная карта не загружается, но координаты и ссылки доступны в списке.

## 5. Обновление

Сначала сохраните резервные копии конфигурации, кода и БД. Затем `git -C /opt/healthcore/src pull --ff-only`, повторите копирование `backend/`, установку `requirements.txt`, `init-db`, `npm ci`, сборку UI и `systemctl restart healthcore-api`. Проверьте `journalctl -u healthcore-api -n 100 --no-pager` и health endpoint. Если изменилась структура таблиц, одного `init-db` недостаточно — нужна миграция до переключения сервиса. Не удаляйте старую БД или данные при обновлении.
