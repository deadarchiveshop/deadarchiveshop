from flask import Flask, render_template_string, request, jsonify, redirect, session, url_for
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import os
import time
import re as _re
import secrets
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from data.storage import load_products_from_file, save_products_to_file

# Cloudinary (загрузка фото товаров)
import cloudinary
import cloudinary.uploader

app = Flask(__name__)
app.secret_key = 'dead_archive_secret_key_2026'
app.config['MAX_CONTENT_LENGTH'] = 25 * 1024 * 1024  # 25 MB

cloudinary.config(
    cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
    api_key=os.environ.get("CLOUDINARY_API_KEY", ""),
    api_secret=os.environ.get("CLOUDINARY_API_SECRET", ""),
    secure=True,
)

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "gif"}


def allowed_image(filename):
    return "." in (filename or "") and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def collect_slot_files():
    """Собирает файлы из отдельных полей images_1..images_4 (порядок = очередь)."""
    files = []
    for i in range(1, 5):
        f = request.files.get(f"images_{i}")
        if f and getattr(f, "filename", None):
            files.append(f)
    # на всякий случай старый multiple input
    for f in request.files.getlist("images"):
        if f and getattr(f, "filename", None):
            files.append(f)
    return files


def save_uploaded_images(file_list):
    """Загружает файлы в Cloudinary в высоком качестве, возвращает HTTPS URL."""
    urls = []
    errors = []
    # Проверка, что ключи вообще заданы
    if not (os.environ.get("CLOUDINARY_CLOUD_NAME") and os.environ.get("CLOUDINARY_API_KEY") and os.environ.get("CLOUDINARY_API_SECRET")):
        print("cloudinary upload error: missing env keys (CLOUDINARY_*)")
        return urls

    for f in file_list:
        if not f or not getattr(f, "filename", None):
            continue
        if not allowed_image(f.filename):
            errors.append(f"неподдерживаемый формат: {f.filename}")
            continue
        try:
            result = cloudinary.uploader.upload(
                f,
                folder="dead_archive",
                resource_type="image",
                # Максимальное качество, без агрессивного сжатия
                quality="auto:best",
                fetch_format="auto",
                colorspace="srgb",
                # Не режем исходник по размеру
                eager_async=False,
                overwrite=False,
                unique_filename=True,
                use_filename=True,
                filename_override=None,
            )
            # Берём оригинал в лучшем виде
            url = result.get("secure_url") or result.get("url")
            if url:
                # Убираем возможные слабые трансформации из URL, оставляем полный ресурс
                # secure_url уже ведёт на оригинал с q_auto:best при upload
                urls.append(url)
        except Exception as e:
            msg = str(e)
            print("cloudinary upload error:", msg)
            errors.append(msg)
    if errors:
        # Сохраняем последнюю ошибку, чтобы показать в админке
        save_uploaded_images.last_error = "; ".join(errors[:3])
    else:
        save_uploaded_images.last_error = ""


def strip_dead_image_urls(images_str):
    """Убирает мёртвые хостинги (kappa.lol и т.п.) из строки картинок."""
    if not images_str:
        return ""
    dead = ("kappa.lol", "segs.lol")
    parts = [u.strip() for u in str(images_str).replace("\r", "\n").replace("\n", ",").split(",")]
    keep = []
    for u in parts:
        if not u:
            continue
        low = u.lower()
        if any(d in low for d in dead):
            continue
        keep.append(u)
    return ", ".join(keep)


def purge_dead_product_images():
    """Чистит kappa.lol из всех товаров в БД + products.json."""
    changed = 0
    try:
        for p in Product.query.all():
            cleaned = strip_dead_image_urls(p.images_str or "")
            if cleaned != (p.images_str or ""):
                p.images_str = cleaned
                changed += 1
        if changed:
            db.session.commit()
            print(f"purged kappa images from {changed} products")
        try:
            sync_products_catalog()
        except Exception as e:
            print("sync after purge:", e)
    except Exception as e:
        print("purge_dead_product_images:", e)
    return changed

    return urls

save_uploaded_images.last_error = ""


# === РЕЖИМ САЙТА ===
# False = закрыт (заглушка), True = открыт
SITE_OPEN = False
# Пароль для входа пока закрыт. Пустая строка "" = без пароля, только заглушка
SITE_ACCESS_PASSWORD = "676767"

# Ссылка на Telegram — поменяй на свой канал или менеджера
TELEGRAM_URL = "https://t.me/deadarchivemanager"

# === Telegram уведомления о заказах ===
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8947851904:AAEsjW-XSwuNTsYp-3inKWIPhDCrUxl01hA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8879626515")

# === Email (верификация) ===
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "deadarchiveshop@gmail.com")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "dcylqdpviefpdjkl")
SMTP_FROM = os.environ.get("SMTP_FROM", "deadarchiveshop@gmail.com")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://your-domain.com")


# Настройка базы данных SQLite
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'archive.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    name_en = db.Column(db.String(200), nullable=True)
    category = db.Column(db.String(50), nullable=False)
    price_eur = db.Column(db.Integer, nullable=False)
    price_uah = db.Column(db.Integer, nullable=False)
    images_str = db.Column(db.Text, nullable=False)
    colors_str = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    description_en = db.Column(db.Text, nullable=True)
    sizes_str = db.Column(db.String(200), nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    in_stock = db.Column(db.Boolean, default=True, nullable=False)
    views = db.Column(db.Integer, default=0, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "name_en": self.name_en or self.name,
            "category": self.category,
            "price_eur": self.price_eur,
            "price_uah": self.price_uah,
            "images": [i.strip() for i in self.images_str.split(',') if i.strip()],
            "colors": [c.strip() for c in self.colors_str.split(',') if c.strip()],
            "description": self.description or "",
            "description_en": self.description_en or self.description or "",
            "sizes": [s.strip() for s in self.sizes_str.split(',') if s.strip()],
            "sort_order": self.sort_order or 0,
            "in_stock": bool(self.in_stock) if self.in_stock is not None else True,
            "views": self.views or 0
        }

class Order(db.Model):
    id = db.Column(db.String(50), primary_key=True)
    items_json = db.Column(db.Text, nullable=False)
    contacts_json = db.Column(db.Text, nullable=False)
    time_str = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(30), default='В обработке', nullable=False)
    note = db.Column(db.Text, nullable=True)
    user_id = db.Column(db.Integer, nullable=True)

    def to_dict(self):
        import json
        return {
            "id": self.id,
            "items": json.loads(self.items_json),
            "contacts": json.loads(self.contacts_json),
            "time": self.time_str,
            "status": self.status,
            "note": self.note or ""
        }

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    telegram = db.Column(db.String(100), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    created_str = db.Column(db.String(50), nullable=True)
    email_verified = db.Column(db.Boolean, default=False, nullable=False)
    verify_token = db.Column(db.String(64), nullable=True)
    favorites_json = db.Column(db.Text, nullable=True)
    preferred_size = db.Column(db.String(20), nullable=True)

    def get_favorites(self):
        try:
            data = json.loads(self.favorites_json or "[]")
            return [int(x) for x in data] if isinstance(data, list) else []
        except Exception:
            return []

    def set_favorites(self, ids):
        clean = []
        for x in ids:
            try:
                clean.append(int(x))
            except Exception:
                pass
        seen = set()
        out = []
        for i in clean:
            if i not in seen:
                seen.add(i)
                out.append(i)
        self.favorites_json = json.dumps(out)

    def to_public(self):
        return {
            "id": self.id,
            "email": self.email,
            "telegram": self.telegram or "",
            "phone": self.phone or "",
            "email_verified": bool(self.email_verified),
            "favorites": self.get_favorites(),
            "preferred_size": self.preferred_size or "",
        }

class LookbookLook(db.Model):
    """Admin-managed outfit in the public lookbook."""
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), nullable=False, default='ARCHIVE FILE')
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    product_ids_json = db.Column(db.Text, nullable=False, default='[]')
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, nullable=False, default=True)

    def product_ids(self):
        try:
            data = json.loads(self.product_ids_json or '[]')
            return [int(x) for x in data]
        except Exception:
            return []

class AdminLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    time_str = db.Column(db.String(50), nullable=False)
    action = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, nullable=True)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    kind = db.Column(db.String(40), default='info')
    is_read = db.Column(db.Boolean, default=False, nullable=False)
    created_str = db.Column(db.String(50), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "body": self.body or "",
            "kind": self.kind or "info",
            "is_read": bool(self.is_read),
            "created_str": self.created_str or "",
        }

class PromoCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False)
    discount_percent = db.Column(db.Integer, nullable=False, default=5)
    active = db.Column(db.Boolean, default=True, nullable=False)
    first_order_only = db.Column(db.Boolean, default=False, nullable=False)
    uses_count = db.Column(db.Integer, default=0, nullable=False)
    max_uses = db.Column(db.Integer, default=0, nullable=False)
    created_str = db.Column(db.String(50), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "discount_percent": int(self.discount_percent or 0),
            "active": bool(self.active),
            "first_order_only": bool(self.first_order_only),
            "uses_count": int(self.uses_count or 0),
            "max_uses": int(self.max_uses or 0),
            "created_str": self.created_str or "",
        }


def sync_products_catalog():
    try:
        items = Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()
        save_products_to_file(basedir, items)
    except Exception as e:
        print("sync_products_catalog error:", e)


def seed_default_products():
    if Product.query.count() == 0:
        default_items = load_products_from_file(basedir)
        for idx, item in enumerate(default_items, start=1):
            db.session.add(Product(
                name=item["name"],
                name_en=item.get("name_en") or item["name"],
                category=item["category"],
                price_eur=int(item.get("price_eur", 0)),
                price_uah=int(item.get("price_uah", 0)),
                images_str=strip_dead_image_urls(item.get("images_str", "")),
                colors_str=item.get("colors_str", "Черный"),
                description=item.get("description", ""),
                description_en=item.get("description_en") or item.get("description", ""),
                sizes_str=item.get("sizes_str", "S, M, L, XL"),
                sort_order=int(item.get("sort_order", idx)),
                in_stock=bool(item.get("in_stock", True)),
            ))
        db.session.commit()


with app.app_context():
    try:
        db.create_all()
    except Exception as e:
        print("create_all:", e)
    try:
        from sqlalchemy import text
        cols = [c["name"] for c in db.session.execute(text("PRAGMA table_info(product)")).mappings()]
        if "sort_order" not in cols:
            db.session.execute(text("ALTER TABLE product ADD COLUMN sort_order INTEGER DEFAULT 0 NOT NULL"))
            db.session.commit()
        if "name_en" not in cols:
            db.session.execute(text("ALTER TABLE product ADD COLUMN name_en VARCHAR(200)"))
            db.session.commit()
        if "description_en" not in cols:
            db.session.execute(text("ALTER TABLE product ADD COLUMN description_en TEXT"))
            db.session.commit()
        if "in_stock" not in cols:
            db.session.execute(text("ALTER TABLE product ADD COLUMN in_stock BOOLEAN DEFAULT 1 NOT NULL"))
            db.session.commit()
        if "views" not in cols:
            db.session.execute(text("ALTER TABLE product ADD COLUMN views INTEGER DEFAULT 0 NOT NULL"))
            db.session.commit()
        try:
            ocols = [c["name"] for c in db.session.execute(text("PRAGMA table_info('order')")).mappings()]
            if ocols and "note" not in ocols:
                db.session.execute(text("ALTER TABLE 'order' ADD COLUMN note TEXT"))
                db.session.commit()
            if ocols and "user_id" not in ocols:
                db.session.execute(text("ALTER TABLE 'order' ADD COLUMN user_id INTEGER"))
                db.session.commit()
        except Exception as ee:
            print("order migrate:", ee)
        try:
            ccols = [c["name"] for c in db.session.execute(text("PRAGMA table_info(customer)")).mappings()]
            if ccols and "email_verified" not in ccols:
                db.session.execute(text("ALTER TABLE customer ADD COLUMN email_verified BOOLEAN DEFAULT 0 NOT NULL"))
                db.session.commit()
            if ccols and "verify_token" not in ccols:
                db.session.execute(text("ALTER TABLE customer ADD COLUMN verify_token VARCHAR(64)"))
                db.session.commit()
            if ccols and "favorites_json" not in ccols:
                db.session.execute(text("ALTER TABLE customer ADD COLUMN favorites_json TEXT"))
                db.session.commit()
            if ccols and "preferred_size" not in ccols:
                db.session.execute(text("ALTER TABLE customer ADD COLUMN preferred_size VARCHAR(20)"))
                db.session.commit()
        except Exception as ce:
            print("customer migrate:", ce)
    except Exception as e:
        print("migrate:", e)
    try:
        seed_default_products()
    except Exception as e:
        print("seed:", e)
    try:
        purge_dead_product_images()
    except Exception as e:
        print("purge images:", e)
    try:
        prods = Product.query.order_by(Product.id).all()
        if prods and all((p.sort_order or 0) == 0 for p in prods):
            for i, p in enumerate(prods):
                p.sort_order = i
            db.session.commit()
    except Exception as e:
        print("sort_order init:", e)
    try:
        from sqlalchemy import text as _sqtext
        pcols = [c["name"] for c in db.session.execute(_sqtext("PRAGMA table_info(promo_code)")).mappings()]
        if pcols and "uses_count" not in pcols:
            db.session.execute(_sqtext("ALTER TABLE promo_code ADD COLUMN uses_count INTEGER DEFAULT 0 NOT NULL"))
            db.session.commit()
        if pcols and "max_uses" not in pcols:
            db.session.execute(_sqtext("ALTER TABLE promo_code ADD COLUMN max_uses INTEGER DEFAULT 0 NOT NULL"))
            db.session.commit()
    except Exception as e:
        print("promo migrate:", e)
    try:
        if PromoCode.query.filter_by(code="DEAD").count() == 0:
            db.session.add(PromoCode(
                code="DEAD",
                discount_percent=5,
                active=True,
                first_order_only=True,
                created_str=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))
            db.session.commit()
    except Exception as e:
        print("promo seed:", e)

ACTIVE_SESSIONS = {}
ONLINE_TIMEOUT = 10

def get_real_online_count():
    now = time.time()
    expired_clients = [cid for cid, last_seen in ACTIVE_SESSIONS.items() if now - last_seen > ONLINE_TIMEOUT]
    for cid in expired_clients:
        del ACTIVE_SESSIONS[cid]
    return len(ACTIVE_SESSIONS)

