# PS4 ULTIMATE STORE — Backend 1.0.0

Flask + SQLite API для настоящих аккаунтов и админ-управления.

## Что уже есть
- Регистрация и вход с хешированием паролей.
- Сессии в HttpOnly cookie.
- CSRF-защита для изменяющих запросов.
- Публичный каталог из 41 записи (загружается в SQLite при первом старте).
- Админ API: список пользователей, бан/разбан, удаление обычных аккаунтов.
- Админ API: добавление, редактирование и удаление записей каталога.
- Администратор создаётся при первом запуске через переменные окружения.
- Пользовательские пароли и секреты не хранятся в HTML.

## Запуск локально (Windows / Linux / Termux)

1. Установи Python 3.10+.
2. Открой папку проекта.
3. Создай виртуальное окружение и установи зависимости:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / Termux:
source .venv/bin/activate
pip install -r requirements.txt
```

4. Скопируй `.env.example` в `.env`, затем задай значения:
   - `SECRET_KEY` — случайная строка минимум 32 символа.
   - `ADMIN_USERNAME` — ник первого администратора.
   - `ADMIN_PASSWORD` — уникальный пароль минимум 12 символов.
   - Не отправляй эти значения в чат и не коммить `.env`.

5. Экспортируй переменные окружения. Самый простой способ — задать их в терминале перед запуском:

```bash
# Linux / Termux (подставь собственные значения локально):
export SECRET_KEY='твоя-длинная-случайная-строка'
export ADMIN_USERNAME='твой_админ_ник'
export ADMIN_PASSWORD='твой_уникальный_пароль'
python app.py
```

Для Windows PowerShell:

```powershell
$env:SECRET_KEY="твоя-длинная-случайная-строка"
$env:ADMIN_USERNAME="твой_админ_ник"
$env:ADMIN_PASSWORD="твой_уникальный_пароль"
python app.py
```

Открой `http://127.0.0.1:5000`.

## API

- `GET /api/health`
- `GET /api/csrf`
- `POST /api/register` — JSON `{ "username": "...", "password": "...", "avatar": "🎮" }`
- `POST /api/login` — JSON `{ "username": "...", "password": "..." }`
- `POST /api/logout`
- `GET /api/me`
- `GET /api/catalog`
- `GET /api/admin/users`
- `PATCH /api/admin/users/<id>` — `{ "is_banned": true }`
- `DELETE /api/admin/users/<id>`
- `POST /api/admin/catalog`
- `PATCH /api/admin/catalog/<id>`
- `DELETE /api/admin/catalog/<id>`

Для POST/PATCH/DELETE сначала запроси `GET /api/csrf`, затем передавай полученный токен в заголовке `X-CSRF-Token`.

## Важно перед публичным запуском

- Сейчас `index.html` — исходный интерфейс 0.9.2; он пока использует локальный профиль.
  Backend API готов, но следующий этап — подключить формы аккаунта и экран админки к API.
- GitHub Pages не запускает Flask. Для backend нужен отдельный Python-хостинг с постоянным диском/БД.
- При HTTPS выставь `COOKIE_SECURE=1`.
- Для нескольких серверных экземпляров вместо локального SQLite понадобится общая PostgreSQL.
- Удаление записи каталога убирает её из базы, но не удаляет внешний файл по ссылке.
- Не загружай `.env`, `data/store.db` и реальные пароли в репозиторий.
