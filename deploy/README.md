# Развёртывание API на Debian 12

Выполнять на собственном сервере. Каталог `/opt/healthcore/ui` не изменяется.

1. Перенести `backend/`, `requirements.txt` в `/opt/healthcore/api/`, а исходные два CSV в `/opt/healthcore/api/data/turbine-1.csv` и `turbine-2.csv`.
2. Создать окружение: `python3 -m venv /opt/healthcore/api/.venv` и `/opt/healthcore/api/.venv/bin/python -m pip install -r /opt/healthcore/api/requirements.txt`.
3. Скопировать `deploy/server.env.example` в `/opt/healthcore/api/.env`. Если используется Supabase, заменить `DATABASE_URL` на строку `postgresql+psycopg://...` с `sslmode=require`. Никогда не публиковать этот файл. Проект Supabase и БД `postgres` должны существовать заранее.
4. Из каталога `/opt/healthcore/api` выполнить `.venv/bin/python -m backend.cli init-db`, затем `.venv/bin/python -m unittest discover -s backend/tests -v`, `import-csv 1 data/turbine-1.csv`, `import-csv 2 data/turbine-2.csv`, `train`. Команда `init-db` создаёт таблицы и индексы, включает RLS в PostgreSQL и не удаляет данные. При старте API она выполняется также автоматически.
5. Создать отдельного пользователя без входа: `useradd -r -d /opt/healthcore/api -s /usr/sbin/nologin healthcore`, если его ещё нет. Выдать ему права на каталог `data/`, на чтение кода и `.env`.
6. Установить `deploy/healthcore-api.service` в `/etc/systemd/system/`, затем `systemctl daemon-reload`, `systemctl enable --now healthcore-api`.
7. Проверить на сервере `curl http://127.0.0.1:8000/health`: нужен ответ `{"status":"ok","database":"ready"}`. Для локального доступа использовать SSH-туннель `ssh -L 8000:127.0.0.1:8000 root@77.37.65.54` и открыть `http://127.0.0.1:8000/docs`.

Сервис не публикуется в Интернет напрямую: операции импорта и расчёта пока не имеют аутентификации. Перед изменением `.env` остановить сервис; после изменения запустить вновь. Не копировать на сервер подготовительные документы и не добавлять секреты в Git.
