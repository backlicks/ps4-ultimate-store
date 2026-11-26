
import os
import re
import json
import hmac
import secrets
import sqlite3
import urllib.request
import urllib.parse
import logging

from pathlib import Path
from datetime import timedelta
from functools import wraps

from flask import Flask, request, jsonify, session, g
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash


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
    "https://ps4-ultimate-store-frontend.onrender.com"
).strip().rstrip("/")

SECRET_KEY = os.getenv("SECRET_KEY")

if not SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is missing. Set a permanent SECRET_KEY in Render."
    )


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

diag_logger = logging.getLogger("registration_diagnostics")


app = Flask(__name__)

app.config.update(
    SECRET_KEY=SECRET_KEY,
    SESSION_COOKIE_NAME="ps4_ultimate_store_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="None",
    SESSION_COOKIE_PATH="/",
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
    ],
    expose_headers=[
        "X-Diagnostic-ID"
    ]
)


# ==========================================
# REGISTRATION DIAGNOSTICS
# ==========================================

@app.before_request
def diagnostic_request_start():
    if request.path in (
        "/api/register",
        "/api/csrf"
    ):
        g.diag_id = secrets.token_hex(4)

        diag_logger.info(
            "REQUEST_START id=%s method=%s path=%s origin=%s cookie=%s",
            g.diag_id,
            request.method,
            request.path,
            request.headers.get("Origin", "missing"),
            bool(request.cookies)
        )


@app.after_request
def diagnostic_request_end(response):
    diag_id = getattr(g, "diag_id", None)

    if diag_id:
        response.headers["X-Diagnostic-ID"] = diag_id

        diag_logger.info(
            "REQUEST_END id=%s method=%s path=%s status=%s cors_origin=%s",
            diag_id,
            request.method,
            request.path,
            response.status_code,
            response.headers.get(
                "Access-Control-Allow-Origin",
                "missing"
            )
        )

    return response


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

    try:
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

    finally:
        connection.close()


# ==========================================
# INITIAL CATALOG
# ==========================================

