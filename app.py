import os
import re
import json
import hmac
import secrets
import sqlite3
import urllib.request
import urllib.parse

from pathlib import Path
from functools import wraps
from flask import Flask, request, jsonify, session, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash


# ==========================================
# PS4 ULTIMATE STORE 0.9.8
# BACKEND + ARTICSUPPORT
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(BASE_DIR / "data" / "store.db")
    )
)

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)

secret = os.getenv("SECRET_KEY", "")

if len(secret) < 32:
    raise RuntimeError(
        "SECRET_KEY must contain at least 32 characters"
    )

app.config.update(
    SECRET_KEY=secret,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "1") == "1",
    SESSION_COOKIE_SAMESITE="None",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 7,
)

allowed_origins = [
    x.strip()
    for x in os.getenv(
        "FRONTEND_ORIGIN",
        "https://backlicks.github.io"
    ).split(",")
    if x.strip()
]

CORS(
    app,
    origins=allowed_origins,
    supports_credentials=True,
    allow_headers=[
        "Content-Type",
        "X-CSRF-Token",
        "X-Support-Key"
    ],
    methods=[
        "GET",
        "POST",
        "PATCH",
        "DELETE",
        "OPTIONS"
    ]
)


# ==========================================
# DATABASE
# ==========================================

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)

    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA foreign_keys = ON")

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        avatar TEXT NOT NULL DEFAULT '🎮',
        role TEXT NOT NULL DEFAULT 'user'
            CHECK(role IN ('user','admin','owner')),
        is_banned INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT 'Другое',
        description TEXT NOT NULL DEFAULT '',
        version TEXT NOT NULL DEFAULT '',
        compatibility TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        icon TEXT NOT NULL DEFAULT '📦',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open'
            CHECK(status IN ('open','closed')),
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
            ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        sender_type TEXT NOT NULL
            CHECK(sender_type IN ('user','admin')),
        sender_name TEXT NOT NULL DEFAULT '',
        message TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(ticket_id) REFERENCES support_tickets(id)
            ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_support_user
        ON support_tickets(user_id);

    CREATE INDEX IF NOT EXISTS idx_support_status
        ON support_tickets(status);

    CREATE INDEX IF NOT EXISTS idx_support_messages_ticket
        ON support_messages(ticket_id);
    """)

    conn.commit()

    # Bootstrap owner/admin.
    # Existing accounts are not overwritten.
    for prefix, role in [
        ("OWNER", "owner"),
        ("ADMIN", "admin")
    ]:
        username = os.getenv(
            f"{prefix}_USERNAME",
            ""
        ).strip()

        password = os.getenv(
            f"{prefix}_PASSWORD",
            ""
        )

        if username and password:
            if len(password) < 12:
                raise RuntimeError(
                    f"{prefix}_PASSWORD must contain at least 12 characters"
                )

            conn.execute(
                """
                INSERT OR IGNORE INTO users
                    (username, password_hash, role)
                VALUES (?, ?, ?)
                """,
                (
                    username,
                    generate_password_hash(password),
                    role
                )
            )

    conn.commit()

    # Seed catalogue only when empty.
    count = conn.execute(
        "SELECT COUNT(*) FROM catalog"
    ).fetchone()[0]

    catalog_file = BASE_DIR / "catalog.json"

    if count == 0 and catalog_file.exists():
        try:
            items = json.loads(
                catalog_file.read_text(encoding="utf-8")
            )

            if isinstance(items, dict):
                items = items.get("apps", [])

            for item in items if isinstance(items, list) else []:
                conn.execute(
                    """
                    INSERT INTO catalog
                    (
                        name,
                        category,
                        description,
                        version,
                        compatibility,
                        source,
                        url,
                        icon
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(item.get("name", "Без названия")),
                        str(item.get("category", "Другое")),
                        str(item.get("desc", item.get("description", ""))),
                        str(item.get("version", "")),
                        str(item.get("compat", item.get("compatibility", ""))),
                        str(item.get("source", "")),
                        str(item.get("url", "")),
                        str(item.get("icon", "📦"))[:16]
                    )
                )

            conn.commit()

        except (ValueError, OSError):
            app.logger.exception(
                "Could not seed catalog.json"
            )

    conn.close()


