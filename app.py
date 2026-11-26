
import os
import re
import json
import hmac
import secrets
import sqlite3
import urllib.request
import urllib.parse

from pathlib import Path
from datetime import timedelta
from functools import wraps

from flask import (
    Flask, request, jsonify, session, g
)
from flask_cors import CORS
from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


# ==========================================
# PS4 ULTIMATE STORE 1.0.0
# BACKEND
# ==========================================

BASE_DIR = Path(__file__).resolve().parent

DB_PATH = Path(
    os.getenv(
        "DATABASE_PATH",
        str(BASE_DIR / "data" / "store.db")
    )
)

DB_PATH.parent.mkdir(parents=True, exist_ok=True)

FRONTEND_ORIGIN = os.getenv(
    "FRONTEND_ORIGIN",
    "https://backlicks.github.io"
).rstrip("/")

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    SECRET_KEY = secrets.token_hex(32)
    print("WARNING: SECRET_KEY is temporary. Set it in Render.")

app = Flask(__name__)

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_NAME="ps4_ultimate_store_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="None",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    MAX_CONTENT_LENGTH=2 * 1024 * 1024
)

CORS(
    app,
    origins=[FRONTEND_ORIGIN],
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
        g.db = sqlite3.connect(
            DB_PATH,
            timeout=20
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


@app.teardown_appcontext
def close_db(error=None):
    connection = g.pop("db", None)

    if connection is not None:
        connection.close()


def init_db():
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA foreign_keys = ON")

    connection.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user',
        is_banned INTEGER NOT NULL DEFAULT 0,
        avatar TEXT DEFAULT '🎮',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        category TEXT NOT NULL,
        description TEXT DEFAULT '',
        version TEXT DEFAULT '',
        compatibility TEXT DEFAULT '',
        source TEXT DEFAULT '',
        url TEXT DEFAULT '',
        icon TEXT DEFAULT '📦',
        platforms TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        category TEXT NOT NULL,
        description TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS support_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        user_id INTEGER,
        sender_type TEXT NOT NULL DEFAULT 'user',
        username TEXT NOT NULL,
        message TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(ticket_id)
            REFERENCES support_tickets(id)
            ON DELETE CASCADE
    );

    CREATE INDEX IF NOT EXISTS idx_ticket_user
    ON support_tickets(user_id);

    CREATE INDEX IF NOT EXISTS idx_messages_ticket
    ON support_messages(ticket_id);
    """)

    connection.commit()
    connection.close()


# ==========================================
# INITIAL CATALOG
# ==========================================

INITIAL_CATALOG = [
    {
        "name": "PS Remote Play",
        "category": "Утилиты",
        "description": "Официальное приложение удалённой игры PlayStation.",
        "version": "Актуальная",
        "compatibility": "Android / iOS / Windows / macOS",
        "source": "PlayStation",
        "url": "https://remoteplay.dl.playstation.net/remoteplay/",
        "icon": "📱",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "Apollo Save Tool",
        "category": "Утилиты",
        "description": "Инструмент управления сохранениями PS4.",
        "version": "Последняя",
        "compatibility": "PS4 homebrew",
        "source": "GitHub",
        "url": "https://github.com/bucanero/apollo-ps4/releases",
        "icon": "💾",
        "platforms": ["PS4"]
    },
    {
        "name": "PS4-Xplorer",
        "category": "Утилиты",
        "description": "Файловый менеджер для PS4.",
        "version": "Последняя",
        "compatibility": "PS4 homebrew",
        "source": "GitHub",
        "url": "https://github.com/Lapy055/PS4-Xplorer/releases",
        "icon": "📁",
        "platforms": ["PS4"]
    },
    {
        "name": "VLC",
        "category": "Медиа",
        "description": "Универсальный медиаплеер.",
        "version": "Последняя",
        "compatibility": "Android / iOS / Windows / macOS / Linux",
        "source": "VideoLAN",
        "url": "https://www.videolan.org/vlc/",
        "icon": "🎬",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "Telegram",
        "category": "Утилиты",
        "description": "Мессенджер для общения и передачи файлов.",
        "version": "Последняя",
        "compatibility": "Android / iOS / Windows / macOS",
        "source": "Telegram",
        "url": "https://telegram.org/apps",
        "icon": "✈️",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "7-Zip",
        "category": "Утилиты",
        "description": "Бесплатный архиватор.",
        "version": "Последняя",
        "compatibility": "Windows",
        "source": "Официальный сайт",
        "url": "https://www.7-zip.org/",
        "icon": "🗜️",
        "platforms": ["ПК"]
    },
    {
        "name": "OBS Studio",
        "category": "Медиа",
        "description": "Запись экрана и трансляции.",
        "version": "Последняя",
        "compatibility": "Windows / macOS / Linux",
        "source": "OBS",
        "url": "https://obsproject.com/download",
        "icon": "🎥",
        "platforms": ["ПК"]
    }
]


def seed_catalog():
    connection = sqlite3.connect(DB_PATH)
    count = connection.execute(
        "SELECT COUNT(*) FROM catalog"
    ).fetchone()[0]

    if count == 0:
        for item in INITIAL_CATALOG:
            connection.execute("""
                INSERT INTO catalog (
                    name, category, description,
                    version, compatibility, source,
                    url, icon, platforms
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                item["name"],
                item["category"],
                item["description"],
                item["version"],
                item["compatibility"],
                item["source"],
                item["url"],
                item["icon"],
                json.dumps(item["platforms"], ensure_ascii=False)
            ))

    connection.commit()
    connection.close()


