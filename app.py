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

app = Flask(__name__)
app.secret_key = 'dead_archive_secret_key_2026'

# === РЕЖИМ САЙТА ===
# False = закрыт (заглушка), True = открыт
SITE_OPEN = True
# Пароль для входа пока закрыт. Пустая строка "" = без пароля, только заглушка
SITE_ACCESS_PASSWORD = "1234"

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
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://sssdopsinn.pythonanywhere.com")


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

def seed_default_products():
    if Product.query.count() == 0:
        default_items = [
            Product(
                name="ФУТБОЛКА VET@MENTS ANTISOCIAL",
                category="tshirts",
                price_eur=60,
                price_uah=3050,
                images_str="https://kappa.lol/zlEwzv, https://picsum.photos/id/1015/800/800",
                colors_str="Черный, Серый",
                description="Чёрная оверсайзка с надписью Antisocial спереди. Сидит свободно, хлопок плотнее обычного.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="ФУТБОЛКА VET@MENTS.COM",
                category="tshirts",
                price_eur=60,
                price_uah=3050,
                images_str="https://kappa.lol/34ieZw, https://kappa.lol/gJtShU",
                colors_str="Черный",
                description="Футболка с .com логотипом, края чуть потрёпанные. На спине вышивка.",
                sizes_str="S, M, L"
            ),
            Product(
                name="Rick Owens Cargo Pants",
                category="pants",
                price_eur=1,
                price_uah=1,
                images_str="https://kappa.lol/rXB04I",
                colors_str="Черный",
                description="Клёш с накладными карманами. Вверху свободные, к низу уже.",
                sizes_str="S, M, L"
            ),
            Product(
                name="Mowalola Graffiti Bomber",
                category="tshirts",
                price_eur=150,
                price_uah=7800,
                images_str="https://kappa.lol/b33UXf, https://kappa.lol/dkpCvw",
                colors_str="Базовый",
                description="Короткий бомбер с граффити по верху. Один цвет, без лишнего.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Balenciaga Printed Bull King Denim",
                category="pants",
                price_eur=100,
                price_uah=5180,
                images_str="https://kappa.lol/EHLdXM, https://kappa.lol/kMKGUZ",
                colors_str="Чёрный",
                description="Прямые чёрные джинсы с принтом Bull King. Деним жёсткий.",
                sizes_str="1, 2, 3, 4,"
            ),
            Product(
                name="Enfants Riches Deprimes Tee",
                category="tshirts",
                price_eur=40,
                price_uah=2050,
                images_str="https://kappa.lol/XctFqs, https://kappa.lol/2hWvXD",
                colors_str="Черный,",
                description="Чёрная ERD, принт на спине. Обычная посадка, без оверсайза.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Enfants Riches Deprimes 13 Days In Detox",
                category="tshirts",
                price_eur=40,
                price_uah=2050,
                images_str="https://kappa.lol/DJ3ksD, https://kappa.lol/pqmTgT",
                colors_str="Черный, Серый",
                description="Принт 13 Days In Detox. Есть чёрный и серый.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Vetements Polizei zip-hoodie",
                category="outerwear",
                price_eur=100,
                price_uah=5150,
                images_str="https://kappa.lol/VykYzK, https://kappa.lol/UXhsCf",
                colors_str="Зелёный",
                description="Зелёная зипка, на спине Polizei белым. Оверсайз.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Acne Studios Loose Fit Trousers",
                category="tshirts",
                price_eur=90,
                price_uah=4600,
                images_str="https://kappa.lol/5x993V, https://kappa.lol/Xt2F1F, https://kappa.lol/D1M4kN",
                colors_str="Базовый",
                description="Свободные штаны с разводами краски. Сидят мешковато.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Rick Owens x Moncler Boots",
                category="shoes",
                price_eur=193,
                price_uah=10000,
                images_str="https://kappa.lol/FOHSIe, https://kappa.lol/Lg1wIZ, https://kappa.lol/FR8qp0",
                colors_str="Черный, ",
                description="Высокие ботинки, полный комплект в коробке. Размерная сетка широкая — с 35 по 46.",
                sizes_str="35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46,"
            ),
            Product(
                name="Paiki Zip",
                category="outerwear",
                price_eur=82,
                price_uah=4200,
                images_str="https://kappa.lol/NES97H, https://kappa.lol/6CnOX2, https://kappa.lol/Bc0064",
                colors_str="Черный, Белый,",
                description="Зип-худи с опушкой на капюшоне. Чёрный / белый.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Project G/R Bomber",
                category="outerwear",
                price_eur=105,
                price_uah=5400,
                images_str="https://kappa.lol/AmGcCv, https://kappa.lol/Dxi8Ab, https://kappa.lol/OE7UmS",
                colors_str="Черный,",
                description="Чёрный бомбер. Можно взять с потёртостями или чистый.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Project G/R x Mastermind",
                category="outerwear",
                price_eur=67,
                price_uah=3450,
                images_str="https://kappa.lol/1zikza, https://kappa.lol/mg3AFe",
                colors_str="Черный,",
                description="Чёрная зипка, принт на плече.",
                sizes_str="S, M, L, "
            ),
            Product(
                name="Vetements Eye of Horus",
                category="tshirts",
                price_eur=85,
                price_uah=4300,
                images_str="https://kappa.lol/DWS7U5, https://kappa.lol/ZYhtz1, https://kappa.lol/8nymHD",
                colors_str="Черный, Серый",
                description="Глаз Хоруса на груди. Чёрный или серый, размеры идут с запасом.",
                sizes_str="XS, S, M(oversized),"
            ),
            Product(
                name="Vetements Zurich",
                category="tshirts",
                price_eur=78,
                price_uah=4000,
                images_str="https://kappa.lol/nNy6Fr, https://kappa.lol/JlogA4, https://kappa.lol/OBkEIW",
                colors_str="Черный,",
                description="Зипка Zurich, один цвет. XS–M — уже оверсайз.",
                sizes_str="XS, S, M(oversized),"
            ),
            Product(
                name="Vetements Ecstasy",
                category="outerwear",
                price_eur=90,
                price_uah=4650,
                images_str="https://kappa.lol/5PjOXe, https://kappa.lol/Ay55wg, https://kappa.lol/R6qzBZ",
                colors_str="Синий,",
                description="Синий худи Ecstasy, других цветов нет. Ткань плотная.",
                sizes_str="XS, S, M(oversized),"
            ),
            Product(
                name="Vetements Target",
                category="outerwear",
                price_eur=90,
                price_uah=4600,
                images_str="https://kappa.lol/nFpe2w, https://kappa.lol/VVO97M",
                colors_str="Черный, ",
                description="Чёрный Target на груди. Сетка размеров близка к оригиналу.",
                sizes_str="XXS, XS, S, M(oversized),"
            ),
            Product(
                name="Vetements Denim Pants",
                category="pants",
                price_eur=95,
                price_uah=4900,
                images_str="https://kappa.lol/4A1BU9, https://kappa.lol/wd2YJk, https://kappa.lol/SF7GFI",
                colors_str="Черный, Серый",
                description="Широкие джинсы, чёрный и серый. Пояса 28–34.",
                sizes_str="28, 30, 32, 34(oversized),"
            ),
            Product(
                name="Enfants Riches Deprimes 'Pour La P0rn0graphie?",
                category="tshirts",
                price_eur=67,
                price_uah=3500,
                images_str="https://kappa.lol/GAmb11, https://kappa.lol/jsfzjc, https://kappa.lol/vDU0wt",
                colors_str="Черный,",
                description="Худи с этой графикой на груди. M–XL.",
                sizes_str="M, L, XL,"
            ),
            Product(
                name="Rick Owens x Moncler Boots",
                category="tshirts",
                price_eur=193,
                price_uah=10000,
                images_str="https://kappa.lol/vDU0wt, https://kappa.lol/Lg1wIZ, https://kappa.lol/FR8qp0",
                colors_str="Черный,",
                description="Высокие ботинки, полный комплект в коробке. Размерная сетка широкая — с 35 по 46.",
                sizes_str="37, 38, 39, 40, 41, 42, 43, 44, 45, 46,"
            ),
            Product(
                name="Balenciaga x Adidas bomber",
                category="tshirts",
                price_eur=150,
                price_uah=7750,
                images_str="https://kappa.lol/cVU7gq, https://kappa.lol/K36XyS",
                colors_str="Черный,",
                description="Тяжёлый чёрный бомбер, коллаба с Adidas. Плечи широкие.",
                sizes_str="44, 46, 48,"
            ),
            Product(
                name="Haveyoudiedbefore Long Sleeve",
                category="tshirts",
                price_eur=50,
                price_uah=2500,
                images_str="https://kappa.lol/na5hCC, https://kappa.lol/nmvn9P, https://kappa.lol/DqJ20a",
                colors_str="Черный,",
                description="Чёрный лонг, спереди кукла. Простой крой.",
                sizes_str="S, M, L, "
            ),
            Product(
                name="Kai Angel Damage Long Sleeve ",
                category="tshirts",
                price_eur=50,
                price_uah=2500,
                images_str="https://kappa.lol/wMl0Rx, https://kappa.lol/rmLi2T, https://kappa.lol/eLPyos",
                colors_str="Черный,",
                description="Лонг Damage, чёрный. S–L.",
                sizes_str="S, M, L, "
            ),
            Product(
                name="9mice Jenna",
                category="outerwear",
                price_eur=45,
                price_uah=2320,
                images_str="https://kappa.lol/SSWmqf, https://kappa.lol/ZHuw4o, https://kappa.lol/CdZ47d",
                colors_str="Черный,",
                description="Чёрная футболка с Jenna — мерч 9mice.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Ann Demeulemeester",
                category="tshirts",
                price_eur=40,
                price_uah=2065,
                images_str="https://kappa.lol/1N2YCe, https://kappa.lol/fPRB2I",
                colors_str="Черный,",
                description="Футболка с принтом спереди. Тонкая, без оверсайза.",
                sizes_str=" M, L, XL,"
            ),
            Product(
                name="Enfants Riches Deprimes Zip-Hoodie",
                category="tshirts",
                price_eur=87,
                price_uah=4500,
                images_str="https://kappa.lol/k9YYLT, https://kappa.lol/Zg7lhK, https://kappa.lol/oBQT0P",
                colors_str="Черный, ",
                description="Полная молния, капюшон. Близко к линейке Narcotica.",
                sizes_str="S, M, L, XL,"
            ),
            Product(
                name="Project G/R Army Zip-hoodie",
                category="tshirts",
                price_eur=60,
                price_uah=3100,
                images_str="https://kappa.lol/Xckzfc, https://kappa.lol/WR5LUF, https://kappa.lol/T6KsC0",
                colors_str="Серый,",
                description="Серая army-зипка. Только серый.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Balenciaga Paris Soccer",
                category="outerwear",
                price_eur=80,
                price_uah=4130,
                images_str="https://kappa.lol/WCDNkk, https://kappa.lol/cK5Bef",
                colors_str="Черный, Белый",
                description="Принты спереди и сзади, оверсайз. Несколько цветов.",
                sizes_str="S, M, L,"
            ),
            Product(
                name="Balenciaga Black Metal Stencil Hoodie",
                category="outerwear",
                price_eur=95,
                price_uah=4900,
                images_str="https://kappa.lol/vK7tDD, https://kappa.lol/rVt0pL, https://kappa.lol/2tWq8q",
                colors_str="Черный,",
                description="Чёрный худи, трафаретный принт. Один цвет, свободный.",
                sizes_str="XS, S, M, L,"
            ),
            Product(
                name="Balenciaga Paris Moon Hoodie",
                category="outerwear",
                price_eur=110,
                price_uah=5700,
                images_str="https://kappa.lol/6xU9WF, https://kappa.lol/kJRwVn",
                colors_str="Черный, Серый",
                description="Луна / Paris Moon на худи. Плотная ткань.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Balenciaga Polo zip-hoodie",
                category="outerwear",
                price_eur=80,
                price_uah=4130,
                images_str="https://kappa.lol/JAZFwH, https://kappa.lol/ONloJ8, https://kappa.lol/TUAWKd",
                colors_str="Розовый, Белый, Чёрный, Темно-синий,",
                description="Зипка с воротником как у поло. Цветов несколько.",
                sizes_str="1, 2, 3,"
            ),
            Product(
                name="Balenciaga x Adidas Zip Hoodie",
                category="outerwear",
                price_eur=75,
                price_uah=3870,
                images_str="https://kappa.lol/J0bjt4, https://kappa.lol/xNobML, https://kappa.lol/b60CxR",
                colors_str="Черный,",
                description="Зип-худи коллабы с Adidas, спортивный силуэт.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Vetements Deconstructed Zip-Hoodie",
                category="tshirts",
                price_eur=90,
                price_uah=4650,
                images_str="https://kappa.lol/58cyqT, https://kappa.lol/vVSWAV, https://kappa.lol/IckeeQ",
                colors_str="Серый,",
                description="Открытые швы, оверсайз, один цвет.",
                sizes_str="S, M, L, "
            ),
            Product(
                name="Balenciaga Archives Music Limited Edition",
                category="outerwear",
                price_eur=80,
                price_uah=4130,
                images_str="https://kappa.lol/v69uKd, https://kappa.lol/FYuY0O, https://kappa.lol/gV3zEb",
                colors_str="Черный, Серый",
                description="Зипка из music-линейки. Один цвет.",
                sizes_str="XS, S, M,"
            ),
            Product(
                name="Vetements 18+ Restricted Tee",
                category="tshirts",
                price_eur=40,
                price_uah=2065,
                images_str="https://kappa.lol/YevSXB, https://kappa.lol/A3Lng0, https://kappa.lol/gNo9fo",
                colors_str="Черный, Белый,",
                description="Restricted 18+, тёмный принт. Прямая футболка.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Vetements Restricted Hoodie",
                category="outerwear",
                price_eur=68,
                price_uah=3500,
                images_str="https://kappa.lol/sEzq7A, https://kappa.lol/WgQL1G, https://kappa.lol/RFV8vk",
                colors_str="Черный, Серый",
                description="Худи Restricted. Если нужна дешевле — напиши, есть вариант проще.",
                sizes_str="S, M, L, XL"
            ),
            Product(
                name="Acne Studios Jeans",
                category="pants",
                price_eur=70,
                price_uah=3620,
                images_str="https://kappa.lol/ksAPIV, https://kappa.lol/qgeBLZ, https://kappa.lol/US0fEL",
                colors_str="Базовый,",
                description="Джинсы с мелким принтом аксессуаров по штанинам.",
                sizes_str="XS, S, M, L, XL,"
            ),
            Product(
                name="ERD Logo Hoodie",
                category="outerwear",
                price_eur=95,
                price_uah=4900,
                images_str="https://kappa.lol/AVimNA, https://kappa.lol/6RjXou",
                colors_str="Черный, ",
                description="Худи с лого ERD, обычный капюшон, плотная ткань.",
                sizes_str="M, L, XL,"
            ),
            Product(
                name="Balenciaga All In Bomber",
                category="outerwear",
                price_eur=140,
                price_uah=7227,
                images_str="https://kappa.lol/QIzOiP, https://kappa.lol/kj5lT9, https://kappa.lol/hwvbTS",
                colors_str="Черный,",
                description="Дутый All In, оверсайз. На холод как раз.",
                sizes_str="XS, S, M, L,"
            ),
        ]
        for item in default_items:
            db.session.add(item)
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
        _DESC_BY_NAME = {
            "ФУТБОЛКА VET@MENTS ANTISOCIAL": "Чёрная футболка свободной посадки с надписью Antisocial спереди. Хорошо вписывается в многослойный образ, а плотный на вид хлопок и оверсайз-силуэт задают ей нужную форму.",
            "ФУТБОЛКА VET@MENTS.COM": "Минималистичная чёрная футболка с логотипом .com. Потёртые по краям детали и вышивка на спине делают её интереснее обычной базовой футболки.",
            "Rick Owens Cargo Pants": "Свободные карго с накладными карманами и расклешённым силуэтом. Если хочется добавить образу объёма и чуть более необычных пропорций, эта пара подойдёт.",
            "Mowalola Graffiti Bomber": "Короткий бомбер с граффити по верхней части. За счёт укороченного кроя и заметного рисунка он сам задаёт настроение образу — сложный верх уже не обязателен.",
            "Balenciaga Printed Bull King Denim": "Чёрные джинсы прямого кроя с принтом Bull King. Деним выглядит довольно жёстким, поэтому силуэт держится чётко, а принт добавляет акцент без лишних деталей.",
            "Enfants Riches Deprimes Tee": "Чёрная футболка ERD с принтом на спине и обычной посадкой — без выраженного оверсайза. Спереди образ остаётся сдержанным, а графика раскрывается, когда смотришь со спины.",
            "Enfants Riches Deprimes 13 Days In Detox": "Футболка с графикой 13 Days In Detox. Можно выбрать чёрный или серый вариант — оба цвета легко сочетать с потёртым денимом, карго и простыми тёмными слоями.",
            "Vetements Polizei zip-hoodie": "Зелёная зипка с крупной белой надписью Polizei на спине. Оверсайз-посадка делает её удобной для многослойных сочетаний, а цвет заметно выделяется среди привычного чёрного.",
            "Acne Studios Loose Fit Trousers": "Свободные брюки с эффектом разводов краски. Посадка задумана расслабленной, так что лучше всего они смотрятся с простым верхом и обувью, которая не теряется на фоне широких штанин.",
            "Rick Owens x Moncler Boots": "Высокие ботинки в тёмном цвете с массивным силуэтом. В комплекте заявлена коробка; доступны размеры с 35 по 46, поэтому перед заказом лучше сверить размер с привычной обувью.",
            "Paiki Zip": "Зип-худи с опушкой на капюшоне, доступно в чёрном и белом цвете. Деталь на капюшоне делает вещь заметной даже в простом комплекте с джинсами или широкими брюками.",
            "Project G/R Bomber": "Чёрный бомбер можно выбрать в варианте с потёртостями или в более чистом исполнении. Первый выглядит более поношенным и грубым, второй проще вписать в повседневный гардероб.",
            "Project G/R x Mastermind": "Чёрная зипка с акцентным принтом на плече. В целом силуэт остаётся спокойным, поэтому вещь можно носить как самостоятельный верх или под куртку.",
            "Vetements Eye of Horus": "Футболка с изображением Глаза Хоруса на груди. Есть чёрный и серый варианты; учти, что размеры могут сидеть свободнее привычного, особенно если выбираешь между двумя.",
            "Vetements Zurich": "Зипка Zurich в чёрном цвете с расслабленной посадкой. Даже XS–M рассчитаны на оверсайз-эффект, так что ориентируйся не только на букву размера, но и на то, насколько объёмную посадку хочешь получить.",
            "Vetements Ecstasy": "Синий худи Ecstasy — без выбора между несколькими цветами, здесь акцент именно на насыщенном синем. Плотная ткань и свободный силуэт хорошо подходят для повседневных многослойных образов.",
            "Vetements Target": "Чёрная футболка с принтом Target на груди. Дизайн читается сразу и не требует сложного сочетания; размерная сетка заявлена близкой к оригинальной.",
            "Vetements Denim Pants": "Широкие джинсы в чёрном или сером цвете. Размеры по поясу — 28–34; свободные штанины лучше всего раскрываются с лаконичным верхом или объёмным худи.",
            "Enfants Riches Deprimes 'Pour La P0rn0graphie?": "Худи с графикой Pour La P0rn0graphie? на груди. Принт здесь главный акцент, поэтому вещь хорошо работает с простыми брюками и обувью без слишком активных деталей.",
            "Balenciaga x Adidas bomber": "Тяжёлый чёрный бомбер в спортивном духе коллаборации Adidas. Широкие плечи дают объёмный силуэт — особенно хорошо он смотрится с прямыми или широкими брюками.",
            "Haveyoudiedbefore Long Sleeve": "Чёрный лонгслив с изображением куклы спереди. Крой простой, поэтому вся выразительность — в графике; можно носить отдельно или как нижний слой под худи и куртку.",
            "Kai Angel Damage Long Sleeve": "Чёрный лонгслив Damage в размерах S–L. Удобный вариант, если хочется добавить графику в образ, но оставить силуэт более лёгким, чем у худи.",
            "9mice Jenna": "Чёрная футболка Jenna из мерча 9mice. Базовый цвет делает её простой в сочетании, а сам принт добавляет характер без необходимости собирать сложный образ.",
            "Ann Demeulemeester": "Футболка с принтом спереди и более тонким материалом, без оверсайз-посадки. Если не любишь слишком объёмные футболки, этот вариант будет ближе к обычному прямому силуэту.",
            "Enfants Riches Deprimes Zip-Hoodie": "Чёрная зип-худи с полной молнией и капюшоном, по настроению близкая к линейке Narcotica. Её удобно носить расстёгнутой поверх футболки или застёгнутой как самостоятельный верх.",
            "Project G/R Army Zip-hoodie": "Серая army-зипка без выбора других цветов. Спокойный оттенок хорошо сочетается с чёрными брюками и денимом, а формат на молнии удобен для многослойных комплектов.",
            "Balenciaga Paris Soccer": "Оверсайз-модель с принтами спереди и сзади; доступны несколько цветовых вариантов. За счёт графики вещь заметная, поэтому её легко сделать центральной частью комплекта.",
            "Balenciaga Black Metal Stencil Hoodie": "Чёрный худи с трафаретным принтом в духе metal-графики. Посадка свободная, а однотонная база оставляет принту всё внимание.",
            "Balenciaga Paris Moon Hoodie": "Худи Paris Moon с изображением луны, доступное в чёрном и сером цвете. Плотная ткань и спокойная палитра делают его удобной основой для тёмного повседневного образа.",
            "Balenciaga Polo zip-hoodie": "Зип-худи с воротником в стиле поло — немного необычнее привычной модели с обычным капюшоном. Есть розовый, белый, чёрный и тёмно-синий варианты, так что можно выбрать как спокойный, так и более заметный цвет.",
            "Balenciaga x Adidas Zip Hoodie": "Зип-худи в спортивном стиле коллаборации Balenciaga и Adidas. Полная молния позволяет носить его по-разному, а силуэт легко сочетается с джинсами и широкими спортивными брюками.",
            "Vetements Deconstructed Zip-Hoodie": "Серая зип-худи с открытыми швами и свободной посадкой. Деконструированные детали придают ей более необработанный вид, поэтому лучше не перегружать образ другими акцентами.",
            "Balenciaga Archives Music Limited Edition": "Зипка из линейки Archives Music в чёрном или сером исполнении. Дизайн остаётся в тёмной палитре, так что её удобно сочетать с денимом, карго и многослойным верхом.",
            "Vetements 18+ Restricted Tee": "Футболка Restricted 18+ с тёмной графикой, доступна в чёрном и белом цвете. Посадка прямая, поэтому она подойдёт тем, кто предпочитает более привычный силуэт вместо сильного оверсайза.",
            "Vetements Restricted Hoodie": "Худи Restricted в чёрном или сером цвете с выразительной графикой. Универсальная тёмная палитра позволяет носить его каждый день; если нужен другой вариант по цене, можно уточнить у поддержки.",
            "Acne Studios Jeans": "Джинсы с мелкими принтами аксессуаров вдоль штанин. Деталей достаточно, чтобы пара не выглядела скучно, поэтому верх можно оставить простым — например, однотонную футболку или худи.",
            "ERD Logo Hoodie": "Худи с логотипом ERD, обычным капюшоном и плотной тканью. Это вариант без лишних конструктивных деталей: основной акцент остаётся на логотипе и общем тёмном силуэте.",
            "Balenciaga All In Bomber": "Объёмный чёрный бомбер All In с дутым силуэтом и оверсайз-посадкой. Хорошо подходит для холодной погоды и лучше всего смотрится с прямыми или широкими брюками, которые поддерживают его объём.",
        }
        for _p in Product.query.all():
            _nd = _DESC_BY_NAME.get((_p.name or "").strip()) or _DESC_BY_NAME.get(_p.name or "")
            if _nd and (_p.description or "").strip() != _nd:
                _p.description = _nd
        db.session.commit()
    except Exception as e:
        print("desc update:", e)
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
  <link rel="preconnect" href="https://kappa.lol">
  <link rel="dns-prefetch" href="https://kappa.lol">
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
      background-image: url('https://kappa.lol/dBPkbe');
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
            <input type="text" id="cust-tg" placeholder="@OPIUM_CRW" oninput="clearFieldError(this, 'tg-err')" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3.5 md:py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-wider">
            <p id="tg-err" class="text-[10px] text-[#ff00a8] font-bold mt-1.5 hidden uppercase tracking-wider"></p>
          </div>
          <div>
            <label class="block text-[11px] font-black text-zinc-400 mb-2 tracking-widest uppercase">НОМЕР ТЕЛЕФОНА</label>
            <input type="text" id="cust-phone" placeholder="+380991234567" oninput="clearFieldError(this, 'phone-err')" class="w-full bg-zinc-950 border border-zinc-800 px-4 py-3.5 md:py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-wider">
            <p id="phone-err" class="text-[10px] text-[#ff00a8] font-bold mt-1.5 hidden uppercase tracking-wider"></p>
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
            <li class="flex gap-3"><span class="text-zinc-500 shrink-0">•</span><span><b class="text-zinc-100">Европа</b> — 10–18 дней, от 35 €</span></li>
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

    // ========== i18n ==========
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

    function clearFieldError(inputEl, errId) {
      inputEl.style.borderColor = '';
      const err = document.getElementById(errId);
      if (err) {
        err.classList.add('hidden');
        err.textContent = '';
      }
    }

    function showFieldError(inputEl, errId, msg) {
      inputEl.style.borderColor = '#ff00a8';
      inputEl.focus();
      const err = document.getElementById(errId);
      if (err) {
        err.textContent = '⚠ ' + msg;
        err.classList.remove('hidden');
      }
    }

    function submitOrder() {
      const tgInput = document.getElementById('cust-tg');
      const phoneInput = document.getElementById('cust-phone');

      let tg = tgInput.value.trim();
      const phone = phoneInput.value.trim();

      // Если юзер написал ник без @ (например OPIUM_CRW), аккуратно подставим @
      if (tg && !tg.startsWith('@') && /^[a-zA-Z0-9_]{4,32}$/.test(tg)) {
        tg = '@' + tg;
        tgInput.value = tg;
      }

      // Проверка Telegram: должен начинаться с @ и содержать 4-32 латинские буквы, цифры или _
      const tgRegex = /^@[a-zA-Z0-9_]{4,32}$/;
      if (!tgRegex.test(tg)) {
        showFieldError(tgInput, 'tg-err', 'Введи реальный Telegram username (например: @OPIUM_CRW)');
        return;
      }

      // Проверка телефона: убираем пробелы, скобки, дефисы и проверяем наличие 10-15 цифр (с плюсом или без)
      const cleanPhone = phone.replace(/[\s\-\(\)]/g, '');
      const phoneRegex = /^\+?[0-9]{10,15}$/;
      // Дополнительная защита от спама одинаковыми цифрами вроде +11111111111
      const isFakeRepeat = /^(\+?)(\d)\2{9,14}$/.test(cleanPhone);

      if (!phoneRegex.test(cleanPhone) || isFakeRepeat) {
        showFieldError(phoneInput, 'phone-err', 'Введи корректный номер телефона (например: +380991234567)');
        return;
      }

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

      const submitBtn = document.getElementById('submit-order-btn');
      if (submitBtn) submitBtn.disabled = true;

      fetch('/create_order', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items: payloadItems, contacts: contacts })
      })
      .then(res => res.json())
      .then(data => {
        if (submitBtn) submitBtn.disabled = false;
        if (data.success) {
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
        } else if (data.error) {
          if (data.field === 'telegram') {
            showFieldError(tgInput, 'tg-err', data.error);
          } else if (data.field === 'phone') {
            showFieldError(phoneInput, 'phone-err', data.error);
          } else {
            alert(data.error);
          }
        }
      })
      .catch(() => {
        if (submitBtn) submitBtn.disabled = false;
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
  <section id="home-hero" class="home-hero min-h-[100svh] h-[100svh] flex items-center justify-center bg-cover bg-center bg-no-repeat relative px-3 overflow-hidden" style="background-image: url('https://kappa.lol/dBPkbe');">
    <video id="home-bg-video" class="home-bg-video absolute inset-0 w-full h-full object-cover" src="https://kappa.lol/bGf3TR" autoplay muted loop playsinline webkit-playsinline preload="auto" disablepictureinpicture controlslist="nodownload noplaybackrate" aria-hidden="true"></video>
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
<section class="min-h-screen pt-28 sm:pt-32 pb-20 px-3 sm:px-6 relative bg-cover bg-center" style="background-image:url('https://kappa.lol/dBPkbe')">
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
  <section class="shop-section min-h-screen pt-28 sm:pt-32 md:pt-40 pb-20 sm:pb-20 md:pb-24 px-2 sm:px-4 md:px-6 relative bg-cover bg-center bg-no-repeat" style="background-image: url('https://kappa.lol/dBPkbe');">
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
                error = 'новый пароль минимум 4 символа'
            else:
                u.password_hash = generate_password_hash(new_pw)
                db.session.commit()
                ok = 'пароль изменён'

    orders = Order.query.filter_by(user_id=u.id).order_by(Order.time_str.desc()).all()
    if not orders and u.telegram:
        tg_clean = u.telegram.strip().lower()
        for o in Order.query.order_by(Order.time_str.desc()).limit(100).all():
            try:
                c = _json.loads(o.contacts_json)
                if (c.get('telegram') or '').strip().lower() == tg_clean:
                    orders.append(o)
            except Exception:
                pass

    total_spent = 0
    for o in orders:
        if o.status == 'Отменен':
            continue
        try:
            c = _json.loads(o.contacts_json)
            if c.get('total_eur') is not None:
                total_spent += float(c.get('total_eur') or 0)
            else:
                for it in _json.loads(o.items_json):
                    total_spent += int(it.get('price_eur') or 0)
        except Exception:
            pass
    total_spent_str = f"{total_spent:g}"

    orders_html = ""
    if orders:
        for o in orders:
            try:
                items = _json.loads(o.items_json)
            except Exception:
                items = []
            try:
                c = _json.loads(o.contacts_json)
            except Exception:
                c = {}
            items_txt = ", ".join(
                f"{it.get('name', '?')} [{it.get('selectedSize', '-')}]"
                for it in items
            ) or "—"
            tot = c.get('total_eur')
            if tot is None:
                tot = sum(int(it.get('price_eur') or 0) for it in items)
            promo_used = c.get('promo')
            promo_badge = f' · промо {promo_used}' if promo_used else ''
            st_cls = "text-zinc-200 border-zinc-700"
            if o.status == 'Отменен':
                st_cls = "text-red-400 border-red-900/50"
            elif o.status == 'Отправлен':
                st_cls = "text-sky-400 border-sky-900/50"
            elif o.status == 'Доставлен':
                st_cls = "text-emerald-400 border-emerald-900/50"
            orders_html += (
                '<div class="border border-white/10 bg-black/50 p-3.5 text-xs space-y-2">'
                '<div class="flex justify-between items-center gap-2">'
                f'<span class="font-black text-[#ff00a8] tracking-wider">{o.id}</span>'
                f'<span class="text-[10px] font-bold uppercase px-2 py-0.5 border {st_cls}">{o.status}</span>'
                '</div>'
                f'<p class="text-zinc-300 leading-relaxed">{items_txt}</p>'
                f'<div class="flex justify-between items-center text-[10px] text-zinc-500 pt-1 border-t border-white/5">'
                f'<span>{o.time_str}{promo_badge}</span>'
                f'<span class="font-black text-zinc-200">{tot:g} EUR</span>'
                '</div></div>'
            )
    else:
        orders_html = '<p class="text-xs text-zinc-500 py-6 text-center">заказов пока нет — выбери вещь в <a href="/shop" class="underline text-zinc-300">каталоге</a></p>'

    fav_ids = u.get_favorites()
    fav_products = [Product.query.get(pid) for pid in fav_ids]
    fav_products = [p for p in fav_products if p is not None]
    fav_html = ""
    if fav_products:
        cards = []
        for p in fav_products[:12]:
            imgs = [i.strip() for i in (p.images_str or '').split(',') if i.strip()]
            img = imgs[0] if imgs else ''
            cards.append(
                '<a href="/shop" class="group border border-white/10 bg-black/50 overflow-hidden block hover:border-white/25 transition-colors">'
                f'<div class="aspect-[3/4] bg-zinc-900 overflow-hidden"><img src="{img}" alt="" class="w-full h-full object-cover object-top group-hover:scale-[1.03] transition-transform duration-300" loading="lazy"></div>'
                '<div class="p-2">'
                f'<p class="text-[10px] font-bold text-zinc-200 uppercase truncate">{p.name}</p>'
                f'<p class="text-[10px] text-zinc-500 font-bold mt-0.5">{p.price_eur} EUR</p>'
                '</div></a>'
            )
        fav_html = '<div class="grid grid-cols-2 sm:grid-cols-3 gap-2.5">' + ''.join(cards) + '</div>'
    else:
        fav_html = '<p class="text-xs text-zinc-500 py-6 text-center">пока пусто — нажми ♡ на карточке в <a href="/shop" class="underline text-zinc-300">каталоге</a></p>'

    notifs = Notification.query.filter_by(user_id=u.id).order_by(Notification.id.desc()).limit(25).all()
    notif_html = ""
    if notifs:
        rows = []
        for n in notifs:
            dot = '' if n.is_read else '<span class="inline-block w-1.5 h-1.5 rounded-full bg-[#ff00a8] mr-1.5"></span>'
            rows.append(
                '<div class="border border-white/10 bg-black/40 p-3 text-xs">'
                f'<p class="font-black text-zinc-100">{dot}{n.title}</p>'
                + (f'<p class="text-[11px] text-zinc-400 mt-1 leading-relaxed">{n.body}</p>' if n.body else '')
                + f'<p class="text-[9px] text-zinc-600 mt-1.5">{n.created_str or ""}</p>'
                '</div>'
            )
        notif_html = '<div class="space-y-2">' + ''.join(rows) + '</div>'
    else:
        notif_html = '<p class="text-xs text-zinc-500 py-6 text-center">уведомлений пока нет</p>'

    sizes_opts = ['—', 'XS', 'S', 'M', 'L', 'XL']
    cur_sz = u.preferred_size or ''
    size_select_opts = ''.join(
        f'<option value="{"" if s == "—" else s}" {"selected" if (cur_sz == s or (s == "—" and not cur_sz)) else ""}>{s}</option>'
        for s in sizes_opts
    )
    ver_badge = '<span class="text-[9px] font-black uppercase tracking-wider px-2 py-0.5 border border-emerald-900/60 bg-emerald-950/30 text-emerald-400">verified</span>' if u.email_verified else '<span class="text-[9px] font-black uppercase tracking-wider px-2 py-0.5 border border-amber-900/60 text-amber-400">unverified</span>'

    body = (
        '<div class="flex flex-wrap items-center justify-between gap-2 mb-5 pb-4 border-b border-white/10">'
        '<div>'
        f'<p class="text-xs font-black text-zinc-100">{u.email}</p>'
        f'<p class="text-[10px] text-zinc-600 mt-0.5">с нами с {u.created_str or "2026"}</p>'
        '</div>'
        f'{ver_badge}'
        '</div>'

        '<div class="grid grid-cols-3 gap-2 mb-6">'
        '<div class="border border-white/10 bg-black/40 p-3 text-center">'
        f'<p class="text-lg font-black text-zinc-100">{len(orders)}</p>'
        '<p class="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mt-0.5">заказов</p>'
        '</div>'
        '<div class="border border-white/10 bg-black/40 p-3 text-center">'
        f'<p class="text-lg font-black text-zinc-100">{total_spent_str}€</p>'
        '<p class="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mt-0.5">сумма</p>'
        '</div>'
        '<div class="border border-white/10 bg-black/40 p-3 text-center">'
        f'<p class="text-lg font-black text-zinc-100">{len(fav_products)}</p>'
        '<p class="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mt-0.5">избранное</p>'
        '</div>'
        '</div>'

        '<div class="grid grid-cols-4 border border-white/10 mb-5 text-[10px] font-black uppercase tracking-wider">'
        '<button type="button" onclick="accTab(\'orders\')" id="tab-btn-orders" class="acc-tab py-2.5 bg-zinc-100 text-black">заказы</button>'
        '<button type="button" onclick="accTab(\'favs\')" id="tab-btn-favs" class="acc-tab py-2.5 text-zinc-400 hover:text-white border-l border-white/10">избранное</button>'
        '<button type="button" onclick="accTab(\'notifs\')" id="tab-btn-notifs" class="acc-tab py-2.5 text-zinc-400 hover:text-white border-l border-white/10">события</button>'
        '<button type="button" onclick="accTab(\'settings\')" id="tab-btn-settings" class="acc-tab py-2.5 text-zinc-400 hover:text-white border-l border-white/10">настройки</button>'
        '</div>'

        '<div id="tab-orders" class="acc-panel space-y-2.5">' + orders_html + '</div>'
        '<div id="tab-favs" class="acc-panel hidden">' + fav_html + '</div>'
        '<div id="tab-notifs" class="acc-panel hidden">' + notif_html + '</div>'

        '<div id="tab-settings" class="acc-panel hidden space-y-6">'
        '<form method="POST" class="space-y-3">'
        '<input type="hidden" name="action" value="profile">'
        '<p class="text-[10px] font-black uppercase tracking-[0.2em] text-zinc-500">контакты для заказов</p>'
        '<input type="text" name="telegram" value="' + (u.telegram or '') + '" placeholder="TELEGRAM @user" '
        'class="w-full bg-black border border-white/10 px-3.5 py-2.5 text-xs text-zinc-100 font-bold">'
        '<input type="text" name="phone" value="' + (u.phone or '') + '" placeholder="ТЕЛЕФОН" '
        'class="w-full bg-black border border-white/10 px-3.5 py-2.5 text-xs text-zinc-100 font-bold">'
        '<div>'
        '<label class="block text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-1">предпочитаемый размер</label>'
        '<select name="preferred_size" class="w-full bg-black border border-white/10 px-3.5 py-2.5 text-xs text-zinc-100 font-bold">' + size_select_opts + '</select>'
        '</div>'
        '<button class="w-full py-3 bg-zinc-100 text-black text-xs font-black uppercase tracking-widest hover:bg-white">сохранить</button>'
        '</form>'

        '<form method="POST" class="space-y-3 pt-5 border-t border-white/10">'
        '<input type="hidden" name="action" value="password">'
        '<p class="text-[10px] font-black uppercase tracking-[0.2em] text-zinc-500">сменить пароль</p>'
        '<input type="password" name="old_password" required placeholder="ТЕКУЩИЙ ПАРОЛЬ" '
        'class="w-full bg-black border border-white/10 px-3.5 py-2.5 text-xs text-zinc-100 font-bold">'
        '<input type="password" name="new_password" required minlength="4" placeholder="НОВЫЙ ПАРОЛЬ (МИН. 4)" '
        'class="w-full bg-black border border-white/10 px-3.5 py-2.5 text-xs text-zinc-100 font-bold">'
        '<button class="w-full py-3 border border-white/20 text-zinc-200 text-xs font-black uppercase tracking-widest hover:bg-white hover:text-black transition-colors">обновить пароль</button>'
        '</form>'
        '</div>'

        '<div class="mt-6 pt-4 border-t border-white/10 flex justify-between items-center text-[11px]">'
        '<a href="https://t.me/deadarchivemanager" target="_blank" rel="noopener" class="text-zinc-500 hover:text-zinc-200 uppercase font-bold tracking-wider">поддержка tg ↗</a>'
        '<a href="/logout" class="text-zinc-500 hover:text-red-400 uppercase font-bold tracking-wider">выйти</a>'
        '</div>'

        '<script>'
        'function accTab(name){'
        'document.querySelectorAll(".acc-panel").forEach(p=>p.classList.add("hidden"));'
        'document.querySelectorAll(".acc-tab").forEach(b=>{b.classList.remove("bg-zinc-100","text-black");b.classList.add("text-zinc-400");});'
        'var p=document.getElementById("tab-"+name);if(p)p.classList.remove("hidden");'
        'var b=document.getElementById("tab-btn-"+name);if(b){b.classList.add("bg-zinc-100","text-black");b.classList.remove("text-zinc-400");}'
        '}'
        '</script>'
    )
    return _auth_render('ПРОФИЛЬ', body, error=error, ok=ok)


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
        return jsonify({"ok": False, "error": ""})
    pr = PromoCode.query.filter_by(code=code).first()
    if not pr or not pr.active:
        return jsonify({"ok": False, "error": "НЕВЕРНЫЙ ПРОМОКОД"})
    if pr.max_uses and pr.max_uses > 0 and (pr.uses_count or 0) >= pr.max_uses:
        return jsonify({"ok": False, "error": "ЛИМИТ ПРОМОКОДА ИСЧЕРПАН"})
    if pr.first_order_only and not is_first:
        return jsonify({"ok": False, "error": "ПРОМОКОД ТОЛЬКО НА ПЕРВЫЙ ЗАКАЗ"})
    return jsonify({
        "ok": True,
        "code": pr.code,
        "discount_percent": int(pr.discount_percent or 0),
    })


@app.route('/create_order', methods=['POST'])
def create_order():
    import json
    import re
    data = request.get_json() or {}
    items = data.get("items", [])
    contacts = data.get("contacts", {})

    # Серверная валидация контактов: блокируем спам и некорректные форматы
    tg = (contacts.get("telegram") or "").strip()
    phone = (contacts.get("phone") or "").strip()

    if tg and not tg.startswith("@") and re.match(r"^[a-zA-Z0-9_]{4,32}$", tg):
        tg = "@" + tg
        contacts["telegram"] = tg

    if not re.match(r"^@[a-zA-Z0-9_]{4,32}$", tg):
        return jsonify({
            "success": False,
            "field": "telegram",
            "error": "Введи реальный Telegram username (например: @OPIUM_CRW)"
        }), 400

    clean_phone = re.sub(r"[\s\-\(\)]", "", phone)
    is_fake_repeat = bool(re.match(r"^(\+?)(\d)\2{9,14}$", clean_phone))
    if not re.match(r"^\+?[0-9]{10,15}$", clean_phone) or is_fake_repeat:
        return jsonify({
            "success": False,
            "field": "phone",
            "error": "Введи корректный номер телефона (например: +380991234567)"
        }), 400

    order_id = "ORD-" + str(int(time.time()))[-5:]
    time_str = time.strftime("%Y-%m-%d %H:%M:%S")
    uid = session.get("user_id")

    promo_code = (contacts.get("promo") or "").strip().upper()
    if promo_code:
        try:
            pr = PromoCode.query.filter_by(code=promo_code).first()
            if pr and pr.active:
                if not pr.max_uses or (pr.uses_count or 0) < pr.max_uses:
                    pr.uses_count = (pr.uses_count or 0) + 1
                else:
                    contacts["promo"] = None
                    contacts["discount_percent"] = 0
        except Exception as pe:
            print("promo use:", pe)

    new_order = Order(
        id=order_id,
        items_json=json.dumps(items, ensure_ascii=False),
        contacts_json=json.dumps(contacts, ensure_ascii=False),
        time_str=time_str,
        status='В обработке',
        user_id=uid,
    )
    db.session.add(new_order)
    db.session.commit()

    if uid:
        try:
            create_notification(
                uid,
                f"Заказ {order_id} принят",
                "Мы получили заказ. Менеджер свяжется в Telegram для подтверждения.",
                "order",
            )
        except Exception as ne:
            print("notif order:", ne)

    try:
        send_telegram_order(order_id, items, contacts, time_str)
    except Exception as e:
        print("Telegram notify exception:", e)

    return jsonify({"success": True, "order_id": order_id})

# ==========================================
# АДМИН-ПАНЕЛЬ (CRUD + УПРАВЛЕНИЕ ЗАКАЗАМИ)
# ==========================================
_PW_FILE = os.path.join(basedir, ".admin_pw")
def _load_admin_password():
    env_pw = os.environ.get("ADMIN_PASSWORD")
    if env_pw:
        return env_pw
    if os.path.exists(_PW_FILE):
        try:
            with open(_PW_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if val:
                    return val
        except Exception:
            pass
    return "DEAD2026"

def _save_admin_password(new_pw):
    with open(_PW_FILE, "w", encoding="utf-8") as f:
        f.write(new_pw)

ADMIN_PASSWORD = _load_admin_password()

def admin_log(action, detail=""):
    try:
        entry = AdminLog(
            time_str=time.strftime("%Y-%m-%d %H:%M:%S"),
            action=action[:200],
            detail=(detail or "")[:1000],
        )
        db.session.add(entry)
        db.session.commit()
    except Exception as e:
        print("admin_log:", e)

_AUTO_SHUFFLE_FILE = os.path.join(basedir, ".auto_shuffle")
AUTO_SHUFFLE_INTERVAL = 24 * 3600

def _load_auto_shuffle():
    try:
        if os.path.exists(_AUTO_SHUFFLE_FILE):
            with open(_AUTO_SHUFFLE_FILE, "r", encoding="utf-8") as f:
                data = _json.loads(f.read() or "{}")
                return bool(data.get("enabled")), float(data.get("last_ts") or 0)
    except Exception:
        pass
    return False, 0.0

def _save_auto_shuffle(enabled, last_ts):
    try:
        with open(_AUTO_SHUFFLE_FILE, "w", encoding="utf-8") as f:
            f.write(_json.dumps({"enabled": bool(enabled), "last_ts": float(last_ts)}))
    except Exception as e:
        print("save auto_shuffle:", e)

def _do_shuffle_products():
    import random
    prods = Product.query.all()
    if not prods:
        return 0
    order_list = list(range(len(prods)))
    random.shuffle(order_list)
    for p, new_pos in zip(prods, order_list):
        p.sort_order = new_pos
    db.session.commit()
    return len(prods)

def maybe_auto_shuffle():
    enabled, last_ts = _load_auto_shuffle()
    if not enabled:
        return False
    now = time.time()
    if now - last_ts < AUTO_SHUFFLE_INTERVAL:
        return False
    n = _do_shuffle_products()
    _save_auto_shuffle(True, now)
    admin_log("auto_shuffle", f"перемешано {n} товаров (авто раз в 24ч)")
    return True

@app.route('/admin', methods=['GET', 'POST'])
def admin_login():
    if session.get('is_admin'):
        return redirect(url_for('admin_dashboard'))

    error = None
    if request.method == 'POST':
        if request.form.get('password') == ADMIN_PASSWORD:
            session['is_admin'] = True
            admin_log("login", f"ip={request.remote_addr}")
            return redirect(url_for('admin_dashboard'))
        else:
            error = "НЕВЕРНЫЙ ПАРОЛЬ ДОСТУПА"

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
      <title>ADMIN // DEAD ARCHIVE</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@900&family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
      <style>body{font-family:'Inter',sans-serif;background:#020202;color:#e4e4e7}.gothic{font-family:'Cinzel',serif}</style>
    </head>
    <body class="min-h-screen flex items-center justify-center p-4">
      <div class="bg-[#09090b] border border-zinc-800 p-8 md:p-12 w-full max-w-md text-center shadow-2xl">
        <h1 class="text-2xl gothic tracking-[0.2em] text-zinc-50 mb-2">DEAD ARCHIVE</h1>
        <p class="text-[10px] tracking-[0.3em] text-red-500 font-bold uppercase mb-8">панель администратора</p>
        {% if error %}<p class="text-red-500 text-xs font-bold mb-4">{{ error }}</p>{% endif %}
        <form method="POST" class="space-y-4">
          <input type="password" name="password" placeholder="ВВЕДИТЕ ПАРОЛЬ..." required class="w-full bg-zinc-950 border border-zinc-800 px-4 py-4 text-sm text-zinc-100 focus:outline-none focus:border-zinc-500 font-bold tracking-widest text-center">
          <button type="submit" class="w-full py-4 bg-zinc-100 text-black font-black text-xs tracking-[0.2em] hover:bg-black hover:text-white hover:border hover:border-zinc-700 transition-all uppercase">ВОЙТИ В СИСТЕМУ</button>
        </form>
        <a href="/" class="block mt-6 text-xs text-zinc-600 hover:text-zinc-300 tracking-wider">← ВЕРНУТЬСЯ В МАГАЗИН</a>
      </div>
    </body>
    </html>
    ''', error=error)

@app.route('/admin/dashboard')
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    products = Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()
    orders = [o.to_dict() for o in Order.query.order_by(Order.time_str.desc()).all()]
    customers = Customer.query.order_by(Customer.id.desc()).all()
    logs = AdminLog.query.order_by(AdminLog.id.desc()).limit(50).all()
    promos = PromoCode.query.order_by(PromoCode.id.desc()).all()
    auto_shuffle_enabled, auto_shuffle_last = _load_auto_shuffle()
    auto_shuffle_last_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(auto_shuffle_last)) if auto_shuffle_last else "ещё не запускалось"
    try:
        looks = LookbookLook.query.order_by(LookbookLook.sort_order.asc(), LookbookLook.id.asc()).all()
    except Exception:
        looks = []
    products_by_id = {p.id: p for p in products}
    today_prefix = time.strftime("%Y-%m-%d")
    today_orders = [o for o in orders if (o.get("time") or "").startswith(today_prefix)]
    total_revenue = 0
    for o in orders:
        if o.get("status") == "Отменен":
            continue
        for it in o.get("items", []):
            try:
                total_revenue += int(it.get("price_eur") or 0)
            except Exception:
                pass
    pending_count = len([o for o in orders if o.get("status") == "В обработке"])

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
      <title>DASHBOARD // DEAD ARCHIVE</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@900&family=Inter:wght@400;600;800&display=swap" rel="stylesheet">
      <style>body{font-family:'Inter',sans-serif;background:#020202;color:#e4e4e7}.gothic{font-family:'Cinzel',serif}</style>
    </head>
    <body class="p-3 sm:p-4 md:p-8 max-w-7xl mx-auto">
      <div class="flex flex-wrap justify-between items-center border-b border-zinc-800 pb-4 sm:pb-6 mb-6 sm:mb-8 gap-3 sm:gap-4">
        <div>
          <h1 class="text-xl md:text-2xl gothic tracking-[0.2em] text-zinc-50">DEAD ARCHIVE // ADMIN</h1>
          <p class="text-[10px] tracking-[0.2em] text-zinc-500 mt-1 uppercase">управление каталогом и заказами</p>
        </div>
        <div class="flex flex-wrap gap-2 sm:gap-4 items-center">
          <a href="/admin/backup" class="px-3 py-2 border border-zinc-700 text-xs font-bold hover:border-zinc-400">Бэкап БД</a>
          <a href="/admin/export_orders" class="px-3 py-2 border border-zinc-700 text-xs font-bold hover:border-zinc-400">CSV заказов</a>
          <a href="/shop" target="_blank" class="px-4 py-2 border border-zinc-700 text-xs font-bold hover:border-zinc-400">Каталог ↗</a>
          <a href="/admin/logout" class="px-4 py-2 bg-red-950 border border-red-900 text-red-400 text-xs font-bold hover:bg-red-900 hover:text-white">Выйти</a>
        </div>
      </div>

      {% if request.args.get('msg') %}
      <div class="mb-6 px-4 py-3 border border-emerald-900 bg-emerald-950/30 text-emerald-400 text-xs font-bold">{{ request.args.get('msg') }}</div>
      {% endif %}

      <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-8">
        <div class="bg-[#09090b] border border-zinc-800 p-4">
          <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Товары</p>
          <p class="text-xl font-black text-zinc-100 mt-1">{{ products|length }}</p>
        </div>
        <div class="bg-[#09090b] border border-zinc-800 p-4">
          <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Заказы сегодня</p>
          <p class="text-xl font-black text-zinc-100 mt-1">{{ today_orders|length }}</p>
        </div>
        <div class="bg-[#09090b] border border-zinc-800 p-4">
          <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">В обработке</p>
          <p class="text-xl font-black text-amber-400 mt-1">{{ pending_count }}</p>
        </div>
        <div class="bg-[#09090b] border border-zinc-800 p-4">
          <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Выручка ~</p>
          <p class="text-xl font-black text-emerald-400 mt-1">{{ total_revenue }} €</p>
        </div>
        <div class="bg-[#09090b] border border-zinc-800 p-4 col-span-2 md:col-span-1">
          <p class="text-[10px] text-zinc-500 font-bold uppercase tracking-wider">Аккаунты</p>
          <p class="text-xl font-black text-zinc-100 mt-1">{{ customers|length }}</p>
        </div>
      </div>

      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 sm:gap-8">
        <!-- КОЛОНКА 1: ДОБАВИТЬ ТОВАР -->
        <div class="bg-[#09090b] border border-zinc-800 p-4 sm:p-6">
          <h2 class="text-sm font-black tracking-[0.15em] text-zinc-100 mb-6 uppercase border-b border-zinc-900 pb-3">+ Добавить товар</h2>
          <form action="/admin/add_product" method="POST" class="space-y-4">
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Название (RU)</label>
              <input type="text" name="name" required placeholder="ФУТБОЛКА VETEMENTS..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Название (EN, необязательно)</label>
              <input type="text" name="name_en" placeholder="VETEMENTS TEE..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none">
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Категория</label>
                <select name="category" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:outline-none">
                  <option value="tshirts">Футболки</option>
                  <option value="outerwear">Худи / Верх</option>
                  <option value="pants">Штаны / Джинсы</option>
                  <option value="shoes">Обувь</option>
                </select>
              </div>
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Цена (EUR)</label>
                <input type="number" name="price_eur" required placeholder="60" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:outline-none">
              </div>
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Цена (UAH)</label>
              <input type="number" name="price_uah" required placeholder="3050" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none">
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Ссылки на фото (через запятую)</label>
              <textarea name="images_str" rows="2" required placeholder="https://kappa.lol/..., https://..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none"></textarea>
            </div>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Цвета (через запятую)</label>
                <input type="text" name="colors_str" value="Черный" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:outline-none">
              </div>
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Размеры (через запятую)</label>
                <input type="text" name="sizes_str" value="S, M, L, XL" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:outline-none">
              </div>
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Описание (RU)</label>
              <textarea name="description" rows="2" placeholder="Описание товара..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none"></textarea>
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Описание (EN, необязательно)</label>
              <textarea name="description_en" rows="2" placeholder="English description..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100 focus:border-zinc-500 focus:outline-none"></textarea>
            </div>
            <button type="submit" class="w-full py-3.5 bg-zinc-100 text-black font-black text-xs tracking-[0.15em] hover:bg-white transition-all uppercase">ОПУБЛИКОВАТЬ ТОВАР</button>
          </form>

          <div class="mt-8 pt-6 border-t border-zinc-800 space-y-4">
            <h3 class="text-xs font-black tracking-[0.15em] text-zinc-200 uppercase">Аккаунты ({{ customers|length }})</h3>
            <div class="space-y-1.5 max-h-[200px] overflow-y-auto">
              {% for c in customers %}
              <div class="bg-zinc-950 border border-zinc-800 px-3 py-2 text-[11px]">
                <p class="font-bold text-zinc-200">{{ c.email }}</p>
                <p class="text-[10px] text-zinc-500">{{ c.telegram or '—' }} · {{ c.phone or '—' }}</p>
              </div>
              {% else %}
              <p class="text-[11px] text-zinc-600">Пока нет регистраций</p>
              {% endfor %}
            </div>

            <form action="/admin/change_password" method="POST" class="space-y-2 pt-3 border-t border-zinc-800">
              <p class="text-[10px] text-zinc-400 font-bold uppercase">Смена пароля админа</p>
              <input type="password" name="new_password" required minlength="4" placeholder="Новый пароль" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100">
              <button class="w-full py-2 border border-zinc-700 text-[10px] font-black uppercase text-zinc-300 hover:border-zinc-400">Обновить пароль</button>
            </form>

            <div class="pt-3 border-t border-zinc-800">
              <p class="text-[10px] text-zinc-400 font-bold uppercase mb-2">Журнал действий</p>
              <div class="space-y-1 max-h-[160px] overflow-y-auto text-[10px]">
                {% for lg in logs %}
                <p class="text-zinc-500"><span class="text-zinc-600">{{ lg.time_str }}</span> · <b class="text-zinc-300">{{ lg.action }}</b> {{ lg.detail }}</p>
                {% else %}
                <p class="text-zinc-600">Пусто</p>
                {% endfor %}
              </div>
            </div>
          </div>
        </div>

        <!-- КОЛОНКА 2: УПРАВЛЕНИЕ КАТАЛОГОМ -->
        <div class="bg-[#09090b] border border-zinc-800 p-4 sm:p-6">
          <div class="flex flex-wrap justify-between items-center gap-2 mb-3 border-b border-zinc-900 pb-3">
            <h2 class="text-sm font-black tracking-[0.15em] text-zinc-100 uppercase">Каталог ({{ products|length }})</h2>
            <div class="flex items-center gap-2">
              <form action="/admin/shuffle_products" method="POST" onsubmit="return confirm('Перемешать все товары в каталоге в случайном порядке?')">
                <button type="submit" class="px-2.5 py-1.5 border border-[#ff70c8]/50 bg-[#ff70c8]/10 text-[#ff70c8] hover:bg-[#ff70c8] hover:text-black text-[10px] font-black uppercase tracking-wider transition">🔀 Перемешать</button>
              </form>
              <span class="text-[10px] text-zinc-500 font-bold uppercase hidden sm:inline">↑↓ порядок</span>
            </div>
          </div>
          <div class="mb-4 px-3 py-2.5 bg-zinc-950 border border-zinc-800/90 flex flex-wrap items-center justify-between gap-2">
            <div>
              <p class="text-[10px] font-black uppercase tracking-wider text-zinc-200">Авто-перемешивание раз в 24ч · <span class="{{ 'text-emerald-400' if auto_shuffle_enabled else 'text-zinc-500' }}">{{ 'ВКЛ' if auto_shuffle_enabled else 'ВЫКЛ' }}</span></p>
              <p class="text-[9px] text-zinc-500 mt-0.5">Последний раз: {{ auto_shuffle_last_str }}</p>
            </div>
            <form action="/admin/toggle_auto_shuffle" method="POST">
              <button type="submit" class="px-2.5 py-1.5 border text-[10px] font-black uppercase tracking-wider transition {{ 'border-red-900/70 text-red-400 hover:bg-red-950/60' if auto_shuffle_enabled else 'border-emerald-800 text-emerald-400 hover:bg-emerald-950/60' }}">{{ 'Выключить' if auto_shuffle_enabled else 'Включить' }}</button>
            </form>
          </div>
          <div class="space-y-3 max-h-[650px] overflow-y-auto pr-1 sm:pr-2">
            {% for p in products %}
            <div class="bg-zinc-950 border border-zinc-800 p-3 flex justify-between items-center gap-2 sm:gap-3">
              <div class="flex items-center gap-2 sm:gap-3 min-w-0">
                <img src="{{ p.images_str.split(',')[0].strip() }}" class="w-11 h-14 sm:w-12 sm:h-16 object-cover object-top border border-zinc-800 shrink-0 {{ 'opacity-40 grayscale' if not p.in_stock }}">
                <div class="min-w-0">
                  <div class="flex items-center gap-1.5 flex-wrap">
                    <p class="text-xs font-bold text-zinc-100 truncate">{{ p.name }}</p>
                    {% if not p.in_stock %}<span class="text-[9px] font-black bg-red-950 text-red-400 border border-red-900 px-1.5">SOLD OUT</span>{% endif %}
                  </div>
                  <p class="text-[10px] text-zinc-400 mt-0.5">{{ p.price_eur }} EUR | {{ p.price_uah }} UAH</p>
                  <p class="text-[9px] text-zinc-600 uppercase mt-0.5">{{ p.category }} · #{{ loop.index }}</p>
                </div>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <form action="/admin/move_product/{{ p.id }}/up" method="POST" class="inline">
                  <button type="submit" title="Вверх" class="px-2 py-1.5 border border-zinc-800 text-zinc-400 hover:border-zinc-500 hover:text-white text-[10px] font-bold">↑</button>
                </form>
                <form action="/admin/move_product/{{ p.id }}/down" method="POST" class="inline">
                  <button type="submit" title="Вниз" class="px-2 py-1.5 border border-zinc-800 text-zinc-400 hover:border-zinc-500 hover:text-white text-[10px] font-bold">↓</button>
                </form>
                <a href="/admin/edit_product/{{ p.id }}" title="Редактировать" class="px-2 py-1.5 border border-zinc-700 text-zinc-200 hover:border-zinc-400 text-[10px] font-bold uppercase">ред</a>
                <form action="/admin/toggle_stock/{{ p.id }}" method="POST" class="inline">
                  <button type="submit" title="Наличие" class="px-2 py-1.5 border {{ 'border-emerald-900 text-emerald-400' if p.in_stock else 'border-amber-900 text-amber-400' }} text-[10px] font-bold uppercase">{{ 'есть' if p.in_stock else 'нет' }}</button>
                </form>
                <a href="/admin/delete_product/{{ p.id }}" onclick="return confirm('Удалить этот товар?')" class="px-2.5 py-1.5 bg-red-950/50 border border-red-900 text-red-400 text-[10px] font-bold hover:bg-red-900 hover:text-white uppercase">✕</a>
              </div>
            </div>
            {% endfor %}
          </div>
        </div>

        <!-- КОЛОНКА 3: ВХОДЯЩИЕ ЗАКАЗЫ -->
        <div class="bg-[#09090b] border border-zinc-800 p-4 sm:p-6">
          <div class="flex flex-wrap justify-between items-center gap-2 mb-4 border-b border-zinc-900 pb-3">
            <h2 class="text-sm font-black tracking-[0.15em] text-zinc-100 uppercase">Заказы (<span id="orders-shown-count">{{ orders|length }}</span>/{{ orders|length }})</h2>
            <select id="admin-status-filter" onchange="filterAdminOrders()" class="bg-zinc-950 border border-zinc-700 px-2.5 py-1 text-[10px] font-bold text-zinc-200 uppercase">
              <option value="all">Все статусы</option>
              <option value="В обработке">В обработке</option>
              <option value="Принят">Принят</option>
              <option value="Отправлен">Отправлен</option>
              <option value="Доставлен">Доставлен</option>
              <option value="Отменен">Отменён</option>
            </select>
          </div>
          <div class="space-y-4 max-h-[650px] overflow-y-auto pr-2" id="admin-orders-list">
            {% if orders|length == 0 %}
              <p class="text-xs text-zinc-600 text-center py-8">Пока нет заказов</p>
            {% endif %}
            {% for ord in orders %}
            <div class="admin-order-card bg-zinc-950 border border-zinc-800 p-4 space-y-2" data-status="{{ ord.status }}">
              <div class="flex justify-between items-center border-b border-zinc-900 pb-2 gap-2">
                <span class="text-xs font-black text-red-500">{{ ord.id }}</span>
                <span class="text-[10px] text-zinc-500">{{ ord.time }}</span>
              </div>

              <!-- УПРАВЛЕНИЕ СТАТУСОМ ЗАКАЗА -->
              <form action="/admin/update_order_status/{{ ord.id }}" method="POST" class="flex items-center gap-2 py-2 border-b border-zinc-900">
                <select name="status" class="flex-1 bg-zinc-900 border border-zinc-700 px-2.5 py-1.5 text-[11px] font-bold text-zinc-200 focus:outline-none">
                  <option value="В обработке" {% if ord.status == 'В обработке' %}selected{% endif %}>В обработке</option>
                  <option value="Принят" {% if ord.status == 'Принят' %}selected{% endif %}>Принят</option>
                  <option value="Отправлен" {% if ord.status == 'Отправлен' %}selected{% endif %}>Отправлен</option>
                  <option value="Доставлен" {% if ord.status == 'Доставлен' %}selected{% endif %}>Доставлен</option>
                  <option value="Отменен" {% if ord.status == 'Отменен' %}selected{% endif %}>Отменён</option>
                </select>
                <button type="submit" class="px-3 py-1.5 bg-zinc-100 text-black text-[10px] font-black hover:bg-white uppercase">ОК</button>
              </form>

              <div class="text-xs space-y-1 text-zinc-300 pt-1">
                <p><span class="text-zinc-500">TG:</span> <span class="font-bold text-zinc-100">{{ ord.contacts.telegram or '—' }}</span></p>
                <p><span class="text-zinc-500">Тел:</span> <span class="font-bold text-zinc-100">{{ ord.contacts.phone or '—' }}</span></p>
                {% if ord.contacts.promo %}<p><span class="text-zinc-500">Промо:</span> <span class="font-bold text-emerald-400">{{ ord.contacts.promo }} ({{ ord.contacts.total_eur }} EUR)</span></p>{% endif %}
              </div>
              <div class="border-t border-zinc-900 pt-2 mt-2 space-y-1">
                {% for item in ord['items'] %}
                <p class="text-[11px] text-zinc-400">• {{ item.name }} [{{ item.selectedSize }}] - {{ item.price_eur }}€</p>
                {% endfor %}
              </div>

              <form action="/admin/update_order_note/{{ ord.id }}" method="POST" class="flex items-center gap-2 pt-2 border-t border-zinc-900">
                <input type="text" name="note" value="{{ ord.note }}" placeholder="заметка / трек-номер..." class="flex-1 bg-zinc-900 border border-zinc-800 px-2.5 py-1.5 text-[11px] text-zinc-200 focus:outline-none">
                <button type="submit" class="px-2.5 py-1.5 border border-zinc-700 text-[10px] font-bold text-zinc-300 hover:border-zinc-400 uppercase">save</button>
              </form>
            </div>
            {% endfor %}
          </div>
        </div>
      </div>

      <div class="mt-8 bg-[#09090b] border border-zinc-800 p-4 sm:p-6">
        <div class="flex flex-wrap justify-between items-center gap-2 mb-4 border-b border-zinc-900 pb-3">
          <div>
            <h2 class="text-sm font-black tracking-[0.15em] text-zinc-100 uppercase">Промокоды ({{ promos|length }})</h2>
            <p class="text-[10px] text-zinc-500 mt-0.5">Скидка в % при оформлении заказа</p>
          </div>
        </div>
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form action="/admin/promo/add" method="POST" class="space-y-3 border border-zinc-800/80 bg-zinc-950/60 p-4">
            <p class="text-[11px] font-black text-zinc-200 uppercase tracking-wider">+ Новый промокод</p>
            <div class="grid grid-cols-3 gap-2">
              <div>
                <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Код</label>
                <input type="text" name="code" required placeholder="ARCHIVE15" class="w-full bg-black border border-zinc-800 px-2.5 py-2 text-xs text-zinc-100 uppercase font-bold">
              </div>
              <div>
                <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Скидка %</label>
                <input type="number" name="discount_percent" min="1" max="90" value="10" required class="w-full bg-black border border-zinc-800 px-2.5 py-2 text-xs text-zinc-100 font-bold">
              </div>
              <div>
                <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Лимит (0=∞)</label>
                <input type="number" name="max_uses" min="0" value="0" class="w-full bg-black border border-zinc-800 px-2.5 py-2 text-xs text-zinc-100 font-bold">
              </div>
            </div>
            <label class="flex items-center gap-2 text-[11px] text-zinc-400 cursor-pointer">
              <input type="checkbox" name="first_order_only" value="1" class="accent-[#ff00a8]">
              только на первый заказ
            </label>
            <button type="submit" class="w-full py-2.5 bg-zinc-100 text-black text-xs font-black uppercase tracking-wider hover:bg-white">создать промокод</button>
          </form>

          <div class="space-y-2 max-h-[320px] overflow-y-auto">
            {% for pr in promos %}
            <div class="border border-zinc-800 bg-zinc-950 p-3 flex flex-wrap justify-between items-center gap-2">
              <div>
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-xs font-black text-zinc-100 tracking-wider">{{ pr.code }}</span>
                  <span class="text-[10px] font-black px-1.5 py-0.5 bg-emerald-950/60 border border-emerald-900/60 text-emerald-400">−{{ pr.discount_percent }}%</span>
                  <span class="text-[9px] font-bold uppercase px-1.5 py-0.5 border {{ 'border-emerald-900 text-emerald-400' if pr.active else 'border-zinc-700 text-zinc-500' }}">{{ 'активен' if pr.active else 'выкл' }}</span>
                  {% if pr.first_order_only %}<span class="text-[9px] text-zinc-500 uppercase">1-й заказ</span>{% endif %}
                </div>
                <p class="text-[10px] text-zinc-500 mt-1">использован: {{ pr.uses_count or 0 }}{% if pr.max_uses %} / {{ pr.max_uses }}{% endif %}</p>
              </div>
              <div class="flex items-center gap-1.5">
                <form action="/admin/promo/toggle/{{ pr.id }}" method="POST" class="inline">
                  <button type="submit" class="px-2.5 py-1 border border-zinc-700 text-[10px] font-bold text-zinc-300 hover:border-zinc-400 uppercase">{{ 'выкл' if pr.active else 'вкл' }}</button>
                </form>
                <a href="/admin/promo/delete/{{ pr.id }}" onclick="return confirm('Удалить промокод {{ pr.code }}?')" class="px-2.5 py-1 bg-red-950/50 border border-red-900 text-red-400 text-[10px] font-bold hover:bg-red-900 hover:text-white uppercase">✕</a>
              </div>
            </div>
            {% else %}
            <p class="text-xs text-zinc-600 py-8 text-center">Промокодов пока нет</p>
            {% endfor %}
          </div>
        </div>
      </div>

      <div class="mt-8 bg-[#09090b] border border-zinc-800 p-4 sm:p-6">
        <div class="flex flex-wrap justify-between items-center gap-2 mb-4 border-b border-zinc-900 pb-3">
          <div>
            <h2 class="text-sm font-black tracking-[0.15em] text-zinc-100 uppercase">Рассылка уведомлений</h2>
            <p class="text-[10px] text-zinc-500 mt-0.5">Появится в колокольчике на сайте и в профиле</p>
          </div>
        </div>
        <form action="/admin/notify/broadcast" method="POST" class="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
          <div>
            <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Кому</label>
            <select name="target" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100 font-bold">
              <option value="all">Всем аккаунтам ({{ customers|length }})</option>
              {% for c in customers %}
              <option value="{{ c.id }}">{{ c.email }}</option>
              {% endfor %}
            </select>
          </div>
          <div>
            <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Заголовок</label>
            <input type="text" name="title" required placeholder="Новый дроп уже в каталоге" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100 font-bold">
          </div>
          <div>
            <label class="block text-[9px] text-zinc-500 font-bold uppercase mb-1">Текст</label>
            <input type="text" name="body" placeholder="Промокод DROP10 на −10% сегодня" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100">
          </div>
          <button type="submit" class="py-2.5 px-4 bg-zinc-100 text-black text-xs font-black uppercase tracking-wider hover:bg-white">отправить</button>
        </form>
      </div>

      <!-- LOOKBOOK BUILDER -->
      <div class="mt-8 bg-[#09090b] border border-[#ff70c8]/35 p-4 sm:p-6">
        <div class="flex flex-wrap justify-between items-center gap-2 mb-4 border-b border-zinc-900 pb-3">
          <div>
            <h2 class="text-sm font-black tracking-[0.15em] text-[#ff70c8] uppercase">Lookbook // Образы ({{ looks|length }})</h2>
            <p class="text-[10px] text-zinc-500 mt-1">Если образов в списке 0 — на /lookbook показываются авто-подборки из каталога. Как только создашь хотя бы один образ, на сайте будут отображаться твои образы.</p>
          </div>
          <a href="/lookbook" target="_blank" class="px-3 py-1.5 border border-[#ff70c8]/50 text-[#ff70c8] text-[10px] font-black uppercase hover:bg-[#ff70c8] hover:text-black">Открыть /lookbook ↗</a>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <form action="/admin/lookbook/add" method="POST" class="space-y-3 border border-zinc-800/80 bg-zinc-950/60 p-4">
            <p class="text-[11px] font-black text-zinc-200 uppercase tracking-wider">+ Новый образ</p>
            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Код / тег</label>
                <input type="text" name="code" value="0{{ looks|length + 1 }} / DEAD FILE" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100">
              </div>
              <div>
                <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Название образа</label>
                <input type="text" name="title" placeholder="AFTER HOURS" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100">
              </div>
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Описание</label>
              <input type="text" name="description" placeholder="Коротко о настроении и сочетании вещей..." class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2 text-xs text-zinc-100">
            </div>
            <div>
              <label class="block text-[10px] text-zinc-400 font-bold mb-1.5 uppercase">Выбери вещи из каталога (2–4 штуки)</label>
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-56 overflow-y-auto p-2 bg-black border border-zinc-800">
                {% for p in products %}
                <label class="flex items-center gap-2 p-1.5 hover:bg-zinc-900 cursor-pointer text-[11px]">
                  <input type="checkbox" name="product_ids" value="{{ p.id }}" class="accent-[#ff00a8]">
                  <img src="{{ p.images_str.split(',')[0].strip() }}" class="w-7 h-9 object-cover border border-zinc-800 shrink-0">
                  <span class="truncate text-zinc-200">{{ p.name }} <span class="text-zinc-500">({{ p.price_eur }}€)</span></span>
                </label>
                {% endfor %}
              </div>
            </div>
            <button type="submit" class="w-full py-3 bg-[#ff70c8] text-black font-black text-xs tracking-[0.15em] hover:bg-white transition uppercase">Добавить образ в Lookbook</button>
          </form>

          <div class="space-y-3 max-h-[420px] overflow-y-auto pr-1">
            {% if looks|length == 0 %}
              <p class="text-xs text-zinc-500 py-8 text-center border border-dashed border-zinc-800">Пока нет ручных образов — сейчас /lookbook работает в авто-режиме.</p>
            {% endif %}
            {% for lk in looks %}
            <div class="bg-zinc-950 border border-zinc-800 p-3 flex justify-between items-start gap-3">
              <div class="min-w-0">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="text-[10px] font-black text-[#ff70c8]">{{ lk.code }}</span>
                  <span class="text-xs font-black text-zinc-100 uppercase">{{ lk.title }}</span>
                  {% if not lk.active %}<span class="text-[9px] px-1.5 py-0.5 bg-zinc-900 text-zinc-500 border border-zinc-800">скрыт</span>{% endif %}
                </div>
                {% if lk.description %}<p class="text-[11px] text-zinc-400 mt-1">{{ lk.description }}</p>{% endif %}
                <div class="flex flex-wrap gap-1.5 mt-2">
                  {% for pid in lk.product_ids() %}
                    {% set item = products_by_id.get(pid) %}
                    {% if item %}
                    <span class="inline-flex items-center gap-1.5 px-2 py-0.5 bg-black border border-zinc-800 text-[10px] text-zinc-300">
                      <img src="{{ item.images_str.split(',')[0].strip() }}" class="w-4 h-5 object-cover">
                      {{ item.name }}
                    </span>
                    {% endif %}
                  {% endfor %}
                </div>
              </div>
              <div class="flex items-center gap-1 shrink-0">
                <form action="/admin/lookbook/toggle/{{ lk.id }}" method="POST" class="inline">
                  <button type="submit" class="px-2 py-1 border border-zinc-700 text-[10px] font-bold text-zinc-300 hover:border-zinc-400 uppercase">{{ 'скрыть' if lk.active else 'показать' }}</button>
                </form>
                <a href="/admin/lookbook/delete/{{ lk.id }}" onclick="return confirm('Удалить этот образ?')" class="px-2 py-1 bg-red-950/60 border border-red-900 text-red-400 text-[10px] font-bold hover:bg-red-900 hover:text-white">✕</a>
              </div>
            </div>
            {% endfor %}
          </div>
        </div>
      </div>

      <script>
        function filterAdminOrders() {
          const val = document.getElementById('admin-status-filter').value;
          const cards = document.querySelectorAll('.admin-order-card');
          let shown = 0;
          cards.forEach(c => {
            const match = val === 'all' || c.dataset.status === val;
            c.style.display = match ? '' : 'none';
            if (match) shown++;
          });
          document.getElementById('orders-shown-count').textContent = shown;
        }
      </script>
    </body>
    </html>
    ''', products=products, orders=orders, customers=customers, logs=logs, promos=promos, today_orders=today_orders, total_revenue=total_revenue, pending_count=pending_count, looks=looks, products_by_id=products_by_id, auto_shuffle_enabled=auto_shuffle_enabled, auto_shuffle_last_str=auto_shuffle_last_str)

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    max_order = db.session.query(db.func.max(Product.sort_order)).scalar()
    next_order = (max_order + 1) if max_order is not None else 0

    new_prod = Product(
        name=request.form.get('name'),
        name_en=(request.form.get('name_en') or '').strip() or None,
        category=request.form.get('category'),
        price_eur=int(request.form.get('price_eur')),
        price_uah=int(request.form.get('price_uah')),
        images_str=request.form.get('images_str'),
        colors_str=request.form.get('colors_str'),
        description=request.form.get('description'),
        description_en=(request.form.get('description_en') or '').strip() or None,
        sizes_str=request.form.get('sizes_str'),
        sort_order=next_order,
        in_stock=True,
    )
    db.session.add(new_prod)
    db.session.commit()
    admin_log("add_product", f"{new_prod.name} ({new_prod.price_eur}€)")
    return redirect(url_for('admin_dashboard', msg='Товар добавлен'))

@app.route('/admin/edit_product/<int:prod_id>', methods=['GET', 'POST'])
def edit_product(prod_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    prod = Product.query.get_or_404(prod_id)
    if request.method == 'POST':
        prod.name = request.form.get('name') or prod.name
        prod.name_en = (request.form.get('name_en') or '').strip() or None
        prod.category = request.form.get('category') or prod.category
        prod.price_eur = int(request.form.get('price_eur') or prod.price_eur)
        prod.price_uah = int(request.form.get('price_uah') or prod.price_uah)
        prod.images_str = request.form.get('images_str') or prod.images_str
        prod.colors_str = request.form.get('colors_str') or prod.colors_str
        prod.sizes_str = request.form.get('sizes_str') or prod.sizes_str
        prod.description = request.form.get('description') or ''
        prod.description_en = (request.form.get('description_en') or '').strip() or None
        prod.in_stock = bool(request.form.get('in_stock'))
        db.session.commit()
        admin_log("edit_product", f"#{prod.id} {prod.name}")
        return redirect(url_for('admin_dashboard', msg='Товар обновлён'))
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
      <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0, viewport-fit=cover">
      <title>EDIT // {{ prod.name }}</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <style>body{font-family:Inter,sans-serif;background:#020202;color:#e4e4e7}</style>
    </head>
    <body class="min-h-screen p-4 md:p-8 max-w-xl mx-auto">
      <div class="flex justify-between items-center mb-6">
        <a href="/admin/dashboard" class="text-xs text-zinc-400 hover:text-white uppercase font-bold">← назад</a>
        <span class="text-xs text-zinc-500">ID #{{ prod.id }}</span>
      </div>
      <form method="POST" class="bg-[#09090b] border border-zinc-800 p-6 space-y-4">
        <h1 class="text-sm font-black uppercase tracking-widest text-zinc-100">Редактировать товар</h1>
        <div>
          <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Название (RU)</label>
          <input type="text" name="name" value="{{ prod.name }}" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
        </div>
        <div>
          <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Название (EN)</label>
          <input type="text" name="name_en" value="{{ prod.name_en or '' }}" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
        </div>
        <div class="grid grid-cols-3 gap-3">
          <div>
            <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Категория</label>
            <select name="category" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
              <option value="tshirts" {% if prod.category=='tshirts' %}selected{% endif %}>Футболки</option>
              <option value="outerwear" {% if prod.category=='outerwear' %}selected{% endif %}>Худи / Верх</option>
              <option value="pants" {% if prod.category=='pants' %}selected{% endif %}>Штаны</option>
              <option value="shoes" {% if prod.category=='shoes' %}selected{% endif %}>Обувь</option>
            </select>
          </div>
          <div>
            <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">EUR</label>
            <input type="number" name="price_eur" value="{{ prod.price_eur }}" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
          </div>
          <div>
            <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">UAH</label>
            <input type="number" name="price_uah" value="{{ prod.price_uah }}" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
          </div>
        </div>
        <div>
          <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Фото (через запятую)</label>
          <textarea name="images_str" rows="2" required class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">{{ prod.images_str }}</textarea>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Цвета</label>
            <input type="text" name="colors_str" value="{{ prod.colors_str }}" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
          </div>
          <div>
            <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Размеры</label>
            <input type="text" name="sizes_str" value="{{ prod.sizes_str }}" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">
          </div>
        </div>
        <div>
          <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Описание (RU)</label>
          <textarea name="description" rows="2" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">{{ prod.description or '' }}</textarea>
        </div>
        <div>
          <label class="block text-[10px] text-zinc-400 font-bold mb-1 uppercase">Описание (EN)</label>
          <textarea name="description_en" rows="2" class="w-full bg-zinc-950 border border-zinc-800 px-3 py-2.5 text-xs text-zinc-100">{{ prod.description_en or '' }}</textarea>
        </div>
        <label class="flex items-center gap-2 text-xs text-zinc-300 font-bold">
          <input type="checkbox" name="in_stock" value="1" {% if prod.in_stock %}checked{% endif %}>
          В наличии (снять = SOLD OUT)
        </label>
        <button class="w-full py-3.5 bg-zinc-100 text-black text-xs font-black uppercase tracking-widest">Сохранить</button>
      </form>
    </body>
    </html>
    ''', prod=prod)

@app.route('/admin/toggle_stock/<int:prod_id>', methods=['POST'])
def toggle_stock(prod_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    prod = Product.query.get_or_404(prod_id)
    prod.in_stock = not bool(prod.in_stock)
    db.session.commit()
    admin_log("toggle_stock", f"#{prod.id} {prod.name} -> {prod.in_stock}")
    if not prod.in_stock:
        try:
            notify_sold_out(prod.name, prod.id)
        except Exception as e:
            print("sold out tg:", e)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/update_order_note/<order_id>', methods=['POST'])
def update_order_note(order_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    ord_db = Order.query.get(order_id)
    if ord_db:
        ord_db.note = (request.form.get('note') or '').strip()
        db.session.commit()
        admin_log("order_note", f"{order_id}: {ord_db.note}")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export_orders')
def export_orders():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    import csv, io
    from flask import Response
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["order_id", "time", "status", "telegram", "phone", "promo", "total_eur", "items", "note"])
    for o in Order.query.order_by(Order.time_str.desc()).all():
        d = o.to_dict()
        items_s = "; ".join(f"{i.get('name')} [{i.get('selectedSize')}] ({i.get('price_eur')}€)" for i in d["items"])
        c = d["contacts"] or {}
        writer.writerow([d["id"], d["time"], d["status"], c.get("telegram", ""), c.get("phone", ""), c.get("promo", ""), c.get("total_eur", ""), items_s, d["note"]])
    return Response(out.getvalue().encode("utf-8-sig"), mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=dead_archive_orders.csv"})

@app.route('/admin/backup')
def admin_backup():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    from flask import send_file
    db_path = os.path.join(basedir, 'archive.db')
    admin_log("backup", "archive.db downloaded")
    return send_file(db_path, as_attachment=True, download_name=f"archive_backup_{time.strftime('%Y%m%d_%H%M')}.db")

@app.route('/admin/change_password', methods=['POST'])
def admin_change_password():
    global ADMIN_PASSWORD
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    new_pw = (request.form.get('new_password') or '').strip()
    if len(new_pw) < 4:
        return redirect(url_for('admin_dashboard', msg='Пароль минимум 4 символа'))
    _save_admin_password(new_pw)
    ADMIN_PASSWORD = new_pw
    admin_log("change_password", "пароль обновлён")
    return redirect(url_for('admin_dashboard', msg='Пароль изменён'))

@app.route('/admin/promo/add', methods=['POST'])
def admin_promo_add():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    code = (request.form.get('code') or '').strip().upper()
    code = _re.sub(r'[^A-Z0-9_-]', '', code)
    try:
        discount = max(1, min(90, int(request.form.get('discount_percent') or 5)))
    except Exception:
        discount = 5
    try:
        max_uses = max(0, int(request.form.get('max_uses') or 0))
    except Exception:
        max_uses = 0
    first_only = bool(request.form.get('first_order_only'))
    if not code:
        return redirect(url_for('admin_dashboard', msg='Некорректный код промокода'))
    if PromoCode.query.filter_by(code=code).first():
        return redirect(url_for('admin_dashboard', msg=f'Промокод {code} уже существует'))
    db.session.add(PromoCode(
        code=code,
        discount_percent=discount,
        active=True,
        first_order_only=first_only,
        max_uses=max_uses,
        created_str=time.strftime('%Y-%m-%d %H:%M:%S'),
    ))
    db.session.commit()
    admin_log("promo_add", f"{code} −{discount}%")
    return redirect(url_for('admin_dashboard', msg=f'Промокод {code} создан'))

@app.route('/admin/promo/toggle/<int:promo_id>', methods=['POST'])
def admin_promo_toggle(promo_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    pr = PromoCode.query.get_or_404(promo_id)
    pr.active = not bool(pr.active)
    db.session.commit()
    admin_log("promo_toggle", f"{pr.code} -> {pr.active}")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/promo/delete/<int:promo_id>')
def admin_promo_delete(promo_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    pr = PromoCode.query.get_or_404(promo_id)
    code = pr.code
    db.session.delete(pr)
    db.session.commit()
    admin_log("promo_delete", code)
    return redirect(url_for('admin_dashboard', msg=f'Промокод {code} удалён'))

@app.route('/admin/notify/broadcast', methods=['POST'])
def admin_notify_broadcast():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    target = (request.form.get('target') or 'all').strip()
    title = (request.form.get('title') or '').strip()
    body = (request.form.get('body') or '').strip()
    if not title:
        return redirect(url_for('admin_dashboard', msg='Заголовок уведомления пустой'))
    if target == 'all':
        users = Customer.query.all()
        for u in users:
            create_notification(u.id, title, body, 'promo')
        admin_log("notify_broadcast", f"всем ({len(users)}): {title}")
        return redirect(url_for('admin_dashboard', msg=f'Отправлено {len(users)} аккаунтам'))
    try:
        uid = int(target)
    except Exception:
        return redirect(url_for('admin_dashboard', msg='Некорректный получатель'))
    u = Customer.query.get(uid)
    if not u:
        return redirect(url_for('admin_dashboard', msg='Аккаунт не найден'))
    create_notification(u.id, title, body, 'promo')
    admin_log("notify_user", f"#{u.id} {u.email}: {title}")
    return redirect(url_for('admin_dashboard', msg=f'Отправлено {u.email}'))


@app.route('/admin/lookbook/add', methods=['POST'])
def admin_lookbook_add():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    code = (request.form.get('code') or 'ARCHIVE FILE').strip()[:100]
    title = (request.form.get('title') or '').strip()[:200]
    desc = (request.form.get('description') or '').strip()
    raw_ids = request.form.getlist('product_ids')
    ids = []
    for x in raw_ids:
        try:
            v = int(x)
            if v not in ids:
                ids.append(v)
        except Exception:
            pass
    if not title or not ids:
        return redirect(url_for('admin_dashboard', msg='Нужно название и хотя бы 1 вещь в образе'))
    max_ord = db.session.query(db.func.max(LookbookLook.sort_order)).scalar()
    next_ord = (max_ord + 1) if max_ord is not None else 0
    lk = LookbookLook(
        code=code,
        title=title,
        description=desc,
        product_ids_json=_json.dumps(ids),
        sort_order=next_ord,
        active=True,
    )
    db.session.add(lk)
    db.session.commit()
    admin_log("lookbook_add", f"{title} ({len(ids)} items)")
    return redirect(url_for('admin_dashboard', msg='Образ добавлен в lookbook'))

@app.route('/admin/lookbook/toggle/<int:look_id>', methods=['POST'])
def admin_lookbook_toggle(look_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    lk = LookbookLook.query.get_or_404(look_id)
    lk.active = not bool(lk.active)
    db.session.commit()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/lookbook/delete/<int:look_id>')
def admin_lookbook_delete(look_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    lk = LookbookLook.query.get_or_404(look_id)
    db.session.delete(lk)
    db.session.commit()
    admin_log("lookbook_delete", f"#{look_id}")
    return redirect(url_for('admin_dashboard', msg='Образ удалён'))

@app.route('/admin/move_product/<int:prod_id>/<direction>', methods=['POST'])
def move_product(prod_id, direction):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))

    products = Product.query.order_by(Product.sort_order.asc(), Product.id.asc()).all()
    for i, p in enumerate(products):
        p.sort_order = i

    idx = next((i for i, p in enumerate(products) if p.id == prod_id), None)
    if idx is not None:
        if direction == 'up' and idx > 0:
            products[idx].sort_order, products[idx - 1].sort_order = products[idx - 1].sort_order, products[idx].sort_order
        elif direction == 'down' and idx < len(products) - 1:
            products[idx].sort_order, products[idx + 1].sort_order = products[idx + 1].sort_order, products[idx].sort_order
        db.session.commit()

    return redirect(url_for('admin_dashboard'))

@app.route('/admin/shuffle_products', methods=['POST'])
def shuffle_products():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    n = _do_shuffle_products()
    enabled, _ = _load_auto_shuffle()
    if enabled:
        _save_auto_shuffle(True, time.time())
    admin_log("shuffle_products", f"перемешано {n} товаров вручную")
    return redirect(url_for('admin_dashboard', msg=f'Перемешано {n} товаров'))

@app.route('/admin/toggle_auto_shuffle', methods=['POST'])
def toggle_auto_shuffle():
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    enabled, last_ts = _load_auto_shuffle()
    new_state = not enabled
    if new_state and not last_ts:
        last_ts = time.time()
    _save_auto_shuffle(new_state, last_ts)
    admin_log("auto_shuffle_toggle", "включено" if new_state else "выключено")
    return redirect(url_for('admin_dashboard', msg='Авто-перемешивание ' + ('включено (каждые 24ч)' if new_state else 'выключено')))

@app.route('/admin/delete_product/<int:prod_id>')
def delete_product(prod_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    prod = Product.query.get(prod_id)
    if prod:
        name = prod.name
        db.session.delete(prod)
        db.session.commit()
        admin_log("delete_product", f"#{prod_id} {name}")
    return redirect(url_for('admin_dashboard', msg='Товар удалён'))

@app.route('/admin/update_order_status/<order_id>', methods=['POST'])
def update_order_status(order_id):
    if not session.get('is_admin'):
        return redirect(url_for('admin_login'))
    new_status = request.form.get('status')
    ord_db = Order.query.get(order_id)
    if ord_db and new_status:
        ord_db.status = new_status
        db.session.commit()
        admin_log("order_status", f"{order_id} -> {new_status}")
        try:
            if ord_db.user_id:
                create_notification(
                    ord_db.user_id,
                    f"Заказ {ord_db.id}: {new_status}",
                    "Статус твоего заказа обновлён. Смотри детали в профиле.",
                    "order",
                )
        except Exception as ne:
            print("notif status:", ne)
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect(url_for('home'))

if __name__ == '__main__':
    app.run(debug=True)
