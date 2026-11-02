from flask_sqlalchemy import SQLAlchemy

# Инициализируем базу данных
db = SQLAlchemy()

class Product(db.Model):
    __tablename__ = 'products'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    category = db.Column(db.String(50), nullable=False)
    price_eur = db.Column(db.Integer, nullable=False)
    price_uah = db.Column(db.Integer, nullable=False)
    images = db.Column(db.JSON, nullable=False)       # Будет хранить список ссылок
    description = db.Column(db.Text, nullable=True)
    sizes = db.Column(db.JSON, nullable=False)        # Будет хранить список размеров ["S", "M", "L"]

class Order(db.Model):
    __tablename__ = 'orders'
    
    id = db.Column(db.Integer, primary_key=True)
    status = db.Column(db.String(50), default="В обработке")
    contacts = db.Column(db.JSON, nullable=False)     # {"telegram": "...", "phone": "..."}
    items = db.Column(db.JSON, nullable=False)        # Список заказанных товаров
    total = db.Column(db.Integer, nullable=False)