# ==========================================
# OWNER / ADMIN BOOTSTRAP
# ==========================================

def create_bootstrap_user(username_env, password_env, role):
    username = os.getenv(username_env)
    password = os.getenv(password_env)

    if not username or not password:
        return

    if len(password) < 12:
        print(f"WARNING: {username_env} password must be at least 12 characters.")
        return

    connection = sqlite3.connect(DB_PATH)

    exists = connection.execute(
        "SELECT id FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if not exists:
        connection.execute("""
            INSERT INTO users (
                username,
                password_hash,
                role
            )
            VALUES (?, ?, ?)
        """, (
            username,
            generate_password_hash(password),
            role
        ))

    connection.commit()
    connection.close()


# ==========================================
# HELPERS
# ==========================================

def response_error(message, status=400):
    return jsonify({"error": message}), status


def public_user(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "is_banned": bool(user["is_banned"]),
        "avatar": user["avatar"],
        "created_at": user["created_at"]
    }


def current_user():
    user_id = session.get("user_id")

    if not user_id:
        return None

    user = db().execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user or user["is_banned"]:
        session.clear()
        return None

    return user


def require_login(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = current_user()

        if not user:
            return response_error(
                "Требуется авторизация",
                401
            )

        g.current_user = user
        return fn(*args, **kwargs)

    return wrapper


def require_roles(*roles):
    def decorator(fn):
        @wraps(fn)
        @require_login
        def wrapper(*args, **kwargs):
            if g.current_user["role"] not in roles:
                return response_error(
                    "Недостаточно прав",
                    403
                )

            return fn(*args, **kwargs)

        return wrapper

    return decorator


def require_csrf(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = request.headers.get("X-CSRF-Token", "")
        expected = session.get("csrf_token", "")

        if not token or not expected:
            return response_error(
                "CSRF-токен отсутствует",
                403
            )

        if not hmac.compare_digest(token, expected):
            return response_error(
                "Неверный CSRF-токен",
                403
            )

        return fn(*args, **kwargs)

    return wrapper


def validate_username(username):
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9_.-]{3,20}",
            username or ""
        )
    )


def valid_url(url):
    if not url:
        return True

    return bool(
        re.match(r"^https?://", url, re.I)
    )


def catalog_item(row, hide_url=False):
    platforms = []

    try:
        platforms = json.loads(row["platforms"] or "[]")
    except Exception:
        pass

    result = {
        "id": row["id"],
        "name": row["name"],
        "category": row["category"],
        "desc": row["description"],
        "description": row["description"],
        "version": row["version"],
        "compat": row["compatibility"],
        "source": row["source"],
        "icon": row["icon"],
        "platforms": platforms
    }

    if not hide_url:
        result["url"] = row["url"]

    return result