INITIAL_CATALOG = [
    {
        "name": "SHAREfactory",
        "category": "Медиа",
        "description": "Редактирование игровых клипов и скриншотов прямо на PS4.",
        "version": "PS Store",
        "compatibility": "PlayStation 4",
        "source": "PlayStation",
        "url": "https://www.playstation.com/en-us/support/hardware/ps4/",
        "icon": "🎬",
        "platforms": ["PS4"]
    },
    {
        "name": "PS Remote Play",
        "category": "Утилиты",
        "description": "Играй на своей PS4 через смартфон, планшет или компьютер.",
        "version": "Актуальная версия на сайте",
        "compatibility": "PS4 и совместимое устройство",
        "source": "PlayStation Remote Play",
        "url": "https://remoteplay.dl.playstation.net/remoteplay/",
        "icon": "📱",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "Media Player",
        "category": "Медиа",
        "description": "Воспроизведение поддерживаемых видео, музыки и фотографий.",
        "version": "PS Store",
        "compatibility": "PlayStation 4",
        "source": "PlayStation",
        "url": "https://www.playstation.com/en-us/support/hardware/ps4/",
        "icon": "▶️",
        "platforms": ["PS4"]
    },
    {
        "name": "Spotify",
        "category": "Медиа",
        "description": "Музыка и плейлисты во время игры и на других устройствах.",
        "version": "Актуальная версия",
        "compatibility": "Зависит от региона и устройства",
        "source": "Spotify",
        "url": "https://www.spotify.com/download/",
        "icon": "🎵",
        "platforms": ["PS4", "Телефоны", "ПК"]
    },
    {
        "name": "PlayStation App",
        "category": "Утилиты",
        "description": "Официальное приложение-компаньон PlayStation для смартфонов.",
        "version": "Актуальная версия",
        "compatibility": "Android / iOS",
        "source": "PlayStation",
        "url": "https://www.playstation.com/en-us/playstation-app/",
        "icon": "🎮",
        "platforms": ["Телефоны"]
    },
    {
        "name": "GnmScale",
        "category": "Homebrew",
        "description": "Homebrew-приложение GnmScale.",
        "version": "PKG",
        "compatibility": "Требования не подтверждены",
        "source": "MediaFire",
        "url": "https://www.mediafire.com/file/lnrwap92lqeli6y/IV0000-GNMS00001_00-GNMSCALE00000000-nofg.pkg/file",
        "icon": "✨",
        "platforms": ["PS4"]
    },
    {
        "name": "Jobee'sLair",
        "category": "Homebrew",
        "description": "Homebrew-инструмент для PS4.",
        "version": "1.1.4",
        "compatibility": "Проверь требования разработчика",
        "source": "GitHub",
        "url": "https://github.com/JVK3R/JobeesLair-PS4/releases",
        "icon": "🧰",
        "platforms": ["PS4"]
    },
    {
        "name": "Apollo Save Tool",
        "category": "Утилиты",
        "description": "Управление и резервное копирование сохранений игр.",
        "version": "Последняя версия",
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
        "version": "Последняя версия",
        "compatibility": "PS4 homebrew",
        "source": "GitHub",
        "url": "https://github.com/Lapy055/PS4-Xplorer/releases",
        "icon": "📁",
        "platforms": ["PS4"]
    },
    {
        "name": "Itemzflow",
        "category": "Утилиты",
        "description": "Менеджер библиотеки приложений и игр.",
        "version": "Последняя версия",
        "compatibility": "PS4 homebrew",
        "source": "GitHub",
        "url": "https://github.com/LightningMods/Itemzflow",
        "icon": "🗂️",
        "platforms": ["PS4"]
    },
    {
        "name": "PS MTP Explorer",
        "category": "Утилиты",
        "description": "Обмен файлами между PS4 и совместимыми устройствами.",
        "version": "Последняя версия",
        "compatibility": "PS4 homebrew; ПК",
        "source": "GitHub",
        "url": "https://github.com/nueex/ps-mtp-explorer",
        "icon": "🔌",
        "platforms": ["PS4", "ПК"]
    },
    {
        "name": "Homebrew Store",
        "category": "Homebrew",
        "description": "Каталог homebrew-приложений сообщества.",
        "version": "Последняя версия",
        "compatibility": "PS4 homebrew",
        "source": "ConsoleMods",
        "url": "https://consolemods.org/wiki/PS4:Recommended_Setup",
        "icon": "🏪",
        "platforms": ["PS4"]
    },
    {
        "name": "NULL Store",
        "category": "Homebrew",
        "description": "Сторонний каталог приложений для PS4.",
        "version": "1.0.0",
        "compatibility": "Совместимость не подтверждена",
        "source": "GitHub Releases",
        "url": "https://github.com/lastrowcritic/null-store-ps4/releases/download/v1.0.0/NULL_store-PS4-1.0.pkg",
        "icon": "⬇️",
        "platforms": ["PS4"]
    },
    {
        "name": "Playtube",
        "category": "Медиа",
        "description": "YouTube-клиент для PS4.",
        "version": "Последняя версия",
        "compatibility": "Требования проекта",
        "source": "GitHub",
        "url": "https://github.com/LowganDev/PlaytubePS4/releases",
        "icon": "📺",
        "platforms": ["PS4"]
    },
    {
        "name": "Playfin",
        "category": "Медиа",
        "description": "Клиент Jellyfin для личной медиатеки.",
        "version": "Последняя версия",
        "compatibility": "Требуется сервер Jellyfin",
        "source": "GitHub",
        "url": "https://github.com/LowganDev/PlayfinPS4/releases",
        "icon": "🧊",
        "platforms": ["PS4"]
    },
    {
        "name": "JoPlay",
        "category": "Медиа",
        "description": "Медиаплеер для PS4.",
        "version": "Последняя версия",
        "compatibility": "Проверь требования проекта",
        "source": "GitHub",
        "url": "https://github.com/JVK3R/JoPlay/releases",
        "icon": "🎞️",
        "platforms": ["PS4"]
    },
    {
        "name": "PS4 Ambilight",
        "category": "Утилиты",
        "description": "Проект подсветки телевизора с совместимым оборудованием.",
        "version": "Последняя версия",
        "compatibility": "Нужно совместимое оборудование",
        "source": "GitHub",
        "url": "https://github.com/mitchshimy/PS4-Ambilight/releases",
        "icon": "💡",
        "platforms": ["PS4"]
    },
    {
        "name": "orbisRPC",
        "category": "Утилиты",
        "description": "Discord Rich Presence для PS4.",
        "version": "Последняя версия",
        "compatibility": "Проверь требования проекта",
        "source": "GitHub",
        "url": "https://github.com/SirHumza/orbisRPC",
        "icon": "🟣",
        "platforms": ["PS4"]
    },
    {
        "name": "RemotePad NG",
        "category": "Утилиты",
        "description": "Управление PS4 с совместимых устройств.",
        "version": "Последняя версия",
        "compatibility": "Требуется совместимая среда",
        "source": "GitHub",
        "url": "https://github.com/MoHadiShibli/remotePad-ng",
        "icon": "🎮",
        "platforms": ["PS4"]
    },
    {
        "name": "EmuSuNi",
        "category": "Эмуляторы",
        "description": "Лончер ретро-консолей и аркадных систем.",
        "version": "Последняя версия",
        "compatibility": "PS4 homebrew",
        "source": "GitHub",
        "url": "https://github.com/arturofuentes1988/EmuSuNi/releases",
        "icon": "🕹️",
        "platforms": ["PS4"]
    },
    {
        "name": "Iconit (PS4)",
        "category": "Утилиты",
        "description": "Windows-программа для настройки иконок и аватаров PS4.",
        "version": "Последняя версия",
        "compatibility": "Windows",
        "source": "GitHub",
        "url": "https://github.com/OfficialAhmed/Iconit-PS4",
        "icon": "🖼️",
        "platforms": ["ПК"]
    },
    {
        "name": "Remote PKG Installer",
        "category": "Утилиты",
        "description": "Инструмент сетевой передачи установочных файлов.",
        "version": "Последняя версия",
        "compatibility": "Проверь требования проекта",
        "source": "GitHub",
        "url": "https://github.com/AldySan/ps4_remote_pkg_installer_nextgen/releases",
        "icon": "📡",
        "platforms": ["PS4", "ПК"]
    },
    {
        "name": "RetroArch (PS4)",
        "category": "Эмуляторы",
        "description": "Мультисистемная оболочка с ядрами NES, SNES, Genesis, GBA и других платформ.",
        "version": "Зависит от сборки",
        "compatibility": "Неофициальный порт PS4",
        "source": "RArch PS4",
        "url": "https://github.com/gen04177/rarch-ps4",
        "icon": "🕹️",
        "platforms": ["PS4"]
    },
    {
        "name": "PCSX ReARMed",
        "category": "Эмуляторы",
        "description": "Эмуляционное ядро PlayStation 1 для RetroArch.",
        "version": "Зависит от сборки",
        "compatibility": "Зависит от сборки RetroArch",
        "source": "RArch PS4",
        "url": "https://github.com/gen04177/rarch-ps4",
        "icon": "🎮",
        "platforms": ["PS4"]
    },
    {
        "name": "PPSSPP (PS4)",
        "category": "Эмуляторы",
        "description": "Эмулятор портативной консоли PSP.",
        "version": "Зависит от сборки",
        "compatibility": "Совместимость требует проверки",
        "source": "PS4 RetroBox",
        "url": "https://github.com/danyboy666/ps4-retrobox/releases",
        "icon": "🎯",
        "platforms": ["PS4"]
    },
    {
        "name": "Mupen64Plus",
        "category": "Эмуляторы",
        "description": "Эмуляционное ядро Nintendo 64.",
        "version": "Зависит от сборки",
        "compatibility": "Совместимость требует проверки",
        "source": "PS4 RetroBox",
        "url": "https://github.com/danyboy666/ps4-retrobox/releases",
        "icon": "🟢",
        "platforms": ["PS4"]
    },
    {
        "name": "Flycast",
        "category": "Эмуляторы",
        "description": "Эмулятор Sega Dreamcast.",
        "version": "Не подтверждена",
        "compatibility": "Наличие рабочего порта PS4 не подтверждено",
        "source": "Поиск GitHub",
        "url": "https://github.com/search?q=Flycast+PS4&type=repositories",
        "icon": "💿",
        "platforms": ["PS4"]
    },
    {
        "name": "FinalBurn Neo",
        "category": "Эмуляторы",
        "description": "Ядро для эмуляции аркадных автоматов.",
        "version": "Зависит от сборки",
        "compatibility": "Зависит от RetroArch-сборки",
        "source": "PS4 RetroBox",
        "url": "https://github.com/danyboy666/ps4-retrobox/releases",
        "icon": "👾",
        "platforms": ["PS4"]
    },
    {
        "name": "Snes9x",
        "category": "Эмуляторы",
        "description": "Эмуляционное ядро Super Nintendo.",
        "version": "Зависит от сборки",
        "compatibility": "Зависит от RetroArch-сборки",
        "source": "PS4 RetroBox",
        "url": "https://github.com/danyboy666/ps4-retrobox/releases",
        "icon": "🟣",
        "platforms": ["PS4"]
    },
    {
        "name": "DobieStation",
        "category": "Эмуляторы",
        "description": "Экспериментальный эмулятор PlayStation 2.",
        "version": "Не подтверждена",
        "compatibility": "Отдельный порт PS4 не подтверждён",
        "source": "Поиск GitHub",
        "url": "https://github.com/search?q=DobieStation+PS4&type=repositories",
        "icon": "⚙️",
        "platforms": ["PS4"]
    },
    {
        "name": "ZArchiver",
        "category": "Утилиты",
        "description": "Архиватор для Android: работа с ZIP и другими архивами.",
        "version": "Google Play",
        "compatibility": "Android",
        "source": "Google Play",
        "url": "https://play.google.com/store/apps/details?id=ru.zdevs.zarchiver",
        "icon": "📦",
        "platforms": ["Телефоны"]
    },
    {
        "name": "VLC",
        "category": "Медиа",
        "description": "Универсальный медиаплеер для видео и музыки.",
        "version": "Последняя версия",
        "compatibility": "Android, iOS, Windows, macOS, Linux",
        "source": "Официальный сайт VLC",
        "url": "https://www.videolan.org/vlc/",
        "icon": "🎬",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "Discord",
        "category": "Утилиты",
        "description": "Общение, голосовые каналы и сообщества.",
        "version": "Последняя версия",
        "compatibility": "Android, iOS, Windows, macOS",
        "source": "Официальный сайт Discord",
        "url": "https://discord.com/download",
        "icon": "💬",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "Telegram",
        "category": "Утилиты",
        "description": "Мессенджер для общения и передачи файлов.",
        "version": "Последняя версия",
        "compatibility": "Android, iOS, Windows, macOS, Linux",
        "source": "Официальный сайт Telegram",
        "url": "https://telegram.org/apps",
        "icon": "✈️",
        "platforms": ["Телефоны", "ПК"]
    },
    {
        "name": "7-Zip",
        "category": "Утилиты",
        "description": "Бесплатный архиватор для Windows.",
        "version": "Последняя версия",
        "compatibility": "Windows",
        "source": "Официальный сайт",
        "url": "https://www.7-zip.org/",
        "icon": "🗜️",
        "platforms": ["ПК"]
    },
    {
        "name": "ShareX",
        "category": "Утилиты",
        "description": "Инструмент для скриншотов и записи экрана Windows.",
        "version": "Последняя версия",
        "compatibility": "Windows",
        "source": "Официальный сайт",
        "url": "https://getsharex.com/",
        "icon": "📸",
        "platforms": ["ПК"]
    },
    {
        "name": "GIMP",
        "category": "Утилиты",
        "description": "Бесплатный редактор изображений.",
        "version": "Последняя версия",
        "compatibility": "Windows, macOS, Linux",
        "source": "Официальный сайт",
        "url": "https://www.gimp.org/",
        "icon": "🎨",
        "platforms": ["ПК"]
    },
    {
        "name": "Android Studio",
        "category": "Утилиты",
        "description": "Среда разработки Android-приложений.",
        "version": "Последняя версия",
        "compatibility": "Windows, macOS, Linux",
        "source": "Официальный сайт",
        "url": "https://developer.android.com/studio",
        "icon": "🤖",
        "platforms": ["ПК"]
    }
]