HTML_HEADER = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
  <title>DEAD ARCHIVE // 2026</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@400;500;700;900&family=Pirata+One&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #0b070b;
      --bg-card: #110b11;
      --bg-elevated: #191019;
      --border: #1f1f24;
      --border-hover: #3f3f46;
      --text: #f4f4f5;
      --text-muted: #a1a1aa;
      --accent: #ff00a8;
      --accent-2: #ff2bd6;
      --accent-soft: rgba(255, 0, 168, 0.28);
      --accent-glow: rgba(255, 0, 168, 0.62);
    }
    body {
      font-family: 'Inter', sans-serif;
      background-color: #0b070b;
      background-image: linear-gradient(180deg, #0a0608 0%, #120810 40%, #080406 100%);
      background-size: cover;
      background-position: center center;
      background-attachment: fixed;
      background-repeat: no-repeat;
      color: var(--text);
      position: relative;
    }
    .gothic { font-family:'Cinzel', serif; font-weight: 900; }
    .blood-logo { position: relative; transition: all 0.5s cubic-bezier(0.16, 1, 0.3, 1); }
    .blood-logo:hover { color: #ff8be8 !important; text-shadow: 0 0 22px var(--accent-glow), 0 0 40px rgba(255,0,168,0.65); }
    @media (max-width: 640px) {
      .blood-logo { flex: 0 1 auto; min-width: 0; max-width: 58vw; overflow: visible; }
      .blood-logo h1 { font-family: 'Pirata One','Cinzel',serif !important; font-size: clamp(19px, 5.5vw, 27px) !important; font-weight: 400 !important; line-height: 1 !important; letter-spacing: .035em !important; white-space: nowrap; max-width: 58vw; overflow: visible; text-overflow: clip; color: #fff5fc !important; -webkit-text-stroke: .25px rgba(255,0,168,.55); text-shadow: 0 0 3px rgba(255,255,255,.55), 0 0 10px rgba(255,0,168,.9), 0 0 22px rgba(255,0,168,.5); }
      .blood-logo:after { content: ''; display: block; height: 2px; width: 72%; margin: 4px auto 0; background: linear-gradient(90deg, transparent, #ff00a8 25%, #ff8bd8 50%, #ff00a8 75%, transparent); box-shadow: 0 0 9px rgba(255,0,168,.85); border-radius: 999px; }
    }
    .fade-in { animation: fadeIn 0.55s cubic-bezier(0.16, 1, 0.3, 1) both; opacity: 0; }
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(22px) scale(0.98); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }
    #notif-panel {
      width: min(92vw, 340px);
      max-height: min(70vh, 420px);
      max-height: min(70dvh, 420px);
      flex-direction: column;
      -webkit-transform: translateZ(0);
      transform: translateZ(0);
      background: #130b13 !important;
      border: 1px solid rgba(255,255,255,0.12) !important;
      backdrop-filter: none !important;
      -webkit-backdrop-filter: none !important;
    }
    #notif-panel.hidden { display: none !important; }
    #notif-panel:not(.hidden) { display: flex !important; }
    #notif-list {
      max-height: none !important;
      flex: 1 1 auto;
      overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      overscroll-behavior: contain;
      touch-action: pan-y;
    }
    #notif-list .notif-item-title,
    #notif-list .notif-item-body {
      word-break: break-word;
      overflow-wrap: anywhere;
      white-space: pre-wrap;
      -webkit-hyphens: auto;
      hyphens: auto;
    }
    #notif-list button {
      -webkit-tap-highlight-color: rgba(232,77,155,0.15);
      touch-action: manipulation;
    }
    @media (max-width: 640px) {
      #notif-wrap { position: static; }
      #notif-panel {
        position: fixed !important;
        left: 10px !important;
        right: 10px !important;
        top: auto !important;
        bottom: max(12px, env(safe-area-inset-bottom, 0px)) !important;
        width: auto !important;
        max-width: none !important;
        max-height: min(58vh, 460px) !important;
        max-height: min(58dvh, 460px) !important;
        margin: 0 !important;
        z-index: 200 !important;
        border-radius: 4px;
        box-shadow: 0 -8px 40px rgba(0,0,0,0.65);
        padding-bottom: env(safe-area-inset-bottom, 0px);
      }
      #notif-list { max-height: none; }
    }
    @supports (-webkit-touch-callout: none) {
      input.input, select.input, textarea.input,
      input[type="text"], input[type="email"], input[type="password"],
      input[type="search"], select, textarea {
        font-size: 16px !important;
      }
    }
    .product-card {
      background: linear-gradient(155deg, rgba(28, 10, 28, 0.70) 0%, rgba(8, 5, 11, 0.76) 100%) !important;
      background-color: rgba(12, 5, 13, 0.70) !important;
      border: 1px solid rgba(255, 0, 168, 0.26) !important;
      -webkit-backdrop-filter: blur(14px) saturate(125%);
      backdrop-filter: blur(14px) saturate(125%);
      border-radius: 5px;
      transition: border-color 0.35s ease, transform 0.35s cubic-bezier(0.16, 1, 0.3, 1), box-shadow 0.35s ease;
      overflow: hidden;
    }
    .product-card:hover {
      border-color: rgba(232,77,155,0.5) !important;
      transform: translateY(-6px);
      box-shadow: 0 20px 50px rgba(0,0,0,0.75), 0 0 40px rgba(255,112,183,0.1);
    }
    .product-card .card-img-wrap { border: none !important; background: rgba(10, 5, 12, 0.22) !important; }
    .product-card > [class~="p-2.5"],
    .product-card > div:last-child {
      background: linear-gradient(180deg, rgba(20, 8, 20, 0.66), rgba(8, 5, 10, 0.78)) !important;
      -webkit-backdrop-filter: blur(10px);
      backdrop-filter: blur(10px);
    }
    .product-card .card-img-wrap img {
      transition: transform 0.7s cubic-bezier(0.16, 1, 0.3, 1), filter 0.4s ease;
      filter: saturate(0.94) contrast(1.04);
    }
    .product-card:hover .card-img-wrap img {
      transform: scale(1.06);
      filter: saturate(1.06) contrast(1.08);
    }
    .filter-btn { transition: all 0.25s ease; }
    .filter-btn:hover { transform: translateY(-1px); border-color: #71717a !important; }
    .filter-btn.active, .filter-btn.bg-zinc-100 {
      background: var(--accent) !important;
      border-color: var(--accent) !important;
      color: #fff !important;
    }
    .btn-lift { transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1); }
    .btn-lift:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(255,0,168,0.65), 0 0 20px rgba(255,112,183,0.2); }
    .btn-lift:active { transform: translateY(0) scale(0.98); }
    .btn-primary {
      background: #ff00a8;
      color: #fff;
      border: 1px solid #ff00a8;
    }
    .btn-primary:hover {
      background: #ff2bd6;
      border-color: #ff8be8;
      color: #fff;
    }
    nav a, nav button { transition: color 0.25s ease, border-color 0.25s ease, background-color 0.25s ease; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg); }
    ::-webkit-scrollbar-thumb { background: #1f1f24; border: 1px solid #2a2a30; }
    html { -webkit-text-size-adjust: 100%; }
    body { overflow-x: hidden; }
    button, a { -webkit-tap-highlight-color: transparent; }
    .lang-btn.active { color: #fff; border-color: var(--accent); background: var(--accent-soft); }

    .home-hero { isolation: isolate; background-size: cover; background-position: center; }
    .home-bg-video { z-index: 0; object-fit: cover; object-position: center; pointer-events: none; background: #09050a; }
    .home-video-shade { z-index: 1; pointer-events: none; background: linear-gradient(180deg,rgba(5,2,7,.24),rgba(5,2,7,.50)) !important; }
    #home-hero > .relative { z-index: 2; }
    @media (max-width: 767px) {
      .home-hero { min-height: 100svh !important; height: 100svh !important; }
      .home-bg-video { object-position: center center; }
      .gothic { letter-spacing: 0.06em !important; }
      .nav-main { padding-top: 0.5rem !important; padding-bottom: 0.5rem !important; }
      .nav-links { gap: 0.25rem !important; }
      .nav-links a, .nav-links button { min-height: 40px; }
      .filters-row {
        display: flex !important;
        flex-wrap: nowrap !important;
        overflow-x: auto !important;
        justify-content: flex-start !important;
        gap: 0.4rem !important;
        padding-bottom: 0.35rem;
        -webkit-overflow-scrolling: touch;
        scrollbar-width: none;
      }
      .filters-row::-webkit-scrollbar { display: none; }
      .filters-row .filter-btn {
        flex: 0 0 auto;
        white-space: nowrap;
        min-height: 40px;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
      }
      #grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 10px !important; }
      .product-card { padding: 0 !important; min-width: 0; border-radius: 8px !important; box-shadow: 0 8px 24px rgba(0,0,0,.24), inset 0 0 0 1px rgba(255,0,168,.035); }
      .product-card .card-img-wrap { aspect-ratio: 3 / 4 !important; }
      .product-card .card-img-wrap img,
      .product-card img { height: auto !important; min-height: 0 !important; width: 100%; aspect-ratio: 3 / 4; object-fit: cover; }
      .product-card > div:last-child { padding: 9px !important; gap: 7px !important; }
      .product-card h3 { font-size: 10px !important; line-height: 1.35 !important; letter-spacing: .025em !important; min-height: 2.7em !important; }
      .product-card p { font-size: 13px !important; }
      .product-card button { touch-action: manipulation; }
      .product-card .card-img-wrap button { width: 36px !important; height: 36px !important; border-radius: 999px; background: rgba(8,4,10,.74) !important; border-color: rgba(255,0,168,.3) !important; }
      .shop-section { padding-left: 10px !important; padding-right: 10px !important; }
      .collection-title { font-size: clamp(1.25rem, 6vw, 1.8rem) !important; }
      .nav-main { gap: 6px !important; }
      .nav-links { max-width: 70vw; overflow-x: auto; scrollbar-width: none; }
      .nav-links::-webkit-scrollbar { display: none; }
      #product-modal > div {
        max-height: 100dvh !important;
        border-radius: 0 !important;
        margin: 0 !important;
        min-height: 100dvh;
      }
      #cart-modal > div {
        max-height: 92dvh !important;
        border-radius: 1rem 1rem 0 0 !important;
      }
      .collection-title {
        font-size: 1.35rem !important;
        letter-spacing: 0.18em !important;
        margin-bottom: 1rem !important;
      }
      .shop-section {
        padding-top: 6.5rem !important;
        padding-left: 0.6rem !important;
        padding-right: 0.6rem !important;
      }
    }
    @media (min-width: 640px) and (max-width: 1023px) {
      .shop-section { padding-top: 7.5rem !important; }
      .product-card img { height: 16rem !important; }
      .collection-title { letter-spacing: 0.22em !important; }
    }
    @supports (padding: env(safe-area-inset-bottom)) {
      #cart-modal > div { padding-bottom: calc(1.25rem + env(safe-area-inset-bottom)); }
      nav { padding-left: env(safe-area-inset-left); padding-right: env(safe-area-inset-right); }
    }
    #scroll-progress {
      position: fixed; top: 0; left: 0; height: 1px; width: 0%;
      background: #3f3f46;
      z-index: 9999; pointer-events: none;
      transition: width 0.08s linear;
    }
    .float-tg {
      position: fixed; right: 14px; bottom: 18px; z-index: 60;
      width: 52px; height: 52px; border-radius: 999px;
      display: flex; align-items: center; justify-content: center;
      background: #ff00a8;
      color: #fff; font-size: 11px; font-weight: 900; letter-spacing: 0.08em;
      text-transform: uppercase; text-decoration: none;
      border: 1px solid rgba(255,255,255,0.15);
      box-shadow: 0 10px 30px rgba(255,0,168,0.65), 0 0 0 1px rgba(255,112,183,0.25);
      transition: transform 0.25s ease, box-shadow 0.25s ease;
    }
    .float-tg:hover { transform: translateY(-3px) scale(1.04); box-shadow: 0 14px 36px rgba(255,112,183,0.4); }
    .float-tg svg { display: block; width: 26px; height: 26px; }

    @media (max-width: 640px) {
      .float-tg { right: 12px; bottom: 14px; width: 48px; height: 48px; }
    }
    .drawer-trigger{display:inline-flex;align-items:center;justify-content:center;width:42px;height:42px;flex:0 0 42px;border:1px solid rgba(255,0,168,.5);border-radius:8px;background:rgba(255,0,168,.12);color:#ff42bd;box-shadow:0 0 18px rgba(255,0,168,.15);cursor:pointer}
    .drawer-trigger svg{width:22px;height:22px}
    .site-drawer-backdrop{position:fixed;inset:0;z-index:10010;background:rgba(4,1,5,.72);backdrop-filter:blur(7px);opacity:0;visibility:hidden;transition:opacity .25s,visibility .25s}
    .site-drawer-backdrop.open{opacity:1;visibility:visible}
    .site-drawer{position:absolute;top:0;left:0;margin:0;width:min(365px,90vw);height:100%;height:100dvh;min-height:100%;box-sizing:border-box;padding:calc(24px + env(safe-area-inset-top)) 20px calc(24px + env(safe-area-inset-bottom));background:radial-gradient(ellipse at 0% 0%,rgba(255,0,168,.18),transparent 48%),linear-gradient(160deg,rgba(24,8,24,.985),rgba(5,4,9,.995));border:0;border-right:1px solid rgba(255,0,168,.72);border-radius:0 18px 18px 0;box-shadow:18px 0 70px rgba(255,0,168,.22),inset -1px 0 rgba(255,112,183,.12);transform:translate3d(-105%,0,0);transition:transform .32s cubic-bezier(.2,.8,.2,1);overflow-y:auto;overscroll-behavior:contain;isolation:isolate}
    .site-drawer-backdrop.open .site-drawer{transform:translate3d(0,0,0)}
    .site-drawer:before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,#ff00a8,#ff70c8,transparent);box-shadow:0 0 22px rgba(255,0,168,.7);pointer-events:none}
    .site-drawer:after{content:'ARCHIVE / NAVIGATION';display:block;margin-top:26px;padding-top:14px;border-top:1px solid rgba(255,0,168,.16);font-size:9px;letter-spacing:.2em;color:rgba(255,112,200,.5)}
    .drawer-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:0 0 20px;margin:0 0 18px;border-bottom:1px solid rgba(255,0,168,.32)}
    .drawer-brand{font-weight:950;font-size:clamp(15px,4vw,18px);letter-spacing:.14em;color:#fff;text-shadow:0 0 18px rgba(255,0,168,.75);text-decoration:none}
    @media(max-width:640px){.drawer-brand{font-family:'Pirata One','Cinzel',serif;font-size:24px;letter-spacing:.04em;text-shadow:0 0 8px rgba(255,0,168,.95),0 0 20px rgba(255,0,168,.55)}}
    .drawer-close{display:grid;place-items:center;width:42px;height:42px;flex:0 0 42px;border-radius:12px;background:rgba(255,0,168,.12);border:1px solid rgba(255,0,168,.42);color:#ff70c8;font-size:25px;cursor:pointer;box-shadow:0 0 20px rgba(255,0,168,.08)}
    .drawer-label{font-size:9px;letter-spacing:.22em;text-transform:uppercase;color:#ff70c8;margin:20px 0 10px;padding-left:3px}
    .drawer-link{display:flex;align-items:center;gap:13px;min-height:52px;box-sizing:border-box;padding:13px 14px;margin:6px 0;border:1px solid rgba(255,0,168,.17);border-radius:11px;background:linear-gradient(100deg,rgba(255,0,168,.055),rgba(255,255,255,.018));color:#fff0fa;font-size:11px;font-weight:850;letter-spacing:.1em;text-transform:uppercase;text-decoration:none;transition:background .2s,border-color .2s,transform .2s,box-shadow .2s;cursor:pointer}
    .drawer-link:hover,.drawer-link:focus-visible{outline:none;background:linear-gradient(100deg,rgba(255,0,168,.2),rgba(255,112,200,.055));border-color:rgba(255,0,168,.7);transform:translateX(3px);box-shadow:0 0 24px rgba(255,0,168,.09)}
    .drawer-link:active{transform:scale(.985)}
    .drawer-link:focus-visible,.drawer-close:focus-visible{outline:2px solid #ff70c8;outline-offset:3px}
    .drawer-link svg{width:19px;height:19px;color:#ff42bd;flex:0 0 auto}
    .nav-links>a[href="/shop"],.nav-links>button[onclick="toggleDelivery()"],.nav-links>a[href^="https://t.me/"]{display:none!important}
    @media(max-width:640px){.drawer-trigger{width:42px;height:42px;flex-basis:42px}.nav-main{gap:8px!important}.nav-links{gap:4px!important;max-width:none!important;overflow:visible!important}.site-drawer{width:min(350px,88vw);padding-left:18px;padding-right:18px;border-radius:0 16px 16px 0}.drawer-link{min-height:52px;font-size:11px}.drawer-label{margin-top:18px}}
    *, *::before, *::after { box-sizing: border-box; }
    img, video, canvas, iframe { max-width: 100%; }
    button, a, input, select, textarea { touch-action: manipulation; }
    @media (max-width: 767px) {
      body { background-attachment: scroll; }
      main, section, footer, .shop-section { min-width: 0; }
      .nav-main { width: 100%; padding-left: max(12px, env(safe-area-inset-left)) !important; padding-right: max(12px, env(safe-area-inset-right)) !important; gap: 10px !important; }
      .blood-logo { max-width: calc(100vw - 118px) !important; }
      .blood-logo h1 { max-width: 100% !important; font-size: clamp(20px, 6vw, 27px) !important; }
      .drawer-trigger { width: 44px !important; height: 44px !important; flex-basis: 44px !important; border-radius: 10px !important; }
      .nav-links { flex: 0 0 auto; max-width: 44vw !important; gap: 4px !important; }
      .nav-links a, .nav-links button { min-height: 42px; display: inline-flex; align-items: center; justify-content: center; }
      .home-hero { min-height: max(620px, 100svh) !important; height: auto !important; padding-top: 82px !important; padding-bottom: 48px !important; }
      #home-hero h1 { font-size: clamp(2.6rem, 13vw, 5rem) !important; line-height: .94 !important; overflow-wrap: anywhere; }
      #home-hero p { max-width: 34rem; font-size: 13px !important; line-height: 1.7 !important; }
      .shop-section { padding-top: 6.25rem !important; padding-left: 12px !important; padding-right: 12px !important; }
      .collection-title { font-size: clamp(1.2rem, 5.6vw, 1.7rem) !important; line-height: 1.2 !important; letter-spacing: .11em !important; overflow-wrap: anywhere; }
      #grid { grid-template-columns: repeat(2, minmax(0, 1fr)) !important; gap: 10px !important; }
      .product-card { min-width: 0 !important; border-radius: 8px !important; }
      .product-card .card-img-wrap { aspect-ratio: 3 / 4 !important; overflow: hidden; }
      .product-card .card-img-wrap img, .product-card img { display: block; width: 100% !important; height: 100% !important; min-height: 0 !important; aspect-ratio: 3 / 4; object-fit: cover; }
      .product-card > div:last-child { padding: 10px !important; gap: 8px !important; }
      .product-card h3 { font-size: clamp(11px, 3.1vw, 13px) !important; line-height: 1.35 !important; letter-spacing: 0 !important; min-height: 2.7em !important; overflow-wrap: anywhere; }
      .product-card p { font-size: 13px !important; line-height: 1.35 !important; }
      .product-card button { min-height: 38px; }
      .product-card .card-img-wrap button { width: 38px !important; height: 38px !important; }
      .filters-row { margin-left: -2px; margin-right: -2px; padding: 4px 2px 9px !important; }
      .filters-row .filter-btn { min-height: 42px !important; font-size: 11px !important; }
      #product-modal { padding: 0 !important; align-items: stretch !important; }
      #product-modal > div { width: 100% !important; max-width: 100% !important; max-height: 100dvh !important; min-height: 100dvh !important; overflow-y: auto !important; border-radius: 0 !important; }
      #cart-modal { align-items: flex-end !important; padding: 0 !important; }
      #cart-modal > div { width: 100% !important; max-width: 100% !important; max-height: 90dvh !important; overflow-y: auto !important; border-radius: 18px 18px 0 0 !important; padding-bottom: calc(18px + env(safe-area-inset-bottom)) !important; }
      input, select, textarea { max-width: 100%; font-size: 16px !important; }
      button, input[type=submit], a[class*="btn"] { min-height: 42px; }
      .float-tg { width: 46px !important; height: 46px !important; right: 12px !important; bottom: max(12px, env(safe-area-inset-bottom)) !important; }
      .site-drawer { width: min(340px, 88vw) !important; }
      .site-drawer .drawer-link { min-height: 50px; }
      .overflow-x-auto { max-width: 100%; -webkit-overflow-scrolling: touch; }
      [class*="grid-cols-"] { min-width: 0; }
      .max-w-7xl, .max-w-6xl, .max-w-5xl, .max-w-4xl { max-width: 100% !important; }
      footer { padding-left: 16px !important; padding-right: 16px !important; }
    }
    @media (max-width: 380px) {
      .nav-links { max-width: 38vw !important; }
      .nav-links a { font-size: 8px !important; padding-left: 5px !important; padding-right: 5px !important; }
      #grid { gap: 7px !important; }
      .product-card > div:last-child { padding: 8px !important; }
      .product-card h3 { font-size: 10.5px !important; }
    }
    @media (min-width: 768px) and (max-width: 1023px) {
      #grid { grid-template-columns: repeat(3, minmax(0, 1fr)) !important; }
      .shop-section { padding-left: 22px !important; padding-right: 22px !important; }
    }

    @media (max-width: 767px) {
      html, body { width: 100%; max-width: 100%; }
      body { overflow-x: clip; }
      main, main > section, .shop-section, footer {
        max-width: 100%;
        box-sizing: border-box;
      }
      .shop-section {
        padding-left: max(16px, env(safe-area-inset-left)) !important;
        padding-right: max(16px, env(safe-area-inset-right)) !important;
      }
      #grid {
        width: 100%;
        max-width: 100%;
        margin-left: auto;
        margin-right: auto;
        gap: 12px !important;
      }
      .product-card {
        border: 1px solid rgba(255, 0, 168, .28) !important;
        box-shadow: 0 6px 22px rgba(0,0,0,.28), inset 0 0 0 1px rgba(255,0,168,.035) !important;
        overflow: hidden;
      }
      .filters-row {
        max-width: 100%;
        margin-left: 0 !important;
        margin-right: 0 !important;
      }
      .nav-main {
        padding-left: max(14px, env(safe-area-inset-left)) !important;
        padding-right: max(14px, env(safe-area-inset-right)) !important;
      }
      footer {
        padding-left: max(16px, env(safe-area-inset-left)) !important;
        padding-right: max(16px, env(safe-area-inset-right)) !important;
      }
      .home-hero {
        padding-left: 16px !important;
        padding-right: 16px !important;
      }
    }
  </style>
</head>
<body class="antialiased select-none">
  <div id="scroll-progress"></div>
  <nav class="fixed top-0 w-full bg-[#070708]/92 backdrop-blur-xl border-b border-white/[0.06] z-50">
    <div class="nav-main max-w-7xl mx-auto px-2 sm:px-3 md:px-6 flex justify-between items-center gap-1.5 sm:gap-2 py-2.5 sm:py-3 md:py-5">
      <div onclick="location.href='/'" class="blood-logo inline-block cursor-pointer py-0.5 shrink-0 min-w-0">
        <h1 class="text-[13px] xs:text-sm sm:text-xl md:text-3xl gothic tracking-[0.08em] sm:tracking-[0.2em] md:tracking-[0.3em] text-zinc-50 transition-colors truncate max-w-[42vw] sm:max-w-none">DEAD ARCHIVE</h1>
      </div>
      <button type="button" class="drawer-trigger" onclick="toggleSiteDrawer(true)" aria-label="Открыть меню" aria-controls="site-drawer" aria-expanded="false"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 6h18M3 12h18M3 18h18"/></svg></button>
      <div class="nav-links flex gap-0.5 sm:gap-2 md:gap-5 text-[9px] sm:text-[11px] md:text-xs font-bold items-center text-zinc-400 tracking-wide sm:tracking-widest shrink-0">
        <a href="/shop" class="hover:text-zinc-50 transition-colors uppercase px-1.5 sm:px-2 py-2" data-i18n="nav_shop">каталог</a>
        <a href="/lookbook" class="hover:text-[#ff70c8] transition-colors uppercase px-1.5 sm:px-2 py-2">lookbook</a>
        <button onclick="toggleDelivery()" class="hover:text-zinc-50 transition-colors uppercase px-1.5 sm:px-2 py-2 hidden xs:inline sm:inline" data-i18n="nav_delivery">доставка</button>
        <a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="hidden md:inline-flex items-center hover:text-zinc-50 transition-colors px-2 py-2" title="Telegram" aria-label="Telegram">
          <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.15 1.568-.769 5.233-1.087 6.94-.134.72-.398 1.0-.655 1.027-.552.12-1.068-.26-1.656-.508-.922-.39-1.442-.63-2.336-1.01-1.464-.62-1.026-1.01.063-1.595.285-.153 5.23-2.402 5.33-2.607.014-.029.024-.135-.05-.19s-.188-.013-.268.01c-.113.032-1.91 1.213-5.39 3.56-.51.35-.97.52-1.38.51-.454-.01-1.327-.257-1.976-.468-.796-.26-1.43-.4-1.375-.844.028-.232.337-.47.927-.715 3.63-1.58 6.05-2.62 7.26-3.12 3.46-1.43 4.18-1.68 4.65-1.69z"/></svg>
        </a>
        <div class="relative" id="notif-wrap">
          <button type="button" id="notif-btn" title="Уведомления" aria-label="Уведомления" onclick="toggleNotifPanel(event)" class="relative hover:text-zinc-50 transition-colors bg-[#0c0c0e] border border-[#1f1f24] w-10 h-10 min-h-[40px] inline-flex items-center justify-center rounded-sm text-zinc-300">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg>
            <span id="notif-badge" class="hidden absolute -top-1 -right-1 min-w-[16px] h-4 px-1 rounded-full bg-[#ff00a8] text-[9px] font-black text-white flex items-center justify-center">0</span>
          </button>
          <div id="notif-panel" class="hidden absolute right-0 top-full mt-2 z-[80] border border-white/15 bg-[#0a0a0c] shadow-2xl shadow-black/80">
            <div class="flex items-center justify-between gap-2 px-3 py-2.5 border-b border-white/5 shrink-0">
              <span class="text-[10px] font-black uppercase tracking-[0.18em] text-zinc-400">уведомления</span>
              <div class="flex items-center gap-3 shrink-0">
                <button type="button" onclick="event.stopPropagation(); markAllNotifsRead()" class="text-[9px] font-bold uppercase tracking-wider text-zinc-500 hover:text-zinc-200">все</button>
                <button type="button" onclick="event.stopPropagation(); closeNotifPanel()" class="text-zinc-500 hover:text-zinc-100 text-lg leading-none px-1" aria-label="закрыть">×</button>
              </div>
            </div>
            <div id="notif-list" class="overflow-y-auto">
              <p class="text-[11px] text-zinc-600 px-3 py-6 text-center">войди, чтобы видеть уведомления</p>
            </div>
          </div>
        </div>
        <a href="/account" id="nav-account" title="Аккаунт" aria-label="Аккаунт" class="hover:text-zinc-50 transition-colors bg-[#0c0c0e] border border-[#1f1f24] w-10 h-10 min-h-[40px] inline-flex items-center justify-center rounded-sm text-zinc-300">
          <svg id="nav-account-icon" xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
        </a>
        <button id="cart-nav-btn" onclick="toggleCart()" class="font-bold hover:text-zinc-50 transition-all flex items-center gap-1 bg-[#0c0c0e] border border-[#1f1f24] px-2 sm:px-3 md:px-4 py-2 rounded-sm min-h-[40px]">
          <span class="hidden sm:inline" data-i18n="nav_cart">КОРЗИНА //</span><svg class="sm:hidden" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.68 13.39a2 2 0 0 0 2 1.61h9.72a2 2 0 0 0 2-1.61L23 6H6"/></svg> <span class="text-zinc-100" id="count">0</span>
        </button>
        <div class="flex border border-[#1f1f24] rounded-sm overflow-hidden">
          <button onclick="setLang('ru')" id="lang-ru" class="lang-btn px-1.5 sm:px-2 py-2 text-[9px] sm:text-[10px] font-black uppercase min-h-[40px]">RU</button>
          <button onclick="setLang('en')" id="lang-en" class="lang-btn px-1.5 sm:px-2 py-2 text-[9px] sm:text-[10px] font-black uppercase border-l border-[#1f1f24] min-h-[40px]">EN</button>
        </div>
      </div>
    </div>
  </nav>
"""

HTML_FOOTER = """
  <div id="site-drawer-backdrop" class="site-drawer-backdrop" onclick="if(event.target===this)toggleSiteDrawer(false)" aria-hidden="true">
    <aside id="site-drawer" class="site-drawer" role="dialog" aria-modal="true" aria-label="Навигация сайта">
      <div class="drawer-head"><a class="drawer-brand" href="/">DEAD ARCHIVE</a><button class="drawer-close" type="button" onclick="toggleSiteDrawer(false)" aria-label="Закрыть меню">×</button></div>
      <div class="drawer-label">навигация</div>
      <a class="drawer-link" href="/"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z"/></svg>Главная</a>
      <a class="drawer-link" href="/shop"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 7h16l-1 14H5L4 7Z"/><path d="M9 7a3 3 0 0 1 6 0"/></svg>Каталог</a>
      <a class="drawer-link" href="/account"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/></svg>Профиль</a>
      <button class="drawer-link" type="button" onclick="toggleSiteDrawer(false);toggleDelivery()" style="width:100%;text-align:left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7h11v11H3zM14 11h4l3 3v4h-7z"/><circle cx="7" cy="19" r="1.5"/><circle cx="18" cy="19" r="1.5"/></svg>Доставка и оплата</button>
      <a class="drawer-link" href="https://t.me/deadarchivemanager" target="_blank" rel="noopener"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M22 3 2.8 10.4c-1.3.5-1.3 1.3-.2 1.6l4.9 1.5 1.9 6c.2.7.1 1 .8 1 .5 0 .8-.2 1.1-.5l2.4-2.3 5 3.7c.9.5 1.5.2 1.7-.8L23.7 4.4C24 3.1 23.2 2.5 22 3Z"/></svg>Поддержка Telegram</a>
      <button class="drawer-link" type="button" onclick="toggleSiteDrawer(false);toggleCart()" style="width:100%;text-align:left"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/></svg>Корзина</button>
      <div class="drawer-label">аккаунт</div>
      <a class="drawer-link" href="/login"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M10 17l5-5-5-5M15 12H3"/><path d="M12 3h7a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-7"/></svg>Войти</a>
      <a class="drawer-link" href="/register"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0M19 8v6M16 11h6"/></svg>Регистрация</a>
    </aside>
  </div>
  <script>
  (()=>{const backdrop=document.getElementById('site-drawer-backdrop');if(!backdrop)return;window.toggleSiteDrawer=function(open){backdrop.classList.toggle('open',!!open);backdrop.setAttribute('aria-hidden',open?'false':'true');document.body.style.overflow=open?'hidden':'';const trigger=document.querySelector('.drawer-trigger');if(trigger)trigger.setAttribute('aria-expanded',open?'true':'false');if(open){const close=backdrop.querySelector('.drawer-close');if(close)close.focus();}};document.addEventListener('keydown',e=>{if(e.key==='Escape')window.toggleSiteDrawer(false)});backdrop.querySelectorAll('a').forEach(a=>a.addEventListener('click',()=>window.toggleSiteDrawer(false)));})();
  </script>
  <a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="float-tg" title="Поддержка в Telegram" aria-label="Написать в поддержку Telegram">
    <svg viewBox="0 0 24 24" width="26" height="26" fill="currentColor" aria-hidden="true">
      <path d="M11.944 0A12 12 0 0 0 0 12a12 12 0 0 0 12 12 12 12 0 0 0 12-12A12 12 0 0 0 12 0a12 12 0 0 0-.056 0zm4.962 7.224c.1-.002.321.023.465.14a.506.506 0 0 1 .171.325c.016.093.036.306.02.472-.15 1.568-.769 5.233-1.087 6.94-.134.72-.398 1.0-.655 1.027-.552.12-1.068-.26-1.656-.508-.922-.39-1.442-.63-2.336-1.01-1.464-.62-1.026-1.01.063-1.595.285-.153 5.23-2.402 5.33-2.607.014-.029.024-.135-.05-.19s-.188-.013-.268.01c-.113.032-1.91 1.213-5.39 3.56-.51.35-.97.52-1.38.51-.454-.01-1.327-.257-1.976-.468-.796-.26-1.43-.4-1.375-.844.028-.232.337-.47.927-.715 3.63-1.58 6.05-2.62 7.26-3.12 3.46-1.43 4.18-1.68 4.65-1.69z"/>
    </svg>
  </a>
  <!-- ПРИВЕТСТВЕННОЕ ОКНО -->
  <div id="welcome-modal" class="hidden fixed inset-0 bg-black/90 z-[10002] flex items-center justify-center p-4 backdrop-blur-md">
    <div class="bg-[#09090b] border border-zinc-800 w-full max-w-md p-6 sm:p-8 relative text-center">
      <button onclick="closeWelcome()" class="absolute top-3 right-3 text-zinc-500 hover:text-zinc-100 text-2xl w-10 h-10 flex items-center justify-center">✕</button>
      <p class="text-[10px] font-black tracking-[0.3em] text-red-500 uppercase mb-3">открытие</p>
      <h2 class="text-xl sm:text-2xl gothic tracking-[0.15em] text-zinc-50 mb-4">мы открылись.</h2>
      <p class="text-xs text-zinc-400 leading-relaxed mb-2">без лишнего шума.</p>
      <p class="text-xs text-zinc-400 leading-relaxed mb-6">промокод <span class="text-zinc-100 font-black">DEAD</span> — <span class="text-zinc-100 font-black">−5%</span> на первый заказ.</p>
      <div class="flex flex-col gap-2">
        <button onclick="closeWelcome()" class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black tracking-[0.2em] uppercase hover:bg-black hover:text-white hover:border hover:border-zinc-700 transition-all">
          в каталог
        </button>
        <a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="w-full py-3.5 border border-zinc-700 text-zinc-300 text-xs font-black tracking-[0.2em] uppercase hover:border-zinc-400 hover:text-white transition-all">
          telegram
        </a>
      </div>
    </div>
  </div>

  <!-- TOAST УВЕДОМЛЕНИЕ -->
  <div id="toast" class="fixed bottom-6 left-1/2 -translate-x-1/2 z-[10000] hidden px-6 py-3.5 bg-zinc-100 text-black text-xs font-black tracking-[0.15em] uppercase shadow-2xl border border-zinc-300 transition-all duration-300 ease-out opacity-0 translate-y-4 scale-95">
    в корзине
  </div>

  <!-- FULLSCREEN ZOOM ФОТО -->
  <div id="image-zoom" class="hidden fixed inset-0 bg-black/95 z-[10001] flex items-center justify-center p-3 sm:p-6 cursor-zoom-out" onclick="closeImageZoom()">
    <img id="zoom-img" src="" class="max-w-full max-h-full object-contain select-none" onclick="event.stopPropagation()">
    <button onclick="closeImageZoom()" class="absolute top-4 right-4 text-zinc-400 hover:text-white text-3xl w-12 h-12 flex items-center justify-center">✕</button>
  </div>

  <!-- МОДАЛКА КОРЗИНЫ -->
  <div id="cart-modal" class="hidden fixed inset-0 bg-black/98 flex items-end sm:items-center justify-center z-[9999] backdrop-blur-md p-0 sm:p-4">
    <div class="bg-[#09090b] w-full max-w-xl p-5 sm:p-6 md:p-10 border border-zinc-800 max-h-[92vh] sm:max-h-[90vh] overflow-auto relative rounded-t-xl sm:rounded-none">
      <button onclick="toggleCart()" class="absolute top-3 right-3 sm:top-4 sm:right-4 md:top-6 md:right-6 text-zinc-500 hover:text-zinc-100 text-2xl transition-colors w-10 h-10 flex items-center justify-center">✕</button>
      <div id="cart-main-view">
        <h2 class="text-lg md:text-xl font-black mb-6 md:mb-8 tracking-[0.2em] text-center text-zinc-50 uppercase">корзина</h2>
        <div id="cart-items" class="space-y-6 md:space-y-8"></div>
        <div class="mt-8 md:mt-10 border-t border-zinc-900 pt-6 md:pt-8">
          <button onclick="openCheckoutForm()" id="checkout-btn" class="w-full py-4 md:py-5 bg-zinc-100 text-black text-xs font-black tracking-[0.2em] hover:bg-black hover:text-white hover:border hover:border-zinc-700 transition-all uppercase">
            оформить
          </button>
        </div>
      </div>
      <div id="cart-checkout-view" class="hidden">
        <h2 class="text-lg md:text-xl font-black mb-2 tracking-[0.2em] text-center text-zinc-50 uppercase">оформление</h2>
        <div class="space-y-4 md:space-y-6">
          <div>
            <label class="block text-[11px] font-black text-zinc-400 mb-2 tracking-widest uppercase">TELEGRAM USERNAME</label>
            <input type="text" id="cust-tg" placeholder="@username" autocomplete="off"
              oninput="formatTgInput(this)"
              class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3.5 md:py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-wider">
            <p id="tg-error" class="text-red-500 text-[10px] font-bold mt-1.5 tracking-wider hidden uppercase"></p>
          </div>
          <div>
            <label class="block text-[11px] font-black text-zinc-400 mb-2 tracking-widest uppercase">НОМЕР ТЕЛЕФОНА</label>
            <input type="tel" id="cust-phone" placeholder="+380991234567" inputmode="tel" autocomplete="tel"
              oninput="formatPhoneInput(this)"
              class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3.5 md:py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-wider">
            <p id="phone-error" class="text-red-500 text-[10px] font-bold mt-1.5 tracking-wider hidden uppercase"></p>
          </div>
          <div>
            <label class="block text-[11px] font-black text-zinc-400 mb-2 tracking-widest uppercase">ПРОМОКОД</label>
            <div class="flex gap-2">
              <input type="text" id="cust-promo" placeholder="DEAD" class="flex-1 bg-zinc-950 border border-zinc-800 px-4 py-3.5 md:py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-wider uppercase" oninput="checkPromo()">
              <button type="button" onclick="checkPromo()" class="px-4 border border-zinc-700 bg-zinc-900 text-xs font-black text-zinc-300 hover:text-white uppercase">OK</button>
            </div>
            <p id="promo-msg" class="text-[10px] mt-2 font-bold tracking-wider hidden"></p>
          </div>
          <p class="text-[10px] text-zinc-500 leading-relaxed">
            Цена без доставки.
            <button type="button" onclick="toggleDelivery()" class="underline underline-offset-2 text-zinc-300 hover:text-white">Подробнее о доставке и оплате</button>
          </p>
        </div>
        <div class="mt-8 md:mt-10 flex gap-4">
          <button onclick="backToCartItems()" class="w-1/3 py-3.5 md:py-4 border border-zinc-800 text-zinc-300 text-xs font-bold uppercase">НАЗАД</button>
          <button onclick="submitOrder()" id="submit-order-btn" class="w-2/3 py-3.5 md:py-4 bg-zinc-100 text-black font-black text-xs tracking-[0.15em] uppercase">отправить</button>
        </div>
      </div>
      <div id="cart-success-view" class="hidden text-center py-12">
        <h2 class="text-xl md:text-2xl font-black mb-4 tracking-[0.15em] text-zinc-50 uppercase">заказ принят</h2>
      </div>
    </div>
  </div>

  <!-- МОДАЛКА ИСТОРИИ ЗАКАЗОВ ПОКУПАТЕЛЯ -->
  <div id="orders-history-modal" class="hidden fixed inset-0 bg-black/98 flex items-center justify-center z-[9999] backdrop-blur-md p-4">
    <div class="bg-[#09090b] w-full max-w-2xl p-6 md:p-10 border border-zinc-800 max-h-[90vh] overflow-auto relative">
      <button onclick="toggleOrdersHistory()" class="absolute top-4 right-4 md:top-6 md:right-6 text-zinc-500 hover:text-zinc-100 text-2xl transition-colors">✕</button>
      <h2 class="text-lg md:text-xl font-black mb-6 md:mb-8 tracking-[0.2em] text-center text-zinc-50 uppercase">мои заказы</h2>
      <div id="client-orders-list" class="space-y-6">
        <p class="text-zinc-600 text-center py-12 text-xs font-bold tracking-widest">ЗАГРУЗКА...</p>
      </div>
    </div>
  </div>

  <!-- МОДАЛКА ДОСТАВКА И ОПЛАТА -->
  <div id="delivery-modal" class="hidden fixed inset-0 bg-black/98 flex items-center justify-center z-[9999] backdrop-blur-md p-3 sm:p-4">
    <div class="bg-[#09090b] w-full max-w-lg p-5 sm:p-6 md:p-10 border border-zinc-800 max-h-[90vh] overflow-auto relative">
      <button onclick="toggleDelivery()" class="absolute top-3 right-3 sm:top-4 sm:right-4 text-zinc-500 hover:text-zinc-100 text-2xl w-10 h-10 flex items-center justify-center">✕</button>
      <h2 class="text-base sm:text-lg md:text-xl font-black mb-6 sm:mb-8 tracking-[0.2em] text-center text-zinc-50 uppercase" data-i18n="delivery_title">доставка & оплата</h2>

      <div class="space-y-6 sm:space-y-8 text-sm">
        <div>
          <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.25em] mb-3">доставка</p>
          <ul class="space-y-3 text-zinc-300 leading-relaxed">
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span><b class="text-zinc-100">Украина</b> — 14–25 дней, от 800 грн</span></li>
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span><b class="text-zinc-100">Европа</b> — 12–18 дней, от 35 €</span></li>
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span><b class="text-zinc-100">Другие страны</b> — индивидуально в Telegram</span></li>
          </ul>
          <p class="text-[11px] text-zinc-500 mt-3 leading-relaxed">Точная сумма зависит от страны и веса. Считаем после заказа.</p>
        </div>

        <div class="border-t border-zinc-900 pt-6">
          <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.25em] mb-3">оплата</p>
          <ul class="space-y-3 text-zinc-300 leading-relaxed">
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span>Карта — после подтверждения заказа</span></li>
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span>Крипта (USDT TRC20 и др.)</span></li>
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span>Реквизиты придут в Telegram</span></li>
          </ul>
        </div>

        <div class="border-t border-zinc-900 pt-6">
          <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.25em] mb-3">FAQ</p>
          <div class="space-y-2" id="faq-list">
            <details class="group border border-zinc-800 bg-black/40">
              <summary class="cursor-pointer px-3 py-3 text-xs font-bold text-zinc-200 uppercase tracking-wider list-none flex justify-between items-center">Как сделать заказ?<span class="text-zinc-500 group-open:rotate-45 transition">+</span></summary>
              <p class="px-3 pb-3 text-[12px] text-zinc-400 leading-relaxed">Выбери размер → в корзину → оформить → укажи Telegram и телефон. Мы напишем в TG с суммой и оплатой.</p>
            </details>
            <details class="group border border-zinc-800 bg-black/40">
              <summary class="cursor-pointer px-3 py-3 text-xs font-bold text-zinc-200 uppercase tracking-wider list-none flex justify-between items-center">Это оригинал?<span class="text-zinc-500 group-open:rotate-45 transition">+</span></summary>
              <p class="px-3 pb-3 text-[12px] text-zinc-400 leading-relaxed">Нет. Все товары — реплики / высококачественные копии, не оригиналы брендов.</p>
            </details>
            <details class="group border border-zinc-800 bg-black/40">
              <summary class="cursor-pointer px-3 py-3 text-xs font-bold text-zinc-200 uppercase tracking-wider list-none flex justify-between items-center">Можно вернуть?<span class="text-zinc-500 group-open:rotate-45 transition">+</span></summary>
              <p class="px-3 pb-3 text-[12px] text-zinc-400 leading-relaxed">Обмен/возврат обсуждается в Telegram до отправки. После отправки — по ситуации, пиши менеджеру.</p>
            </details>
            <details class="group border border-zinc-800 bg-black/40">
              <summary class="cursor-pointer px-3 py-3 text-xs font-bold text-zinc-200 uppercase tracking-wider list-none flex justify-between items-center">Как выбрать размер?<span class="text-zinc-500 group-open:rotate-45 transition">+</span></summary>
              <p class="px-3 pb-3 text-[12px] text-zinc-400 leading-relaxed">Открой size guide в карточке товара. Много вещей oversized — бери свой обычный или −1.</p>
            </details>
            <details class="group border border-zinc-800 bg-black/40">
              <summary class="cursor-pointer px-3 py-3 text-xs font-bold text-zinc-200 uppercase tracking-wider list-none flex justify-between items-center">Сколько ждать ответ?<span class="text-zinc-500 group-open:rotate-45 transition">+</span></summary>
              <p class="px-3 pb-3 text-[12px] text-zinc-400 leading-relaxed">Обычно в течение дня. Если срочно — пиши сами в Telegram.</p>
            </details>
          </div>
        </div>

        <div class="border-t border-zinc-900 pt-6">
          <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.25em] mb-3">важно</p>
          <p class="text-zinc-400 text-xs leading-relaxed">
            Цена на сайте <b class="text-zinc-200">без доставки</b>. После заказа менеджер пришлёт итоговую сумму и реквизиты.
          </p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <button type="button" onclick="toggleDelivery();toggleSizeGuide();" class="w-full py-3.5 border border-zinc-700 text-center text-[11px] font-black tracking-[0.15em] uppercase text-zinc-200 hover:border-zinc-400 transition-all">таблица размеров</button>
          <a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="block w-full py-3.5 border border-zinc-700 text-center text-[11px] font-black tracking-[0.15em] uppercase text-zinc-200 hover:bg-zinc-100 hover:text-black transition-all">telegram</a>
        </div>
      </div>
    </div>
  </div>

  <div id="size-guide-modal" class="hidden fixed inset-0 bg-black/98 flex items-center justify-center z-[9999] backdrop-blur-md p-3 sm:p-4">
    <div class="bg-[#09090b] w-full max-w-lg p-5 sm:p-6 md:p-8 border border-zinc-800 max-h-[90vh] overflow-auto relative">
      <button onclick="toggleSizeGuide()" class="absolute top-3 right-3 text-zinc-500 hover:text-zinc-100 text-2xl w-10 h-10 flex items-center justify-center">✕</button>
      <h2 class="text-base sm:text-lg font-black mb-2 tracking-[0.2em] text-center text-zinc-50 uppercase" data-i18n="sizes_title">размеры</h2>
      <p class="text-[11px] text-zinc-500 text-center mb-6 leading-relaxed" data-i18n="sizes_hint">Много позиций oversized. Если любишь свободный фит — бери свой размер. Хочешь плотнее — −1.</p>

      <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.2em] mb-2">Верх (футболки / худи / зипки)</p>
      <div class="overflow-x-auto mb-6">
        <table class="w-full text-[11px] sm:text-xs text-left border border-zinc-800">
          <thead class="bg-zinc-900 text-zinc-400 uppercase tracking-wider">
            <tr>
              <th class="px-2 py-2 border-b border-zinc-800">Size</th>
              <th class="px-2 py-2 border-b border-zinc-800">Грудь</th>
              <th class="px-2 py-2 border-b border-zinc-800">Длина</th>
              <th class="px-2 py-2 border-b border-zinc-800">Рукав</th>
            </tr>
          </thead>
          <tbody class="text-zinc-300">
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">XS</td><td class="px-2 py-2">52–54</td><td class="px-2 py-2">68–70</td><td class="px-2 py-2">58–60</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">S</td><td class="px-2 py-2">56–58</td><td class="px-2 py-2">70–72</td><td class="px-2 py-2">60–62</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">M</td><td class="px-2 py-2">58–62</td><td class="px-2 py-2">72–74</td><td class="px-2 py-2">62–64</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">L</td><td class="px-2 py-2">62–66</td><td class="px-2 py-2">74–76</td><td class="px-2 py-2">64–66</td></tr>
            <tr><td class="px-2 py-2 font-bold text-zinc-100">XL</td><td class="px-2 py-2">66–70</td><td class="px-2 py-2">76–78</td><td class="px-2 py-2">66–68</td></tr>
          </tbody>
        </table>
      </div>

      <p class="text-[10px] font-black text-red-500 uppercase tracking-[0.2em] mb-2">Низ (джинсы / штаны)</p>
      <div class="overflow-x-auto mb-6">
        <table class="w-full text-[11px] sm:text-xs text-left border border-zinc-800">
          <thead class="bg-zinc-900 text-zinc-400 uppercase tracking-wider">
            <tr>
              <th class="px-2 py-2 border-b border-zinc-800">Size</th>
              <th class="px-2 py-2 border-b border-zinc-800">Талия</th>
              <th class="px-2 py-2 border-b border-zinc-800">Длина</th>
            </tr>
          </thead>
          <tbody class="text-zinc-300">
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">28 / XS</td><td class="px-2 py-2">72–76</td><td class="px-2 py-2">100–104</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">30 / S</td><td class="px-2 py-2">76–80</td><td class="px-2 py-2">102–106</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">32 / M</td><td class="px-2 py-2">80–86</td><td class="px-2 py-2">104–108</td></tr>
            <tr class="border-b border-zinc-900"><td class="px-2 py-2 font-bold text-zinc-100">34 / L</td><td class="px-2 py-2">86–92</td><td class="px-2 py-2">106–110</td></tr>
            <tr><td class="px-2 py-2 font-bold text-zinc-100">36 / XL</td><td class="px-2 py-2">92–98</td><td class="px-2 py-2">108–112</td></tr>
          </tbody>
        </table>
      </div>

      <p class="text-[10px] text-zinc-500 leading-relaxed mb-4">Все замеры в <b class="text-zinc-300">см</b>, приблизительно. Модели отличаются — если не уверен, напиши рост/вес в Telegram, подскажем.</p>
      <a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="block w-full py-3.5 border border-zinc-700 text-center text-[11px] font-black tracking-[0.15em] uppercase text-zinc-200 hover:bg-zinc-100 hover:text-black transition-all">спросить размер в tg</a>
    </div>
  </div>

  <script>
    let cart = JSON.parse(localStorage.getItem('cart')) || [];
    let clientOrders = JSON.parse(localStorage.getItem('client_orders')) || [];
    const selectedSizes = {};
    const selectedColors = {};
    const currentImageIndices = {};
    let appliedPromo = null;

    const I18N = {
      ru: {
        promo_bar: 'открылись · промокод DEAD −5% ·',
        online: 'ОНЛАЙН:',
        nav_shop: 'каталог',
        nav_delivery: 'доставка',
        nav_sizes: 'размеры',
        sizes_title: 'размеры',
        sizes_hint: 'Много позиций oversized. Свободный фит — свой размер. Плотнее — −1.',
        nav_orders: 'заказы',
        nav_cart: 'КОРЗИНА //',
        cart_title: 'корзина',
        checkout: 'оформить',
        checkout_title: 'оформление',
        back: 'НАЗАД',
        submit_order: 'отправить',
        order_accepted: 'заказ принят',
        my_orders: 'мои заказы',
        delivery_title: 'доставка & оплата',
        empty: 'пусто',
        in_cart: 'в корзину',
        related: 'похожие',
        size: 'Выберите размер:',
        color: 'Цвет:',
        desc: 'Описание:',
        search_ph: 'поиск...',
        all: 'Все',
        tshirts: 'Футболки',
        outerwear: 'Худи / Верх',
        pants: 'Штаны',
        shoes: 'Обувь',
        wishlist: 'Избранное',
        add_to_cart: 'в корзину',
        select_size_warn: '⚠ ВЫБЕРИТЕ РАЗМЕР ПЕРЕД ДОБАВЛЕНИЕМ',
        price_note: 'цена без учёта доставки · страны разные',
        open: 'открыть',
        view: 'смотреть',
        total: 'Итого:',
        promo_active: 'ПРОМОКОД АКТИВИРОВАН: −5%',
        promo_first_only: 'ПРОМОКОД ТОЛЬКО НА ПЕРВЫЙ ЗАКАЗ',
        promo_invalid: 'НЕВЕРНЫЙ ПРОМОКОД',
        welcome_title: 'мы открылись.',
        welcome_sub: 'без лишнего шума.',
        welcome_promo: 'промокод DEAD — −5% на первый заказ.',
        to_catalog: 'в каталог',
        toast_cart: 'в корзине'
      },
      en: {
        promo_bar: 'we are open · promo DEAD −5% ·',
        online: 'ONLINE:',
        nav_shop: 'shop',
        nav_delivery: 'shipping',
        nav_sizes: 'sizes',
        sizes_title: 'size guide',
        sizes_hint: 'Many pieces are oversized. Loose fit — your usual size. Cleaner fit — go −1.',
        nav_orders: 'orders',
        nav_cart: 'CART //',
        cart_title: 'cart',
        checkout: 'checkout',
        checkout_title: 'checkout',
        back: 'BACK',
        submit_order: 'submit',
        order_accepted: 'order accepted',
        my_orders: 'my orders',
        delivery_title: 'shipping & payment',
        empty: 'empty',
        in_cart: 'add to cart',
        related: 'related',
        size: 'Select size:',
        color: 'Color:',
        desc: 'Description:',
        search_ph: 'search...',
        all: 'All',
        tshirts: 'Tees',
        outerwear: 'Hoodies / Outer',
        pants: 'Pants',
        shoes: 'Shoes',
        wishlist: 'Wishlist',
        add_to_cart: 'add to cart',
        select_size_warn: '⚠ SELECT SIZE BEFORE ADDING',
        price_note: 'price excludes shipping · varies by country',
        open: 'open',
        view: 'view',
        total: 'Total:',
        promo_active: 'PROMO ACTIVATED: −5%',
        promo_first_only: 'PROMO FOR FIRST ORDER ONLY',
        promo_invalid: 'INVALID PROMO',
        welcome_title: 'we are open.',
        welcome_sub: 'no extra noise.',
        welcome_promo: 'promo DEAD — −5% on first order.',
        to_catalog: 'to catalog',
        toast_cart: 'added to cart'
      }
    };
    let currentLang = localStorage.getItem('da_lang') || 'ru';

    function t(key) {
      return (I18N[currentLang] && I18N[currentLang][key]) || (I18N.ru[key] || key);
    }

    function productName(p) {
      if (!p) return '';
      return currentLang === 'en' ? (p.name_en || p.name) : p.name;
    }

    function productDesc(p) {
      if (!p) return '';
      return currentLang === 'en' ? (p.description_en || p.description || '') : (p.description || '');
    }

    function setLang(lang) {
      currentLang = lang;
      localStorage.setItem('da_lang', lang);
      document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (I18N[lang] && I18N[lang][key]) el.textContent = I18N[lang][key];
      });
      document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (I18N[lang] && I18N[lang][key]) el.placeholder = I18N[lang][key];
      });
      document.getElementById('lang-ru')?.classList.toggle('active', lang === 'ru');
      document.getElementById('lang-en')?.classList.toggle('active', lang === 'en');
      if (typeof updateCartUI === 'function') updateCartUI();
      if (typeof applyFilters === 'function') applyFilters();
      const pm = document.getElementById('product-modal');
      if (pm && !pm.classList.contains('hidden')) closeProductModal();
    }

    function toggleDelivery() {
      document.getElementById('delivery-modal').classList.toggle('hidden');
    }
    function toggleSizeGuide() {
      document.getElementById('size-guide-modal').classList.toggle('hidden');
    }

    function closeWelcome() {
      const m = document.getElementById('welcome-modal');
      if (m) m.classList.add('hidden');
      localStorage.setItem('da_welcome_seen', '1');
    }

    function showWelcomeIfNeeded() {
      if (localStorage.getItem('da_welcome_seen')) return;
      const m = document.getElementById('welcome-modal');
      if (m) m.classList.remove('hidden');
    }

    function isFirstOrder() {
      return !clientOrders || clientOrders.length === 0;
    }

    function checkPromo() {
      const input = document.getElementById('cust-promo');
      const msg = document.getElementById('promo-msg');
      if (!input || !msg) return;
      const code = (input.value || '').trim().toUpperCase();
      appliedPromo = null;
      msg.classList.remove('hidden', 'text-emerald-400', 'text-red-500', 'text-zinc-500');

      if (!code) {
        msg.classList.add('hidden');
        updateCartUI();
        return;
      }

      fetch('/api/validate_promo', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: code, is_first_order: isFirstOrder() })
      })
      .then(r => r.json())
      .then(data => {
        if (data.ok) {
          appliedPromo = { code: data.code, discount_percent: data.discount_percent };
          msg.textContent = 'ПРОМОКОД АКТИВИРОВАН: −' + data.discount_percent + '%';
          msg.classList.add('text-emerald-400');
        } else {
          appliedPromo = null;
          msg.textContent = data.error || 'НЕВЕРНЫЙ ПРОМОКОД';
          msg.classList.add('text-red-500');
        }
        updateCartUI();
      })
      .catch(() => {
        appliedPromo = null;
        msg.textContent = 'ОШИБКА ПРОВЕРКИ';
        msg.classList.add('text-red-500');
        updateCartUI();
      });
    }

    function getCartTotal() {
      let total = cart.reduce((s, i) => s + (i.price_eur || 0), 0);
      if (appliedPromo && appliedPromo.discount_percent) {
        const p = Number(appliedPromo.discount_percent) || 0;
        total = Math.round(total * (100 - p) / 100 * 100) / 100;
      }
      return total;
    }


    let _notifIgnoreCloseUntil = 0;
    function openNotifPanel() {
      const panel = document.getElementById('notif-panel');
      if (!panel) return;
      panel.classList.remove('hidden');
      _notifIgnoreCloseUntil = Date.now() + 300;
      loadNotifications();
    }
    function closeNotifPanel() {
      const panel = document.getElementById('notif-panel');
      if (panel) panel.classList.add('hidden');
    }
    function toggleNotifPanel(e) {
      if (e) {
        e.preventDefault();
        e.stopPropagation();
      }
      const panel = document.getElementById('notif-panel');
      if (!panel) return;
      if (panel.classList.contains('hidden')) openNotifPanel();
      else closeNotifPanel();
    }
    document.addEventListener('click', function(e) {
      if (Date.now() < _notifIgnoreCloseUntil) return;
      const panel = document.getElementById('notif-panel');
      const wrap = document.getElementById('notif-wrap');
      if (!panel || !wrap || panel.classList.contains('hidden')) return;
      if (wrap.contains(e.target) || panel.contains(e.target)) return;
      closeNotifPanel();
    });
    function notifKindIcon(kind) {
      if (kind === 'order') return '📦';
      if (kind === 'promo') return '🏷';
      return '•';
    }
    function escapeNotifHtml(s) {
      return String(s || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
    }
    function renderNotifList(items) {
      const list = document.getElementById('notif-list');
      if (!list) return;
      if (!items || !items.length) {
        list.innerHTML = '<p class="text-[11px] text-zinc-600 px-3 py-6 text-center">пока пусто</p>';
        return;
      }
      list.innerHTML = items.map(n => {
        const unread = n.is_read ? '' : 'border-l-2 border-l-[#ff00a8] bg-white/[0.03]';
        const title = escapeNotifHtml(n.title);
        const body = escapeNotifHtml(n.body);
        const when = escapeNotifHtml(n.created_str);
        return '<button type="button" onclick="markNotifRead(' + n.id + ')" class="w-full text-left px-3.5 py-3.5 border-b border-white/5 hover:bg-white/[0.04] transition-colors ' + unread + '">'
          + '<div class="flex gap-2.5 items-start">'
          + '<span class="text-sm leading-none mt-0.5 shrink-0">' + notifKindIcon(n.kind) + '</span>'
          + '<div class="min-w-0 flex-1 overflow-hidden">'
          + '<p class="notif-item-title text-[12px] sm:text-[11px] font-black text-zinc-100 leading-snug">' + title + '</p>'
          + (body ? '<p class="notif-item-body text-[11px] sm:text-[10px] text-zinc-400 mt-1.5 leading-relaxed">' + body + '</p>' : '')
          + '<p class="text-[9px] text-zinc-600 mt-2">' + when + '</p>'
          + '</div></div></button>';
      }).join('');
    }
    function updateNotifBadge(count) {
      const badge = document.getElementById('notif-badge');
      if (!badge) return;
      if (count > 0) {
        badge.textContent = count > 9 ? '9+' : String(count);
        badge.classList.remove('hidden');
      } else {
        badge.classList.add('hidden');
      }
    }
    function loadNotifications() {
      fetch('/api/notifications').then(r => r.json()).then(data => {
        if (!data.logged_in) {
          const list = document.getElementById('notif-list');
          if (list) list.innerHTML = '<p class="text-[11px] text-zinc-600 px-3 py-6 text-center"><a href="/login" class="text-zinc-300 underline">войти</a>, чтобы видеть уведомления</p>';
          updateNotifBadge(0);
          return;
        }
        renderNotifList(data.items || []);
        updateNotifBadge(data.unread || 0);
      }).catch(() => {});
    }
    function markNotifRead(id) {
      fetch('/api/notifications/read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id })
      }).then(() => loadNotifications()).catch(() => {});
    }
    function markAllNotifsRead() {
      fetch('/api/notifications/read', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ all: true })
      }).then(() => loadNotifications()).catch(() => {});
    }

    function loadUserProfile() {
      fetch('/api/me').then(r => r.json()).then(data => {
        const nav = document.getElementById('nav-account');
        if (nav) {
          nav.href = data.logged_in ? '/account' : '/login';
          nav.title = data.logged_in ? 'Профиль' : 'Вход';
          nav.setAttribute('aria-label', data.logged_in ? 'Профиль' : 'Вход');
          if (data.logged_in) {
            nav.classList.add('text-zinc-50', 'border-zinc-500');
            nav.classList.remove('text-zinc-300');
          } else {
            nav.classList.add('text-zinc-300');
            nav.classList.remove('text-zinc-50', 'border-zinc-500');
          }
        }
        if (data.logged_in && data.user) {
          const tg = document.getElementById('cust-tg');
          const phone = document.getElementById('cust-phone');
          if (tg && !tg.value && data.user.telegram) tg.value = data.user.telegram;
          if (phone && !phone.value && data.user.phone) phone.value = data.user.phone;
          if (Array.isArray(data.user.favorites) && data.user.favorites.length) {
            const serverFavs = data.user.favorites.map(Number);
            const merged = Array.from(new Set(serverFavs.concat(wishlist.map(Number))));
            wishlist = merged;
            localStorage.setItem('wishlist', JSON.stringify(wishlist));
            document.querySelectorAll('[data-wish-id]').forEach(el => {
              const wid = Number(el.getAttribute('data-wish-id'));
              el.innerHTML = wishlist.includes(wid) ? '♥' : '♡';
            });
          }
          window.__preferredSize = data.user.preferred_size || '';
          loadNotifications();
        } else {
          updateNotifBadge(0);
        }
      }).catch(() => {});
    }

    function initRealOnlineCounter() {
      let clientId = sessionStorage.getItem('da_client_id');
      if (!clientId) {
        clientId = 'usr_' + Math.random().toString(36).substring(2, 9) + '_' + Date.now();
        sessionStorage.setItem('da_client_id', clientId);
      }
      function sendHeartbeat() {
        fetch('/api/heartbeat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ client_id: clientId })
        }).catch(() => {});
      }
      sendHeartbeat();
      setInterval(sendHeartbeat, 8000);
    }

    function updateCartUI() {
      const countEl = document.getElementById('count');
      if (countEl) countEl.textContent = cart.length;

      const container = document.getElementById('cart-items');
      if (!container) return;
      container.innerHTML = '';

      if (cart.length === 0) {
        container.innerHTML = '<p class="text-zinc-600 text-center py-12 text-xs font-bold tracking-widest">пусто</p>';
        if(document.getElementById('checkout-btn')) document.getElementById('checkout-btn').style.display = 'none';
        return;
      } else {
        if(document.getElementById('checkout-btn')) document.getElementById('checkout-btn').style.display = 'block';
      }

      cart.forEach((item, i) => {
        const div = document.createElement('div');
        div.className = "flex gap-4 md:gap-6 border-b border-zinc-900 pb-6 items-center fade-in";
        div.innerHTML = `
          <img src="${item.images && item.images[0] ? item.images[0] : ''}" loading="lazy" decoding="async" class="w-14 h-18 md:w-16 md:h-20 object-cover object-top border border-zinc-800">
          <div class="flex-1 min-w-0">
            <p class="font-black text-xs md:text-sm tracking-wider text-zinc-100 truncate uppercase">${productName(item)}</p>
            <p class="text-zinc-300 text-xs md:text-sm mt-1 font-bold">${appliedPromo && appliedPromo.discount_percent ? `<span class="line-through text-zinc-600 mr-1">${item.price_eur}</span><span class="text-emerald-400">${Math.round(item.price_eur * (100 - appliedPromo.discount_percent) / 100 * 100) / 100} EUR</span>` : `${item.price_eur} EUR`}</p>
            <p class="text-[10px] md:text-xs text-zinc-400 tracking-wider mt-1 font-bold uppercase">РАЗМЕР // [${item.selectedSize}]</p>
            ${item.selectedColor ? `<p class="text-[10px] md:text-xs text-zinc-400 tracking-wider mt-0.5 font-bold uppercase">ЦВЕТ // [${item.selectedColor}]</p>` : ''}
          </div>
          <button onclick="removeFromCart(${i})" class="text-zinc-500 hover:text-zinc-100 text-2xl px-2">✕</button>
        `;
        container.appendChild(div);
      });

      const subtotal = cart.reduce((s, i) => s + (i.price_eur || 0), 0);
      const total = getCartTotal();
      const totalDiv = document.createElement('div');
      totalDiv.className = "pt-4 space-y-1";
      if (appliedPromo && appliedPromo.discount_percent && total < subtotal) {
        const saved = Math.round((subtotal - total) * 100) / 100;
        totalDiv.innerHTML = `
          <div class="border border-zinc-800 bg-black/40 p-3 space-y-2">
            <div class="flex justify-between gap-3 text-xs font-bold uppercase tracking-wider">
              <span class="text-zinc-500">Без промо</span>
              <span class="text-zinc-400 line-through">${subtotal} EUR</span>
            </div>
            <div class="flex justify-between gap-3 text-sm font-black uppercase tracking-wider">
              <span class="text-emerald-400">С промо ${appliedPromo.code} (−${appliedPromo.discount_percent}%)</span>
              <span class="text-zinc-50">${total} EUR</span>
            </div>
            <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">экономия ${saved} EUR · без доставки</p>
          </div>
        `;
      } else {
        totalDiv.innerHTML = `<p class="text-sm text-zinc-100 font-black uppercase tracking-wider">Итого: ${subtotal} EUR <span class="text-zinc-500 text-[10px] font-bold">без доставки</span></p>`;
      }
      container.appendChild(totalDiv);
    }

    function toggleCart() {
      document.getElementById('cart-modal').classList.toggle('hidden');
      updateCartUI();
    }

    function toggleOrdersHistory() {
      const modal = document.getElementById('orders-history-modal');
      modal.classList.toggle('hidden');
      if (!modal.classList.contains('hidden')) {
        fetchAndUpdateClientOrders();
      }
    }

    function fetchAndUpdateClientOrders() {
      const container = document.getElementById('client-orders-list');
      if (!container) return;

      if (clientOrders.length === 0) {
        container.innerHTML = '<p class="text-zinc-600 text-center py-12 text-xs font-bold tracking-widest">пока пусто</p>';
        return;
      }

      const orderIds = clientOrders.map(o => o.order_id);

      fetch('/api/my_orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ order_ids: orderIds })
      })
      .then(res => res.json())
      .then(data => {
        if (data.orders) {
          data.orders.forEach(srvOrd => {
            const localOrd = clientOrders.find(o => o.order_id === srvOrd.id);
            if (localOrd) {
              localOrd.status = srvOrd.status;
            }
          });
          localStorage.setItem('client_orders', JSON.stringify(clientOrders));
        }
        renderClientOrdersHTML();
      })
      .catch(() => {
        renderClientOrdersHTML();
      });
    }

    function renderClientOrdersHTML() {
      const container = document.getElementById('client-orders-list');
      if (!container) return;
      container.innerHTML = '';

      if (clientOrders.length === 0) {
        container.innerHTML = '<p class="text-zinc-600 text-center py-12 text-xs font-bold tracking-widest">пока пусто</p>';
        return;
      }

      clientOrders.forEach(ord => {
        let itemsHtml = '';
        ord.items.forEach(it => {
          itemsHtml += `<li class="text-xs text-zinc-300">• ${it.name} — Размер: <b>${it.selectedSize}</b> ${it.selectedColor ? '| Цвет: <b>'+it.selectedColor+'</b>' : ''} (${it.price_eur} EUR)</li>`;
        });

        let statusColor = "text-emerald-400";
        if (ord.status === 'Отменен') statusColor = "text-red-500";
        if (ord.status === 'Отправлен') statusColor = "text-sky-400";
        if (ord.status === 'Доставлен') statusColor = "text-red-400";

        const div = document.createElement('div');
        div.className = "bg-zinc-950 border border-zinc-800 p-4 md:p-6 fade-in";
        div.innerHTML = `
          <div class="flex justify-between items-center border-b border-zinc-900 pb-3 mb-4">
            <span class="text-xs font-black text-red-500 uppercase">${ord.order_id}</span>
            <span class="text-[10px] md:text-xs font-bold ${statusColor} uppercase bg-zinc-900 px-2.5 py-1 border border-zinc-800">Статус: ${ord.status || 'В обработке'}</span>
          </div>
          <p class="text-[10px] text-zinc-500 mb-3">Дата создания: ${ord.time}</p>
          <p class="text-[10px] font-black text-zinc-400 uppercase mb-2">Состав заказа:</p>
          <ul class="space-y-1 mb-4">${itemsHtml}</ul>
          <div class="text-xs text-zinc-400 border-t border-zinc-900 pt-3">
            <p>Telegram: <span class="text-zinc-200">${ord.contacts.telegram || '—'}</span></p>
            <p>Телефон: <span class="text-zinc-200">${ord.contacts.phone || '—'}</span></p>
          </div>
        `;
        container.appendChild(div);
      });
    }

    function removeFromCart(i) {
      cart.splice(i, 1);
      localStorage.setItem('cart', JSON.stringify(cart));
      updateCartUI();
    }

    function openCheckoutForm() {
      if (cart.length === 0) return;
      document.getElementById('cart-main-view').classList.add('hidden');
      document.getElementById('cart-checkout-view').classList.remove('hidden');
    }

    function backToCartItems() {
      document.getElementById('cart-checkout-view').classList.add('hidden');
      document.getElementById('cart-main-view').classList.remove('hidden');
    }

    function formatTgInput(el) {
      let val = el.value.replace(/\s+/g, '');
      val = val.replace(/[^a-zA-Z0-9_@]/g, '');
      if (val.length > 0 && !val.startsWith('@')) {
        val = '@' + val.replace(/@/g, '');
      } else {
        val = '@' + val.slice(1).replace(/@/g, '');
      }
      if (val === '@') val = '';
      el.value = val;
      el.classList.remove('border-red-500');
      document.getElementById('tg-error')?.classList.add('hidden');
    }

    function formatPhoneInput(el) {
      let raw = el.value.replace(/[^\d+]/g, '');
      if (raw.startsWith('+')) {
        raw = '+' + raw.slice(1).replace(/\+/g, '');
      } else if (raw.length > 0) {
        raw = '+' + raw.replace(/\+/g, '');
      }
      el.value = raw.slice(0, 16);
      el.classList.remove('border-red-500');
      document.getElementById('phone-error')?.classList.add('hidden');
    }

    function submitOrder() {
      const tgEl = document.getElementById('cust-tg');
      const phoneEl = document.getElementById('cust-phone');
      const tgErr = document.getElementById('tg-error');
      const phoneErr = document.getElementById('phone-error');

      let tg = (tgEl.value || '').trim();
      if (tg && !tg.startsWith('@')) tg = '@' + tg;
      tgEl.value = tg;

      const phone = (phoneEl.value || '').trim();
      const digitsOnly = phone.replace(/\D/g, '');

      const tgValid = /^@[a-zA-Z][a-zA-Z0-9_]{4,31}$/.test(tg);
      const isRepeatedDigits = /^(\d)\1+$/.test(digitsOnly);
      const phoneValid = /^\+[1-9]\d{9,14}$/.test(phone) && !isRepeatedDigits;

      let hasError = false;

      if (!tgValid) {
        tgErr.textContent = 'Укажи реальный @username (минимум 5 латинских символов)';
        tgErr.classList.remove('hidden');
        tgEl.classList.add('border-red-500');
        hasError = true;
      }

      if (!phoneValid) {
        phoneErr.textContent = 'Укажи реальный номер в международном формате, например +380991234567';
        phoneErr.classList.remove('hidden');
        phoneEl.classList.add('border-red-500');
        hasError = true;
      }

      if (hasError) return;

      const payloadItems = [...cart];
      const subtotal = payloadItems.reduce((s, i) => s + (i.price_eur || 0), 0);
      const total = getCartTotal();
      const contacts = {
        telegram: tg,
        phone: phone,
        promo: appliedPromo ? appliedPromo.code : null,
        subtotal_eur: subtotal,
        total_eur: total,
        discount_percent: appliedPromo ? (appliedPromo.discount_percent || 0) : 0
      };

      fetch('/create_order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: payloadItems, contacts: contacts })
      })
      .then(res => res.json())
      .then(data => {
        if (!data.success) {
          alert(data.error || 'Проверь корректность данных');
          return;
        }
        const newOrderRecord = {
          order_id: data.order_id,
          items: payloadItems,
          contacts: contacts,
          time: new Date().toLocaleString(),
          status: 'В обработке'
        };
        clientOrders.unshift(newOrderRecord);
        localStorage.setItem('client_orders', JSON.stringify(clientOrders));

        cart = [];
        appliedPromo = null;
        localStorage.setItem('cart', JSON.stringify(cart));
        updateCartUI();
        document.getElementById('cart-checkout-view').classList.add('hidden');
        document.getElementById('cart-success-view').classList.remove('hidden');
        setTimeout(() => {
          document.getElementById('cart-success-view').classList.add('hidden');
          document.getElementById('cart-main-view').classList.remove('hidden');
          toggleCart();
        }, 2000);
      });
    }

    window.addEventListener('DOMContentLoaded', () => {
      setLang(currentLang);
      updateCartUI();
      initRealOnlineCounter();
      loadUserProfile();
      showWelcomeIfNeeded();
    });
  </script>
</body>
</html>
"""

_LAST_VISIT_NOTIFY = 0
VISIT_NOTIFY_COOLDOWN = 45

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    global _LAST_VISIT_NOTIFY
    data = request.get_json() or {}
    client_id = data.get('client_id')
    is_new = False
    if client_id:
        is_new = client_id not in ACTIVE_SESSIONS
        ACTIVE_SESSIONS[client_id] = time.time()
    online = get_real_online_count()
    if is_new and client_id:
        now = time.time()
        if now - _LAST_VISIT_NOTIFY >= VISIT_NOTIFY_COOLDOWN:
            _LAST_VISIT_NOTIFY = now
            try:
                _tg_send(f"👁 На сайте новый посетитель\nСейчас онлайн: {online}")
            except Exception as e:
                print("visit notify:", e)
    return jsonify({"online": online})

@app.route('/api/my_orders', methods=['POST'])
def api_my_orders():
    data = request.get_json() or {}
    order_ids = data.get('order_ids', [])
    orders_data = []

    for oid in order_ids:
        ord_db = Order.query.get(oid)
        if ord_db:
            orders_data.append({
                "id": ord_db.id,
                "status": ord_db.status
            })

    return jsonify({"orders": orders_data})

def site_accessible():
    if SITE_OPEN:
        return True
    if SITE_ACCESS_PASSWORD and session.get('site_unlocked'):
        return True
    return False

def render_closed_page(error=None):
    err_html = f'<p class="text-red-500 text-xs font-bold mt-4 tracking-wider">{error}</p>' if error else ''
    pwd_block = ''
    if SITE_ACCESS_PASSWORD:
        pwd_block = f'''
        <form method="POST" action="/unlock" class="mt-10 w-full max-w-xs mx-auto space-y-3">
          <input type="password" name="password" placeholder="код доступа" required
            class="w-full bg-zinc-950 border border-zinc-700 px-4 py-3 text-sm text-zinc-100 text-center focus:outline-none focus:border-zinc-400 font-bold tracking-wider">
          <button type="submit" class="w-full py-3 border border-zinc-600 text-xs font-black tracking-[0.2em] uppercase text-zinc-200 hover:bg-zinc-100 hover:text-black transition-all">
            войти
          </button>
        </form>
        {err_html}
        '''
    return render_template_string('''
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
  <title>DEAD ARCHIVE</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background: #020202; color: #e4e4e7; }
    .gothic { font-family: 'Cinzel', serif; font-weight: 900; }
  </style>
</head>
<body class="min-h-screen flex items-center justify-center p-6">
  <div class="text-center max-w-md">
    <h1 class="text-3xl sm:text-5xl gothic tracking-[0.2em] text-zinc-100">DEAD ARCHIVE</h1>
    <p class="text-xs sm:text-sm text-zinc-500 mt-6 tracking-[0.25em] uppercase font-bold">пока закрыто</p>
    <p class="text-xs text-zinc-600 mt-4 leading-relaxed">скоро. следи за обновлениями.</p>
    ''' + pwd_block + '''
  </div>
</body>
</html>
''')

@app.route('/unlock', methods=['POST'])
def unlock_site():
    if SITE_OPEN:
        return redirect('/')
    pw = request.form.get('password', '')
    if SITE_ACCESS_PASSWORD and pw == SITE_ACCESS_PASSWORD:
        session['site_unlocked'] = True
        return redirect('/')
    return render_closed_page(error='неверный код')

@app.route('/')
def home():
    if not site_accessible():
        return render_closed_page()
    try:
        maybe_auto_shuffle()
    except Exception:
        pass
    return render_template_string(HTML_HEADER + '''
  <section id="home-hero" class="home-hero min-h-[100svh] h-[100svh] flex items-center justify-center bg-cover bg-center bg-no-repeat relative px-3 overflow-hidden" style="background-image: linear-gradient(180deg, #0a0608 0%, #120810 40%, #080406 100%);">
    <video id="home-bg-video" class="home-bg-video absolute inset-0 w-full h-full object-cover" src="" data-note="upload your video later" autoplay muted loop playsinline webkit-playsinline preload="auto" disablepictureinpicture controlslist="nodownload noplaybackrate" aria-hidden="true"></video>
    <div class="home-video-shade absolute inset-0 bg-black/40"></div>
    <div class="relative text-center z-10 px-4 sm:px-6 w-full max-w-5xl mx-auto">
      <p class="text-xs sm:text-sm tracking-[0.3em] text-[#ff00a8] font-black uppercase mb-4 sm:mb-6 drop-shadow">Opium</p>
      <h1 class="text-4xl sm:text-6xl md:text-7xl lg:text-[7rem] gothic tracking-[0.1em] sm:tracking-[0.15em] md:tracking-[0.2em] text-zinc-100 leading-none drop-shadow-[0_4px_12px_rgba(0,0,0,0.9)]">DEAD ARCHIVE</h1>
      <p class="text-sm sm:text-base md:text-lg tracking-[0.2em] sm:tracking-[0.25em] text-zinc-200 mt-6 sm:mt-8 font-bold uppercase drop-shadow-[0_2px_8px_rgba(0,0,0,0.9)] px-2">мы открылись.</p>
      <p class="text-xs sm:text-sm text-zinc-400 mt-4 sm:mt-5 tracking-wider max-w-lg mx-auto leading-relaxed px-4">Промокод <span class="text-zinc-200 font-bold">DEAD</span> — −5% на первый заказ.</p>
      <div class="mt-8 sm:mt-12 md:mt-16 flex flex-wrap justify-center gap-4 sm:gap-6">
        <a href="/shop" class="btn-lift px-8 sm:px-10 md:px-14 py-4 sm:py-5 border border-zinc-500 hover:bg-zinc-100 hover:text-black text-xs sm:text-sm tracking-[0.25em] text-zinc-100 font-black uppercase backdrop-blur-sm bg-black/40 shadow-lg">в каталог</a>
      </div>
    </div>
  </section>
  <script>
  (() => {
    const video = document.getElementById('home-bg-video');
    if (!video) return;

    video.muted = true;
    video.defaultMuted = true;
    video.setAttribute('muted', '');
    video.setAttribute('autoplay', '');
    video.setAttribute('loop', '');
    video.setAttribute('playsinline', '');
    video.setAttribute('webkit-playsinline', '');
    video.setAttribute('disablepictureinpicture', '');
    video.playsInline = true;
    video.loop = true;
    video.controls = false;

    let retryTimer = null;
    let retryDelay = 700;
    const attemptPlay = () => {
      if (document.visibilityState === 'hidden') return;
      video.muted = true;
      video.defaultMuted = true;
      try {
        const result = video.play();
        if (result && typeof result.catch === 'function') {
          result.then(() => { retryDelay = 700; }).catch(() => {
            if (retryTimer) clearTimeout(retryTimer);
            retryTimer = setTimeout(attemptPlay, Math.min(retryDelay, 5000));
            retryDelay = Math.min(retryDelay * 1.6, 5000);
          });
        }
      } catch (_) {
        if (retryTimer) clearTimeout(retryTimer);
        retryTimer = setTimeout(attemptPlay, Math.min(retryDelay, 5000));
        retryDelay = Math.min(retryDelay * 1.6, 5000);
      }
    };

    ['loadedmetadata', 'loadeddata', 'canplay', 'canplaythrough', 'playing'].forEach(eventName => {
      video.addEventListener(eventName, () => {
        if (eventName !== 'playing') attemptPlay();
      });
    });
    video.addEventListener('pause', () => {
      if (!video.ended && document.visibilityState === 'visible') attemptPlay();
    });
    video.addEventListener('ended', () => {
      try { video.currentTime = 0; } catch (_) {}
      attemptPlay();
    });
    video.addEventListener('stalled', () => {
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(attemptPlay, 1200);
    });
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') attemptPlay();
    });
    window.addEventListener('pageshow', attemptPlay);
    window.addEventListener('focus', attemptPlay);
    window.addEventListener('online', attemptPlay);
    attemptPlay();
  })();
  </script>
''' + HTML_FOOTER)


@app.route('/lookbook')
def lookbook():
    if not site_accessible():
        return render_closed_page()
    products = [p.to_dict() for p in Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()]
    if not products:
        outfits = []
    else:
        tops = [p for p in products if p.get('category') in ('tshirts', 'outerwear')]
        bottoms = [p for p in products if p.get('category') == 'pants']
        shoes = [p for p in products if p.get('category') == 'shoes']
        extras = [p for p in products if p.get('category') not in ('tshirts', 'outerwear', 'pants', 'shoes')]
        looks_spec = [
            ('01 / DEAD SIGNAL', 'AFTER HOURS', 'Тёмная база, свободный силуэт и минимум лишнего.', 0),
            ('02 / PINK STATIC', 'TOXIC ROMANCE', 'Акцентная вещь и грубые формы для ночного города.', 1),
            ('03 / LOST FILE', 'ARCHIVE UNIFORM', 'Собранный повседневный образ из вещей архива.', 2),
            ('04 / NO FUTURE', 'UNDERGROUND', 'Уличный комплект с тяжёлым настроением.', 3),
            ('05 / SYSTEM ERROR', 'GLITCH FIT', 'Нарушай правила сочетаний и собирай собственный силуэт.', 4),
            ('06 / FINAL FORM', 'VOID WALKER', 'Финальный образ для тех, кто не хочет сливаться с толпой.', 5),
        ]
        outfits = []
        for code, title, desc, idx in looks_spec:
            chosen = []
            pools = [tops, bottoms, shoes, extras]
            for pool_i, pool in enumerate(pools):
                if pool:
                    item = pool[(idx + (1 if pool_i == 2 else 0)) % len(pool)]
                    if item not in chosen:
                        chosen.append(item)
            if len(chosen) < 2:
                for item in products:
                    if item not in chosen:
                        chosen.append(item)
                    if len(chosen) >= min(3, len(products)):
                        break
            if not chosen:
                continue
            outfits.append({'code': code, 'title': title, 'description': desc, 'items': chosen,
                            'total_eur': sum(int(x.get('price_eur') or 0) for x in chosen),
                            'total_uah': sum(int(x.get('price_uah') or 0) for x in chosen)})
    try:
        managed = LookbookLook.query.filter_by(active=True).order_by(LookbookLook.sort_order.asc(), LookbookLook.id.asc()).all()
        if managed:
            by_id = {int(p['id']): p for p in products}
            outfits = []
            for look in managed:
                chosen = [by_id[pid] for pid in look.product_ids() if pid in by_id]
                if not chosen:
                    continue
                outfits.append({'code': look.code, 'title': look.title, 'description': look.description or '', 'items': chosen,
                                'total_eur': sum(int(x.get('price_eur') or 0) for x in chosen),
                                'total_uah': sum(int(x.get('price_uah') or 0) for x in chosen)})
    except Exception as e:
        print('lookbook managed looks:', e)
    return render_template_string(HTML_HEADER + '''
<section class="min-h-screen pt-28 sm:pt-32 pb-20 px-3 sm:px-6 relative bg-cover bg-center" style="background-image:linear-gradient(180deg,#0a0608,#120810 50%,#080406)">
 <div class="absolute inset-0 bg-black/70"></div>
 <div class="relative z-10 max-w-7xl mx-auto">
  <div class="text-center mb-10 sm:mb-14">
   <p class="text-[#ff70c8] text-[10px] sm:text-xs tracking-[.45em] font-black uppercase mb-4">DEAD ARCHIVE / EDITORIAL FILES</p>
   <h1 class="text-4xl sm:text-6xl md:text-8xl font-black uppercase tracking-tight text-white" style="text-shadow:0 0 28px #ff00a855">UNDERGROUND<br><span style="color:#ff70c8">LOOKBOOK</span></h1>
   <p class="max-w-xl mx-auto mt-5 text-xs sm:text-sm text-zinc-300 tracking-wider leading-6">ГОТОВЫЕ ФИТЫ ИЗ ТОВАРОВ, КОТОРЫЕ УЖЕ ЕСТЬ В DEAD ARCHIVE. ВЫБИРАЙ ОБРАЗ И ПЕРЕХОДИ К ВЕЩАМ.</p>
   <div class="mt-5 text-[10px] text-zinc-500 tracking-[.25em] uppercase">{{ outfits|length }} looks // {{ products|length }} archived pieces</div>
  </div>
  {% if not outfits %}<div class="border border-[#ff70c8]/30 bg-black/60 p-8 text-center text-zinc-300">Архив пока пуст. Добавь товары в каталоге — и образы появятся здесь.</div>{% endif %}
  <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-6">
   {% for look in outfits %}
   <article class="group border border-white/10 bg-black/65 backdrop-blur-md hover:border-[#ff70c8]/70 transition-all duration-300 overflow-hidden">
    <div class="flex items-center justify-between px-4 py-3 border-b border-white/10"><span class="text-[9px] tracking-[.25em] font-black text-[#ff70c8]">{{ look.code }}</span><span class="text-[9px] tracking-widest text-zinc-500">{{ '%02d'|format(loop.index) }} / {{ '%02d'|format(outfits|length) }}</span></div>
    <div class="grid grid-cols-{{ 2 if look['items']|length < 3 else 3 }} gap-px bg-white/10">
     {% for item in look['items'] %}
     <a href="/shop" class="relative block aspect-[3/4] bg-zinc-950 overflow-hidden" title="Открыть каталог: {{ item.name|e }}">
      {% if item.images and item.images[0] %}<img src="{{ item.images[0]|e }}" alt="{{ item.name|e }}" loading="lazy" class="w-full h-full object-cover opacity-85 group-hover:opacity-100 transition duration-500 group-hover:scale-105">{% else %}<div class="w-full h-full flex items-center justify-center text-[#ff70c8] text-3xl">✳</div>{% endif %}
      <div class="absolute inset-x-0 bottom-0 p-2 bg-gradient-to-t from-black via-black/75 to-transparent"><p class="text-[9px] sm:text-[10px] font-black text-white leading-4">{{ item.name }}</p><p class="text-[9px] text-[#ff70c8] mt-1">€{{ item.price_eur }}</p></div>
     </a>
     {% endfor %}
    </div>
    <div class="p-4 sm:p-5">
     <h2 class="text-xl sm:text-2xl font-black tracking-wider text-white uppercase">{{ look.title }}</h2>
     <p class="text-xs leading-5 text-zinc-400 mt-2 min-h-[40px]">{{ look.description }}</p>
     <div class="flex items-end justify-between gap-3 mt-5 pt-4 border-t border-white/10"><div><p class="text-[9px] tracking-[.2em] text-zinc-500 uppercase">fit total</p><p class="text-lg font-black text-[#ff70c8]">€{{ look.total_eur }} <span class="text-xs text-zinc-400">/ ₴{{ look.total_uah }}</span></p></div><a href="/shop" class="inline-flex items-center justify-center min-h-[42px] px-4 border border-[#ff70c8]/70 text-[10px] font-black tracking-widest text-white hover:bg-[#ff70c8] hover:text-black transition uppercase">К вещам ↗</a></div>
    </div>
   </article>
   {% endfor %}
  </div>
  <p class="text-center text-[9px] sm:text-[10px] tracking-[.2em] text-zinc-600 mt-10 uppercase">Styling ideas by DEAD ARCHIVE · наличие и цены проверяй в каталоге</p>
 </div>
</section>
''' + HTML_FOOTER, products=products, outfits=outfits)

@app.route('/shop')
def shop():
    if not site_accessible():
        return render_closed_page()
    try:
        maybe_auto_shuffle()
    except Exception as e:
        print('shop auto shuffle:', e)
    products = [p.to_dict() for p in Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()]
    return render_template_string(HTML_HEADER + '''
  <section class="shop-section min-h-screen pt-28 sm:pt-32 md:pt-40 pb-20 sm:pb-20 md:pb-24 px-2 sm:px-4 md:px-6 relative bg-cover bg-center bg-no-repeat" style="background-image: linear-gradient(180deg, #0a0608 0%, #120810 40%, #080406 100%);">
    <div class="absolute inset-0 bg-black/55"></div>
    <div class="relative z-10 max-w-7xl mx-auto">
      <h2 class="collection-title text-xl sm:text-3xl md:text-5xl font-black text-center mb-2 sm:mb-3 tracking-[0.2em] sm:tracking-[0.35em] text-zinc-50 uppercase drop-shadow-[0_4px_12px_rgba(0,0,0,0.9)]">COLLECTION</h2>
      <p class="text-center text-[10px] sm:text-[11px] font-bold tracking-[0.25em] uppercase text-zinc-500 mb-4 sm:mb-8">
        <span id="archive-count">—</span> pieces in archive
      </p>

      <div class="mb-4 sm:mb-8 space-y-2.5 sm:space-y-3">
        <div class="relative">
          <input type="text" id="search-input" placeholder="поиск..." data-i18n-placeholder="search_ph"
            class="w-full bg-black/50 border border-white/10 px-3 sm:px-4 py-3 min-h-[44px] text-sm text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-[#ff00a8]/80 focus:bg-black/70 font-bold tracking-wider uppercase transition-all"
            oninput="debouncedFilter()">
        </div>
        <div class="filters-row flex flex-wrap gap-2 justify-center">
          <button onclick="setCategory('all')" id="cat-all" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-[#ff00a8] bg-[#ff00a8] text-white transition-all" data-i18n="all">Все</button>
          <button onclick="setCategory('tshirts')" id="cat-tshirts" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-white/10 bg-black/40 text-zinc-400 hover:border-[#ff00a8]/70 hover:text-zinc-100 transition-all" data-i18n="tshirts">Футболки</button>
          <button onclick="setCategory('outerwear')" id="cat-outerwear" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-white/10 bg-black/40 text-zinc-400 hover:border-[#ff00a8]/70 hover:text-zinc-100 transition-all" data-i18n="outerwear">Худи / Верх</button>
          <button onclick="setCategory('pants')" id="cat-pants" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-white/10 bg-black/40 text-zinc-400 hover:border-[#ff00a8]/70 hover:text-zinc-100 transition-all" data-i18n="pants">Штаны</button>
          <button onclick="setCategory('shoes')" id="cat-shoes" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-white/10 bg-black/40 text-zinc-400 hover:border-[#ff00a8]/70 hover:text-zinc-100 transition-all">Обувь</button>
          <button onclick="setCategory('wishlist')" id="cat-wishlist" class="filter-btn px-3 sm:px-4 py-2 text-[10px] sm:text-xs font-black uppercase border border-white/10 bg-black/40 text-zinc-400 hover:border-[#ff00a8]/70 hover:text-zinc-100 transition-all">♥ <span data-i18n="wishlist">Избранное</span></button>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div class="flex flex-wrap gap-2 items-center">
            <label class="text-[9px] font-black uppercase tracking-wider text-zinc-500">сорт</label>
            <select id="sort-select" onchange="currentPage=1;applyFilters()" class="bg-black/60 border border-white/10 text-[10px] sm:text-[11px] font-bold uppercase tracking-wider text-zinc-300 px-2 py-2 min-h-[40px] focus:outline-none focus:border-[#ff00a8]/70">
              <option value="default">по умолчанию</option>
              <option value="new">сначала новые</option>
              <option value="price_asc">цена ↑</option>
              <option value="price_desc">цена ↓</option>
              <option value="popular">популярные</option>
            </select>
            <label class="flex items-center gap-1.5 text-[9px] font-black uppercase tracking-wider text-zinc-500 cursor-pointer select-none">
              <input type="checkbox" id="only-stock" onchange="currentPage=1;applyFilters()" class="accent-[#ff00a8]">
              только в наличии
            </label>
          </div>
          <p id="filter-hint" class="text-[9px] text-zinc-600 font-bold uppercase tracking-wider"></p>
        </div>
      </div>

      <div id="recent-wrap" class="hidden mb-5 sm:mb-7">
        <div class="flex items-center justify-between mb-2 px-0.5">
          <p class="text-[10px] font-black uppercase tracking-[0.2em] text-zinc-500">недавно смотрел</p>
          <button type="button" onclick="clearRecentViewed()" class="text-[9px] font-bold uppercase tracking-wider text-zinc-600 hover:text-zinc-300">очистить</button>
        </div>
        <div id="recent-row" class="flex gap-2 overflow-x-auto pb-1" style="-webkit-overflow-scrolling:touch"></div>
      </div>

      <div class="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-4 gap-2.5 sm:gap-4 md:gap-6" id="grid">
        <div class="bg-zinc-950 border border-zinc-800 p-2 sm:p-3 animate-pulse">
          <div class="w-full h-44 sm:h-64 bg-zinc-900 border border-zinc-800"></div>
          <div class="h-3 bg-zinc-800 rounded mt-4 w-3/4"></div>
          <div class="h-3 bg-zinc-800 rounded mt-2 w-1/3"></div>
          <div class="h-10 bg-zinc-800 rounded mt-4"></div>
        </div>
        <div class="bg-zinc-950 border border-zinc-800 p-2 sm:p-3 animate-pulse">
          <div class="w-full h-44 sm:h-64 bg-zinc-900 border border-zinc-800"></div>
          <div class="h-3 bg-zinc-800 rounded mt-4 w-3/4"></div>
          <div class="h-3 bg-zinc-800 rounded mt-2 w-1/3"></div>
          <div class="h-10 bg-zinc-800 rounded mt-4"></div>
        </div>
        <div class="bg-zinc-950 border border-zinc-800 p-3 sm:p-4 animate-pulse hidden lg:block">
          <div class="w-full h-64 sm:h-72 bg-zinc-900 border border-zinc-800"></div>
          <div class="h-3 bg-zinc-800 rounded mt-4 w-3/4"></div>
          <div class="h-3 bg-zinc-800 rounded mt-2 w-1/3"></div>
          <div class="h-10 bg-zinc-800 rounded mt-4"></div>
        </div>
        <div class="bg-zinc-950 border border-zinc-800 p-3 sm:p-4 animate-pulse hidden xl:block">
          <div class="w-full h-64 sm:h-72 bg-zinc-900 border border-zinc-800"></div>
          <div class="h-3 bg-zinc-800 rounded mt-4 w-3/4"></div>
          <div class="h-3 bg-zinc-800 rounded mt-2 w-1/3"></div>
          <div class="h-10 bg-zinc-800 rounded mt-4"></div>
        </div>
      </div>
      <p id="no-results" class="hidden text-center text-zinc-500 text-xs font-bold tracking-widest py-16 uppercase">ничего нет</p>
      <div id="pagination" class="flex flex-wrap items-center justify-center gap-2 mt-10 sm:mt-12"></div>
    </div>
  </section>

  <!-- МОДАЛКА ДЕТАЛЬНОЙ КАРТОЧКИ ТОВАРА -->
  <div id="product-modal" class="hidden fixed inset-0 bg-black/95 flex items-stretch sm:items-center justify-center z-[9999] backdrop-blur-md p-0 sm:p-3 md:p-6 overflow-y-auto">
    <div class="bg-[#0a0a0b] w-full max-w-5xl p-0 sm:p-0 border-0 sm:border border-zinc-800 relative sm:my-auto max-h-[100dvh] sm:max-h-[92vh] overflow-y-auto rounded-none sm:rounded-sm">
      <button onclick="closeProductModal()" class="absolute top-2 right-2 sm:top-3 sm:right-3 text-zinc-400 hover:text-white text-xl transition-colors z-20 w-10 h-10 flex items-center justify-center bg-black/60 border border-white/10">✕</button>
      <div id="modal-content" class="grid grid-cols-1 md:grid-cols-2 gap-0 items-stretch pt-0">
      </div>
    </div>
  </div>

  <script>
    const products = {{ products|tojson|safe }};
    let currentCategory = 'all';
    let wishlist = JSON.parse(localStorage.getItem('wishlist') || '[]');

    (function initArchiveExtras(){
      const countEl = document.getElementById('archive-count');
      if (countEl) countEl.textContent = String(products.length).padStart(2, '0');
      const bar = document.getElementById('scroll-progress');
      const onScroll = () => {
        if (!bar) return;
        const h = document.documentElement;
        const max = h.scrollHeight - h.clientHeight;
        const p = max > 0 ? (h.scrollTop / max) * 100 : 0;
        bar.style.width = p + '%';
      };
      window.addEventListener('scroll', onScroll, { passive: true });
      onScroll();
    })();

    function openImageZoom(src) {
      const zoom = document.getElementById('image-zoom');
      const img = document.getElementById('zoom-img');
      if (!zoom || !img) return;
      img.src = src;
      zoom.classList.remove('hidden');
      document.body.style.overflow = 'hidden';
    }

    function closeImageZoom() {
      const zoom = document.getElementById('image-zoom');
      if (zoom) zoom.classList.add('hidden');
      document.body.style.overflow = '';
    }

    function showToast(msg) {
      const toast = document.getElementById('toast');
      if (!toast) return;
      toast.textContent = msg || 'в корзине';
      toast.classList.remove('hidden', 'opacity-0', 'translate-y-4', 'scale-95');
      void toast.offsetWidth;
      toast.classList.add('opacity-100', 'translate-y-0', 'scale-100');
      setTimeout(() => {
        toast.classList.remove('opacity-100', 'translate-y-0', 'scale-100');
        toast.classList.add('opacity-0', 'translate-y-4', 'scale-95');
        setTimeout(() => toast.classList.add('hidden'), 350);
      }, 2300);
    }

    function animateAddToCart(btn) {
      if (!btn) return;
      btn.classList.add('scale-95', 'bg-black', 'text-white');
      setTimeout(() => {
        btn.classList.remove('scale-95', 'bg-black', 'text-white');
      }, 180);

      const cartBtn = document.getElementById('cart-nav-btn');
      if (cartBtn) {
        cartBtn.classList.add('scale-110');
        setTimeout(() => cartBtn.classList.remove('scale-110'), 300);
      }
    }

    function toggleWishlist(id, btn) {
      id = Number(id);
      const idx = wishlist.indexOf(id);
      if (idx === -1) {
        wishlist.push(id);
        if (btn) btn.innerHTML = '♥';
        showToast('в избранном');
      } else {
        wishlist.splice(idx, 1);
        if (btn) btn.innerHTML = '♡';
        showToast('убрано');
      }
      localStorage.setItem('wishlist', JSON.stringify(wishlist));
      document.querySelectorAll('[data-wish-id]').forEach(el => {
        const wid = Number(el.getAttribute('data-wish-id'));
        el.innerHTML = wishlist.includes(wid) ? '♥' : '♡';
      });
      if (currentCategory === 'wishlist') applyFilters();
      fetch('/api/favorites', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ product_id: id })
      }).then(r => r.json()).then(data => {
        if (data && data.ok && Array.isArray(data.favorites)) {
          wishlist = data.favorites.map(Number);
          localStorage.setItem('wishlist', JSON.stringify(wishlist));
          document.querySelectorAll('[data-wish-id]').forEach(el => {
            const wid = Number(el.getAttribute('data-wish-id'));
            el.innerHTML = wishlist.includes(wid) ? '♥' : '♡';
          });
        }
      }).catch(() => {});
    }

    function isInWishlist(id) {
      return wishlist.includes(Number(id));
    }

    const PER_PAGE = 12;
    let currentPage = 1;
    let filteredProducts = [];

    function setCategory(cat) {
      currentCategory = cat;
      currentPage = 1;
      document.querySelectorAll('.filter-btn').forEach(b => {
        b.classList.remove('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]', 'bg-zinc-100', 'text-black', 'border-zinc-100');
        b.classList.add('bg-[#0c0c0e]', 'text-zinc-300', 'border-[#1f1f24]');
      });
      const active = document.getElementById('cat-' + cat);
      if (active) {
        active.classList.remove('bg-[#0c0c0e]', 'text-zinc-300', 'border-[#1f1f24]', 'bg-zinc-900', 'border-zinc-700');
        active.classList.add('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]');
      }
      applyFilters();
    }

    let filterTimeout = null;
    function debouncedFilter() {
      clearTimeout(filterTimeout);
      filterTimeout = setTimeout(() => { currentPage = 1; applyFilters(); }, 180);
    }

    function applyFilters() {
      const query = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
      const sort = document.getElementById('sort-select')?.value || 'default';
      const onlyStock = !!document.getElementById('only-stock')?.checked;
      let filtered = products.slice();

      if (currentCategory === 'wishlist') {
        filtered = filtered.filter(p => wishlist.includes(p.id));
      } else if (currentCategory !== 'all') {
        filtered = filtered.filter(p => p.category === currentCategory);
      }

      if (onlyStock) {
        filtered = filtered.filter(p => p.in_stock !== false);
      }

      if (query) {
        filtered = filtered.filter(p => {
          const n = (productName(p) || '').toLowerCase();
          const n2 = (p.name || '').toLowerCase();
          const n3 = (p.name_en || '').toLowerCase();
          return n.includes(query) || n2.includes(query) || n3.includes(query);
        });
      }

      if (sort === 'new') {
        filtered.sort((a, b) => (b.id || 0) - (a.id || 0));
      } else if (sort === 'price_asc') {
        filtered.sort((a, b) => (a.price_eur || 0) - (b.price_eur || 0));
      } else if (sort === 'price_desc') {
        filtered.sort((a, b) => (b.price_eur || 0) - (a.price_eur || 0));
      } else if (sort === 'popular') {
        filtered.sort((a, b) => (b.views || 0) - (a.views || 0) || (b.id || 0) - (a.id || 0));
      } else {
        filtered.sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0) || a.id - b.id);
      }

      filteredProducts = filtered;
      const totalPages = Math.max(1, Math.ceil(filteredProducts.length / PER_PAGE));
      if (currentPage > totalPages) currentPage = totalPages;
      const start = (currentPage - 1) * PER_PAGE;
      renderProducts(filteredProducts.slice(start, start + PER_PAGE));
      renderPagination();
      const hint = document.getElementById('filter-hint');
      if (hint) hint.textContent = filtered.length ? (filtered.length + ' шт') : 'ничего не найдено';
    }

    function goToPage(page) {
      currentPage = page;
      applyFilters();
      const grid = document.getElementById('grid');
      if (grid) grid.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function renderPagination() {
      const el = document.getElementById('pagination');
      if (!el) return;
      const total = filteredProducts.length;
      const totalPages = Math.ceil(total / PER_PAGE);
      if (totalPages <= 1) {
        el.innerHTML = total ? `<p class="text-[10px] text-zinc-500 font-bold tracking-wider uppercase">${total} товаров</p>` : '';
        return;
      }
      let html = `<button onclick="goToPage(${Math.max(1, currentPage-1)})" class="px-3 py-2 text-xs border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-zinc-400 disabled:opacity-30" ${currentPage<=1?'disabled':''}>←</button>`;
      for (let i = 1; i <= totalPages; i++) {
        if (totalPages > 7 && Math.abs(i - currentPage) > 2 && i !== 1 && i !== totalPages) {
          if (i === 2 || i === totalPages - 1) html += `<span class="text-zinc-600 px-1">…</span>`;
          continue;
        }
        const active = i === currentPage ? 'bg-zinc-100 text-black border-zinc-100' : 'bg-zinc-900 text-zinc-300 border-zinc-700 hover:border-zinc-400';
        html += `<button onclick="goToPage(${i})" class="px-3 py-2 text-xs border font-bold ${active}">${i}</button>`;
      }
      html += `<button onclick="goToPage(${Math.min(totalPages, currentPage+1)})" class="px-3 py-2 text-xs border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-zinc-400" ${currentPage>=totalPages?'disabled':''}>→</button>`;
      html += `<span class="text-[10px] text-zinc-500 font-bold tracking-wider uppercase ml-2">${total} шт</span>`;
      el.innerHTML = html;
    }

    function shareProductById(id) {
      const p = products.find(x => x.id === id);
      if (!p) return;
      const text = productName(p) + ' — ' + p.price_eur + ' EUR\\nDEAD ARCHIVE';
      if (navigator.share) {
        navigator.share({ title: productName(p), text: text }).catch(() => {});
      } else {
        navigator.clipboard.writeText(text).then(() => showToast('СКОПИРОВАНО')).catch(() => showToast('НЕ УДАЛОСЬ СКОПИРОВАТЬ'));
      }
    }

    function selectSizeModal(id, sz, btn) {
      selectedSizes[id] = sz;
      const errEl = document.getElementById('modal-error-' + id);
      if (errEl) errEl.classList.add('hidden');

      document.querySelectorAll('.modal-size-btn-' + id).forEach(b => {
        b.classList.remove('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]', 'bg-zinc-100', 'text-black', 'border-zinc-100');
        b.classList.add('bg-zinc-900', 'text-zinc-300', 'border-zinc-700');
      });
      btn.classList.remove('bg-zinc-900', 'text-zinc-300', 'border-zinc-700');
      btn.classList.add('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]');
    }

    function selectColorModal(id, col, btn) {
      selectedColors[id] = col;
      document.querySelectorAll('.modal-color-btn-' + id).forEach(b => {
        b.classList.remove('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]', 'bg-zinc-100', 'text-black', 'border-zinc-100');
        b.classList.add('bg-zinc-900', 'text-zinc-300', 'border-zinc-700');
      });
      btn.classList.remove('bg-zinc-900', 'text-zinc-300', 'border-zinc-700');
      btn.classList.add('bg-[#ff00a8]', 'text-white', 'border-[#ff00a8]');
    }

    function changeModalImage(id, index) {
      currentImageIndices[id] = index;
      const p = products.find(x => x.id === id);
      if (p && p.images[index]) {
        const imgEl = document.getElementById('modal-main-img-' + id);
        if (imgEl) imgEl.src = p.images[index];

        document.querySelectorAll('.modal-thumb').forEach(t => t.classList.remove('border-zinc-100'));
        const activeThumb = document.getElementById('thumb-' + id + '-' + index);
        if (activeThumb) activeThumb.classList.add('border-zinc-100');
      }
    }

    function openProductModal(id) {
      const p = products.find(x => x.id === id);
      if (!p) return;
      trackProductView(id);

      currentImageIndices[id] = 0;
      if (!selectedColors[id] && p.colors && p.colors.length > 0) {
        selectedColors[id] = p.colors[0];
      }

      const modalContent = document.getElementById('modal-content');

      let thumbsHtml = '';
      if (p.images && p.images.length > 1) {
        thumbsHtml += '<div class="flex gap-1.5 mt-2 overflow-x-auto pb-1">';
        p.images.forEach((img, idx) => {
          const activeBorder = idx === 0 ? 'border-zinc-200' : 'border-zinc-800';
          thumbsHtml += `<img id="thumb-${p.id}-${idx}" src="${img}" onclick="changeModalImage(${p.id}, ${idx})" class="modal-thumb w-12 h-14 md:w-14 md:h-16 object-cover object-top border ${activeBorder} cursor-pointer hover:border-zinc-500 transition-all flex-shrink-0">`;
        });
        thumbsHtml += '</div>';
      }

      let sizesHtml = '<div class="flex flex-wrap gap-2 mt-3">';
      if (p.sizes && p.sizes.length > 0) {
        p.sizes.forEach((sz, idx) => {
          const isSelected = (!selectedSizes[p.id] && idx === 0) || (selectedSizes[p.id] === sz);
          if (isSelected && !selectedSizes[p.id]) selectedSizes[p.id] = sz;

          const btnClasses = isSelected
            ? 'bg-[#ff00a8] text-white border-[#ff00a8]'
            : 'bg-zinc-900 text-zinc-300 border-zinc-700';

          sizesHtml += `<button onclick="selectSizeModal(${p.id}, '${sz}', this)" class="modal-size-btn-${p.id} px-3.5 py-2.5 min-h-[40px] min-w-[44px] text-xs border ${btnClasses} transition-colors font-bold active:scale-95">${sz}</button>`;
        });
      }
      sizesHtml += '</div>';

      let colorsHtml = '';
      if (p.colors && p.colors.length > 0) {
        colorsHtml += `<div class="flex flex-wrap gap-2 mt-4"><span class="w-full text-[10px] font-bold text-zinc-500 uppercase tracking-widest">${t('color')}</span>`;
        p.colors.forEach((col, idx) => {
          const isSelected = selectedColors[p.id] === col;
          const btnClasses = isSelected
            ? 'bg-[#ff00a8] text-white border-[#ff00a8]'
            : 'bg-zinc-900 text-zinc-300 border-zinc-700';

          colorsHtml += `<button onclick="selectColorModal(${p.id}, '${col}', this)" class="modal-color-btn-${p.id} px-3 py-2 min-h-[36px] text-xs border ${btnClasses} transition-colors uppercase font-bold active:scale-95">${col}</button>`;
        });
        colorsHtml += '</div>';
      }

      const related = products
        .filter(x => x.category === p.category && x.id !== p.id)
        .slice(0, 4);
      let relatedHtml = '';
      if (related.length > 0) {
        relatedHtml = `<div class="col-span-1 md:col-span-2 border-t border-zinc-800 p-4 sm:p-6 bg-[#070708]">
          <p class="text-[11px] font-black text-[#c084fc] uppercase tracking-[0.2em] mb-4">${t('related')}</p>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-3">
            ${related.map(r => `
              <div class="cursor-pointer group" onclick="openProductModal(${r.id})">
                <div class="border border-zinc-800 overflow-hidden mb-2">
                  <img src="${r.images && r.images[0] ? r.images[0] : ''}" class="w-full h-28 sm:h-32 object-cover object-top group-hover:scale-105 transition-transform duration-300">
                </div>
                <p class="text-[10px] font-bold text-zinc-200 uppercase truncate leading-tight">${productName(r)}</p>
                <p class="text-[10px] text-zinc-400 font-bold mt-0.5">${r.price_eur} EUR</p>
              </div>
            `).join('')}
          </div>
        </div>`;
      }

      const stockLabel = p.in_stock === false
        ? '<span class="text-red-400 border border-red-900/50 px-2 py-0.5">sold out</span>'
        : '<span class="text-zinc-400 border border-zinc-700 px-2 py-0.5">in stock</span>';
      const catLabel = (p.category || '').replace('tshirts','футболки').replace('outerwear','верх').replace('pants','штаны').replace('shoes','обувь');

      modalContent.innerHTML = `
        <div class="bg-[#070708] border-b md:border-b-0 md:border-r border-zinc-800 p-3 sm:p-5 md:p-6 flex flex-col">
          <div class="relative overflow-hidden bg-black border border-zinc-800">
            <img id="modal-main-img-${p.id}" src="${p.images && p.images[0] ? p.images[0] : ''}" onclick="openImageZoom(this.src)"
              class="w-full h-[320px] sm:h-[420px] md:h-[520px] object-cover object-top cursor-zoom-in">
          </div>
          ${thumbsHtml}
          <div class="mt-4 sm:mt-5">
            <div class="flex flex-wrap items-center gap-2 text-[9px] font-bold uppercase tracking-[0.16em] text-zinc-500 mb-2">
              <span>${catLabel}</span>
              ${stockLabel}
            </div>
            <h2 class="text-lg sm:text-xl md:text-2xl font-black text-zinc-50 uppercase leading-tight tracking-wide">${productName(p)}</h2>
            <p class="mt-2 sm:mt-3 text-xl sm:text-2xl font-black text-zinc-100">${p.price_eur} <span class="text-sm font-bold text-zinc-500">EUR</span>
              <span class="text-xs font-semibold text-zinc-600 ml-2">~${p.price_uah} UAH</span>
            </p>
            <p class="mt-2 text-[10px] text-zinc-600 font-bold uppercase tracking-wider">${t('price_note')}</p>
          </div>
        </div>

        <div class="p-4 sm:p-6 md:p-8 flex flex-col bg-[#0a0a0b]">
          <div class="flex-1">
            <p class="text-[10px] font-black uppercase tracking-[0.2em] text-zinc-500 mb-2">${t('desc')}</p>
            <p class="text-sm text-zinc-300 leading-relaxed mb-5">${productDesc(p) || (currentLang === 'en' ? 'No description.' : 'Описание скоро.')}</p>

            <div class="h-px bg-zinc-800 mb-5"></div>

            ${colorsHtml}

            <div class="mt-4">
              <div class="flex justify-between items-center gap-2 mb-1">
                <span class="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">${t('size')}</span>
                <button type="button" onclick="toggleSizeGuide()" class="text-[10px] font-black uppercase tracking-wider text-zinc-400 hover:text-white">size guide</button>
              </div>
              ${sizesHtml}
            </div>

            <p id="modal-error-${p.id}" class="text-red-500 text-[10px] font-bold mt-3 hidden uppercase tracking-wider">${t('select_size_warn')}</p>

            <div class="mt-5 p-3 border border-zinc-800 bg-black/40">
              <p class="text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1">доставка / заказ</p>
              <p class="text-[11px] text-zinc-400 leading-relaxed">После оформления менеджер напишет в Telegram. Сроки и стоимость доставки зависят от страны.</p>
            </div>
          </div>

          <div class="mt-6 sm:mt-8 flex gap-2 sticky bottom-0 pt-2 bg-[#0a0a0b]">
            <button onclick="addToCartFromModal(${p.id})" class="btn-lift flex-1 py-3.5 sm:py-4 min-h-[48px] bg-zinc-100 text-black text-xs font-black uppercase tracking-[0.15em] hover:bg-white border border-zinc-100">
              ${t('add_to_cart')}
            </button>
            <button onclick="toggleWishlist(${p.id}, this)" data-wish-id="${p.id}" class="w-12 sm:w-14 min-h-[48px] border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-red-900 hover:text-red-300 text-lg flex items-center justify-center transition-all">
              ${isInWishlist(p.id) ? '♥' : '♡'}
            </button>
            <button onclick="shareProductById(${p.id})" class="w-12 sm:w-14 min-h-[48px] border border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-zinc-400 text-sm flex items-center justify-center transition-all" title="Share">
              ↗
            </button>
          </div>
        </div>
        ${relatedHtml}
      `;

      document.getElementById('product-modal').classList.remove('hidden');
    }

    function closeProductModal() {
      document.getElementById('product-modal').classList.add('hidden');
    }

    const _maxProductId = products.reduce((m, p) => Math.max(m, p.id || 0), 0);
    const _newThreshold = Math.max(0, _maxProductId - 7);
    function isNewDrop(p) {
      return (p.id || 0) >= _newThreshold && _maxProductId > 0;
    }

    function updateCategoryCounts() {
      const counts = { all: products.length, tshirts: 0, outerwear: 0, pants: 0, shoes: 0 };
      products.forEach(p => {
        if (counts[p.category] !== undefined) counts[p.category]++;
      });
      const map = {
        'cat-all': ['Все', counts.all],
        'cat-tshirts': ['Футболки', counts.tshirts],
        'cat-outerwear': ['Худи / Верх', counts.outerwear],
        'cat-pants': ['Штаны', counts.pants],
        'cat-shoes': ['Обувь', counts.shoes],
      };
      Object.keys(map).forEach(id => {
        const el = document.getElementById(id);
        if (!el) return;
        const [label, n] = map[id];
        if (n > 0 || id === 'cat-all') el.innerHTML = label + ' <span class="opacity-50">' + n + '</span>';
        else el.innerHTML = label;
      });
      const ac = document.getElementById('archive-count');
      if (ac) ac.textContent = String(products.length);
    }

    function getRecentViewed() {
      try {
        const raw = JSON.parse(localStorage.getItem('recent_viewed') || '[]');
        return Array.isArray(raw) ? raw.map(Number).filter(Boolean) : [];
      } catch (e) { return []; }
    }
    function saveRecentViewed(ids) {
      localStorage.setItem('recent_viewed', JSON.stringify(ids.slice(0, 12)));
    }
    function pushRecentViewed(id) {
      id = Number(id);
      if (!id) return;
      let ids = getRecentViewed().filter(x => x !== id);
      ids.unshift(id);
      saveRecentViewed(ids);
      renderRecentViewed();
    }
    function clearRecentViewed() {
      localStorage.removeItem('recent_viewed');
      renderRecentViewed();
    }
    function renderRecentViewed() {
      const wrap = document.getElementById('recent-wrap');
      const row = document.getElementById('recent-row');
      if (!wrap || !row) return;
      const ids = getRecentViewed();
      const items = ids.map(id => products.find(p => p.id === id)).filter(Boolean).slice(0, 8);
      if (!items.length) {
        wrap.classList.add('hidden');
        row.innerHTML = '';
        return;
      }
      wrap.classList.remove('hidden');
      row.innerHTML = items.map(p => {
        const img = (p.images && p.images[0]) ? p.images[0] : '';
        return '<button type="button" onclick="openProductModal(' + p.id + ')" class="shrink-0 w-20 sm:w-24 text-left group">'
          + '<div class="aspect-[3/4] bg-zinc-900 border border-white/10 overflow-hidden mb-1">'
          + '<img src="' + img + '" alt="" class="w-full h-full object-cover object-top group-hover:opacity-90" loading="lazy">'
          + '</div>'
          + '<p class="text-[9px] font-bold text-zinc-400 uppercase truncate leading-tight">' + productName(p) + '</p>'
          + '<p class="text-[9px] text-zinc-600 font-bold">' + p.price_eur + '€</p>'
          + '</button>';
      }).join('');
    }

    function trackProductView(id) {
      pushRecentViewed(id);
      fetch('/api/track_view', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: id })
      }).then(r => r.json()).then(d => {
        if (d && d.ok) {
          const p = products.find(x => x.id === id);
          if (p) p.views = d.views;
        }
      }).catch(() => {});
    }

    function renderProducts(list) {
      const grid = document.getElementById('grid');
      const noResults = document.getElementById('no-results');
      if (!grid) return;

      const items = list || products;

      if (items.length === 0) {
        grid.innerHTML = '';
        if (noResults) noResults.classList.remove('hidden');
        return;
      }
      if (noResults) noResults.classList.add('hidden');

      const fragment = document.createDocumentFragment();
      const maxAnim = Math.min(items.length, 12);

      items.forEach((p, index) => {
        const div = document.createElement('div');
        div.className = "product-card group relative bg-[#0a0a0b] border border-zinc-800/80 overflow-hidden flex flex-col" + (index < maxAnim ? " fade-in" : "");
        if (index < maxAnim) div.style.animationDelay = (index * 40) + 'ms';

        const imgSrc = (p.images && p.images[0]) ? p.images[0] : '';
        const wish = isInWishlist(p.id) ? '♥' : '♡';

        div.innerHTML = `
          <div class="card-img-wrap relative aspect-[3/4] overflow-hidden cursor-pointer bg-zinc-900" onclick="openProductModal(${p.id})">
            <img src="${imgSrc}" loading="lazy" decoding="async" width="400" height="500" class="w-full h-full object-cover object-top transition-transform duration-500 group-hover:scale-[1.03]">
            ${p.in_stock===false?'<span class="absolute bottom-2 left-2 text-[9px] font-black bg-black/80 text-red-400 border border-red-900/50 px-2 py-0.5 tracking-wider">SOLD OUT</span>':''}
            ${isNewDrop(p)?'<span class="absolute top-2 left-2 text-[9px] font-black bg-black/70 text-red-300 border border-red-950/40 px-2 py-0.5 tracking-wider">NEW</span>':''}
            ${(!isNewDrop(p) && (p.views||0)>=15)?'<span class="absolute top-2 left-2 text-[9px] font-black bg-black/70 text-zinc-200 border border-zinc-700 px-2 py-0.5 tracking-wider">HOT</span>':''}
            <button type="button" onclick="event.stopPropagation(); toggleWishlist(${p.id}, this)" data-wish-id="${p.id}"
              class="absolute top-2 right-2 w-8 h-8 flex items-center justify-center text-sm bg-black/50 border border-white/10 text-zinc-300 hover:text-red-300 hover:border-red-900/40 transition-all">${wish}</button>
          </div>
          <div class="p-2.5 sm:p-3 flex flex-col gap-1.5 border-t border-zinc-800/80">
            <h3 class="text-[11px] sm:text-xs font-bold text-zinc-100 uppercase tracking-wide leading-snug line-clamp-2 min-h-[2.4em]">${productName(p)}</h3>
            <div class="flex items-center justify-between gap-2">
              <p class="text-sm font-black text-zinc-50">${p.price_eur} <span class="text-[10px] font-bold text-zinc-500">EUR</span></p>
              <button type="button" onclick="openProductModal(${p.id})" class="text-[10px] font-black uppercase tracking-[0.14em] text-zinc-500 hover:text-zinc-100 transition-colors">${t('view')} →</button>
            </div>
          </div>
        `;
        fragment.appendChild(div);
      });

      grid.innerHTML = '';
      grid.appendChild(fragment);
    }

    function addToCartFromModal(id) {
      const p = products.find(x => x.id === id);
      const sz = selectedSizes[id];
      const col = selectedColors[id] || (p.colors && p.colors[0] ? p.colors[0] : '');

      if (!sz && p.sizes && p.sizes.length > 0) {
        const errEl = document.getElementById('modal-error-' + id);
        if (errEl) errEl.classList.remove('hidden');
        return;
      }

      cart.push({ ...p, selectedSize: sz || (p.sizes[0] || 'ONE SIZE'), selectedColor: col });
      localStorage.setItem('cart', JSON.stringify(cart));
      updateCartUI();

      const addBtn = document.querySelector(`button[onclick="addToCartFromModal(${id})"]`);
      animateAddToCart(addBtn);

      showToast('в корзине');
      closeProductModal();
      setTimeout(() => toggleCart(), 450);
    }

    window.addEventListener('DOMContentLoaded', () => {
      updateCategoryCounts();
      renderRecentViewed();
      setCategory('all');
    });
  </script>
''' + HTML_FOOTER, products=products)


import json as _json
import urllib.parse
import urllib.request

def _tg_api(method, payload_dict):
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    if not token:
        return None
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in payload_dict.items() if v is not None}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print("tg api error", method, e)
        return None

def _tg_send(text_msg, reply_markup=None, chat_id=None):
    chat_id = (chat_id or TELEGRAM_CHAT_ID or "").strip()
    token = (TELEGRAM_BOT_TOKEN or "").strip()
    if not token or not chat_id:
        print("Telegram: token/chat_id missing")
        return False
    payload = {
        "chat_id": chat_id,
        "text": text_msg,
        "disable_web_page_preview": "1",
    }
    if reply_markup:
        payload["reply_markup"] = _json.dumps(reply_markup, ensure_ascii=False)
    res = _tg_api("sendMessage", payload)
    return bool(res and res.get("ok"))

def _order_total(items):
    total = 0
    for it in (items or []):
        try:
            total += int(it.get("price_eur") or 0)
        except Exception:
            pass
    return total

def _format_items(items):
    lines = []
    for it in (items or []):
        name = it.get("name") or "Товар"
        size = it.get("selectedSize") or "—"
        color = it.get("selectedColor") or ""
        price = it.get("price_eur") or 0
        extra = f" · {color}" if color else ""
        lines.append(f"• {name} [{size}]{extra} — {price}€")
    return "\n".join(lines) if lines else "—"

def _order_keyboard(order_id, tg_username=""):
    tg_clean = (tg_username or "").replace("@", "").strip()
    rows = [
        [
            {"text": "✅ Принял", "callback_data": f"ord:{order_id}:accept"},
            {"text": "📦 Отправлен", "callback_data": f"ord:{order_id}:shipped"},
        ],
        [
            {"text": "⏳ В обработке", "callback_data": f"ord:{order_id}:pending"},
            {"text": "❌ Отменён", "callback_data": f"ord:{order_id}:cancel"},
        ],
    ]
    if tg_clean:
        rows.append([{"text": "👤 Написать клиенту", "url": f"https://t.me/{tg_clean}"}])
    return {"inline_keyboard": rows}

def send_telegram_order(order_id, items, contacts, time_str):
    tg = (contacts or {}).get("telegram") or "—"
    phone = (contacts or {}).get("phone") or "—"
    subtotal = _order_total(items)
    promo_code = (contacts or {}).get("promo") or ""
    try:
        disc = int((contacts or {}).get("discount_percent") or 0)
    except Exception:
        disc = 0
    paid = (contacts or {}).get("total_eur")
    try:
        paid = float(paid) if paid is not None else float(subtotal)
    except Exception:
        paid = float(subtotal)
    if promo_code and disc > 0:
        price_line = (
            f"💰 Без промо: {subtotal} EUR\n"
            f"🏷 С промо {promo_code} (−{disc}%): {paid:g} EUR\n"
            f"💵 К оплате: {paid:g} EUR (без доставки)"
        )
    else:
        price_line = f"💰 Итого: {subtotal} EUR (без доставки)"
    text_msg = (
        f"🛒 НОВЫЙ ЗАКАЗ {order_id}\n"
        f"⏰ {time_str}\n\n"
        f"{_format_items(items)}\n\n"
        f"{price_line}\n"
        f"👤 Telegram: {tg}\n"
        f"📞 Телефон: {phone}\n"
        f"📌 Статус: В обработке\n\n"
        f"Команды: /orders /stats /find {order_id}"
    )
    ok = _tg_send(text_msg, reply_markup=_order_keyboard(order_id, tg if tg != "—" else ""))
    if ok:
        print("Telegram: уведомление отправлено", order_id)
    return ok

def notify_sold_out(product_name, product_id):
    _tg_send(f"⚠️ SOLD OUT\n#{product_id} {product_name}\nТовар отмечен как нет в наличии.")

def _cmd_orders():
    orders = Order.query.order_by(Order.time_str.desc()).limit(8).all()
    if not orders:
        return "Заказов пока нет."
    lines = ["📦 Последние заказы:\n"]
    for o in orders:
        try:
            items = _json.loads(o.items_json)
            contacts = _json.loads(o.contacts_json)
        except Exception:
            items, contacts = [], {}
        total = _order_total(items)
        tg = (contacts or {}).get("telegram") or "—"
        lines.append(f"{o.id} · {o.status} · ~{total}€ · {tg}\n{o.time_str}")
    return "\n\n".join(lines)

def _cmd_stats():
    today = time.strftime("%Y-%m-%d")
    orders = Order.query.all()
    today_orders = [o for o in orders if (o.time_str or "").startswith(today)]
    total_sum = 0
    week_sum = 0
    week_n = 0
    now = time.time()
    for o in orders:
        try:
            items = _json.loads(o.items_json)
        except Exception:
            items = []
        tot = _order_total(items)
        if (o.time_str or "").startswith(today):
            total_sum += tot
        try:
            day = (o.time_str or "")[:10]
            if len(day) == 10:
                tstruct = time.strptime(day, "%Y-%m-%d")
                if now - time.mktime(tstruct) <= 7 * 86400:
                    week_sum += tot
                    week_n += 1
        except Exception:
            pass
    pending = len([o for o in orders if o.status == "В обработке"])
    shipped = len([o for o in orders if o.status == "Отправлен"])
    users = Customer.query.count()
    sold_out = Product.query.filter_by(in_stock=False).count()
    prods = Product.query.count()
    return (
        f"📊 Статистика\n"
        f"Сегодня: {len(today_orders)} зак. · ~{total_sum}€\n"
        f"7 дней: {week_n} зак. · ~{week_sum}€\n"
        f"Всего заказов: {len(orders)}\n"
        f"В обработке: {pending} · Отправлено: {shipped}\n"
        f"Юзеры: {users}\n"
        f"Товары: {prods} · sold out: {sold_out}"
    )

def _cmd_find(order_id):
    order_id = (order_id or "").strip().upper()
    if not order_id.startswith("ORD-"):
        order_id = "ORD-" + order_id.replace("ORD-", "")
    o = Order.query.get(order_id)
    if not o:
        all_o = Order.query.all()
        o = next((x for x in all_o if order_id in x.id), None)
    if not o:
        return f"Заказ {order_id} не найден."
    try:
        items = _json.loads(o.items_json)
        contacts = _json.loads(o.contacts_json)
    except Exception:
        items, contacts = [], {}
    tg = (contacts or {}).get("telegram") or "—"
    phone = (contacts or {}).get("phone") or "—"
    return (
        f"🔎 {o.id}\n"
        f"⏰ {o.time_str}\n"
        f"📌 {o.status}\n\n"
        f"{_format_items(items)}\n\n"
        f"💰 ~{_order_total(items)} EUR\n"
        f"👤 {tg}\n"
        f"📞 {phone}\n"
        f"📝 {(o.note or '—')}"
    )

def _cmd_pending():
    orders = Order.query.filter_by(status="В обработке").order_by(Order.time_str.desc()).limit(12).all()
    if not orders:
        return "✅ Нет заказов в обработке."
    lines = [f"⏳ В обработке ({len(orders)}):\n"]
    for o in orders:
        try:
            items = _json.loads(o.items_json)
            contacts = _json.loads(o.contacts_json)
        except Exception:
            items, contacts = [], {}
        total = _order_total(items)
        tg = (contacts or {}).get("telegram") or "—"
        lines.append(f"{o.id} · ~{total}€ · {tg}\n{o.time_str}")
    return "\n\n".join(lines)

def _cmd_stock():
    sold = Product.query.filter_by(in_stock=False).order_by(Product.id.desc()).limit(20).all()
    if not sold:
        return "✅ Все товары в наличии."
    lines = [f"⚠️ Sold out ({len(sold)}):\n"]
    for p in sold:
        lines.append(f"#{p.id} {p.name} — {p.price_eur}€")
    return "\n".join(lines)

def _cmd_promos():
    promos = PromoCode.query.order_by(PromoCode.id.desc()).limit(15).all()
    if not promos:
        return "Промокодов нет."
    lines = ["🏷 Промокоды:\n"]
    for pr in promos:
        st = "ON" if pr.active else "OFF"
        lim = f"{pr.uses_count or 0}"
        lim += f"/{pr.max_uses}" if pr.max_uses else "/∞"
        lines.append(f"{pr.code} −{pr.discount_percent}% · {st} · {lim}")
    return "\n".join(lines)

def _cmd_users():
    users = Customer.query.order_by(Customer.id.desc()).limit(10).all()
    total = Customer.query.count()
    if not users:
        return "Пользователей пока нет."
    lines = [f"👥 Пользователи (всего {total}), последние:\n"]
    for u in users:
        ver = "✓" if getattr(u, "email_verified", False) else "✗"
        tg = u.telegram or "—"
        lines.append(f"#{u.id} {u.email} [{ver}]\nTG: {tg} · {u.created_str or '—'}")
    return "\n\n".join(lines)

def _cmd_top():
    prods = Product.query.order_by(Product.views.desc()).limit(8).all()
    if not prods:
        return "Нет товаров."
    lines = ["🔥 Топ по просмотрам:\n"]
    for i, p in enumerate(prods, 1):
        stock = "" if p.in_stock else " [sold out]"
        lines.append(f"{i}. {p.name} — {p.views or 0} views · {p.price_eur}€{stock}")
    return "\n".join(lines)

def _cmd_help():
    return (
        "🩸 DEAD ARCHIVE bot\n\n"
        "Заказы:\n"
        "/orders — последние 8\n"
        "/pending — только «в обработке»\n"
        "/find ORD-12345 — найти заказ\n"
        "/status ORD-12345 отправлен — сменить статус\n\n"
        "Магазин:\n"
        "/stats — статистика\n"
        "/stock — sold out\n"
        "/promos — промокоды\n"
        "/top — топ товаров\n"
        "/users — последние юзеры\n\n"
        "Под уведомлением о заказе — кнопки статусов."
    )

def _set_order_status(order_id, status):
    o = Order.query.get(order_id)
    if not o:
        return None, "Заказ не найден"
    o.status = status
    db.session.commit()
    try:
        if o.user_id:
            create_notification(
                o.user_id,
                f"Заказ {o.id}: {status}",
                "Статус твоего заказа обновлён. Смотри детали в профиле.",
                "order",
            )
    except Exception as ne:
        print("notif status:", ne)
    try:
        admin_log("tg_status", f"{order_id} → {status}")
    except Exception:
        pass
    return o, None

@app.route("/telegram_webhook", methods=["POST"])
def telegram_webhook():
    try:
        update = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"ok": True})

    cq = update.get("callback_query")
    if cq:
        data = (cq.get("data") or "")
        cq_id = cq.get("id")
        msg = cq.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        from_id = str((cq.get("from") or {}).get("id") or "")
        admin_id = str(TELEGRAM_CHAT_ID or "").strip()
        if admin_id and from_id and from_id != admin_id:
            _tg_api("answerCallbackQuery", {"callback_query_id": cq_id, "text": "Нет доступа", "show_alert": "true"})
            return jsonify({"ok": True})

        parts = data.split(":")
        if len(parts) == 3 and parts[0] == "ord":
            oid, action = parts[1], parts[2]
            status_map = {
                "accept": "В обработке",
                "pending": "В обработке",
                "shipped": "Отправлен",
                "cancel": "Отменен",
            }
            status = status_map.get(action)
            if status:
                o, err = _set_order_status(oid, status)
                if err:
                    _tg_api("answerCallbackQuery", {"callback_query_id": cq_id, "text": err, "show_alert": "true"})
                else:
                    label = {"accept": "Принят в работу", "pending": "В обработке", "shipped": "Отправлен", "cancel": "Отменён"}.get(action, status)
                    _tg_api("answerCallbackQuery", {"callback_query_id": cq_id, "text": f"Статус: {label}"})
                    try:
                        old_text = msg.get("text") or ""
                        new_text = old_text
                        if "📌 Статус:" in old_text:
                            import re
                            new_text = re.sub(r"📌 Статус:.*", f"📌 Статус: {status}", old_text)
                        else:
                            new_text = old_text + f"\n📌 Статус: {status}"
                        _tg_api("editMessageText", {
                            "chat_id": str(chat),
                            "message_id": str(msg.get("message_id")),
                            "text": new_text,
                            "reply_markup": _json.dumps(_order_keyboard(oid), ensure_ascii=False),
                            "disable_web_page_preview": "1",
                        })
                    except Exception as e:
                        print("edit msg", e)
        return jsonify({"ok": True})

    message = update.get("message") or update.get("edited_message") or {}
    text_in = (message.get("text") or "").strip()
    chat_id = str((message.get("chat") or {}).get("id") or "")
    admin_id = str(TELEGRAM_CHAT_ID or "").strip()
    if not text_in:
        return jsonify({"ok": True})
    if admin_id and chat_id and chat_id != admin_id:
        _tg_send("Нет доступа.", chat_id=chat_id)
        return jsonify({"ok": True})

    low = text_in.lower()
    if low.startswith("/start") or low.startswith("/help"):
        _tg_send(_cmd_help())
    elif low.startswith("/orders"):
        _tg_send(_cmd_orders())
    elif low.startswith("/pending"):
        _tg_send(_cmd_pending())
    elif low.startswith("/stats"):
        _tg_send(_cmd_stats())
    elif low.startswith("/stock"):
        _tg_send(_cmd_stock())
    elif low.startswith("/promos") or low.startswith("/promo"):
        _tg_send(_cmd_promos())
    elif low.startswith("/users"):
        _tg_send(_cmd_users())
    elif low.startswith("/top"):
        _tg_send(_cmd_top())
    elif low.startswith("/find"):
        parts = text_in.split(maxsplit=1)
        q = parts[1] if len(parts) > 1 else ""
        if not q:
            _tg_send("Использование: /find ORD-12345")
        else:
            _tg_send(_cmd_find(q))
    elif low.startswith("/status"):
        parts = text_in.split(maxsplit=2)
        if len(parts) < 3:
            _tg_send("Использование: /status ORD-12345 отправлен\nСтатусы: принят | в обработке | отправлен | отменен")
        else:
            oid = parts[1].strip()
            st_raw = parts[2].strip().lower()
            status_map = {
                "принят": "Принят", "accept": "Принят",
                "в обработке": "В обработке", "pending": "В обработке",
                "отправлен": "Отправлен", "shipped": "Отправлен",
                "отменен": "Отменен", "отменён": "Отменен", "cancel": "Отменен",
            }
            status = status_map.get(st_raw)
            if not status:
                _tg_send("Неизвестный статус. Варианты: принят / в обработке / отправлен / отменен")
            else:
                oid_u = oid.upper()
                if not oid_u.startswith("ORD-"):
                    oid_u = "ORD-" + oid_u.replace("ORD-", "")
                o, err = _set_order_status(oid_u, status)
                if err:
                    all_o = Order.query.all()
                    hit = next((x for x in all_o if oid_u.replace("ORD-", "") in x.id), None)
                    if hit:
                        o, err = _set_order_status(hit.id, status)
                if err:
                    _tg_send(err)
                else:
                    _tg_send(f"✅ {o.id} → {status}")
    else:
        _tg_send("Неизвестная команда. /help — список")

    return jsonify({"ok": True})

@app.route("/cron/daily_report")
def cron_daily_report():
    secret = request.args.get("key", "")
    expected = (os.environ.get("CRON_KEY") or "deadarchive_cron")
    if secret != expected:
        return "forbidden", 403
    msg = "🌙 Дневной отчёт\n\n" + _cmd_stats()
    _tg_send(msg)
    return "ok"


def send_email(to_email, subject, body_text):
    host = (SMTP_HOST or "").strip()
    user = (SMTP_USER or "").strip()
    password = (SMTP_PASSWORD or "").strip()
    if not host or not user or not password:
        print("SMTP not configured. Email to", to_email)
        print(subject)
        print(body_text)
        return False
    try:
        from_addr = (SMTP_FROM or user).strip()
        msg = MIMEMultipart("alternative")
        msg["From"] = "Dead Archive Shop <" + from_addr + ">"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Reply-To"] = from_addr
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        with smtplib.SMTP(host, SMTP_PORT, timeout=20) as server:
            server.starttls()
            server.login(user, password)
            server.sendmail(from_addr, [to_email], msg.as_string())
        print("Email sent to", to_email)
        return True
    except Exception as e:
        print("Email error:", e)
        return False

def send_verification_email(user):
    token = user.verify_token or ""
    link = SITE_BASE_URL.rstrip("/") + "/verify_email?token=" + token
    body = (
        "Привет.\n\n"
        "Ты создал аккаунт в магазине Dead Archive Shop.\n"
        "Чтобы войти, открой эту ссылку и подтверди почту:\n\n"
        + link
        + "\n\n"
        "Ссылка действует один раз. Если ты не регистрировался — просто удали письмо.\n\n"
        "— команда Dead Archive Shop\n"
        + SITE_BASE_URL.rstrip("/")
    )
    return send_email(user.email, "Ссылка для входа в аккаунт", body)

def create_notification(user_id, title, body='', kind='info'):
    if not user_id:
        return
    try:
        n = Notification(
            user_id=int(user_id),
            title=(title or 'Уведомление')[:200],
            body=(body or '')[:1000],
            kind=(kind or 'info')[:40],
            is_read=False,
            created_str=time.strftime('%Y-%m-%d %H:%M:%S'),
        )
        db.session.add(n)
        db.session.commit()
    except Exception as e:
        print('create_notification:', e)
        try:
            db.session.rollback()
        except Exception:
            pass

def _current_customer():
    uid = session.get('user_id')
    if not uid:
        return None
    return Customer.query.get(uid)

AUTH_SHELL = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
  <title>__TITLE__ // DEAD ARCHIVE</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@400;500;700;900&family=Pirata+One&display=swap" rel="stylesheet">
  <style>
    body { font-family: Inter, sans-serif; background: #0b070b; color: #f4f4f5; }
    .gothic { font-family: Cinzel, serif; font-weight: 900; }
    .auth-card { background: linear-gradient(180deg, #0e0e12 0%, #0a0a0c 100%); }
    input:focus { outline: none; border-color: #ff00a8 !important; }
  </style>
</head>
<body class="min-h-screen flex items-center justify-center p-4 relative">
  <div class="pointer-events-none fixed inset-0 opacity-40" style="background:radial-gradient(ellipse at 50% 0%,rgba(232,77,155,0.16),transparent 55%),radial-gradient(ellipse at 80% 100%,rgba(88,28,135,0.1),transparent 50%);"></div>
  <div class="relative w-full max-w-lg auth-card border border-white/10 p-6 sm:p-8 shadow-2xl shadow-black/50">
    <div class="flex items-center justify-between gap-3 mb-6">
      <a href="/shop" class="text-[10px] font-black tracking-[0.2em] uppercase text-zinc-500 hover:text-zinc-200 transition-colors">← каталог</a>
      <span class="text-[9px] font-black tracking-[0.25em] uppercase text-zinc-600">DEAD ARCHIVE</span>
    </div>
    <h1 class="gothic text-2xl sm:text-3xl tracking-[0.15em] text-zinc-50 mb-1">__TITLE__</h1>
    <div class="h-px w-14 bg-gradient-to-r from-[#ff00a8] to-transparent mb-6"></div>
    __FLASH__
    __BODY__
  </div>
</body>
</html>
"""

def _auth_render(title, body, error=None, ok=None):
    flash_html = ""
    if error:
        flash_html = f'<div class="mb-5 px-3 py-2.5 border border-red-900/60 bg-red-950/40 text-red-400 text-xs font-bold">{error}</div>'
    if ok:
        flash_html = f'<div class="mb-5 px-3 py-2.5 border border-emerald-900/50 bg-emerald-950/30 text-emerald-400 text-xs font-bold">{ok}</div>'
    return AUTH_SHELL.replace("__TITLE__", title).replace("__FLASH__", flash_html).replace("__BODY__", body)

@app.route('/api/me')
def api_me():
    u = _current_customer()
    if not u:
        return jsonify({"logged_in": False})
    return jsonify({"logged_in": True, "user": u.to_public()})

@app.route('/api/favorites', methods=['POST'])
def api_favorites():
    u = _current_customer()
    if not u:
        return jsonify({"ok": False, "error": "login"}), 401
    data = request.get_json() or {}
    try:
        pid = int(data.get('product_id') or 0)
    except Exception:
        pid = 0
    if not pid:
        return jsonify({"ok": False, "error": "bad id"})
    favs = u.get_favorites()
    if pid in favs:
        favs = [x for x in favs if x != pid]
        action = 'removed'
    else:
        favs = [pid] + favs
        action = 'added'
    u.set_favorites(favs)
    db.session.commit()
    return jsonify({"ok": True, "action": action, "favorites": u.get_favorites()})

@app.route('/api/notifications')
def api_notifications():
    u = _current_customer()
    if not u:
        return jsonify({"logged_in": False, "items": [], "unread": 0})
    items = Notification.query.filter_by(user_id=u.id).order_by(Notification.id.desc()).limit(40).all()
    unread = Notification.query.filter_by(user_id=u.id, is_read=False).count()
    return jsonify({"logged_in": True, "items": [n.to_dict() for n in items], "unread": unread})

@app.route('/api/notifications/read', methods=['POST'])
def api_notifications_read():
    u = _current_customer()
    if not u:
        return jsonify({"ok": False}), 401
    data = request.get_json() or {}
    if data.get('all'):
        Notification.query.filter_by(user_id=u.id, is_read=False).update({"is_read": True})
        db.session.commit()
        return jsonify({"ok": True})
    try:
        nid = int(data.get('id') or 0)
    except Exception:
        nid = 0
    n = Notification.query.filter_by(id=nid, user_id=u.id).first()
    if n:
        n.is_read = True
        db.session.commit()
    return jsonify({"ok": True})


@app.route('/register', methods=['GET', 'POST'])
def register():
    if session.get('user_id'):
        return redirect(url_for('account_profile'))
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        telegram = (request.form.get('telegram') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        if not email or not _re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', email):
            error = 'Введите корректный email'
        elif len(password) < 4:
            error = 'Пароль минимум 4 символа'
        elif Customer.query.filter_by(email=email).first():
            error = 'Этот email уже зарегистрирован'
        else:
            token = secrets.token_urlsafe(32)
            u = Customer(
                email=email,
                password_hash=generate_password_hash(password),
                telegram=telegram,
                phone=phone,
                created_str=time.strftime('%Y-%m-%d %H:%M:%S'),
                email_verified=False,
                verify_token=token,
            )
            db.session.add(u)
            db.session.commit()
            try:
                create_notification(
                    u.id,
                    "Добро пожаловать в DEAD ARCHIVE",
                    "Аккаунт создан. Подтверди email и загляни в каталог.",
                    "system",
                )
            except Exception:
                pass
            sent = send_verification_email(u)
            link = SITE_BASE_URL.rstrip('/') + '/verify_email?token=' + token
            if sent:
                return _auth_render('ПРОВЕРЬ EMAIL',
                    '<p class="text-sm text-zinc-300 leading-relaxed mb-4">Мы отправили ссылку на <b class="text-zinc-100">'
                    + email + '</b>. Открой письмо, подтверди почту и войди.</p>'
                    '<a href="/login" class="block w-full py-3.5 bg-zinc-100 text-black text-center text-xs font-black uppercase tracking-widest">войти</a>'
                    '<p class="text-[11px] text-zinc-500 mt-4 text-center"><a href="/resend_verification?email='
                    + email + '" class="underline text-zinc-300">отправить снова</a></p>')
            return _auth_render('ПОДТВЕРДИ EMAIL',
                '<p class="text-sm text-zinc-300 mb-3">SMTP не настроен — письмо не ушло. Для теста открой ссылку:</p>'
                '<a href="' + link + '" class="block text-xs text-emerald-400 break-all underline mb-6">' + link + '</a>'
                '<a href="/login" class="block w-full py-3.5 bg-zinc-100 text-black text-center text-xs font-black uppercase tracking-widest">войти после подтверждения</a>')
    body = """
    <form method="POST" class="space-y-3">
      <input type="email" name="email" required placeholder="EMAIL" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold uppercase">
      <input type="password" name="password" required minlength="4" placeholder="ПАРОЛЬ" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold">
      <input type="text" name="telegram" placeholder="TELEGRAM @user" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold">
      <input type="text" name="phone" placeholder="ТЕЛЕФОН" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold">
      <button class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black uppercase tracking-widest">создать аккаунт</button>
    </form>
    <p class="text-[11px] text-zinc-500 mt-5 text-center">Уже есть аккаунт? <a href="/login" class="text-zinc-200 underline">войти</a></p>
    """
    return _auth_render('РЕГИСТРАЦИЯ', body, error=error)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('user_id'):
        return redirect(url_for('account_profile'))
    error = None
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        password = request.form.get('password') or ''
        u = Customer.query.filter_by(email=email).first()
        if not u or not check_password_hash(u.password_hash, password):
            error = 'Неверный email или пароль'
        elif not getattr(u, 'email_verified', False):
            error = 'Email не подтверждён. Проверь почту или запроси письмо снова.'
        else:
            session['user_id'] = u.id
            return redirect(url_for('account_profile'))
    body = """
    <form method="POST" class="space-y-3">
      <input type="email" name="email" required placeholder="EMAIL" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold uppercase">
      <input type="password" name="password" required placeholder="ПАРОЛЬ" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold">
      <button class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black uppercase tracking-widest">войти</button>
    </form>
    <p class="text-[11px] text-zinc-500 mt-5 text-center">Нет аккаунта? <a href="/register" class="text-zinc-200 underline">регистрация</a></p>
    <p class="text-[11px] text-zinc-600 mt-2 text-center"><a href="/resend_verification" class="underline">не пришло письмо?</a></p>
    """
    return _auth_render('ВХОД', body, error=error)

@app.route('/logout')
def logout_user():
    session.pop('user_id', None)
    return redirect(url_for('shop'))

@app.route('/verify_email')
def verify_email():
    token = (request.args.get('token') or '').strip()
    if not token:
        return _auth_render('ОШИБКА', '<p class="text-sm text-zinc-400">Нет токена.</p><a href="/login" class="underline text-xs text-zinc-200">войти</a>')
    u = Customer.query.filter_by(verify_token=token).first()
    if not u:
        return _auth_render('ОШИБКА', '<p class="text-sm text-zinc-400">Ссылка недействительна или уже использована.</p><a href="/login" class="underline text-xs text-zinc-200">войти</a>')
    u.email_verified = True
    u.verify_token = None
    db.session.commit()
    return _auth_render('EMAIL ПОДТВЕРЖДЁН',
        '<p class="text-sm text-zinc-300 mb-6">Почта подтверждена. Теперь можно войти.</p>'
        '<a href="/login" class="block w-full py-3.5 bg-zinc-100 text-black text-center text-xs font-black uppercase tracking-widest">войти</a>')

@app.route('/resend_verification', methods=['GET', 'POST'])
def resend_verification():
    error = None
    ok = None
    prefill = (request.args.get('email') or '').strip().lower()
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        u = Customer.query.filter_by(email=email).first()
        if not u:
            error = 'Аккаунт не найден'
        elif u.email_verified:
            ok = 'Email уже подтверждён'
        else:
            u.verify_token = secrets.token_urlsafe(32)
            db.session.commit()
            sent = send_verification_email(u)
            if sent:
                ok = 'Письмо отправлено'
            else:
                link = SITE_BASE_URL.rstrip('/') + '/verify_email?token=' + u.verify_token
                ok = 'SMTP не настроен. Ссылка: ' + link
        prefill = email
    body = (
        '<form method="POST" class="space-y-3">'
        '<input type="email" name="email" required value="' + prefill + '" placeholder="EMAIL" '
        'class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3 text-sm text-zinc-100 font-bold uppercase">'
        '<button class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black uppercase tracking-widest">отправить письмо</button>'
        '</form>'
        '<p class="text-[11px] text-zinc-500 mt-5 text-center"><a href="/login" class="underline text-zinc-300">назад ко входу</a></p>'
    )
    return _auth_render('ПОВТОРНАЯ ОТПРАВКА', body, error=error, ok=ok)


@app.route('/account', methods=['GET', 'POST'])
def account_profile():
    u = _current_customer()
    if not u:
        return redirect(url_for('login'))
    error = None
    ok = None
    if request.method == 'POST':
        action = request.form.get('action') or 'profile'
        if action == 'profile':
            u.telegram = (request.form.get('telegram') or '').strip()
            u.phone = (request.form.get('phone') or '').strip()
            u.preferred_size = (request.form.get('preferred_size') or '').strip()[:20]
            db.session.commit()
            ok = 'данные сохранены'
        elif action == 'password':
            old_pw = request.form.get('old_password') or ''
            new_pw = request.form.get('new_password') or ''
            if not check_password_hash(u.password_hash, old_pw):
                error = 'неверный текущий пароль'
            elif len(new_pw) < 4:
                error = 'новый пароль — минимум 4 символа'
            else:
                u.password_hash = generate_password_hash(new_pw)
                db.session.commit()
                ok = 'пароль обновлён'
        elif action == 'remove_fav':
            try:
                pid = int(request.form.get('product_id') or 0)
            except Exception:
                pid = 0
            if pid:
                u.set_favorites([x for x in u.get_favorites() if x != pid])
                db.session.commit()
                ok = 'убрано из избранного'

    orders = Order.query.filter_by(user_id=u.id).order_by(Order.time_str.desc()).limit(30).all()
    total_spent = 0
    order_rows = []
    for o in orders:
        try:
            items = _json.loads(o.items_json)
        except Exception:
            items = []
        total = 0
        names = []
        for it in items:
            try:
                total += int(it.get('price_eur') or 0)
            except Exception:
                pass
            names.append(it.get('name') or 'item')
        total_spent += total
        st = o.status or 'В обработке'
        if st == 'Отправлен':
            st_style = 'color:#4ade80;border-color:#166534'
        elif st == 'Отменен':
            st_style = 'color:#f87171;border-color:#7f1d1d'
        else:
            st_style = 'color:#fbbf24;border-color:#78350f'
        preview = ', '.join(names[:2])
        if len(names) > 2:
            preview += ' +' + str(len(names) - 2)
        order_rows.append(
            '<article class="ord">'
            '<div class="ord-top">'
            '<span class="ord-id">' + str(o.id) + '</span>'
            '<span class="ord-st" style="' + st_style + '">' + st + '</span>'
            '</div>'
            '<p class="ord-items">' + (preview or '—') + '</p>'
            '<div class="ord-bot">'
            '<span>' + (o.time_str or '') + '</span>'
            '<span class="ord-price">~' + str(total) + ' EUR</span>'
            '</div></article>'
        )
    orders_html = ''.join(order_rows) if order_rows else (
        '<div class="empty">заказов пока нет<br>'
        '<a href="/shop">перейти в каталог →</a></div>'
    )

    fav_ids = u.get_favorites()
    fav_parts = []
    for pid in fav_ids[:12]:
        p = Product.query.get(pid)
        if not p:
            continue
        img = ''
        try:
            imgs = [i.strip() for i in (p.images_str or '').split(',') if i.strip()]
            img = imgs[0] if imgs else ''
        except Exception:
            pass
        name = (p.name or '').replace('<', '')
        stock = '' if getattr(p, 'in_stock', True) else '<span class="sold">sold out</span>'
        fav_parts.append(
            '<article class="fav">'
            '<a href="/shop" class="fav-img"><img src="' + img + '" alt=""></a>'
            '<div class="fav-body">'
            '<p class="fav-name">' + name + '</p>'
            '<p class="fav-price">' + str(p.price_eur or 0) + ' EUR ' + stock + '</p>'
            '<form method="POST">'
            '<input type="hidden" name="action" value="remove_fav">'
            '<input type="hidden" name="product_id" value="' + str(pid) + '">'
            '<button type="submit" class="fav-rm">убрать</button>'
            '</form></div></article>'
        )
    if fav_parts:
        fav_html = '<div class="fav-grid">' + ''.join(fav_parts) + '</div>'
    else:
        fav_html = (
            '<div class="empty">избранное пусто<br>'
            '<span>в каталоге нажми ♥ на товаре</span></div>'
        )

    tg_val = (u.telegram or '').replace('"', '&quot;')
    ph_val = (u.phone or '').replace('"', '&quot;')
    verified = bool(getattr(u, 'email_verified', False))
    size_opts = ''
    for s in ['', 'XS', 'S', 'M', 'L', 'XL', 'XXL']:
        sel = ' selected' if (u.preferred_size or '') == s else ''
        size_opts += '<option value="' + s + '"' + sel + '>' + (s if s else 'не выбран') + '</option>'

    flash = ''
    if error:
        flash = '<div class="flash bad">' + error + '</div>'
    if ok:
        flash = '<div class="flash ok">' + ok + '</div>'
    verify_html = ''
    if not verified:
        verify_html = (
            '<div class="flash warn">email не подтверждён — '
            '<a href="/resend_verification?email=' + u.email + '">отправить письмо ещё раз</a></div>'
        )

    letter = (u.email or '?')[0].upper()
    v_badge = (
        '<span class="badge ok">verified</span>' if verified else
        '<span class="badge no">email?</span>'
    )

    html = (
        '<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">'
        '<title>профиль // dead archive</title>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">'
        '<style>'
        '*{box-sizing:border-box;margin:0;padding:0}'
        'body{font-family:Inter,-apple-system,BlinkMacSystemFont,sans-serif;background-color:#09050a;'
        'background-image:linear-gradient(180deg,rgba(8,3,9,.34),rgba(8,3,9,.62)),none;'
        'background-size:cover;background-position:center center;background-repeat:no-repeat;background-attachment:fixed;'
        'color:#f4eaf2;min-height:100vh;line-height:1.5;-webkit-font-smoothing:antialiased;overflow-x:hidden}'
        '@media(max-width:700px){body{background-attachment:scroll;background-position:center top;background-image:linear-gradient(180deg,rgba(8,3,9,.28),rgba(8,3,9,.56)),none}}'
        '::selection{background:#ff00a8;color:#fff}'
        'a{color:inherit;text-decoration:none}'
        '.nav{position:sticky;top:0;z-index:40;border-bottom:1px solid rgba(255,0,168,.28);background:rgba(9,4,10,.88);'
        'backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}'
        '.nav-in{max-width:980px;margin:0 auto;min-height:60px;padding:8px 18px;display:flex;align-items:center;justify-content:space-between;gap:12px}'
        '.logo{font-size:12px;font-weight:900;letter-spacing:.18em;text-transform:uppercase;color:#fff;text-shadow:0 0 18px rgba(255,0,168,.45);white-space:nowrap}'
        '.nav-a{display:inline-flex;align-items:center;justify-content:center;min-height:40px;font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase;color:#c4aabd;margin-left:8px;padding:8px 10px;border:1px solid transparent;border-radius:3px;transition:all .2s}'
        '.nav-a:hover{color:#fff;border-color:rgba(255,0,168,.4);background:rgba(255,0,168,.09)}.nav-a.out{color:#ff70c8}'
        '.profile-menu-btn{width:42px;height:42px;border:1px solid rgba(255,0,168,.5);border-radius:8px;background:rgba(255,0,168,.12);color:#ff42bd;display:flex;align-items:center;justify-content:center;font-size:22px;cursor:pointer}'
        '.profile-drawer-bg{position:fixed;inset:0;z-index:1000;background:rgba(4,1,5,.75);backdrop-filter:blur(7px);opacity:0;visibility:hidden;transition:.25s}'
        '.profile-drawer-bg.open{opacity:1;visibility:visible}'
        '.profile-drawer{position:absolute;top:0;left:0;margin:0;box-sizing:border-box;height:100%;height:100dvh;width:min(365px,90vw);overflow-y:auto;overscroll-behavior:contain;padding:calc(24px + env(safe-area-inset-top)) 20px calc(24px + env(safe-area-inset-bottom));background:radial-gradient(ellipse at 0 0,rgba(255,0,168,.18),transparent 48%),linear-gradient(160deg,rgba(24,8,24,.985),rgba(5,4,9,.995));border:0;border-right:1px solid rgba(255,0,168,.72);border-radius:0 18px 18px 0;box-shadow:18px 0 70px rgba(255,0,168,.22);transform:translate3d(-105%,0,0);transition:transform .32s cubic-bezier(.2,.8,.2,1)}'
        '.profile-drawer-bg.open .profile-drawer{transform:translate3d(0,0,0)}'
        '.profile-drawer:before{content:"";position:absolute;top:0;left:0;width:3px;height:100%;background:linear-gradient(180deg,#ff00a8,#ff70c8,transparent);box-shadow:0 0 22px rgba(255,0,168,.7);pointer-events:none}'
        '.profile-drawer a{display:flex;align-items:center;min-height:52px;padding:13px 14px;margin:6px 0;border:1px solid rgba(255,0,168,.2);border-radius:11px;background:linear-gradient(100deg,rgba(255,0,168,.07),rgba(255,255,255,.02));color:#fff0fa;font-size:11px;font-weight:850;letter-spacing:.1em;text-decoration:none;text-transform:uppercase;transition:background .2s,border-color .2s,transform .2s}'
        '.profile-drawer a:hover{background:rgba(255,0,168,.17);border-color:rgba(255,0,168,.7);transform:translateX(3px)}'
        '.profile-drawer-head{display:flex;align-items:center;justify-content:space-between;gap:12px;color:#fff;font-weight:950;letter-spacing:.12em;padding-bottom:20px;border-bottom:1px solid rgba(255,0,168,.32);margin-bottom:18px;text-shadow:0 0 18px rgba(255,0,168,.6)}'
        '.nav-in>div{display:none!important}.nav-in{justify-content:flex-start}.profile-menu-btn{margin-left:auto}'
        '.wrap{max-width:980px;margin:0 auto;padding:32px 20px 80px;position:relative}'
        '.hero{display:flex;gap:18px;align-items:center;margin-bottom:28px;padding-bottom:24px;border-bottom:1px solid #1a1a1e}'
        '.av{width:68px;height:68px;border-radius:20px;background:linear-gradient(145deg,#ff00a8,#7c1d3a);box-shadow:0 0 28px rgba(255,0,168,.25);'
        'display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;color:#fff;'
        'flex-shrink:0;border:1px solid rgba(255,255,255,.08)}'
        '.hero h1{font-size:17px;font-weight:800;color:#fafafa;word-break:break-all;line-height:1.3}'
        '.hero-meta{margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;align-items:center}'
        '.badge{font-size:9px;font-weight:800;letter-spacing:.12em;text-transform:uppercase;'
        'padding:3px 8px;border:1px solid #27272a;border-radius:999px}'
        '.badge.ok{color:#4ade80;border-color:#14532d;background:rgba(20,83,45,.25)}'
        '.badge.no{color:#f87171;border-color:#7f1d1d;background:rgba(127,29,29,.2)}'
        '.muted{font-size:11px;color:#71717a}'
        '.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-bottom:28px}'
        '.stat{border:1px solid rgba(255,0,168,.22);background:linear-gradient(145deg,rgba(28,10,27,.92),rgba(10,7,12,.92));padding:16px 12px;text-align:center;border-radius:5px;box-shadow:0 8px 28px rgba(0,0,0,.18)}'
        '.stat b{display:block;font-size:20px;font-weight:800;color:#f4f4f5;margin-bottom:4px}'
        '.stat span{font-size:9px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#52525b}'
        '.flash{font-size:12px;font-weight:700;padding:12px 14px;margin-bottom:16px;border:1px solid #27272a;background:#0c0c0e}'
        '.flash.ok{color:#d4d4d8}.flash.bad{color:#f87171;border-color:#7f1d1d}'
        '.flash.warn{color:#a1a1aa}.flash a{text-decoration:underline;color:#e4e4e7}'
        '.layout{display:grid;gap:20px}'
        '@media(min-width:800px){.layout{grid-template-columns:1fr 1.15fr}.span{grid-column:1/-1}}'
        '.card{border:1px solid rgba(255,0,168,.2);background:linear-gradient(145deg,rgba(20,10,20,.94),rgba(8,7,11,.94));padding:20px;border-radius:5px;box-shadow:0 14px 36px rgba(0,0,0,.24);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px)}'
        '.card h2{font-size:10px;font-weight:800;letter-spacing:.18em;text-transform:uppercase;color:#71717a;margin-bottom:14px}'
        '.field{margin-bottom:12px}'
        '.field label{display:block;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;'
        'color:#52525b;margin-bottom:6px}'
        '.field input,.field select{width:100%;min-height:48px;background:rgba(7,5,9,.88);border:1px solid #49203d;border-radius:3px;color:#fff;'
        'padding:12px 14px;font-size:14px;font-weight:600;font-family:inherit}'
        '.field input:focus,.field select:focus{outline:2px solid rgba(255,0,168,.2);outline-offset:1px;border-color:#ff00a8}'
        '.btn{width:100%;padding:13px 16px;font-size:11px;font-weight:800;letter-spacing:.16em;'
        'text-transform:uppercase;font-family:inherit;cursor:pointer;border:none;margin-top:4px}'
        '.btn-main{background:#ff00a8;color:#fff;border:1px solid #ff00a8;border-radius:3px;box-shadow:0 6px 22px rgba(255,0,168,.22)}.btn-main:hover{background:#ff35c0}'
        '.btn-ghost{background:rgba(255,0,168,.06);color:#ffe8f7;border:1px solid #49203d;border-radius:3px}.btn-ghost:hover{border-color:#ff00a8;background:rgba(255,0,168,.12)}'
        '.quick{display:grid;gap:8px;margin-bottom:4px}'
        '.ql{display:block;border:1px solid rgba(255,0,168,.18);background:rgba(9,5,10,.82);padding:16px;border-radius:3px;transition:transform .2s,border-color .2s,background .2s}'
        '.ql:hover{border-color:#ff00a8;background:rgba(255,0,168,.08);transform:translateY(-2px)}'
        '.ql small{display:block;font-size:9px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:#52525b;margin-bottom:4px}'
        '.ql strong{font-size:13px;font-weight:700;color:#e4e4e7}'
        '.ord{border:1px solid rgba(255,0,168,.16);background:rgba(9,5,10,.78);padding:14px;margin-bottom:8px;border-radius:3px}'
        '.ord:last-child{margin-bottom:0}'
        '.ord-top{display:flex;justify-content:space-between;gap:10px;align-items:center}'
        '.ord-id{font-size:12px;font-weight:800;color:#f4f4f5;letter-spacing:.04em}'
        '.ord-st{font-size:9px;font-weight:800;text-transform:uppercase;letter-spacing:.1em;padding:3px 8px;border:1px solid}'
        '.ord-items{font-size:11px;color:#71717a;margin-top:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
        '.ord-bot{display:flex;justify-content:space-between;margin-top:10px;font-size:10px;color:#52525b}'
        '.ord-price{color:#a1a1aa;font-weight:700}'
        '.fav-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px}'
        '.fav{border:1px solid rgba(255,0,168,.18);background:rgba(9,5,10,.9);overflow:hidden;border-radius:3px;transition:transform .2s,border-color .2s}.fav:hover{transform:translateY(-3px);border-color:#ff00a8}'
        '.fav-img{display:block;aspect-ratio:3/4;background:#111;overflow:hidden}'
        '.fav-img img{width:100%;height:100%;object-fit:cover;object-position:top}'
        '.fav-body{padding:10px}'
        '.fav-name{font-size:10px;font-weight:800;color:#e4e4e7;text-transform:uppercase;letter-spacing:.03em;'
        'white-space:nowrap;overflow:hidden;text-overflow:ellipsis}'
        '.fav-price{font-size:11px;color:#a1a1aa;font-weight:700;margin-top:4px}'
        '.fav-rm{margin-top:6px;font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;'
        'color:#71717a;background:none;border:none;cursor:pointer;padding:0;font-family:inherit}'
        '.fav-rm:hover{color:#ff55c7}.sold{color:#ff55c7;font-size:9px;margin-left:4px}'
        '.empty{text-align:center;padding:28px 12px;font-size:12px;color:#52525b;line-height:1.7}'
        '.empty a,.empty span{display:inline-block;margin-top:6px;color:#a1a1aa}'
        '.empty a{text-decoration:underline;color:#e4e4e7}'
        '@media(max-width:640px){.stats{gap:7px;margin-bottom:20px}.stat{padding:12px 5px}.stat b{font-size:16px}.stat span{font-size:8px;letter-spacing:.08em}.av{width:54px;height:54px;font-size:18px;border-radius:15px}'
        '.hero{gap:12px;align-items:flex-start;margin-bottom:22px;padding-bottom:20px}.hero h1{font-size:14px;overflow-wrap:anywhere}.wrap{padding:22px 12px 56px}.layout{gap:14px}.card{padding:15px 13px 16px}.nav-in{min-height:56px;padding:6px 10px;gap:4px}.logo{font-size:10px;letter-spacing:.1em}.nav-a{font-size:9px;letter-spacing:.04em;margin-left:2px;padding:7px 6px;min-height:38px}.fav-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.fav-img{aspect-ratio:3/4}.ord-top{align-items:flex-start}.ord-st{max-width:55%;text-align:center}.field input,.field select{font-size:16px}.btn{min-height:48px}}'
        '</style></head><body>'
        '<div id="profile-drawer-bg" class="profile-drawer-bg" onclick="if(event.target===this)profileMenu(false)"><aside class="profile-drawer" role="dialog" aria-modal="true" aria-label="Навигация"><div class="profile-drawer-head"><span>DEAD ARCHIVE</span><button type="button" onclick="profileMenu(false)" aria-label="Закрыть" style="font-size:26px;color:#ff70c8">×</button></div><a href="/">Главная</a><a href="/shop">Каталог</a><a href="/lookbook">UNDERGROUND LOOKBOOK</a><a href="/account">Профиль</a><a href="/login">Войти</a><a href="/register">Регистрация</a><a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener">Поддержка · Telegram</a><a href="/logout">Выйти</a></aside></div>'
        '<nav class="nav"><div class="nav-in">'
        '<a class="logo" href="/">dead archive</a>'
        '<button class="profile-menu-btn" type="button" onclick="profileMenu(true)" aria-label="Открыть меню">☰</button>'
        '<div><a class="nav-a" href="/shop">каталог</a>'
        '<a class="nav-a" href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" aria-label="Поддержка в Telegram"><svg aria-hidden="true" viewBox="0 0 24 24" width="15" height="15" fill="currentColor" style="margin-right:6px"><path d="M22 3 2.8 10.4c-1.3.5-1.3 1.3-.2 1.6l4.9 1.5 1.9 6c.2.7.1 1 .8 1 .5 0 .8-.2 1.1-.5l2.4-2.3 5 3.7c.9.5 1.5.2 1.7-.8L23.7 4.4C24 3.1 23.2 2.5 22 3ZM9.2 13.2l10-6.3c.5-.3.9-.1.5.2l-8.3 7.5-.3 3.2-1.9-4.6Z"/></svg>support</a>'
        '<a class="nav-a out" href="/logout">выйти</a></div></div></nav>'
        '<main class="wrap">'
        + flash + verify_html +
        '<section class="hero">'
        '<div class="av">' + letter + '</div>'
        '<div><h1>' + u.email + '</h1>'
        '<div class="hero-meta">' + v_badge +
        '<span class="muted">с ' + (u.created_str or '—') + '</span></div></div></section>'
        '<div class="stats">'
        '<div class="stat"><b>' + str(len(orders)) + '</b><span>заказы</span></div>'
        '<div class="stat"><b>~' + str(total_spent) + '€</b><span>сумма</span></div>'
        '<div class="stat"><b>' + str(len(fav_ids)) + '</b><span>избранное</span></div>'
        '</div>'
        '<div class="layout">'
        '<div>'
        '<div class="card" style="margin-bottom:16px"><h2>контакты</h2>'
        '<form method="POST"><input type="hidden" name="action" value="profile">'
        '<div class="field"><label>telegram</label>'
        '<input type="text" name="telegram" value="' + tg_val + '" placeholder="@username"></div>'
        '<div class="field"><label>телефон</label>'
        '<input type="text" name="phone" value="' + ph_val + '" placeholder="+380..."></div>'
        '<div class="field"><label>размер</label>'
        '<select name="preferred_size">' + size_opts + '</select></div>'
        '<button type="submit" class="btn btn-main">сохранить</button></form></div>'
        '<div class="card"><h2>пароль</h2>'
        '<form method="POST"><input type="hidden" name="action" value="password">'
        '<div class="field"><label>текущий</label>'
        '<input type="password" name="old_password" required></div>'
        '<div class="field"><label>новый</label>'
        '<input type="password" name="new_password" required minlength="4"></div>'
        '<button type="submit" class="btn btn-ghost">изменить</button></form></div>'
        '</div>'
        '<div class="card"><h2>заказы</h2>' + orders_html + '</div>'
        '<div class="card span"><h2>избранное</h2>' + fav_html + '</div>'
        '</div></main><script>function profileMenu(open){const el=document.getElementById("profile-drawer-bg");if(!el)return;el.classList.toggle("open",!!open);document.body.style.overflow=open?"hidden":"";}document.addEventListener("keydown",e=>{if(e.key==="Escape")profileMenu(false);});</script></body></html>'
    )
    return html


@app.route('/api/track_view', methods=['POST'])
def api_track_view():
    data = request.get_json() or {}
    try:
        pid = int(data.get('id') or 0)
    except Exception:
        pid = 0
    if not pid:
        return jsonify({"ok": False})
    p = Product.query.get(pid)
    if not p:
        return jsonify({"ok": False})
    p.views = (p.views or 0) + 1
    db.session.commit()
    return jsonify({"ok": True, "views": p.views})

@app.route('/api/validate_promo', methods=['POST'])
def api_validate_promo():
    data = request.get_json() or {}
    code = (data.get('code') or '').strip().upper()
    is_first = bool(data.get('is_first_order', True))
    if not code:
        return jsonify({"ok": False, "error": "ВВЕДИ ПРОМОКОД"})
    promo = PromoCode.query.filter_by(code=code).first()
    if not promo or not promo.active:
        return jsonify({"ok": False, "error": "НЕВЕРНЫЙ ПРОМОКОД"})
    if (promo.max_uses or 0) > 0 and (promo.uses_count or 0) >= promo.max_uses:
        return jsonify({"ok": False, "error": "ЛИМИТ ИСПОЛЬЗОВАНИЙ ИСЧЕРПАН"})
    if promo.first_order_only and not is_first:
        return jsonify({"ok": False, "error": "ПРОМОКОД ТОЛЬКО НА ПЕРВЫЙ ЗАКАЗ"})
    return jsonify({
        "ok": True,
        "code": promo.code,
        "discount_percent": int(promo.discount_percent or 0),
    })

@app.route('/create_order', methods=['POST'])
def create_order():
    import json
    data = request.get_json() or {}
    contacts = data.get('contacts', {})
    items = data.get('items', [])

    tg = (contacts.get('telegram') or '').strip()
    if tg and not tg.startswith('@'):
        tg = '@' + tg
    contacts['telegram'] = tg

    phone = (contacts.get('phone') or '').strip()
    digits_only = _re.sub(r'\D', '', phone)

    if not items:
        return jsonify({"success": False, "error": "Корзина пуста"}), 400
    if not _re.match(r'^@[a-zA-Z][a-zA-Z0-9_]{4,31}$', tg):
        return jsonify({"success": False, "error": "Укажи реальный Telegram @username"}), 400
    if not _re.match(r'^\+[1-9]\d{9,14}$', phone) or len(set(digits_only)) == 1:
        return jsonify({"success": False, "error": "Укажи реальный номер телефона"}), 400

    promo_code = (contacts.get('promo') or '').strip().upper()
    promo_obj = None
    if promo_code:
        promo_obj = PromoCode.query.filter_by(code=promo_code, active=True).first()
        if promo_obj:
            if (promo_obj.max_uses or 0) > 0 and (promo_obj.uses_count or 0) >= promo_obj.max_uses:
                return jsonify({"success": False, "error": "Лимит использований промокода исчерпан"}), 400
            if promo_obj.first_order_only and session.get('user_id'):
                if Order.query.filter_by(user_id=session.get('user_id')).count() > 0:
                    return jsonify({"success": False, "error": "Промокод только на первый заказ"}), 400
            contacts['promo'] = promo_obj.code
            contacts['discount_percent'] = int(promo_obj.discount_percent or 0)
        else:
            contacts['promo'] = None
            contacts['discount_percent'] = 0

    order_id = f"ORD-{int(time.time())}"
    time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    uid = session.get('user_id')

    new_order = Order(
        id=order_id,
        items_json=json.dumps(items, ensure_ascii=False),
        contacts_json=json.dumps(contacts, ensure_ascii=False),
        time_str=time_str,
        status='В обработке',
        user_id=uid
    )
    db.session.add(new_order)
    if promo_obj:
        promo_obj.uses_count = (promo_obj.uses_count or 0) + 1
    db.session.commit()

    if promo_obj:
        try:
            left = f"{promo_obj.uses_count}/{promo_obj.max_uses}" if (promo_obj.max_uses or 0) > 0 else f"{promo_obj.uses_count} раз"
            _tg_send(f"🏷 Промокод {promo_obj.code} (−{promo_obj.discount_percent}%) использован в {order_id}\nВсего использований: {left}")
        except Exception as e:
            print("promo notify:", e)

    if uid:
        try:
            create_notification(
                uid,
                f"Заказ {order_id} принят",
                "Мы получили твой заказ. Менеджер напишет в Telegram для подтверждения.",
                "order",
            )
        except Exception as ne:
            print("notif order:", ne)

    try:
        send_telegram_order(order_id, items, contacts, time_str)
    except Exception as e:
        print("Telegram notify error:", e)

    return jsonify({"success": True, "order_id": order_id})

# ==================== АДМИН-ПАНЕЛЬ ====================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "0879385")
_PW_FILE = os.path.join(basedir, ".admin_pw")
_SHUFFLE_FILE = os.path.join(basedir, ".shuffle_cfg.json")
DEFAULT_SHUFFLE_INTERVAL_HOURS = 12


def get_shuffle_cfg():
    cfg = {"enabled": True, "interval_hours": DEFAULT_SHUFFLE_INTERVAL_HOURS, "last_ts": 0}
    try:
        if os.path.exists(_SHUFFLE_FILE):
            with open(_SHUFFLE_FILE, "r", encoding="utf-8") as f:
                data = _json.load(f) or {}
            cfg["enabled"] = bool(data.get("enabled", True))
            hrs = int(data.get("interval_hours", DEFAULT_SHUFFLE_INTERVAL_HOURS) or DEFAULT_SHUFFLE_INTERVAL_HOURS)
            cfg["interval_hours"] = max(1, min(168, hrs))
            cfg["last_ts"] = float(data.get("last_ts", 0) or 0)
    except Exception as e:
        print("shuffle cfg read:", e)
    return cfg


def save_shuffle_cfg(cfg):
    try:
        with open(_SHUFFLE_FILE, "w", encoding="utf-8") as f:
            _json.dump(cfg, f, ensure_ascii=False)
    except Exception as e:
        print("shuffle cfg write:", e)


def shuffle_products_now():
    import random
    prods = Product.query.order_by(Product.id.asc()).all()
    if len(prods) <= 1:
        return 0
    shuffled = list(prods)
    random.shuffle(shuffled)
    if [p.id for p in shuffled] == [p.id for p in prods]:
        shuffled = shuffled[1:] + shuffled[:1]
    for idx, p in enumerate(shuffled):
        p.sort_order = idx
    db.session.commit()
    sync_products_catalog()
    cfg = get_shuffle_cfg()
    cfg["last_ts"] = time.time()
    save_shuffle_cfg(cfg)
    return len(shuffled)


def maybe_auto_shuffle():
    cfg = get_shuffle_cfg()
    if not cfg.get("enabled"):
        return False
    interval_sec = max(1, int(cfg.get("interval_hours", DEFAULT_SHUFFLE_INTERVAL_HOURS))) * 3600
    now = time.time()
    last_ts = float(cfg.get("last_ts") or 0)
    if last_ts == 0:
        cfg["last_ts"] = now
        save_shuffle_cfg(cfg)
        return False
    if now - last_ts >= interval_sec:
        cnt = shuffle_products_now()
        try:
            admin_log("auto_shuffle", f"перемешано {cnt} товаров")
        except Exception:
            pass
        return True
    return False


def get_admin_password():
    try:
        if os.path.exists(_PW_FILE):
            with open(_PW_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
    except Exception:
        pass
    return ADMIN_PASSWORD

def admin_log(action, detail=""):
    try:
        entry = AdminLog(time_str=time.strftime("%Y-%m-%d %H:%M:%S"), action=action, detail=detail)
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        print("admin_log:", e)

def is_admin():
    return bool(session.get("is_admin"))

def admin_guard():
    if not is_admin():
        return redirect(url_for("admin_login"))
    return None

ADMIN_SHELL = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
  <title>ADMIN // DEAD ARCHIVE</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@400;500;700;900&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background: #060607; color: #e4e4e7; }
    .gothic { font-family: 'Cinzel', serif; font-weight: 900; }
    input, textarea, select { color-scheme: dark; }
    .card { background: #0c0c0e; border: 1px solid #1f1f24; }
    .input { width: 100%; background: #070709; border: 1px solid #27272a; padding: 10px 12px; font-size: 13px; color: #f4f4f5; }
    .input:focus { outline: none; border-color: #71717a; }
    .btn { padding: 10px 16px; font-size: 11px; font-weight: 900; letter-spacing: 0.15em; text-transform: uppercase; cursor: pointer; transition: all 0.2s; }
    .btn-primary { background: #f4f4f5; color: #09090b; }
    .btn-primary:hover { background: #d4d4d8; }
    .btn-ghost { border: 1px solid #27272a; color: #a1a1aa; background: transparent; }
    .btn-ghost:hover { border-color: #71717a; color: #f4f4f5; }
    .btn-danger { border: 1px solid #7f1d1d; color: #f87171; background: rgba(127,29,29,0.15); }
    .btn-danger:hover { background: #991b1b; color: #fff; }
  </style>
</head>
<body class="min-h-screen pb-16">
  <header class="border-b border-zinc-900 bg-[#08080a] sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-3 sm:px-6 py-3 sm:py-4 flex flex-wrap items-center justify-between gap-2">
      <div class="flex items-center gap-3">
        <a href="/admin" class="gothic text-base sm:text-xl tracking-[0.2em] text-zinc-100">DEAD ARCHIVE</a>
        <span class="text-[10px] font-black uppercase tracking-widest px-2 py-0.5 border border-red-900/60 bg-red-950/30 text-red-400">admin</span>
      </div>
      <nav class="flex flex-wrap items-center gap-1.5 sm:gap-2 text-[11px] font-bold uppercase tracking-wider">
        <a href="/admin" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_DASH__">дашборд</a>
        <a href="/admin/orders" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_ORD__">заказы (__ORD_CNT__)</a>
        <a href="/admin/products" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_PROD__">товары (__PROD_CNT__)</a>
        <a href="/admin/lookbook" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_LOOK__">lookbook</a>
        <a href="/admin/promos" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_PROMO__">промо (__PROMO_CNT__)</a>
        <a href="/admin/users" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_USR__">юзеры (__USR_CNT__)</a>
        <a href="/admin/settings" class="px-2.5 py-2 border border-zinc-800 hover:border-zinc-500 __NAV_SET__">настройки</a>
        <a href="/shop" target="_blank" class="px-2.5 py-2 border border-zinc-800 text-zinc-400 hover:text-zinc-100">сайт ↗</a>
        <a href="/admin/logout" class="px-2.5 py-2 border border-red-950 text-red-400 hover:bg-red-950/40">выйти</a>
      </nav>
    </div>
  </header>
  <main class="max-w-7xl mx-auto px-3 sm:px-6 pt-6 sm:pt-8">
    __FLASH__
    __CONTENT__
  </main>
</body>
</html>
"""

def render_admin(content, active="dash", flash=None):
    ord_cnt = Order.query.count()
    prod_cnt = Product.query.count()
    promo_cnt = PromoCode.query.count()
    usr_cnt = Customer.query.count()
    flash_html = ""
    if flash:
        flash_html = f'<div class="mb-6 px-4 py-3 border border-emerald-900/50 bg-emerald-950/30 text-emerald-300 text-xs font-bold">{flash}</div>'
    act = "bg-zinc-100 text-black border-zinc-100"
    html = ADMIN_SHELL
    html = html.replace("__NAV_DASH__", act if active == "dash" else "text-zinc-300")
    html = html.replace("__NAV_ORD__", act if active == "orders" else "text-zinc-300")
    html = html.replace("__NAV_PROD__", act if active == "products" else "text-zinc-300")
    html = html.replace("__NAV_LOOK__", act if active == "lookbook" else "text-zinc-300")
    html = html.replace("__NAV_PROMO__", act if active == "promos" else "text-zinc-300")
    html = html.replace("__NAV_USR__", act if active == "users" else "text-zinc-300")
    html = html.replace("__NAV_SET__", act if active == "settings" else "text-zinc-300")
    html = html.replace("__ORD_CNT__", str(ord_cnt))
    html = html.replace("__PROD_CNT__", str(prod_cnt))
    html = html.replace("__PROMO_CNT__", str(promo_cnt))
    html = html.replace("__USR_CNT__", str(usr_cnt))
    html = html.replace("__FLASH__", flash_html)
    html = html.replace("__CONTENT__", content)
    return html

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if is_admin():
        return redirect(url_for("admin_dashboard"))
    error = None
    if request.method == "POST":
        pw = (request.form.get("password") or "").strip()
        if pw == get_admin_password():
            session["is_admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "неверный пароль"
    err_html = f'<p class="text-red-500 text-xs font-bold mt-3">{error}</p>' if error else ""
    return f"""<!DOCTYPE html><html lang="ru"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ADMIN LOGIN // DEAD ARCHIVE</title><script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@900&family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<style>body{{font-family:Inter,sans-serif;background:#050506;color:#e4e4e7}}.gothic{{font-family:Cinzel,serif;font-weight:900}}</style>
</head><body class="min-h-screen flex items-center justify-center p-4">
<form method="POST" class="w-full max-w-sm bg-[#0c0c0e] border border-zinc-800 p-6 sm:p-8 text-center">
  <p class="text-[10px] font-black tracking-[0.3em] text-red-500 uppercase mb-2">restricted</p>
  <h1 class="gothic text-2xl tracking-[0.2em] text-zinc-100 mb-6">ADMIN</h1>
  <input type="password" name="password" placeholder="пароль" autofocus required
    class="w-full bg-black border border-zinc-800 px-4 py-3 text-sm text-zinc-100 text-center focus:outline-none focus:border-zinc-500 font-bold mb-4">
  <button class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black tracking-[0.2em] uppercase hover:bg-zinc-300">войти</button>
  {err_html}
  <a href="/" class="block mt-5 text-[11px] text-zinc-600 hover:text-zinc-300">← на сайт</a>
</form></body></html>"""

@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin_login"))

@app.route("/admin")
def admin_dashboard():
    guard = admin_guard()
    if guard:
        return guard
    import json
    orders = Order.query.order_by(Order.time_str.desc()).all()
    products = Product.query.all()
    online = get_real_online_count()

    today_prefix = time.strftime("%Y-%m-%d")
    today_orders = [o for o in orders if (o.time_str or "").startswith(today_prefix)]
    pending = [o for o in orders if o.status == "В обработке"]
    shipped = [o for o in orders if o.status == "Отправлен"]
    sold_out_cnt = len([p for p in products if not getattr(p, "in_stock", True)])

    total_revenue_eur = 0
    today_revenue_eur = 0
    week_revenue_eur = 0
    now_ts = time.time()
    for o in orders:
        if o.status == "Отменен":
            continue
        try:
            items = json.loads(o.items_json)
            s = sum(int(i.get("price_eur") or 0) for i in items)
            total_revenue_eur += s
            if (o.time_str or "").startswith(today_prefix):
                today_revenue_eur += s
            try:
                day = (o.time_str or "")[:10]
                if len(day) == 10 and (now_ts - time.mktime(time.strptime(day, "%Y-%m-%d"))) <= 7 * 86400:
                    week_revenue_eur += s
            except Exception:
                pass
        except Exception:
            pass

    recent_html = ""
    for o in orders[:8]:
        try:
            items = json.loads(o.items_json)
            contacts = json.loads(o.contacts_json)
        except Exception:
            items, contacts = [], {}
        st_cls = "text-emerald-400"
        if o.status == "Отменен": st_cls = "text-red-400"
        elif o.status == "Отправлен": st_cls = "text-sky-400"
        elif o.status == "Доставлен": st_cls = "text-red-400"
        recent_html += f"""
        <div class="flex flex-wrap items-center justify-between gap-2 py-3 border-b border-zinc-900 text-xs">
          <div>
            <span class="font-black text-red-400">{o.id}</span>
            <span class="text-zinc-500 ml-2">{o.time_str}</span>
            <span class="text-zinc-400 ml-2">{contacts.get('telegram','')} · {contacts.get('phone','')}</span>
          </div>
          <div class="flex items-center gap-3">
            <span class="text-zinc-300 font-bold">{len(items)} шт</span>
            <span class="font-bold {st_cls}">{o.status}</span>
          </div>
        </div>"""
    if not recent_html:
        recent_html = '<p class="text-zinc-600 text-xs py-6 text-center">заказов пока нет</p>'

    top_prods = Product.query.order_by(Product.views.desc(), Product.id.desc()).limit(6).all()
    top_html = ""
    for tp in top_prods:
        top_html += f"""
        <div class="flex items-center justify-between gap-2 py-2 border-b border-zinc-900 text-xs">
          <span class="text-zinc-200 truncate">{tp.name}</span>
          <span class="text-zinc-400 font-black shrink-0">{tp.views or 0} views</span>
        </div>"""
    if not top_html:
        top_html = '<p class="text-zinc-600 text-xs py-4 text-center">нет данных</p>'

    logs = AdminLog.query.order_by(AdminLog.id.desc()).limit(10).all()
    logs_html = ""
    for lg in logs:
        logs_html += f'<div class="py-1.5 border-b border-zinc-900/80 text-[11px] flex justify-between gap-2"><span class="text-zinc-300"><b>{lg.action}</b> {lg.detail or ""}</span><span class="text-zinc-600 shrink-0">{lg.time_str}</span></div>'
    if not logs_html:
        logs_html = '<p class="text-zinc-600 text-xs py-3">лог пуст</p>'

    content = f"""
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 mb-6">
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">онлайн</p>
        <p class="text-2xl font-black text-emerald-400 mt-1">{online}</p>
      </div>
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">сегодня</p>
        <p class="text-2xl font-black text-zinc-100 mt-1">{len(today_orders)}</p>
        <p class="text-[10px] text-zinc-500 mt-0.5">~{today_revenue_eur} EUR</p>
      </div>
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">7 дней</p>
        <p class="text-2xl font-black text-zinc-100 mt-1">~{week_revenue_eur} €</p>
      </div>
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">в обработке</p>
        <p class="text-2xl font-black text-amber-400 mt-1">{len(pending)}</p>
      </div>
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">всего заказов</p>
        <p class="text-2xl font-black text-zinc-100 mt-1">{len(orders)}</p>
        <p class="text-[10px] text-zinc-500 mt-0.5">отпр: {len(shipped)}</p>
      </div>
      <div class="card p-4">
        <p class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">выручка</p>
        <p class="text-2xl font-black text-zinc-100 mt-1">~{total_revenue_eur} €</p>
        <p class="text-[10px] text-red-400 mt-0.5">sold out: {sold_out_cnt}</p>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-3 gap-4 mb-6">
      <div class="card p-5 lg:col-span-2">
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400">последние заказы</h2>
          <a href="/admin/orders" class="text-[11px] font-bold text-zinc-400 hover:text-white underline">все →</a>
        </div>
        {recent_html}
      </div>
      <div class="card p-5">
        <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400 mb-4">топ просмотров</h2>
        {top_html}
      </div>
    </div>

    <div class="card p-5 mb-6">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-3">
        <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400">бэкап & экспорт</h2>
        <div class="flex flex-wrap gap-2">
          <a href="/admin/export_orders.csv" class="btn btn-ghost">скачать заказы CSV</a>
          <a href="/admin/backup_db" class="btn btn-ghost">скачать archive.db</a>
        </div>
      </div>
      <h3 class="text-[10px] font-black uppercase tracking-[0.2em] text-zinc-500 mt-4 mb-2">лог действий</h3>
      {logs_html}
    </div>
    """
    return render_admin(content, active="dash")

@app.route("/admin/export_orders.csv")
def admin_export_orders():
    guard = admin_guard()
    if guard:
        return guard
    import csv, io, json
    from flask import Response
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time", "status", "telegram", "phone", "promo", "items", "total_eur", "note"])
    for o in Order.query.order_by(Order.time_str.desc()).all():
        try:
            items = json.loads(o.items_json)
            c = json.loads(o.contacts_json)
        except Exception:
            items, c = [], {}
        tot = sum(int(i.get("price_eur") or 0) for i in items)
        items_txt = "; ".join(f"{i.get('name')} [{i.get('selectedSize')}]" for i in items)
        w.writerow([o.id, o.time_str, o.status, c.get("telegram",""), c.get("phone",""), c.get("promo",""), items_txt, tot, o.note or ""])
    admin_log("export_csv", "orders")
    return Response(
        "\ufeff" + buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=deadarchive_orders.csv"}
    )

@app.route("/admin/backup_db")
def admin_backup_db():
    guard = admin_guard()
    if guard:
        return guard
    from flask import send_file
    db_path = os.path.join(basedir, "archive.db")
    admin_log("backup_db")
    return send_file(db_path, as_attachment=True, download_name=f"archive_{time.strftime('%Y%m%d_%H%M')}.db")

@app.route("/admin/orders", methods=["GET", "POST"])
def admin_orders():
    guard = admin_guard()
    if guard:
        return guard
    import json
    flash = None
    if request.method == "POST":
        action = request.form.get("action")
        oid = request.form.get("order_id")
        if action == "status" and oid:
            o = Order.query.get(oid)
            if o:
                o.status = request.form.get("status", o.status)
                o.note = request.form.get("note", o.note)
                db.session.commit()
                if o.user_id:
                    create_notification(
                        o.user_id,
                        f"Заказ {o.id}: {o.status}",
                        "Статус твоего заказа обновлён. Смотри детали в профиле.",
                        "order",
                    )
                admin_log("order_update", f"{oid} → {o.status}")
                flash = f"Заказ {oid} обновлён"
        elif action == "bulk_status":
            ids = request.form.getlist("ids")
            st = request.form.get("bulk_status_val") or "Отправлен"
            for bid in ids:
                o = Order.query.get(bid)
                if o:
                    o.status = st
                    if o.user_id:
                        create_notification(
                            o.user_id,
                            f"Заказ {o.id}: {o.status}",
                            "Статус твоего заказа обновлён.",
                            "order",
                        )
            db.session.commit()
            admin_log("orders_bulk", f"{len(ids)} → {st}")
            flash = f"Обновлено заказов: {len(ids)}"
        elif action == "delete" and oid:
            o = Order.query.get(oid)
            if o:
                db.session.delete(o)
                db.session.commit()
                admin_log("order_delete", oid)
                flash = f"Заказ {oid} удалён"

    status_filter = request.args.get("status", "all")
    q = (request.args.get("q") or "").strip().lower()
    orders = Order.query.order_by(Order.time_str.desc()).all()
    if status_filter != "all":
        orders = [o for o in orders if o.status == status_filter]
    if q:
        orders = [o for o in orders if q in o.id.lower() or q in (o.contacts_json or "").lower() or q in (o.items_json or "").lower()]

    statuses = ["В обработке", "Принят", "Отправлен", "Доставлен", "Отменен"]
    tabs = f'<a href="/admin/orders?status=all" class="px-3 py-1.5 text-[11px] font-bold border {"bg-zinc-100 text-black border-zinc-100" if status_filter=="all" else "border-zinc-800 text-zinc-400"}">все</a>'
    for st in statuses:
        active_cls = "bg-zinc-100 text-black border-zinc-100" if status_filter == st else "border-zinc-800 text-zinc-400"
        tabs += f'<a href="/admin/orders?status={st}" class="px-3 py-1.5 text-[11px] font-bold border {active_cls}">{st}</a>'

    cards_html = ""
    for o in orders:
        try:
            items = json.loads(o.items_json)
            contacts = json.loads(o.contacts_json)
        except Exception:
            items, contacts = [], {}
        total_eur = sum(int(i.get("price_eur") or 0) for i in items)
        items_li = ""
        for it in items:
            items_li += f'<li class="text-xs text-zinc-300">• {it.get("name","")} — <b>{it.get("selectedSize","")}</b> {("· "+it.get("selectedColor")) if it.get("selectedColor") else ""} ({it.get("price_eur",0)} EUR)</li>'

        opts = "".join([f'<option value="{s}" {"selected" if o.status==s else ""}>{s}</option>' for s in statuses])
        tg_clean = (contacts.get("telegram") or "").lstrip("@")
        tg_link = f'<a href="https://t.me/{tg_clean}" target="_blank" class="text-sky-400 underline">{contacts.get("telegram")}</a>' if tg_clean else "—"

        cards_html += f"""
        <div class="card p-4 sm:p-6">
          <div class="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-900 pb-3 mb-4">
            <div class="flex items-center gap-2">
              <input type="checkbox" name="ids" value="{o.id}" form="bulk-orders-form" class="accent-zinc-100">
              <span class="text-sm font-black text-red-400">{o.id}</span>
              <span class="text-xs text-zinc-500">{o.time_str}</span>
            </div>
            <span class="text-xs font-black text-zinc-200">~{total_eur} EUR</span>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <p class="text-[10px] font-black text-zinc-500 uppercase mb-1.5">состав</p>
              <ul class="space-y-1">{items_li}</ul>
            </div>
            <div class="text-xs space-y-1 text-zinc-400">
              <p class="text-[10px] font-black text-zinc-500 uppercase mb-1.5">контакты</p>
              <p>TG: {tg_link}</p>
              <p>Тел: <span class="text-zinc-200">{contacts.get('phone','—')}</span></p>
              {f"<p>Промо: <span class='text-emerald-400 font-bold'>{contacts.get('promo')}</span></p>" if contacts.get('promo') else ""}
            </div>
          </div>
          <form method="POST" class="flex flex-wrap items-center gap-2 pt-3 border-t border-zinc-900">
            <input type="hidden" name="action" value="status">
            <input type="hidden" name="order_id" value="{o.id}">
            <select name="status" class="input !w-auto text-xs font-bold">{opts}</select>
            <input type="text" name="note" value="{(o.note or '').replace('"','&quot;')}" placeholder="заметка (трек-номер, оплата...)" class="input flex-1 min-w-[180px] text-xs">
            <button class="btn btn-primary">сохранить</button>
          </form>
          <form method="POST" class="mt-2 text-right" onsubmit="return confirm('Удалить заказ {o.id}?')">
            <input type="hidden" name="action" value="delete">
            <input type="hidden" name="order_id" value="{o.id}">
            <button class="text-[10px] text-zinc-600 hover:text-red-400 uppercase font-bold">удалить заказ</button>
          </form>
        </div>"""

    if not cards_html:
        cards_html = '<div class="card p-10 text-center text-xs text-zinc-500">заказов не найдено</div>'

    content = f"""
    <div class="flex flex-wrap items-center justify-between gap-3 mb-5">
      <div class="flex flex-wrap gap-1.5">{tabs}</div>
      <form method="GET" class="flex gap-2">
        <input type="hidden" name="status" value="{status_filter}">
        <input type="text" name="q" value="{q}" placeholder="поиск ID / TG / тел..." class="input !w-48 sm:!w-64 text-xs">
        <button class="btn btn-ghost">найти</button>
      </form>
    </div>
    <form id="bulk-orders-form" method="POST" class="flex flex-wrap items-center gap-2 mb-4 card p-3">
      <input type="hidden" name="action" value="bulk_status">
      <span class="text-[10px] font-black text-zinc-500 uppercase tracking-wider">выбранным:</span>
      <select name="bulk_status_val" class="input !w-auto text-xs font-bold">
        {''.join(f'<option value="{s}">{s}</option>' for s in statuses)}
      </select>
      <button class="btn btn-ghost">применить</button>
    </form>
    <div class="space-y-4">{cards_html}</div>
    """
    return render_admin(content, active="orders", flash=flash)

@app.route("/admin/products", methods=["GET", "POST"])
def admin_products():
    guard = admin_guard()
    if guard:
        return guard
    flash = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            name = (request.form.get("name") or "").strip()
            name_en = (request.form.get("name_en") or "").strip() or name
            category = request.form.get("category") or "tshirts"
            price_eur = int(request.form.get("price_eur") or 0)
            price_uah = int(request.form.get("price_uah") or 0)
            # Фото с компьютера → Cloudinary
            uploaded_urls = save_uploaded_images(collect_slot_files())
            # Опционально ссылки вручную
            images_raw = (request.form.get("images_str") or "").replace("\r", "\n").replace("\n", ",")
            link_urls = [u.strip() for u in strip_dead_image_urls(images_raw).split(",") if u.strip()]
            all_urls = uploaded_urls + link_urls
            images_str = ", ".join(all_urls)
            colors_str = (request.form.get("colors_str") or "Черный").strip()
            sizes_str = (request.form.get("sizes_str") or "S, M, L, XL").strip()
            description = (request.form.get("description") or "").strip()
            description_en = (request.form.get("description_en") or "").strip() or description
            min_order = db.session.query(db.func.min(Product.sort_order)).scalar()
            next_order = (min_order - 1) if min_order is not None else 0
            if name and images_str:
                db.session.add(Product(
                    name=name, name_en=name_en, category=category,
                    price_eur=price_eur, price_uah=price_uah,
                    images_str=images_str, colors_str=colors_str,
                    description=description, description_en=description_en,
                    sizes_str=sizes_str, sort_order=next_order,
                    in_stock=bool(request.form.get("in_stock"))
                ))
                db.session.commit()
                sync_products_catalog()
                admin_log("product_create", name)
                flash = f"Товар «{name}» добавлен ({len(all_urls)} фото)"
            elif name and not images_str:
                err = getattr(save_uploaded_images, "last_error", "") or ""
                flash = "Добавь хотя бы одно фото (файл или ссылку)"
                if err:
                    flash += f" · ошибка загрузки: {err}"
        elif action == "update":
            pid = int(request.form.get("product_id") or 0)
            p = Product.query.get(pid)
            if p:
                p.name = (request.form.get("name") or p.name).strip()
                p.name_en = (request.form.get("name_en") or "").strip() or p.name
                p.category = request.form.get("category") or p.category
                p.price_eur = int(request.form.get("price_eur") or 0)
                p.price_uah = int(request.form.get("price_uah") or 0)
                uploaded_urls = save_uploaded_images(collect_slot_files())
                images_raw = (request.form.get("images_str") or "").replace("\r", "\n").replace("\n", ",")
                link_urls = [u.strip() for u in strip_dead_image_urls(images_raw).split(",") if u.strip()]
                if uploaded_urls:
                    # отдельные слоты 1–4 полностью задают новый набор фото
                    all_urls = uploaded_urls
                else:
                    all_urls = link_urls
                p.images_str = ", ".join(all_urls) if all_urls else ""
                p.colors_str = (request.form.get("colors_str") or p.colors_str).strip()
                p.sizes_str = (request.form.get("sizes_str") or p.sizes_str).strip()
                p.description = (request.form.get("description") or "").strip()
                p.description_en = (request.form.get("description_en") or "").strip() or p.description
                was_in_stock = bool(p.in_stock)
                p.in_stock = bool(request.form.get("in_stock"))
                db.session.commit()
                sync_products_catalog()
                if was_in_stock and not p.in_stock:
                    notify_sold_out(p.name, p.id)
                admin_log("product_update", f"#{pid} {p.name}")
                extra = f" (+{len(uploaded_urls)} новых фото)" if uploaded_urls else ""
                flash = f"Товар «{p.name}» сохранён{extra}"
                err = getattr(save_uploaded_images, "last_error", "") or ""
                if err and not uploaded_urls:
                    flash += f" · ошибка загрузки: {err}"
        elif action == "toggle_stock":
            pid = int(request.form.get("product_id") or 0)
            p = Product.query.get(pid)
            if p:
                p.in_stock = not bool(p.in_stock)
                db.session.commit()
                sync_products_catalog()
                if not p.in_stock:
                    notify_sold_out(p.name, p.id)
                admin_log("product_stock", f"#{pid} → {p.in_stock}")
                flash = f"Статус «{p.name}»: {'в наличии' if p.in_stock else 'sold out'}"
        elif action == "duplicate":
            pid = int(request.form.get("product_id") or 0)
            p = Product.query.get(pid)
            if p:
                copy_p = Product(
                    name=p.name + " (копия)",
                    name_en=(p.name_en or p.name) + " (copy)",
                    category=p.category,
                    price_eur=p.price_eur,
                    price_uah=p.price_uah,
                    images_str=p.images_str,
                    colors_str=p.colors_str,
                    description=p.description,
                    description_en=p.description_en,
                    sizes_str=p.sizes_str,
                    sort_order=(p.sort_order or 0) + 1,
                    in_stock=p.in_stock,
                )
                db.session.add(copy_p)
                db.session.commit()
                sync_products_catalog()
                admin_log("product_duplicate", f"#{pid} → #{copy_p.id}")
                flash = f"Создана копия «{copy_p.name}»"
        elif action == "delete":
            pid = int(request.form.get("product_id") or 0)
            p = Product.query.get(pid)
            if p:
                name = p.name
                db.session.delete(p)
                db.session.commit()
                sync_products_catalog()
                admin_log("product_delete", f"#{pid} {name}")
                flash = f"Товар «{name}» удалён"
        elif action == "shuffle_now":
            cnt = shuffle_products_now()
            admin_log("manual_shuffle", f"перемешано {cnt} товаров")
            flash = f"Товары перемешаны ({cnt} шт) — новый порядок уже на сайте"
        elif action == "shuffle_settings":
            cfg = get_shuffle_cfg()
            cfg["enabled"] = bool(request.form.get("shuffle_enabled"))
            try:
                hrs = int(request.form.get("shuffle_hours") or DEFAULT_SHUFFLE_INTERVAL_HOURS)
            except Exception:
                hrs = DEFAULT_SHUFFLE_INTERVAL_HOURS
            cfg["interval_hours"] = max(1, min(168, hrs))
            if cfg["enabled"] and not cfg.get("last_ts"):
                cfg["last_ts"] = time.time()
            save_shuffle_cfg(cfg)
            admin_log("shuffle_settings", f"enabled={cfg['enabled']}, hours={cfg['interval_hours']}")
            flash = f"Авто-перемешивание: {'включено' if cfg['enabled'] else 'выключено'}, каждые {cfg['interval_hours']} ч"

    cat_filter = request.args.get("cat", "all")
    q = (request.args.get("q") or "").strip().lower()
    products = Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()
    if cat_filter != "all":
        products = [p for p in products if p.category == cat_filter]
    if q:
        products = [p for p in products if q in p.name.lower()]

    cats = [("all", "все"), ("tshirts", "футболки"), ("outerwear", "худи / верх"), ("pants", "штаны"), ("shoes", "обувь")]
    cat_tabs = "".join([
        f'<a href="/admin/products?cat={k}" class="px-3 py-1.5 text-[11px] font-bold border {"bg-zinc-100 text-black border-zinc-100" if cat_filter==k else "border-zinc-800 text-zinc-400"}">{v}</a>'
        for k, v in cats
    ])

    rows_html = ""
    for p in products:
        img_list = [u.strip() for u in (p.images_str or "").split(",") if u.strip()]
        first_img = img_list[0] if img_list else ""
        queue_html = ""
        for qi, qu in enumerate(img_list, 1):
            queue_html += (
                f'<div class="relative w-14 h-16 border border-zinc-700 bg-black overflow-hidden shrink-0" title="{qi}">'
                f'<img src="{qu}" class="w-full h-full object-cover object-top" onerror="this.parentElement.style.opacity=0.3">'
                f'<span class="absolute bottom-0 left-0 bg-black/85 text-[9px] text-zinc-200 px-1">{qi}</span>'
                f'</div>'
            )
        if not queue_html:
            queue_html = '<span class="text-[10px] text-zinc-600">очередь пуста — загрузи фото</span>'
        cat_opts = "".join([
            f'<option value="{k}" {"selected" if p.category==k else ""}>{v}</option>'
            for k, v in cats[1:]
        ])
        rows_html += f"""
        <details class="card group" data-prod-id="{p.id}">
          <summary class="p-3 sm:p-4 flex items-center justify-between gap-3 cursor-pointer list-none">
            <div class="flex items-center gap-3 min-w-0">
              <span class="drag-handle cursor-grab active:cursor-grabbing text-zinc-500 hover:text-zinc-200 px-1.5 py-2 select-none text-base" title="Перетащи чтобы поменять место" onclick="event.preventDefault(); event.stopPropagation();">⋮⋮</span>
              <img src="{first_img}" class="w-12 h-14 object-cover object-top border border-zinc-800 shrink-0">
              <div class="min-w-0">
                <p class="text-xs sm:text-sm font-black text-zinc-100 truncate">#{p.id} · {p.name} {'' if getattr(p,'in_stock',True) else '<span class="ml-1 text-[9px] px-1.5 py-0.5 bg-red-950/60 border border-red-800 text-red-400">SOLD OUT</span>'}</p>
                <p class="text-[11px] text-zinc-500">{p.category} · <b>{p.price_eur} EUR</b> / {p.price_uah} UAH</p>
              </div>
            </div>
            <span class="text-[11px] font-bold text-zinc-400 group-open:hidden shrink-0">редактировать ↓</span>
            <span class="text-[11px] font-bold text-zinc-400 hidden group-open:inline shrink-0">свернуть ↑</span>
          </summary>
          <div class="p-4 sm:p-5 border-t border-zinc-900 bg-black/40">
            <form method="POST" enctype="multipart/form-data" class="space-y-3">
              <input type="hidden" name="action" value="update">
              <input type="hidden" name="product_id" value="{p.id}">
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">название (RU)</label>
                  <input type="text" name="name" value="{p.name.replace('"','&quot;')}" required class="input">
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">название (EN)</label>
                  <input type="text" name="name_en" value="{(p.name_en or '').replace('"','&quot;')}" class="input">
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">категория</label>
                  <select name="category" class="input">{cat_opts}</select>
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">цена EUR</label>
                  <input type="number" name="price_eur" value="{p.price_eur}" required class="input">
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">цена UAH</label>
                  <input type="number" name="price_uah" value="{p.price_uah}" required class="input">
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">цвета (через запятую)</label>
                  <input type="text" name="colors_str" value="{(p.colors_str or '').replace('"','&quot;')}" class="input">
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">размеры (через запятую)</label>
                  <input type="text" name="sizes_str" value="{(p.sizes_str or '').replace('"','&quot;')}" class="input">
                </div>
              </div>
              <div>
                <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">текущие фото</label>
                <div class="flex flex-wrap gap-2 mb-2">{queue_html}</div>
                <textarea name="images_str" rows="2" class="input font-mono text-xs" placeholder="оставьте пустым и загрузите новые ниже">{p.images_str}</textarea>
                <p class="text-[10px] text-zinc-600 mt-1 mb-3">можно очистить поле и залить заново. мёртвые ссылки (kappa) удаляются сами.</p>
                <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-2">загрузить фото отдельно (1–4)</label>
                <div class="space-y-2">
                  <div>
                    <label class="text-[10px] text-zinc-500">фото 1 (главное)</label>
                    <input type="file" name="images_1" accept="image/png,image/jpeg,image/webp,image/gif" class="input file:mr-2 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold">
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500">фото 2</label>
                    <input type="file" name="images_2" accept="image/png,image/jpeg,image/webp,image/gif" class="input file:mr-2 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold">
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500">фото 3</label>
                    <input type="file" name="images_3" accept="image/png,image/jpeg,image/webp,image/gif" class="input file:mr-2 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold">
                  </div>
                  <div>
                    <label class="text-[10px] text-zinc-500">фото 4</label>
                    <input type="file" name="images_4" accept="image/png,image/jpeg,image/webp,image/gif" class="input file:mr-2 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold">
                  </div>
                </div>
                <p class="text-[10px] text-zinc-600 mt-2">если заполнишь слоты — они <b>заменят</b> текущие фото (в этом же порядке)</p>
              </div>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">описание (RU)</label>
                  <textarea name="description" rows="2" class="input">{p.description or ''}</textarea>
                </div>
                <div>
                  <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">описание (EN)</label>
                  <textarea name="description_en" rows="2" class="input">{p.description_en or ''}</textarea>
                </div>
              </div>
              <label class="inline-flex items-center gap-2 text-xs text-zinc-300 font-bold cursor-pointer">
                <input type="checkbox" name="in_stock" value="1" {"checked" if getattr(p,'in_stock',True) else ""} class="accent-zinc-100">
                в наличии
              </label>
              <div class="flex flex-wrap items-center justify-between gap-2 pt-2">
                <button class="btn btn-primary">сохранить изменения</button>
              </div>
            </form>
            <div class="flex flex-wrap gap-2 mt-3 pt-3 border-t border-zinc-900">
              <form method="POST">
                <input type="hidden" name="action" value="toggle_stock">
                <input type="hidden" name="product_id" value="{p.id}">
                <button class="btn btn-ghost">{'пометить sold out' if getattr(p,'in_stock',True) else 'вернуть в наличие'}</button>
              </form>
              <form method="POST">
                <input type="hidden" name="action" value="duplicate">
                <input type="hidden" name="product_id" value="{p.id}">
                <button class="btn btn-ghost">копировать</button>
              </form>
              <form method="POST" onsubmit="return confirm('Удалить товар «{p.name}»?')">
                <input type="hidden" name="action" value="delete">
                <input type="hidden" name="product_id" value="{p.id}">
                <button class="btn btn-danger">удалить товар</button>
              </form>
            </div>
          </div>
        </details>"""

    sh_cfg = get_shuffle_cfg()
    sh_last = time.strftime("%Y-%m-%d %H:%M", time.localtime(sh_cfg["last_ts"])) if sh_cfg.get("last_ts") else "ещё не запускалось"
    if sh_cfg.get("enabled") and sh_cfg.get("last_ts"):
        next_ts = sh_cfg["last_ts"] + sh_cfg["interval_hours"] * 3600
        sh_next = time.strftime("%Y-%m-%d %H:%M", time.localtime(next_ts))
    else:
        sh_next = "—"

    content = f"""
    <div class="card p-4 mb-5 flex flex-wrap items-center justify-between gap-3">
      <div>
        <p class="text-xs font-black uppercase tracking-wider text-zinc-200">авто-перемешивание каталога</p>
        <p class="text-[11px] text-zinc-500 mt-0.5">последнее: <span class="text-zinc-300">{sh_last}</span> · следующее: <span class="text-zinc-300">{sh_next}</span></p>
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <form method="POST" class="flex flex-wrap items-center gap-2">
          <input type="hidden" name="action" value="shuffle_settings">
          <label class="inline-flex items-center gap-1.5 text-xs text-zinc-300 font-bold cursor-pointer">
            <input type="checkbox" name="shuffle_enabled" value="1" {"checked" if sh_cfg.get("enabled") else ""} class="accent-zinc-100">
            авто
          </label>
          <input type="number" name="shuffle_hours" value="{sh_cfg.get('interval_hours', DEFAULT_SHUFFLE_INTERVAL_HOURS)}" min="1" max="168" class="input !w-20 text-xs text-center">
          <span class="text-[11px] text-zinc-500">ч</span>
          <button class="btn btn-ghost">сохранить</button>
        </form>
        <form method="POST">
          <input type="hidden" name="action" value="shuffle_now">
          <button class="btn btn-primary">перемешать сейчас</button>
        </form>
      </div>
    </div>

    <div class="card mb-6 p-4 sm:p-6 border-zinc-700/80">
      <div class="flex flex-wrap items-center justify-between gap-2 mb-4 pb-3 border-b border-zinc-900">
        <div>
          <h2 class="text-xs sm:text-sm font-black uppercase tracking-[0.2em] text-zinc-100">+ добавить новый товар</h2>
          <p class="text-[11px] text-zinc-500 mt-0.5">Заполни основные поля — карточка сразу появится в каталоге и сохранится в базе.</p>
        </div>
        <span class="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 border border-zinc-800 text-zinc-400">быстрое добавление</span>
      </div>
      <form method="POST" enctype="multipart/form-data" class="space-y-4">
        <input type="hidden" name="action" value="create">
        <input type="hidden" name="in_stock" value="1">
        <div class="grid grid-cols-1 md:grid-cols-12 gap-3">
          <div class="md:col-span-6">
            <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider mb-1.5">название товара *</label>
            <input type="text" name="name" placeholder="Например: BALENCIAGA SKATER ZIP HOODIE" required class="input">
          </div>
          <div class="md:col-span-2">
            <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider mb-1.5">категория *</label>
            <select name="category" class="input">
              <option value="tshirts">Футболки</option>
              <option value="outerwear">Худи / Верх</option>
              <option value="pants">Штаны</option>
              <option value="shoes">Обувь</option>
            </select>
          </div>
          <div class="md:col-span-2">
            <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider mb-1.5">цена в EUR *</label>
            <input type="number" name="price_eur" min="0" placeholder="145" required class="input">
          </div>
          <div class="md:col-span-2">
            <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider mb-1.5">цена в UAH *</label>
            <input type="number" name="price_uah" min="0" placeholder="6500" required class="input">
          </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider">фото товара (до 4)</label>
              <span class="text-[10px] text-zinc-500">каждая строка — отдельное фото</span>
            </div>
            <div class="space-y-2">
              <div>
                <label class="text-[10px] text-zinc-500 font-bold">фото 1 (главное)</label>
                <input type="file" name="images_1" accept="image/png,image/jpeg,image/webp,image/gif"
                  class="input file:mr-3 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold file:cursor-pointer cursor-pointer"
                  onchange="previewSlot(this, 'slot-prev-1')">
                <div id="slot-prev-1" class="mt-1"></div>
              </div>
              <div>
                <label class="text-[10px] text-zinc-500 font-bold">фото 2</label>
                <input type="file" name="images_2" accept="image/png,image/jpeg,image/webp,image/gif"
                  class="input file:mr-3 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold file:cursor-pointer cursor-pointer"
                  onchange="previewSlot(this, 'slot-prev-2')">
                <div id="slot-prev-2" class="mt-1"></div>
              </div>
              <div>
                <label class="text-[10px] text-zinc-500 font-bold">фото 3</label>
                <input type="file" name="images_3" accept="image/png,image/jpeg,image/webp,image/gif"
                  class="input file:mr-3 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold file:cursor-pointer cursor-pointer"
                  onchange="previewSlot(this, 'slot-prev-3')">
                <div id="slot-prev-3" class="mt-1"></div>
              </div>
              <div>
                <label class="text-[10px] text-zinc-500 font-bold">фото 4</label>
                <input type="file" name="images_4" accept="image/png,image/jpeg,image/webp,image/gif"
                  class="input file:mr-3 file:py-1 file:px-2 file:border-0 file:bg-zinc-800 file:text-zinc-200 file:text-[10px] file:font-bold file:cursor-pointer cursor-pointer"
                  onchange="previewSlot(this, 'slot-prev-4')">
                <div id="slot-prev-4" class="mt-1"></div>
              </div>
            </div>
            <details class="mt-2">
              <summary class="text-[10px] text-zinc-500 cursor-pointer hover:text-zinc-300">или вставить ссылки вручную</summary>
              <textarea id="new-product-images" name="images_str" rows="2" placeholder="https://...&#10;https://..." class="input font-mono text-xs mt-1" oninput="previewNewProductImages()"></textarea>
              <div id="new-product-preview" class="flex flex-wrap gap-2 mt-2"></div>
            </details>
          </div>
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="block text-[10px] font-black text-zinc-400 uppercase tracking-wider">описание товара</label>
              <span class="text-[10px] text-zinc-500">материал, фит, детали</span>
            </div>
            <textarea name="description" rows="4" placeholder="Плотный хлопок, оверсайз крой, состаренный принт..." class="input"></textarea>
          </div>
        </div>

        <div class="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-zinc-900">
          <p class="text-[11px] text-zinc-500">По умолчанию: цвет <b class="text-zinc-300">Черный</b>, размеры <b class="text-zinc-300">S, M, L, XL</b> (можно поменять после создания).</p>
          <button type="submit" class="btn btn-primary px-6 py-3">добавить товар в каталог</button>
        </div>
      </form>
    </div>

    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <div class="flex flex-wrap items-center gap-2">
        <div class="flex flex-wrap gap-1.5">{cat_tabs}</div>
        <span id="order-status" class="text-[11px] font-bold text-zinc-500">перетащи за ⋮⋮ чтобы поменять место</span>
      </div>
      <form method="GET" class="flex gap-2">
        <input type="hidden" name="cat" value="{cat_filter}">
        <input type="text" name="q" value="{q}" placeholder="поиск товара..." class="input !w-48 sm:!w-64 text-xs">
        <button class="btn btn-ghost">найти</button>
      </form>
    </div>
    <div id="products-list" class="space-y-2">{rows_html}</div>
    <script>
      function previewNewProductImages() {{
        const input = document.getElementById('new-product-images');
        const box = document.getElementById('new-product-preview');
        if (!input || !box) return;
        const urls = input.value.replace(/\\r/g, '\\n').replace(/\\n/g, ',').split(',').map(s => s.trim()).filter(Boolean).slice(0, 8);
        if (!urls.length) {{
          box.innerHTML = '';
          return;
        }}
        box.innerHTML = urls.map((u, idx) =>
          '<div class="relative w-14 h-16 border border-zinc-800 bg-black overflow-hidden">' +
          '<img src="' + u.replace(/"/g, '&quot;') + '" class="w-full h-full object-cover object-top" onerror="this.style.opacity=0.2">' +
          '<span class="absolute bottom-0 right-0 bg-black/80 text-[9px] text-zinc-300 px-1">' + (idx + 1) + '</span>' +
          '</div>'
        ).join('');
      }}

      function previewSlot(input, boxId) {{
        const box = document.getElementById(boxId);
        if (!box) return;
        box.innerHTML = '';
        if (!input.files || !input.files[0]) return;
        const url = URL.createObjectURL(input.files[0]);
        box.innerHTML = '<img src="' + url + '" class="w-16 h-20 object-cover object-top border border-zinc-700">';
      }}

      function previewLocalImages(input, boxId) {{
        const box = document.getElementById(boxId);
        if (!box || !input.files) return;
        box.innerHTML = '';
        Array.from(input.files).slice(0, 12).forEach((file, idx) => {{
          const url = URL.createObjectURL(file);
          const div = document.createElement('div');
          div.className = 'relative w-14 h-16 border border-zinc-800 bg-black overflow-hidden';
          div.innerHTML = '<img src="' + url + '" class="w-full h-full object-cover object-top">' +
            '<span class="absolute bottom-0 right-0 bg-black/80 text-[9px] text-zinc-300 px-1">' + (idx + 1) + '</span>';
          box.appendChild(div);
        }});
      }}

      (function() {{
        const list = document.getElementById('products-list');
        const statusEl = document.getElementById('order-status');
        if (!list) return;
        let dragged = null;

        list.querySelectorAll('details[data-prod-id]').forEach(card => {{
          const handle = card.querySelector('.drag-handle');
          if (!handle) return;

          handle.addEventListener('pointerdown', () => card.setAttribute('draggable', 'true'));
          card.addEventListener('dragstart', e => {{
            dragged = card;
            card.style.opacity = '0.4';
            e.dataTransfer.effectAllowed = 'move';
          }});
          card.addEventListener('dragend', () => {{
            card.style.opacity = '';
            card.removeAttribute('draggable');
            if (dragged) saveOrder();
            dragged = null;
          }});
          card.addEventListener('dragover', e => {{
            e.preventDefault();
            if (!dragged || dragged === card) return;
            const rect = card.getBoundingClientRect();
            const after = (e.clientY - rect.top) > rect.height / 2;
            list.insertBefore(dragged, after ? card.nextSibling : card);
          }});
        }});

        function saveOrder() {{
          const ids = Array.from(list.querySelectorAll('details[data-prod-id]'))
            .map(el => Number(el.dataset.prodId));
          if (statusEl) statusEl.textContent = 'сохраняю порядок...';
          fetch('/admin/products/reorder', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ ids }})
          }})
          .then(r => r.json())
          .then(d => {{
            if (statusEl) statusEl.textContent = d.ok ? 'порядок сохранён ✓' : 'ошибка сохранения';
          }})
          .catch(() => {{
            if (statusEl) statusEl.textContent = 'ошибка сохранения';
          }});
        }}
      }})();
    </script>
    """
    return render_admin(content, active="products", flash=flash)

@app.route("/admin/products/reorder", methods=["POST"])
def admin_products_reorder():
    if not is_admin():
        return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get("ids") or []
    for idx, pid in enumerate(ids):
        p = Product.query.get(int(pid))
        if p:
            p.sort_order = idx
    db.session.commit()
    sync_products_catalog()
    admin_log("products_reorder", f"{len(ids)} items")
    return jsonify({"ok": True})


@app.route('/admin/lookbook', methods=['GET', 'POST'])
def admin_lookbook():
    guard = admin_guard()
    if guard:
        return guard
    flash = None
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            code = (request.form.get('code') or 'ARCHIVE FILE').strip()
            title = (request.form.get('title') or '').strip()
            description = (request.form.get('description') or '').strip()
            raw_ids = [int(x) for x in request.form.getlist('product_ids') if str(x).isdigit()]
            sort_order = int(request.form.get('sort_order') or 0)
            if title and raw_ids:
                db.session.add(LookbookLook(code=code, title=title, description=description,
                                            product_ids_json=json.dumps(raw_ids[:4]), sort_order=sort_order, active=True))
                db.session.commit()
                admin_log('lookbook_create', title)
                flash = f'Образ «{title}» добавлен в Lookbook'
            else:
                flash = 'Укажи название образа и выбери хотя бы 1 товар'
        elif action == 'update':
            lid = int(request.form.get('look_id') or 0)
            look = LookbookLook.query.get(lid)
            if look:
                look.code = (request.form.get('code') or look.code).strip()
                look.title = (request.form.get('title') or look.title).strip()
                look.description = (request.form.get('description') or '').strip()
                raw_ids = [int(x) for x in request.form.getlist('product_ids') if str(x).isdigit()]
                if raw_ids:
                    look.product_ids_json = json.dumps(raw_ids[:4])
                look.sort_order = int(request.form.get('sort_order') or 0)
                look.active = bool(request.form.get('active'))
                db.session.commit()
                admin_log('lookbook_update', f'#{lid} {look.title}')
                flash = f'Образ «{look.title}» сохранён'
        elif action == 'delete':
            lid = int(request.form.get('look_id') or 0)
            look = LookbookLook.query.get(lid)
            if look:
                t = look.title
                db.session.delete(look)
                db.session.commit()
                admin_log('lookbook_delete', f'#{lid} {t}')
                flash = f'Образ «{t}» удалён'

    products = Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()
    looks = LookbookLook.query.order_by(LookbookLook.sort_order.asc(), LookbookLook.id.asc()).all()
    by_id = {p.id: p for p in products}

    def prod_checks(selected_ids=None):
        sel = set(selected_ids or [])
        out = []
        for p in products:
            img = (p.images_str or '').split(',')[0].strip()
            chk = 'checked' if p.id in sel else ''
            out.append(f'<label class="flex items-center gap-2 p-2 border border-zinc-800 bg-black/50 hover:border-pink-500/60 cursor-pointer text-xs"><input type="checkbox" name="product_ids" value="{p.id}" {chk} class="accent-pink-500">' + (f'<img src="{img}" class="w-8 h-10 object-cover border border-zinc-800 shrink-0">' if img else '') + f'<span class="truncate">#{p.id} {p.name} <b class="text-pink-400">€{p.price_eur}</b></span></label>')
        return ''.join(out)

    looks_html = ''
    for look in looks:
        selected = look.product_ids()
        names = ', '.join(by_id[i].name for i in selected if i in by_id) or 'товары не найдены'
        looks_html += f'''
        <details class="card">
          <summary class="p-4 flex flex-wrap items-center justify-between gap-2 cursor-pointer list-none">
            <div><span class="text-[10px] font-black text-pink-400 uppercase tracking-widest">{look.code}</span>
            <p class="text-sm font-black text-zinc-100">{look.title} {'' if look.active else '<span class="text-[10px] text-red-400">[скрыт]</span>'}</p>
            <p class="text-[11px] text-zinc-500">{names}</p></div>
            <span class="text-[11px] font-bold text-zinc-400">редактировать ↓</span>
          </summary>
          <div class="p-4 border-t border-zinc-900 bg-black/40 space-y-3">
            <form method="POST" class="space-y-3">
              <input type="hidden" name="action" value="update"><input type="hidden" name="look_id" value="{look.id}">
              <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div><label class="block text-[10px] text-zinc-500 uppercase mb-1">код / номер</label><input name="code" value="{look.code.replace('"','&quot;')}" class="input"></div>
                <div><label class="block text-[10px] text-zinc-500 uppercase mb-1">название образа</label><input name="title" value="{look.title.replace('"','&quot;')}" required class="input"></div>
                <div><label class="block text-[10px] text-zinc-500 uppercase mb-1">порядок</label><input type="number" name="sort_order" value="{look.sort_order}" class="input"></div>
              </div>
              <div><label class="block text-[10px] text-zinc-500 uppercase mb-1">описание</label><textarea name="description" rows="2" class="input">{look.description or ''}</textarea></div>
              <div><label class="block text-[10px] text-zinc-500 uppercase mb-1">товары в образе (до 4)</label><div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-64 overflow-y-auto p-1">{prod_checks(selected)}</div></div>
              <label class="inline-flex items-center gap-2 text-xs font-bold text-zinc-300"><input type="checkbox" name="active" value="1" {'checked' if look.active else ''} class="accent-pink-500"> показывать на /lookbook</label>
              <div><button class="btn btn-primary">сохранить образ</button></div>
            </form>
            <form method="POST" onsubmit="return confirm('Удалить образ {look.title}?')"><input type="hidden" name="action" value="delete"><input type="hidden" name="look_id" value="{look.id}"><button class="btn btn-danger">удалить</button></form>
          </div>
        </details>'''
    if not looks_html:
        looks_html = '<div class="card p-6 text-xs text-zinc-400">Пока ни один ручной образ не создан — на <a href="/lookbook" target="_blank" class="underline text-pink-400">/lookbook</a> сейчас показываются автоматические подборки из каталога. Создай первый образ выше, чтобы управлять страницей вручную.</div>'

    content = f'''
    <div class="flex items-center justify-between gap-2 mb-4"><h1 class="text-sm font-black uppercase tracking-widest">управление lookbook</h1><a href="/lookbook" target="_blank" class="btn btn-ghost">открыть /lookbook ↗</a></div>
    <details class="card mb-6" open>
      <summary class="p-4 text-xs font-black uppercase tracking-[0.2em] text-zinc-200 cursor-pointer">+ создать образ для lookbook</summary>
      <form method="POST" class="p-4 sm:p-6 border-t border-zinc-900 space-y-3">
        <input type="hidden" name="action" value="create">
        <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div><label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">код / метка</label><input name="code" value="01 / DEAD SIGNAL" class="input"></div>
          <div><label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">название образа *</label><input name="title" placeholder="AFTER HOURS" required class="input"></div>
          <div><label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">порядок</label><input type="number" name="sort_order" value="{len(looks)+1}" class="input"></div>
        </div>
        <div><label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">описание образа</label><textarea name="description" rows="2" placeholder="Тёмная база, свободный силуэт..." class="input"></textarea></div>
        <div><label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">выбери товары для образа (2–4 вещи) *</label><div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 max-h-72 overflow-y-auto p-1">{prod_checks()}</div></div>
        <button class="btn btn-primary">добавить образ</button>
      </form>
    </details>
    <div class="space-y-3">{looks_html}</div>'''
    return render_admin(content, active='lookbook', flash=flash)

@app.route("/admin/promos", methods=["GET", "POST"])
def admin_promos():
    guard = admin_guard()
    if guard:
        return guard
    flash = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "create":
            code = (request.form.get("code") or "").strip().upper()
            try:
                pct = int(request.form.get("discount_percent") or 5)
            except Exception:
                pct = 5
            pct = max(1, min(99, pct))
            first_only = bool(request.form.get("first_order_only"))
            try:
                max_u = max(0, int(request.form.get("max_uses") or 0))
            except Exception:
                max_u = 0
            if not code:
                flash = "Введи код"
            elif PromoCode.query.filter_by(code=code).first():
                flash = f"Промокод {code} уже существует"
            else:
                db.session.add(PromoCode(
                    code=code,
                    discount_percent=pct,
                    active=True,
                    first_order_only=first_only,
                    uses_count=0,
                    max_uses=max_u,
                    created_str=time.strftime("%Y-%m-%d %H:%M:%S"),
                ))
                db.session.commit()
                admin_log("promo_create", f"{code} -{pct}%")
                flash = f"Промокод {code} (−{pct}%) добавлен"
        elif action == "toggle":
            pid = int(request.form.get("promo_id") or 0)
            pr = PromoCode.query.get(pid)
            if pr:
                pr.active = not bool(pr.active)
                db.session.commit()
                admin_log("promo_toggle", f"{pr.code} → {pr.active}")
                flash = f"{pr.code}: {'включён' if pr.active else 'выключен'}"
        elif action == "delete":
            pid = int(request.form.get("promo_id") or 0)
            pr = PromoCode.query.get(pid)
            if pr:
                c = pr.code
                db.session.delete(pr)
                db.session.commit()
                admin_log("promo_delete", c)
                flash = f"Промокод {c} удалён"

    promos = PromoCode.query.order_by(PromoCode.id.desc()).all()
    rows = ""
    for pr in promos:
        badge = '<span class="text-emerald-400">активен</span>' if pr.active else '<span class="text-zinc-500">выключен</span>'
        fo = '<span class="text-[10px] text-amber-400 ml-2">только 1-й заказ</span>' if pr.first_order_only else ''
        lim_txt = f"{pr.uses_count or 0} / {pr.max_uses}" if (pr.max_uses or 0) > 0 else f"{pr.uses_count or 0} / ∞"
        rows += f"""
        <div class="card p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-sm font-black text-zinc-100">{pr.code} <span class="text-emerald-400 ml-1">−{pr.discount_percent}%</span>{fo}</p>
            <p class="text-[11px] text-zinc-500">{badge} · использований: <b class="text-zinc-300">{lim_txt}</b> · {pr.created_str or ''}</p>
          </div>
          <div class="flex gap-2">
            <form method="POST">
              <input type="hidden" name="action" value="toggle">
              <input type="hidden" name="promo_id" value="{pr.id}">
              <button class="btn btn-ghost">{'выключить' if pr.active else 'включить'}</button>
            </form>
            <form method="POST" onsubmit="return confirm('Удалить промокод {pr.code}?')">
              <input type="hidden" name="action" value="delete">
              <input type="hidden" name="promo_id" value="{pr.id}">
              <button class="btn btn-danger">удалить</button>
            </form>
          </div>
        </div>"""
    if not rows:
        rows = '<div class="card p-8 text-center text-xs text-zinc-500">промокодов пока нет</div>'

    content = f"""
    <div class="card p-5 mb-6">
      <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400 mb-4">+ новый промокод</h2>
      <form method="POST" class="flex flex-wrap items-end gap-3">
        <input type="hidden" name="action" value="create">
        <div>
          <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">код</label>
          <input type="text" name="code" placeholder="ARCHIVE10" required class="input !w-44 uppercase font-bold">
        </div>
        <div>
          <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">скидка %</label>
          <input type="number" name="discount_percent" value="10" min="1" max="99" required class="input !w-28 font-bold">
        </div>
        <div>
          <label class="block text-[10px] font-bold text-zinc-500 uppercase mb-1">лимит (0 = безлимит)</label>
          <input type="number" name="max_uses" value="0" min="0" class="input !w-36 font-bold">
        </div>
        <label class="flex items-center gap-2 text-xs text-zinc-300 font-bold pb-2.5 cursor-pointer">
          <input type="checkbox" name="first_order_only" value="1" class="accent-zinc-100">
          только на первый заказ
        </label>
        <button class="btn btn-primary">добавить</button>
      </form>
    </div>
    <div class="space-y-3">{rows}</div>
    """
    return render_admin(content, active="promos", flash=flash)

@app.route("/admin/users", methods=["GET", "POST"])
def admin_users():
    guard = admin_guard()
    if guard:
        return guard
    flash = None
    if request.method == "POST":
        action = request.form.get("action")
        if action == "broadcast":
            title = (request.form.get("title") or "").strip()
            body = (request.form.get("body") or "").strip()
            kind = (request.form.get("kind") or "promo").strip()
            target = (request.form.get("target") or "all").strip()
            if title:
                if target == "all":
                    targets = Customer.query.all()
                else:
                    u_one = Customer.query.get(int(target)) if target.isdigit() else None
                    targets = [u_one] if u_one else []
                for tu in targets:
                    create_notification(tu.id, title, body, kind)
                admin_log("notify_send", f"{target} ({len(targets)}): {title}")
                flash = f"Уведомление отправлено ({len(targets)} юз.)"
        elif action == "delete":
            uid = int(request.form.get("user_id") or 0)
            u = Customer.query.get(uid)
            if u:
                em = u.email
                db.session.delete(u)
                db.session.commit()
                admin_log("user_delete", em)
                flash = f"Аккаунт {em} удалён"

    q = (request.args.get("q") or "").strip().lower()
    users = Customer.query.order_by(Customer.id.desc()).all()
    if q:
        users = [u for u in users if q in u.email.lower() or q in (u.telegram or "").lower() or q in (u.phone or "").lower()]

    rows = ""
    for u in users:
        cnt = Order.query.filter_by(user_id=u.id).count()
        ver = '<span class="text-emerald-400">verified</span>' if getattr(u, "email_verified", False) else '<span class="text-zinc-500">unverified</span>'
        rows += f"""
        <div class="card p-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <p class="text-xs sm:text-sm font-black text-zinc-100">#{u.id} · {u.email} <span class="text-[10px] font-bold ml-1">{ver}</span></p>
            <p class="text-[11px] text-zinc-500">TG: {u.telegram or '—'} · Тел: {u.phone or '—'} · Заказов: <b>{cnt}</b> · {u.created_str or ''}</p>
          </div>
          <form method="POST" onsubmit="return confirm('Удалить аккаунт {u.email}?')">
            <input type="hidden" name="action" value="delete">
            <input type="hidden" name="user_id" value="{u.id}">
            <button class="btn btn-danger">удалить</button>
          </form>
        </div>"""
    if not rows:
        rows = '<div class="card p-8 text-center text-xs text-zinc-500">пользователей пока нет</div>'

    user_opts = '<option value="all">Всем пользователям (' + str(len(users)) + ')</option>'
    for u in users[:100]:
        user_opts += f'<option value="{u.id}">#{u.id} · {u.email}</option>'

    content = f"""
    <div class="card p-4 mb-5">
      <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400 mb-3">отправить уведомление в колокольчик</h2>
      <form method="POST" class="grid grid-cols-1 sm:grid-cols-4 gap-2">
        <input type="hidden" name="action" value="broadcast">
        <select name="target" class="input text-xs">{user_opts}</select>
        <input type="text" name="title" placeholder="заголовок (напр. новый дроп)" required class="input text-xs">
        <input type="text" name="body" placeholder="текст уведомления (необязательно)" class="input text-xs">
        <button class="btn btn-primary">отправить</button>
      </form>
    </div>
    <div class="flex flex-wrap items-center justify-between gap-3 mb-4">
      <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400">зарегистрированные ({len(users)})</h2>
      <form method="GET" class="flex gap-2">
        <input type="text" name="q" value="{q}" placeholder="поиск email / TG / тел..." class="input !w-48 sm:!w-64 text-xs">
        <button class="btn btn-ghost">найти</button>
      </form>
    </div>
    <div class="space-y-3">{rows}</div>
    """
    return render_admin(content, active="users", flash=flash)

@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    guard = admin_guard()
    if guard:
        return guard
    flash = None
    if request.method == "POST":
        new_pw = (request.form.get("new_password") or "").strip()
        if len(new_pw) >= 4:
            with open(_PW_FILE, "w", encoding="utf-8") as f:
                f.write(new_pw)
            flash = "Пароль админки обновлён"
        else:
            flash = "Пароль должен быть минимум 4 символа"

    content = """
    <div class="max-w-lg card p-6">
      <h2 class="text-xs font-black uppercase tracking-[0.2em] text-zinc-400 mb-4">пароль админки</h2>
      <form method="POST" class="space-y-3">
        <input type="password" name="new_password" placeholder="новый пароль (мин. 4 символа)" required class="input">
        <button class="btn btn-primary">обновить пароль</button>
      </form>
      <div class="mt-8 pt-6 border-t border-zinc-900 text-xs text-zinc-500 space-y-1">
        <p><b>Режим сайта:</b> сейчас в app.py стоит <code>SITE_OPEN = """ + str(SITE_OPEN) + """</code></p>
        <p><b>Telegram:</b> """ + TELEGRAM_URL + """</p>
      </div>
    </div>
    """
    return render_admin(content, active="settings", flash=flash)

@app.route("/cron/shuffle_products")
def cron_shuffle_products():
    secret = request.args.get("key", "")
    expected = (os.environ.get("CRON_KEY") or "deadarchive_cron")
    if secret != expected:
        return "forbidden", 403
    cnt = shuffle_products_now()
    try:
        admin_log("cron_shuffle", f"перемешано {cnt} товаров")
    except Exception:
        pass
    return f"ok:{cnt}"


if __name__ == '__main__':
    app.run(debug=True, port=5000)