# ==========================================
# AUTHENTICATION
# ==========================================

def user_from_session():
    uid = session.get("user_id")

    if not uid:
        return None

    return db().execute(
        """
        SELECT id, username, avatar, role, is_banned, created_at
        FROM users
        WHERE id=?
        """,
        (uid,)
    ).fetchone()


def require_login(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        user = user_from_session()

        if not user:
            return jsonify(
                error="Требуется вход в аккаунт"
            ), 401

        if user["is_banned"]:
            session.clear()

            return jsonify(
                error="Аккаунт заблокирован"
            ), 403

        g.current_user = user

        return fn(*args, **kwargs)

    return wrapped


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        @require_login
        def wrapped(*args, **kwargs):
            if g.current_user["role"] not in roles:
                return jsonify(
                    error="Недостаточно прав"
                ), 403

            return fn(*args, **kwargs)

        return wrapped

    return decorator


def require_csrf(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected = session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token", "")

        if (
            not expected
            or not supplied
            or not hmac.compare_digest(expected, supplied)
        ):
            return jsonify(
                error="CSRF-проверка не пройдена. Обнови страницу и попробуй снова."
            ), 403

        return fn(*args, **kwargs)

    return wrapped


def public_user(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "avatar": row["avatar"],
        "role": row["role"],
        "is_banned": bool(row["is_banned"]),
        "created_at": row["created_at"]
    }


# ==========================================
# TELEGRAM NOTIFICATIONS
# ==========================================

def notify_owner_about_ticket(ticket_id, username, category, description):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    owner_id = os.getenv("OWNER_ID", "")

    if not token or not owner_id.isdigit():
        app.logger.warning(
            "Telegram notification skipped: bot settings are missing"
        )
        return False

    message = (
        f"🆕 НОВОЕ ОБРАЩЕНИЕ #{ticket_id}\n\n"
        f"👤 Пользователь: {username}\n"
        f"📂 Категория: {category}\n\n"
        f"Описание:\n{description[:3000]}\n\n"
        f"Открой админ-панель ArticSupport для ответа."
    )

    payload = urllib.parse.urlencode({
        "chat_id": owner_id,
        "text": message
    }).encode("utf-8")

    url = (
        f"https://api.telegram.org/bot{token}/sendMessage"
    )

    try:
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST"
        )

        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(
                response.read().decode("utf-8")
            )

        if not result.get("ok"):
            app.logger.warning(
                "Telegram API did not accept the notification"
            )
            return False

        return True

    except Exception:
        app.logger.exception(
            "Could not send Telegram support notification"
        )
        return False


# ==========================================
# SUPPORT HELPERS
# ==========================================

SUPPORT_CATEGORIES = [
    "🎮 Проблема с PS4",
    "📱 Проблема с телефоном",
    "💻 Проблема с ПК",
    "🔗 Не работает ссылка",
    "💡 Предложение",
    "❓ Другая проблема"
]


def ticket_public(row):
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "username": row["username"],
        "category": row["category"],
        "description": row["description"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"]
    }


def message_public(row):
    return {
        "id": row["id"],
        "sender_type": row["sender_type"],
        "sender_name": row["sender_name"],
        "message": row["message"],
        "created_at": row["created_at"]
    }


def get_ticket_for_user(ticket_id, user_id):
    return db().execute(
        """
        SELECT
            t.*,
            u.username
        FROM support_tickets t
        JOIN users u ON u.id = t.user_id
        WHERE t.id=? AND t.user_id=?
        """,
        (ticket_id, user_id)
    ).fetchone()


def get_ticket_details(ticket_id):
    ticket = db().execute(
        """
        SELECT
            t.*,
            u.username
        FROM support_tickets t
        JOIN users u ON u.id = t.user_id
        WHERE t.id=?
        """,
        (ticket_id,)
    ).fetchone()

    if not ticket:
        return None

    messages = db().execute(
        """
        SELECT *
        FROM support_messages
        WHERE ticket_id=?
        ORDER BY id ASC
        """,
        (ticket_id,)
    ).fetchall()

    result = ticket_public(ticket)
    result["messages"] = [
        message_public(message)
        for message in messages
    ]

    return result


def support_api_key_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        expected = os.getenv("SUPPORT_API_KEY", "")
        supplied = request.headers.get("X-Support-Key", "")

        if len(expected) < 32:
            return jsonify(
                error="Внутренний API поддержки не настроен."
            ), 503

        if (
            not supplied
            or not hmac.compare_digest(expected, supplied)
        ):
            return jsonify(
                error="Доступ запрещён."
            ), 403

        return fn(*args, **kwargs)

    return wrapped


# ==========================================
# GENERAL API
# ==========================================

@app.get("/")
def index():
    return jsonify(
        service="PS4 ULTIMATE STORE API",
        version="0.9.8",
        status="ok"
    )


@app.get("/api/health")
def health():
    return jsonify(
        status="ok",
        service="ps4-ultimate-store-api",
        version="0.9.8"
    )


@app.get("/api/csrf")
def csrf():
    token = session.get("csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    session.permanent = True

    return jsonify(csrf_token=token)


@app.get("/api/me")
def me():
    user = user_from_session()

    if not user:
        return jsonify(user=None)

    if user["is_banned"]:
        session.clear()
        return jsonify(user=None), 403

    return jsonify(user=public_user(user))


# ==========================================
# REGISTER / LOGIN / LOGOUT
# ==========================================

def validate_credentials(username, password):
    if not re.fullmatch(
        r"[A-Za-z0-9_.-]{3,20}",
        username or ""
    ):
        return (
            "Логин должен содержать 3–20 символов: "
            "латиницу, цифры, точку, дефис или подчёркивание."
        )

    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        return "Пароль должен содержать от 10 до 128 символов."

    return None


@app.post("/api/register")
@require_csrf
def register():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    password = data.get("password", "")

    error = validate_credentials(username, password)

    if error:
        return jsonify(error=error), 400

    try:
        cur = db().execute(
            """
            INSERT INTO users
                (username, password_hash, avatar, role)
            VALUES (?, ?, ?, 'user')
            """,
            (
                username,
                generate_password_hash(password),
                str(data.get("avatar", "🎮"))[:16]
            )
        )

        db().commit()

    except sqlite3.IntegrityError:
        return jsonify(
            error="Такой логин уже занят."
        ), 409

    session.clear()
    session["user_id"] = cur.lastrowid
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True

    return jsonify(
        user=public_user(user_from_session()),
        csrf_token=session["csrf_token"]
    ), 201


@app.post("/api/login")
@require_csrf
def login():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    password = data.get("password", "")

    row = db().execute(
        """
        SELECT *
        FROM users
        WHERE username=? COLLATE NOCASE
        """,
        (username,)
    ).fetchone()

    if (
        not row
        or not check_password_hash(
            row["password_hash"],
            password if isinstance(password, str) else ""
        )
    ):
        return jsonify(
            error="Неверный логин или пароль."
        ), 401

    if row["is_banned"]:
        return jsonify(
            error="Аккаунт заблокирован."
        ), 403

    session.clear()
    session["user_id"] = row["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)
    session.permanent = True

    return jsonify(
        user=public_user(user_from_session()),
        csrf_token=session["csrf_token"]
    )


@app.post("/api/logout")
@require_login
@require_csrf
def logout():
    session.clear()

    return jsonify(ok=True)


# ==========================================
# CATALOG
# ==========================================

@app.get("/api/catalog")
def get_catalog():
    rows = db().execute(
        "SELECT * FROM catalog ORDER BY id DESC"
    ).fetchall()

    return jsonify(
        apps=[
            {
                "id": r["id"],
                "name": r["name"],
                "category": r["category"],
                "desc": r["description"],
                "version": r["version"],
                "compat": r["compatibility"],
                "source": r["source"],
                "url": r["url"],
                "icon": r["icon"]
            }
            for r in rows
        ]
    )


# ==========================================
# ADMIN: USERS
# ==========================================

@app.get("/api/admin/users")
@require_roles("admin", "owner")
def admin_users():
    rows = db().execute(
        """
        SELECT id, username, avatar, role, is_banned, created_at
        FROM users
        ORDER BY id
        """
    ).fetchall()

    return jsonify(
        users=[public_user(r) for r in rows]
    )


@app.patch("/api/admin/users/<int:user_id>")
@require_roles("admin", "owner")
@require_csrf
def update_user(user_id):
    data = request.get_json(silent=True) or {}

    target = db().execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not target:
        return jsonify(error="Пользователь не найден"), 404

    actor_role = g.current_user["role"]

    if target["role"] == "owner":
        return jsonify(
            error="Аккаунт владельца нельзя изменять через эту операцию."
        ), 403

    if target["role"] == "admin" and actor_role != "owner":
        return jsonify(
            error="Только владелец может управлять администраторами."
        ), 403

    if "role" in data:
        if actor_role != "owner":
            return jsonify(
                error="Только владелец может менять роли."
            ), 403

        new_role = data["role"]

        if new_role not in ("user", "admin"):
            return jsonify(
                error="Допустимые роли: user или admin."
            ), 400

        db().execute(
            "UPDATE users SET role=? WHERE id=?",
            (new_role, user_id)
        )

    if "is_banned" in data:
        db().execute(
            "UPDATE users SET is_banned=? WHERE id=?",
            (1 if data["is_banned"] else 0, user_id)
        )

    db().commit()

    row = db().execute(
        """
        SELECT id, username, avatar, role, is_banned, created_at
        FROM users
        WHERE id=?
        """,
        (user_id,)
    ).fetchone()

    return jsonify(user=public_user(row))


@app.delete("/api/admin/users/<int:user_id>")
@require_roles("admin", "owner")
@require_csrf
def delete_user(user_id):
    target = db().execute(
        "SELECT * FROM users WHERE id=?",
        (user_id,)
    ).fetchone()

    if not target:
        return jsonify(error="Пользователь не найден"), 404

    if (
        target["role"] == "owner"
        or (
            target["role"] == "admin"
            and g.current_user["role"] != "owner"
        )
    ):
        return jsonify(
            error="Недостаточно прав для удаления этого аккаунта."
        ), 403

    if target["id"] == g.current_user["id"]:
        return jsonify(
            error="Нельзя удалить текущий аккаунт."
        ), 400

    db().execute(
        "DELETE FROM users WHERE id=?",
        (user_id,)
    )

    db().commit()

    return jsonify(ok=True)


# ==========================================
# ADMIN: CATALOG
# ==========================================

def catalog_payload(data):
    return (
        str(data.get("name", "")).strip(),
        str(data.get("category", "Другое")).strip(),
        str(data.get("desc", data.get("description", ""))),
        str(data.get("version", "")),
        str(data.get("compat", data.get("compatibility", ""))),
        str(data.get("source", "")),
        str(data.get("url", "")),
        str(data.get("icon", "📦"))[:16]
    )


@app.post("/api/admin/catalog")
@require_roles("admin", "owner")
@require_csrf
def add_catalog_item():
    data = request.get_json(silent=True) or {}
    values = catalog_payload(data)

    if not values[0] or not values[6]:
        return jsonify(
            error="Название и ссылка обязательны."
        ), 400

    cur = db().execute(
        """
        INSERT INTO catalog
        (
            name, category, description, version,
            compatibility, source, url, icon
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        values
    )

    db().commit()

    return jsonify(id=cur.lastrowid), 201


@app.patch("/api/admin/catalog/<int:item_id>")
@require_roles("admin", "owner")
@require_csrf
def edit_catalog_item(item_id):
    data = request.get_json(silent=True) or {}

    current = db().execute(
        "SELECT * FROM catalog WHERE id=?",
        (item_id,)
    ).fetchone()

    if not current:
        return jsonify(error="Запись не найдена."), 404

    merged = {
        "name": current["name"],
        "category": current["category"],
        "desc": current["description"],
        "version": current["version"],
        "compat": current["compatibility"],
        "source": current["source"],
        "url": current["url"],
        "icon": current["icon"]
    }

    merged.update(data)

    values = catalog_payload(merged)

    if not values[0] or not values[6]:
        return jsonify(
            error="Название и ссылка обязательны."
        ), 400

    db().execute(
        """
        UPDATE catalog
        SET
            name=?,
            category=?,
            description=?,
            version=?,
            compatibility=?,
            source=?,
            url=?,
            icon=?
        WHERE id=?
        """,
        (*values, item_id)
    )

    db().commit()

    return jsonify(ok=True)


@app.delete("/api/admin/catalog/<int:item_id>")
@require_roles("admin", "owner")
@require_csrf
def remove_catalog_item(item_id):
    cur = db().execute(
        "DELETE FROM catalog WHERE id=?",
        (item_id,)
    )

    db().commit()

    if not cur.rowcount:
        return jsonify(error="Запись не найдена."), 404

    return jsonify(ok=True)


# ==========================================
# SUPPORT: USER API
# Only logged-in users can use these routes.
# ==========================================

@app.get("/api/support/categories")
@require_login
def support_categories():
    return jsonify(categories=SUPPORT_CATEGORIES)


@app.get("/api/support/tickets")
@require_login
def support_my_tickets():
    rows = db().execute(
        """
        SELECT
            t.*,
            u.username
        FROM support_tickets t
        JOIN users u ON u.id = t.user_id
        WHERE t.user_id=?
        ORDER BY t.id DESC
        """,
        (g.current_user["id"],)
    ).fetchall()

    return jsonify(
        tickets=[ticket_public(row) for row in rows]
    )


@app.post("/api/support/tickets")
@require_login
@require_csrf
def support_create_ticket():
    data = request.get_json(silent=True) or {}

    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()

    if category not in SUPPORT_CATEGORIES:
        return jsonify(
            error="Выбери категорию из списка."
        ), 400

    if len(description) < 5:
        return jsonify(
            error="Опиши проблему подробнее. Минимум 5 символов."
        ), 400

    if len(description) > 3000:
        return jsonify(
            error="Максимальная длина обращения — 3000 символов."
        ), 400

    cur = db().execute(
        """
        INSERT INTO support_tickets
            (user_id, category, description)
        VALUES (?, ?, ?)
        """,
        (
            g.current_user["id"],
            category,
            description
        )
    )

    ticket_id = cur.lastrowid

    db().execute(
        """
        INSERT INTO support_messages
            (ticket_id, sender_type, sender_name, message)
        VALUES (?, 'user', ?, ?)
        """,
        (
            ticket_id,
            g.current_user["username"],
            description
        )
    )

    db().commit()

    notify_owner_about_ticket(
        ticket_id,
        g.current_user["username"],
        category,
        description
    )

    return jsonify(
        ok=True,
        ticket_id=ticket_id,
        message="Обращение создано."
    ), 201


@app.get("/api/support/tickets/<int:ticket_id>")
@require_login
def support_ticket_detail(ticket_id):
    ticket = get_ticket_for_user(
        ticket_id,
        g.current_user["id"]
    )

    if not ticket:
        return jsonify(
            error="Обращение не найдено."
        ), 404

    result = ticket_public(ticket)

    messages = db().execute(
        """
        SELECT *
        FROM support_messages
        WHERE ticket_id=?
        ORDER BY id ASC
        """,
        (ticket_id,)
    ).fetchall()

    result["messages"] = [
        message_public(message)
        for message in messages
    ]

    return jsonify(ticket=result)


@app.post("/api/support/tickets/<int:ticket_id>/messages")
@require_login
@require_csrf
def support_user_message(ticket_id):
    ticket = get_ticket_for_user(
        ticket_id,
        g.current_user["id"]
    )

    if not ticket:
        return jsonify(
            error="Обращение не найдено."
        ), 404

    if ticket["status"] != "open":
        return jsonify(
            error="Это обращение закрыто."
        ), 409

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if len(message) < 1:
        return jsonify(
            error="Напиши сообщение."
        ), 400

    if len(message) > 3000:
        return jsonify(
            error="Максимальная длина сообщения — 3000 символов."
        ), 400

    db().execute(
        """
        INSERT INTO support_messages
            (ticket_id, sender_type, sender_name, message)
        VALUES (?, 'user', ?, ?)
        """,
        (
            ticket_id,
            g.current_user["username"],
            message
        )
    )

    db().execute(
        """
        UPDATE support_tickets
        SET updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (ticket_id,)
    )

    db().commit()

    return jsonify(
        ok=True,
        message="Сообщение отправлено."
    ), 201


# ==========================================
# SUPPORT: INTERNAL API FOR ARTICSUPPORT BOT
# Protected by SUPPORT_API_KEY.
# ==========================================

@app.get("/api/support/bot/tickets")
@support_api_key_required
def support_bot_tickets():
    mode = request.args.get("status", "open")

    if mode not in ("open", "closed", "all"):
        return jsonify(
            error="Допустимые статусы: open, closed, all."
        ), 400

    if mode == "all":
        rows = db().execute(
            """
            SELECT
                t.*,
                u.username
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            ORDER BY t.id DESC
            LIMIT 100
            """
        ).fetchall()

    else:
        rows = db().execute(
            """
            SELECT
                t.*,
                u.username
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.status=?
            ORDER BY t.id DESC
            LIMIT 100
            """,
            (mode,)
        ).fetchall()

    return jsonify(
        tickets=[ticket_public(row) for row in rows]
    )


@app.get("/api/support/bot/tickets/<int:ticket_id>")
@support_api_key_required
def support_bot_ticket_detail(ticket_id):
    ticket = get_ticket_details(ticket_id)

    if not ticket:
        return jsonify(
            error="Обращение не найдено."
        ), 404

    return jsonify(ticket=ticket)


@app.post("/api/support/bot/tickets/<int:ticket_id>/messages")
@support_api_key_required
def support_bot_reply(ticket_id):
    ticket = db().execute(
        """
        SELECT id, status
        FROM support_tickets
        WHERE id=?
        """,
        (ticket_id,)
    ).fetchone()

    if not ticket:
        return jsonify(
            error="Обращение не найдено."
        ), 404

    if ticket["status"] != "open":
        return jsonify(
            error="Обращение закрыто."
        ), 409

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return jsonify(
            error="Сообщение не может быть пустым."
        ), 400

    if len(message) > 3000:
        return jsonify(
            error="Максимальная длина сообщения — 3000 символов."
        ), 400

    db().execute(
        """
        INSERT INTO support_messages
            (ticket_id, sender_type, sender_name, message)
        VALUES (?, 'admin', 'ArticSupport', ?)
        """,
        (ticket_id, message)
    )

    db().execute(
        """
        UPDATE support_tickets
        SET updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (ticket_id,)
    )

    db().commit()

    return jsonify(
        ok=True,
        message="Ответ сохранён."
    ), 201


@app.patch("/api/support/bot/tickets/<int:ticket_id>")
@support_api_key_required
def support_bot_update_ticket(ticket_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in ("open", "closed"):
        return jsonify(
            error="Статус должен быть open или closed."
        ), 400

    cur = db().execute(
        """
        UPDATE support_tickets
        SET status=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (status, ticket_id)
    )

    db().commit()

    if not cur.rowcount:
        return jsonify(
            error="Обращение не найдено."
        ), 404

    return jsonify(
        ok=True,
        ticket_id=ticket_id,
        status=status
    )


# ==========================================
# STARTUP
# ==========================================

init_db()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=False
    )