def seed_catalog():
    connection = sqlite3.connect(DB_PATH)

    try:
        for item in INITIAL_CATALOG:
            exists = connection.execute(
                "SELECT id FROM catalog WHERE name = ? LIMIT 1",
                (item["name"],)
            ).fetchone()

            if exists:
                continue

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
                json.dumps(
                    item["platforms"],
                    ensure_ascii=False
                )
            ))

        connection.commit()

    except Exception:
        connection.rollback()
        raise

    finally:
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
        print(
            f"WARNING: {username_env} password must be at least 12 characters."
        )
        return

    connection = sqlite3.connect(DB_PATH)

    try:
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

    finally:
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
        diag_id = getattr(g, "diag_id", "no-id")

        if not token or not expected:
            diag_logger.warning(
                "CSRF_FAILED id=%s header_present=%s session_token_present=%s",
                diag_id,
                bool(token),
                bool(expected)
            )

            return response_error(
                "CSRF-токен отсутствует",
                403
            )

        if not hmac.compare_digest(token, expected):
            diag_logger.warning(
                "CSRF_MISMATCH id=%s",
                diag_id
            )

            return response_error(
                "Неверный CSRF-токен",
                403
            )

        diag_logger.info(
            "CSRF_OK id=%s",
            diag_id
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


def notify_owner(message):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    owner_id = os.getenv("OWNER_ID")

    if not token or not owner_id:
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"

        payload = urllib.parse.urlencode({
            "chat_id": owner_id,
            "text": message
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
    session.permanent = True

    token = session.get("csrf_token")

    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token

    response = jsonify({
        "csrf_token": token
    })

    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"

    return response


@app.route("/api/csrf-debug", methods=["GET"])
def csrf_debug():
    cookie_name = app.config["SESSION_COOKIE_NAME"]

    return jsonify({
        "cookie_received": bool(
            request.cookies.get(cookie_name)
        ),
        "session_has_csrf": bool(
            session.get("csrf_token")
        ),
        "session_has_user": bool(
            session.get("user_id")
        ),
        "frontend_origin": FRONTEND_ORIGIN,
        "status": "ok"
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
    diag_id = getattr(g, "diag_id", "no-id")

    diag_logger.info(
        "REGISTER_ENTER id=%s content_type=%s",
        diag_id,
        request.content_type
    )

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            diag_logger.warning(
                "REGISTER_BAD_JSON id=%s",
                diag_id
            )

            return response_error(
                "Некорректные данные регистрации",
                400
            )

        username = str(data.get("username", "")).strip()
        password = str(data.get("password", ""))
        avatar = str(data.get("avatar", "🎮"))[:10]

        diag_logger.info(
            "REGISTER_DATA_OK id=%s username_length=%s password_length=%s",
            diag_id,
            len(username),
            len(password)
        )

        if not validate_username(username):
            diag_logger.warning(
                "REGISTER_INVALID_USERNAME id=%s",
                diag_id
            )

            return response_error(
                "Ник должен содержать 3–20 символов: латиница, цифры, _ . -"
            )

        if len(password) < 10 or len(password) > 128:
            diag_logger.warning(
                "REGISTER_INVALID_PASSWORD_LENGTH id=%s",
                diag_id
            )

            return response_error(
                "Пароль должен содержать от 10 до 128 символов"
            )

        exists = db().execute(
            "SELECT id FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if exists:
            diag_logger.warning(
                "REGISTER_USERNAME_TAKEN id=%s",
                diag_id
            )

            return response_error(
                "Этот ник уже занят",
                409
            )

        diag_logger.info(
            "REGISTER_VALIDATION_OK id=%s",
            diag_id
        )

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

        diag_logger.info(
            "REGISTER_DATABASE_INSERT_OK id=%s",
            diag_id
        )

        user = db().execute(
            "SELECT * FROM users WHERE id = ?",
            (cursor.lastrowid,)
        ).fetchone()

        if not user:
            diag_logger.error(
                "REGISTER_USER_NOT_FOUND_AFTER_INSERT id=%s",
                diag_id
            )

            return response_error(
                "Не удалось получить созданный аккаунт",
                500
            )

        session.clear()
        session.permanent = True
        session["user_id"] = user["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)

        diag_logger.info(
            "REGISTER_SESSION_CREATED id=%s",
            diag_id
        )

        notify_owner(
            f"Новый пользователь PS4 ULTIMATE STORE: {username}"
        )

        diag_logger.info(
            "REGISTER_SUCCESS id=%s",
            diag_id
        )

        return jsonify({
            "user": public_user(user),
            "csrf_token": session["csrf_token"],
            "diagnostic_id": diag_id
        }), 201

    except sqlite3.IntegrityError:
        db().rollback()

        diag_logger.exception(
            "REGISTER_DATABASE_INTEGRITY_ERROR id=%s",
            diag_id
        )

        return response_error(
            "Не удалось создать аккаунт",
            409
        )

    except Exception:
        db().rollback()

        diag_logger.exception(
            "REGISTER_UNEXPECTED_ERROR id=%s",
            diag_id
        )

        return response_error(
            "Внутренняя ошибка регистрации",
            500
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
        raise ValueError(
            "Ссылка должна начинаться с http:// или https://"
        )

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
                "user_id": row["user_id"],
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
            "user_id": g.current_user["id"],
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
            "user_id": ticket["user_id"],
            "category": ticket["category"],
            "description": ticket["description"],
            "status": ticket["status"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "messages": [
                {
                    "id": m["id"],
                    "user_id": m["user_id"],
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
                "user_id": r["user_id"],
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
            "user_id": ticket["user_id"],
            "username": ticket["username"],
            "category": ticket["category"],
            "description": ticket["description"],
            "status": ticket["status"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "messages": [
                {
                    "id": m["id"],
                    "user_id": m["user_id"],
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
