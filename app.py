import os
import re
import json
import hmac
import secrets
import sqlite3
from pathlib import Path
from functools import wraps

from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DATABASE_PATH", ROOT / "data" / "store.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", ""),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
    PERMANENT_SESSION_LIFETIME=60 * 60 * 24 * 14,
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
)
if len(app.config["SECRET_KEY"]) < 32:
    raise RuntimeError("Укажи SECRET_KEY длиной не менее 32 символов в переменных окружения.")

USERNAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё0-9_.-]{3,20}$")


def connect_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db():
    with connect_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            avatar TEXT NOT NULL DEFAULT '🎮',
            role TEXT NOT NULL DEFAULT 'user',
            is_banned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS catalog (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            icon TEXT NOT NULL DEFAULT '📦',
            category TEXT NOT NULL,
            platforms_json TEXT NOT NULL,
            description TEXT NOT NULL,
            version TEXT NOT NULL DEFAULT '',
            compatibility TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL,
            is_download INTEGER NOT NULL DEFAULT 0
        );
        """)
        count = db.execute("SELECT COUNT(*) FROM catalog").fetchone()[0]
        if count == 0:
            seed = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))
            for item in seed:
                db.execute("""
                    INSERT OR IGNORE INTO catalog
                    (name, icon, category, platforms_json, description, version,
                     compatibility, source, url, is_download)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    item["name"], item.get("icon", "📦"), item["category"],
                    json.dumps(item["platforms"], ensure_ascii=False),
                    item["desc"], item.get("version", ""), item.get("compat", ""),
                    item.get("source", ""), item["url"], int(bool(item.get("download", False)))
                ))

        # Optional first admin. Credentials are supplied only through environment variables.
        admin_name = os.environ.get("ADMIN_USERNAME", "").strip()
        admin_password = os.environ.get("ADMIN_PASSWORD", "")
        if admin_name and admin_password:
            if not USERNAME_RE.fullmatch(admin_name):
                raise RuntimeError("ADMIN_USERNAME: используй 3–20 букв, цифр, точек, _ или -.")
            if len(admin_password) < 12:
                raise RuntimeError("ADMIN_PASSWORD должен содержать минимум 12 символов.")
            existing = db.execute("SELECT id FROM users WHERE username = ?", (admin_name,)).fetchone()
            if not existing:
                db.execute(
                    "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'admin')",
                    (admin_name, generate_password_hash(admin_password))
                )


def user_from_session():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with connect_db() as db:
        row = db.execute(
            "SELECT id, username, avatar, role, is_banned FROM users WHERE id = ?",
            (user_id,)
        ).fetchone()
    if not row or row["is_banned"]:
        session.clear()
        return None
    return dict(row)