def notify_owner(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    owner_id = os.getenv("OWNER_ID")

    if not token or not owner_id:
        return

    try:
        url = (
            f"https://api.telegram.org/bot{token}/sendMessage"
        )

        payload = urllib.parse.urlencode({
            "chat_id": owner_id,
            "text": text
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            method="POST"
        )

        urllib.request.urlopen(req, timeout=8)

    except Exception as error:
        print("Telegram notification error:", error)


# ==========================================
# CSRF
# ==========================================

@app.route("/api/csrf", methods=["GET"])
def csrf():
    token = session.get("csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    return jsonify({
        "csrf_token": token
    })


# ==========================================
# AUTH
# ==========================================

@app.route("/api/me", methods=["GET"])
def me():
    user = current_user()

    return jsonify({
        "user": public_user(user) if user else None
    })


@app.route("/api/register", methods=["POST"])
@require_csrf
def register():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    avatar = str(data.get("avatar", "🎮"))[:10]

    if not validate_username(username):
        return response_error(
            "Ник должен содержать 3–20 символов: латиница, цифры, _ . -"
        )

    if len(password) < 10 or len(password) > 128:
        return response_error(
            "Пароль должен содержать от 10 до 128 символов"
        )

    if db().execute(
        "SELECT id FROM users WHERE username = ?",
        (username,)
    ).fetchone():
        return response_error(
            "Этот ник уже занят",
            409
        )

    try:
        cursor = db().execute("""
            INSERT INTO users (
                username,
                password_hash,
                avatar
            )
            VALUES (?, ?, ?)
        """, (
            username,
            generate_password_hash(password),
            avatar
        ))

        db().commit()

        user = db().execute(
            "SELECT * FROM users WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)

        notify_owner(
            f"Новый пользователь PS4 ULTIMATE STORE: {username}"
        )

        return jsonify({
            "user": public_user(user),
            "csrf_token": session["csrf_token"]
        }), 201

    except sqlite3.IntegrityError:
        return response_error(
            "Не удалось создать аккаунт",
            409
        )


@app.route("/api/login", methods=["POST"])
@require_csrf
def login():
    data = request.get_json(silent=True) or {}

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    user = db().execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    if not user or not check_password_hash(
        user["password_hash"],
        password
    ):
        return response_error(
            "Неверный ник или пароль",
            401
        )

    if user["is_banned"]:
        return response_error(
            "Этот аккаунт заблокирован",
            403
        )

    session.clear()
    session.permanent = True
    session["user_id"] = user["id"]
    session["csrf_token"] = secrets.token_urlsafe(32)

    return jsonify({
        "user": public_user(user),
        "csrf_token": session["csrf_token"]
    })


@app.route("/api/logout", methods=["POST"])
@require_csrf
@require_login
def logout():
    session.clear()
    return jsonify({"success": True})


# ==========================================
# CATALOG
# ==========================================

@app.route("/api/catalog", methods=["GET"])
def get_catalog():
    user = current_user()

    rows = db().execute(
        "SELECT * FROM catalog ORDER BY id DESC"
    ).fetchall()

    return jsonify({
        "apps": [
            catalog_item(row, hide_url=not bool(user))
            for row in rows
        ]
    })


# ==========================================
# ADMIN USERS
# ==========================================

@app.route("/api/admin/users", methods=["GET"])
@require_roles("owner", "admin")
def admin_users():
    rows = db().execute("""
        SELECT * FROM users
        ORDER BY id DESC
    """).fetchall()

    return jsonify({
        "users": [
            public_user(row)
            for row in rows
        ]
    })


@app.route("/api/admin/users/<int:user_id>", methods=["PATCH"])
@require_csrf
@require_roles("owner", "admin")
def update_user(user_id):
    data = request.get_json(silent=True) or {}

    target = db().execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not target:
        return response_error("Пользователь не найден", 404)

    actor = g.current_user

    if target["role"] == "owner":
        return response_error(
            "Нельзя изменять владельца",
            403
        )

    if actor["role"] == "admin" and target["role"] == "admin":
        return response_error(
            "Администратор не может изменять другого администратора",
            403
        )

    if "is_banned" in data:
        is_banned = bool(data["is_banned"])

        db().execute(
            "UPDATE users SET is_banned = ? WHERE id = ?",
            (int(is_banned), user_id)
        )

    if "role" in data:
        new_role = data["role"]

        if actor["role"] != "owner":
            return response_error(
                "Только владелец может менять роли",
                403
            )

        if new_role not in ("user", "admin"):
            return response_error(
                "Недопустимая роль"
            )

        db().execute(
            "UPDATE users SET role = ? WHERE id = ?",
            (new_role, user_id)
        )

    db().commit()

    return jsonify({"success": True})


@app.route("/api/admin/users/<int:user_id>", methods=["DELETE"])
@require_csrf
@require_roles("owner", "admin")
def remove_user(user_id):
    target = db().execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not target:
        return response_error("Пользователь не найден", 404)

    if target["role"] == "owner":
        return response_error(
            "Нельзя удалить владельца",
            403
        )

    if (
        g.current_user["role"] == "admin"
        and target["role"] == "admin"
    ):
        return response_error(
            "Администратор не может удалить другого администратора",
            403
        )

    db().execute(
        "DELETE FROM support_tickets WHERE user_id = ?",
        (user_id,)
    )

    db().execute(
        "DELETE FROM users WHERE id = ?",
        (user_id,)
    )

    db().commit()

    return jsonify({"success": True})


# ==========================================
# ADMIN CATALOG
# ==========================================

def catalog_payload(data):
    name = str(data.get("name", "")).strip()
    category = str(data.get("category", "")).strip()
    url = str(data.get("url", "")).strip()

    if not name or not category:
        raise ValueError("Название и категория обязательны")

    if len(name) > 100 or len(category) > 60:
        raise ValueError("Слишком длинное название или категория")

    if not valid_url(url):
        raise ValueError("Ссылка должна начинаться с http:// или https://")

    platforms = data.get("platforms", [])

    if isinstance(platforms, str):
        platforms = [
            x.strip()
            for x in platforms.split(",")
            if x.strip()
        ]

    if not isinstance(platforms, list):
        platforms = []

    platforms = [
        str(x)[:30]
        for x in platforms[:10]
    ]

    return (
        name,
        category,
        str(data.get("desc", data.get("description", "")))[:3000],
        str(data.get("version", ""))[:50],
        str(data.get("compat", data.get("compatibility", "")))[:200],
        str(data.get("source", ""))[:100],
        url,
        str(data.get("icon", "📦"))[:10],
        json.dumps(platforms, ensure_ascii=False)
    )


@app.route("/api/admin/catalog", methods=["POST"])
@require_csrf
@require_roles("owner", "admin")
def add_catalog():
    data = request.get_json(silent=True) or {}

    try:
        values = catalog_payload(data)
    except ValueError as error:
        return response_error(str(error))

    db().execute("""
        INSERT INTO catalog (
            name, category, description,
            version, compatibility, source,
            url, icon, platforms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, values)

    db().commit()

    return jsonify({"success": True}), 201


@app.route("/api/admin/catalog/<int:item_id>", methods=["PATCH"])
@require_csrf
@require_roles("owner", "admin")
def update_catalog(item_id):
    if not db().execute(
        "SELECT id FROM catalog WHERE id = ?",
        (item_id,)
    ).fetchone():
        return response_error("Запись не найдена", 404)

    data = request.get_json(silent=True) or {}

    try:
        values = catalog_payload(data)
    except ValueError as error:
        return response_error(str(error))

    db().execute("""
        UPDATE catalog SET
            name = ?,
            category = ?,
            description = ?,
            version = ?,
            compatibility = ?,
            source = ?,
            url = ?,
            icon = ?,
            platforms = ?
        WHERE id = ?
    """, values + (item_id,))

    db().commit()

    return jsonify({"success": True})


@app.route("/api/admin/catalog/<int:item_id>", methods=["DELETE"])
@require_csrf
@require_roles("owner", "admin")
def delete_catalog(item_id):
    db().execute(
        "DELETE FROM catalog WHERE id = ?",
        (item_id,)
    )

    db().commit()

    return jsonify({"success": True})


# ==========================================
# SUPPORT CATEGORIES
# ==========================================

SUPPORT_CATEGORIES = [
    {"value": "PS4", "label": "🎮 Проблема с PS4"},
    {"value": "Телефон", "label": "📱 Проблема с телефоном"},
    {"value": "ПК", "label": "💻 Проблема с ПК"},
    {"value": "Ссылка/скачивание", "label": "🔗 Не работает ссылка"},
    {"value": "Предложение", "label": "💡 Предложение"},
    {"value": "Другая проблема", "label": "❔ Другая проблема"}
]


@app.route("/api/support/categories", methods=["GET"])
def support_categories():
    return jsonify({
        "categories": SUPPORT_CATEGORIES
    })


# ==========================================
# SUPPORT USER API
# ==========================================

@app.route("/api/support/tickets", methods=["GET"])
@require_login
def support_tickets():
    rows = db().execute("""
        SELECT * FROM support_tickets
        WHERE user_id = ?
        ORDER BY id DESC
    """, (g.current_user["id"],)).fetchall()

    return jsonify({
        "tickets": [
            {
                "id": row["id"],
                "category": row["category"],
                "description": row["description"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"]
            }
            for row in rows
        ]
    })


@app.route("/api/support/tickets", methods=["POST"])
@require_csrf
@require_login
def create_ticket():
    data = request.get_json(silent=True) or {}

    category = str(data.get("category", "")).strip()
    description = str(data.get("description", "")).strip()

    allowed = [
        item["value"]
        for item in SUPPORT_CATEGORIES
    ]

    if category not in allowed:
        return response_error("Выбери корректную категорию")

    if len(description) < 10 or len(description) > 2000:
        return response_error(
            "Описание должно содержать от 10 до 2000 символов"
        )

    cursor = db().execute("""
        INSERT INTO support_tickets (
            user_id, category, description
        )
        VALUES (?, ?, ?)
    """, (
        g.current_user["id"],
        category,
        description
    ))

    ticket_id = cursor.lastrowid

    db().execute("""
        INSERT INTO support_messages (
            ticket_id,
            user_id,
            sender_type,
            username,
            message
        )
        VALUES (?, ?, 'user', ?, ?)
    """, (
        ticket_id,
        g.current_user["id"],
        g.current_user["username"],
        description
    ))

    db().commit()

    notify_owner(
        "Новое обращение ArticSupport\n"
        f"Пользователь: {g.current_user['username']}\n"
        f"Категория: {category}\n"
        f"Обращение: #{ticket_id}"
    )

    return jsonify({
        "success": True,
        "ticket": {
            "id": ticket_id,
            "category": category,
            "status": "open"
        }
    }), 201


@app.route("/api/support/tickets/<int:ticket_id>", methods=["GET"])
@require_login
def get_ticket(ticket_id):
    ticket = db().execute("""
        SELECT * FROM support_tickets
        WHERE id = ? AND user_id = ?
    """, (
        ticket_id,
        g.current_user["id"]
    )).fetchone()

    if not ticket:
        return response_error("Обращение не найдено", 404)

    messages = db().execute("""
        SELECT * FROM support_messages
        WHERE ticket_id = ?
        ORDER BY id ASC
    """, (ticket_id,)).fetchall()

    return jsonify({
        "ticket": {
            "id": ticket["id"],
            "category": ticket["category"],
            "description": ticket["description"],
            "status": ticket["status"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "messages": [
                {
                    "id": m["id"],
                    "username": m["username"],
                    "sender_type": m["sender_type"],
                    "message": m["message"],
                    "created_at": m["created_at"]
                }
                for m in messages
            ]
        }
    })


@app.route(
    "/api/support/tickets/<int:ticket_id>/messages",
    methods=["POST"]
)
@require_csrf
@require_login
def send_ticket_message(ticket_id):
    ticket = db().execute("""
        SELECT * FROM support_tickets
        WHERE id = ? AND user_id = ?
    """, (
        ticket_id,
        g.current_user["id"]
    )).fetchone()

    if not ticket:
        return response_error("Обращение не найдено", 404)

    if ticket["status"] == "closed":
        return response_error("Обращение закрыто", 403)

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message or len(message) > 2000:
        return response_error(
            "Сообщение должно содержать от 1 до 2000 символов"
        )

    db().execute("""
        INSERT INTO support_messages (
            ticket_id,
            user_id,
            sender_type,
            username,
            message
        )
        VALUES (?, ?, 'user', ?, ?)
    """, (
        ticket_id,
        g.current_user["id"],
        g.current_user["username"],
        message
    ))

    db().execute("""
        UPDATE support_tickets
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (ticket_id,))

    db().commit()

    notify_owner(
        f"Новое сообщение в ArticSupport #{ticket_id}\n"
        f"От: {g.current_user['username']}\n"
        f"{message[:500]}"
    )

    return jsonify({"success": True}), 201


# ==========================================
# PRIVATE SUPPORT BOT API
# ==========================================

def require_support_key(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = os.getenv("SUPPORT_API_KEY", "")
        received = request.headers.get("X-Support-Key", "")

        if not expected:
            return response_error(
                "Support bot API is disabled",
                503
            )

        if not hmac.compare_digest(expected, received):
            return response_error(
                "Invalid support key",
                403
            )

        return fn(*args, **kwargs)

    return wrapper


@app.route("/api/support/bot/tickets", methods=["GET"])
@require_support_key
def bot_tickets():
    status = request.args.get("status", "open")

    if status not in ("open", "closed", "all"):
        return response_error("Invalid status")

    if status == "all":
        rows = db().execute("""
            SELECT t.*, u.username
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            ORDER BY t.updated_at DESC
            LIMIT 100
        """).fetchall()
    else:
        rows = db().execute("""
            SELECT t.*, u.username
            FROM support_tickets t
            JOIN users u ON u.id = t.user_id
            WHERE t.status = ?
            ORDER BY t.updated_at DESC
            LIMIT 100
        """, (status,)).fetchall()

    return jsonify({
        "tickets": [
            {
                "id": r["id"],
                "username": r["username"],
                "category": r["category"],
                "description": r["description"],
                "status": r["status"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"]
            }
            for r in rows
        ]
    })


@app.route("/api/support/bot/tickets/<int:ticket_id>", methods=["GET"])
@require_support_key
def bot_get_ticket(ticket_id):
    ticket = db().execute("""
        SELECT t.*, u.username
        FROM support_tickets t
        JOIN users u ON u.id = t.user_id
        WHERE t.id = ?
    """, (ticket_id,)).fetchone()

    if not ticket:
        return response_error("Ticket not found", 404)

    messages = db().execute("""
        SELECT * FROM support_messages
        WHERE ticket_id = ?
        ORDER BY id ASC
    """, (ticket_id,)).fetchall()

    return jsonify({
        "ticket": {
            "id": ticket["id"],
            "username": ticket["username"],
            "category": ticket["category"],
            "description": ticket["description"],
            "status": ticket["status"],
            "messages": [
                {
                    "id": m["id"],
                    "username": m["username"],
                    "sender_type": m["sender_type"],
                    "message": m["message"],
                    "created_at": m["created_at"]
                }
                for m in messages
            ]
        }
    })


@app.route(
    "/api/support/bot/tickets/<int:ticket_id>/messages",
    methods=["POST"]
)
@require_support_key
def bot_send_message(ticket_id):
    ticket = db().execute(
        "SELECT * FROM support_tickets WHERE id = ?",
        (ticket_id,)
    ).fetchone()

    if not ticket:
        return response_error("Ticket not found", 404)

    if ticket["status"] == "closed":
        return response_error("Ticket is closed", 403)

    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message or len(message) > 2000:
        return response_error("Invalid message")

    db().execute("""
        INSERT INTO support_messages (
            ticket_id,
            user_id,
            sender_type,
            username,
            message
        )
        VALUES (?, NULL, 'admin', 'Поддержка', ?)
    """, (ticket_id, message))

    db().execute("""
        UPDATE support_tickets
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (ticket_id,))

    db().commit()

    return jsonify({"success": True}), 201


@app.route(
    "/api/support/bot/tickets/<int:ticket_id>",
    methods=["PATCH"]
)
@require_support_key
def bot_update_ticket(ticket_id):
    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in ("open", "closed"):
        return response_error("Invalid status")

    cursor = db().execute("""
        UPDATE support_tickets
        SET status = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (status, ticket_id))

    db().commit()

    if cursor.rowcount == 0:
        return response_error("Ticket not found", 404)

    return jsonify({"success": True})


# ==========================================
# HEALTH CHECK
# ==========================================

@app.route("/", methods=["GET"])
def index():
    return jsonify({
        "name": "PS4 ULTIMATE STORE",
        "version": "1.0.0",
        "status": "online"
    })


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok"
    })


# ==========================================
# STARTUP
# ==========================================

init_db()
seed_catalog()

create_bootstrap_user(
    "OWNER_USERNAME",
    "OWNER_PASSWORD",
    "owner"
)

create_bootstrap_user(
    "ADMIN_USERNAME",
    "ADMIN_PASSWORD",
    "admin"
)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000)),
        debug=False
    )