def csrf_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        expected = session.get("csrf_token", "")
        supplied = request.headers.get("X-CSRF-Token", "")
        if not expected or not supplied or not hmac.compare_digest(expected, supplied):
            return jsonify(error="CSRF-проверка не пройдена. Обнови страницу и повтори."), 403
        return fn(*args, **kwargs)
    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = user_from_session()
        if not user:
            return jsonify(error="Требуется вход в аккаунт."), 401
        return fn(user, *args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        user = user_from_session()
        if not user:
            return jsonify(error="Требуется вход в аккаунт."), 401
        if user["role"] != "admin":
            return jsonify(error="Недостаточно прав."), 403
        return fn(user, *args, **kwargs)
    return wrapper


def clean_text(value, max_len, field):
    if not isinstance(value, str):
        raise ValueError(f"Поле {field} должно быть текстом.")
    value = value.strip()
    if not value or len(value) > max_len:
        raise ValueError(f"Поле {field}: допустимо от 1 до {max_len} символов.")
    return value


def catalog_item(row):
    return {
        "id": row["id"], "name": row["name"], "icon": row["icon"],
        "category": row["category"], "platforms": json.loads(row["platforms_json"]),
        "desc": row["description"], "version": row["version"],
        "compat": row["compatibility"], "source": row["source"],
        "url": row["url"], "download": bool(row["is_download"])
    }


@app.get("/")
def home():
    return send_from_directory(ROOT, "index.html")


@app.get("/api/health")
def health():
    return jsonify(status="ok", service="PS4 ULTIMATE STORE API", version="1.0.0")


@app.get("/api/csrf")
def csrf():
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return jsonify(csrf_token=token)


@app.get("/api/me")
def me():
    return jsonify(user=user_from_session())


@app.post("/api/register")
@csrf_required
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    avatar = data.get("avatar", "🎮")
    if not USERNAME_RE.fullmatch(username):
        return jsonify(error="Ник: 3–20 символов, только буквы, цифры, точка, _ или -."), 400
    if not isinstance(password, str) or len(password) < 10 or len(password) > 128:
        return jsonify(error="Пароль должен содержать от 10 до 128 символов."), 400
    if not isinstance(avatar, str) or len(avatar) > 12:
        avatar = "🎮"
    try:
        with connect_db() as db:
            cur = db.execute(
                "INSERT INTO users (username, password_hash, avatar) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), avatar)
            )
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify(error="Такой ник уже занят."), 409
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(user={"id": user_id, "username": username, "avatar": avatar, "role": "user"}), 201


@app.post("/api/login")
@csrf_required
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = data.get("password", "")
    with connect_db() as db:
        row = db.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if not row or not isinstance(password, str) or not check_password_hash(row["password_hash"], password):
        return jsonify(error="Неверный ник или пароль."), 401
    if row["is_banned"]:
        return jsonify(error="Аккаунт заблокирован. Обратись в поддержку."), 403
    session.clear()
    session["user_id"] = row["id"]
    session.permanent = True
    session["csrf_token"] = secrets.token_urlsafe(32)
    return jsonify(user={"id": row["id"], "username": row["username"],
                         "avatar": row["avatar"], "role": row["role"]})


@app.post("/api/logout")
@csrf_required
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/catalog")
def get_catalog():
    with connect_db() as db:
        rows = db.execute("SELECT * FROM catalog ORDER BY id").fetchall()
    return jsonify(items=[catalog_item(row) for row in rows])


@app.get("/api/admin/users")
@admin_required
def admin_users(_user):
    with connect_db() as db:
        rows = db.execute(
            "SELECT id, username, role, is_banned, created_at FROM users ORDER BY id DESC"
        ).fetchall()
    return jsonify(users=[dict(row) for row in rows])


@app.patch("/api/admin/users/<int:user_id>")
@csrf_required
@admin_required
def admin_update_user(_admin, user_id):
    data = request.get_json(silent=True) or {}
    banned = data.get("is_banned")
    if not isinstance(banned, bool):
        return jsonify(error="Ожидается is_banned: true или false."), 400
    with connect_db() as db:
        target = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return jsonify(error="Пользователь не найден."), 404
        if target["role"] == "admin":
            return jsonify(error="Нельзя заблокировать администратора через этот endpoint."), 400
        db.execute("UPDATE users SET is_banned = ? WHERE id = ?", (int(banned), user_id))
    return jsonify(ok=True, is_banned=banned)


@app.delete("/api/admin/users/<int:user_id>")
@csrf_required
@admin_required
def admin_delete_user(_admin, user_id):
    with connect_db() as db:
        target = db.execute("SELECT id, role FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            return jsonify(error="Пользователь не найден."), 404
        if target["role"] == "admin":
            return jsonify(error="Удаление администратора через API запрещено."), 400
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return jsonify(ok=True)


@app.post("/api/admin/catalog")
@csrf_required
@admin_required
def admin_create_catalog(_admin):
    data = request.get_json(silent=True) or {}
    try:
        name = clean_text(data.get("name"), 80, "name")
        category = clean_text(data.get("category"), 40, "category")
        desc = clean_text(data.get("desc"), 500, "desc")
        url = clean_text(data.get("url"), 500, "url")
        if not re.match(r"^https://", url, re.I):
            raise ValueError("Ссылка должна начинаться с https://.")
        platforms = data.get("platforms", [])
        if not isinstance(platforms, list) or not platforms or len(platforms) > 5:
            raise ValueError("Укажи от 1 до 5 платформ.")
        platforms = [clean_text(x, 30, "platforms") for x in platforms]
        icon = data.get("icon", "📦")
        version = data.get("version", "")
        compat = data.get("compat", "")
        source = data.get("source", "")
        download = bool(data.get("download", False))
        with connect_db() as db:
            cur = db.execute("""
                INSERT INTO catalog
                (name, icon, category, platforms_json, description, version,
                 compatibility, source, url, is_download)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (name, str(icon)[:12], category, json.dumps(platforms, ensure_ascii=False),
                  desc, str(version)[:60], str(compat)[:160], str(source)[:80],
                  url, int(download)))
            item_id = cur.lastrowid
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    except sqlite3.IntegrityError:
        return jsonify(error="Приложение с таким названием уже есть."), 409
    return jsonify(id=item_id, ok=True), 201


@app.patch("/api/admin/catalog/<int:item_id>")
@csrf_required
@admin_required
def admin_update_catalog(_admin, item_id):
    data = request.get_json(silent=True) or {}
    allowed = {"name", "icon", "category", "desc", "url", "platforms",
               "version", "compat", "source", "download"}
    if not data or set(data) - allowed:
        return jsonify(error="Переданы пустые или неизвестные поля."), 400
    with connect_db() as db:
        row = db.execute("SELECT * FROM catalog WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return jsonify(error="Запись не найдена."), 404
        item = catalog_item(row)
        item.update(data)
        try:
            name = clean_text(item["name"], 80, "name")
            category = clean_text(item["category"], 40, "category")
            desc = clean_text(item["desc"], 500, "desc")
            url = clean_text(item["url"], 500, "url")
            if not re.match(r"^https://", url, re.I):
                raise ValueError("Ссылка должна начинаться с https://.")
            platforms = item["platforms"]
            if not isinstance(platforms, list) or not platforms:
                raise ValueError("Укажи хотя бы одну платформу.")
            db.execute("""
                UPDATE catalog SET name=?, icon=?, category=?, platforms_json=?,
                description=?, version=?, compatibility=?, source=?, url=?, is_download=?
                WHERE id=?
            """, (name, str(item.get("icon", "📦"))[:12], category,
                  json.dumps(platforms, ensure_ascii=False), desc, str(item.get("version", ""))[:60],
                  str(item.get("compat", ""))[:160], str(item.get("source", ""))[:80],
                  url, int(bool(item.get("download", False))), item_id))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        except sqlite3.IntegrityError:
            return jsonify(error="Такое название уже используется."), 409
    return jsonify(ok=True)


@app.delete("/api/admin/catalog/<int:item_id>")
@csrf_required
@admin_required
def admin_delete_catalog(_admin, item_id):
    with connect_db() as db:
        cur = db.execute("DELETE FROM catalog WHERE id = ?", (item_id,))
        if cur.rowcount == 0:
            return jsonify(error="Запись не найдена."), 404
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
