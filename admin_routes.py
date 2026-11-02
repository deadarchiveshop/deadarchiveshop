import os
import json
import csv
import io
from flask import Blueprint, render_template_string, request, redirect, session, current_app, Response

admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Пароль для входа в админ-панель
ADMIN_PASSWORD = "0879385"

# Жестко привязываем пути к папке проекта на сервере, чтобы файлы не терялись
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PRODUCTS_FILE = os.path.join(BASE_DIR, 'products.json')
ORDERS_FILE = os.path.join(BASE_DIR, 'orders.json')

def load_json_data(filepath, default_value):
    """Вспомогательная функция для загрузки данных из JSON"""
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default_value
    return default_value

def save_json_data(filepath, data):
    """Вспомогательная функция для сохранения данных в JSON"""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Ошибка сохранения {filepath}: {e}")

def get_orders():
    """Получение заказов с гарантированной загрузкой из файла"""
    if not hasattr(current_app, 'orders_data') or not current_app.orders_data:
        current_app.orders_data = load_json_data(ORDERS_FILE, [])
    return current_app.orders_data

def save_orders(orders):
    """Сохранение заказов в память и в файл навсегда"""
    current_app.orders_data = orders
    save_json_data(ORDERS_FILE, orders)

def get_products():
    """Получение товаров с гарантированной загрузкой из файла"""
    if not hasattr(current_app, 'products_data') or not current_app.products_data:
        current_app.products_data = load_json_data(PRODUCTS_FILE, [])
    return current_app.products_data

def save_products(products):
    """Сохранение товаров в память и в файл навсегда"""
    current_app.products_data = products
    save_json_data(PRODUCTS_FILE, products)


ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>DEAD ARCHIVE // ADMIN PANEL</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@700;900&family=Inter:wght@400;500;700;900&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Inter', sans-serif; background: #020202; color: #e4e4e7; }
    .gothic { font-family: 'Cinzel', serif; font-weight: 900; }
    ::-webkit-scrollbar { width: 6px; }
    ::-webkit-scrollbar-track { background: #020202; }
    ::-webkit-scrollbar-thumb { background: #18181b; border: 1px solid #27272a; }
  </style>
</head>
<body class="p-6 md:p-12 min-h-screen">
  <div class="max-w-7xl mx-auto">
    <!-- Header -->
    <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-zinc-800 pb-6 mb-8 gap-4">
      <div>
        <h1 class="text-2xl font-black gothic tracking-widest text-zinc-100">SYSTEM CONTROL // ADMIN</h1>
        <p class="text-xs text-zinc-500 font-bold mt-1 tracking-wider uppercase">Панель управления заказами и каталогом</p>
      </div>
      <div class="flex items-center gap-4 flex-wrap">
        <a href="/admin/export_orders" class="text-xs font-bold text-zinc-300 hover:text-white border border-zinc-700 bg-zinc-900 px-4 py-2 transition-all">ЭКСПОРТ CSV</a>
        <a href="/" class="text-xs font-bold text-zinc-400 hover:text-white border border-zinc-800 px-4 py-2 transition-all">НА САЙТ</a>
        <a href="/admin/logout" class="text-xs font-bold text-red-500 hover:text-red-400 border border-red-950 bg-red-950/20 px-4 py-2 transition-all">ВЫХОД</a>
      </div>
    </div>

    <!-- Orders Section -->
    <div class="mb-16">
      <h2 class="text-lg font-black tracking-wider text-zinc-200 mb-6 uppercase border-l-2 border-red-800 pl-3">Входящие Заказы ({{ orders|length }})</h2>

      {% if not orders %}
        <div class="bg-zinc-950 border border-zinc-900 p-8 text-center text-xs text-zinc-600 font-bold uppercase tracking-widest">
          Заказов пока нет
        </div>
      {% else %}
        <div class="space-y-6">
          {% for order in orders %}
            <div class="bg-zinc-950 border border-zinc-800 p-6 flex flex-col md:flex-row justify-between gap-6 relative">
              <div class="space-y-4 flex-1">
                <div class="flex items-center gap-4">
                  <span class="text-xs font-black bg-zinc-800 text-zinc-200 px-2.5 py-1">#{{ order.id }}</span>
                  <span class="text-xs font-bold text-zinc-400 uppercase">Статус: <strong class="text-zinc-200">{{ order.status }}</strong></span>
                </div>

                <div class="text-xs text-zinc-300 space-y-1 bg-zinc-900/50 p-3 border border-zinc-850">
                  <p><strong>Telegram:</strong> {{ order.contacts.telegram or 'Не указан' }}</p>
                  <p><strong>Телефон:</strong> {{ order.contacts.phone or 'Не указан' }}</p>
                </div>

                <div class="space-y-2 border-t border-zinc-900 pt-3">
                  <p class="text-[10px] text-zinc-500 font-black uppercase tracking-wider">Товары в заказе:</p>
                  {% for item in order['items'] %}
                    <div class="flex justify-between items-center text-xs text-zinc-400 border-b border-zinc-900/50 pb-1">
                      <span>{{ item.name }}</span>
                      <span class="font-bold text-zinc-200">{{ item.price_eur }} EUR</span>
                    </div>
                  {% endfor %}
                </div>

                <p class="text-sm font-black text-zinc-100">Итого: {{ order.total }} EUR</p>
              </div>

              <!-- Actions -->
              <div class="flex md:flex-col justify-end gap-2 min-w-[160px]">
                <form action="/admin/update_order_status" method="POST" class="w-full">
                  <input type="hidden" name="order_id" value="{{ order.id }}">
                  <select name="status" onchange="this.form.submit()" class="w-full bg-zinc-900 border border-zinc-700 text-xs text-zinc-200 px-3 py-2 focus:outline-none font-bold">
                    <option value="В обработке" {% if order.status == 'В обработке' %}selected{% endif %}>В обработке</option>
                    <option value="Подтвержден" {% if order.status == 'Подтвержден' %}selected{% endif %}>Подтвержден</option>
                    <option value="Отправлен" {% if order.status == 'Отправлен' %}selected{% endif %}>Отправлен</option>
                    <option value="Завершен" {% if order.status == 'Завершен' %}selected{% endif %}>Завершен</option>
                  </select>
                </form>
                <form action="/admin/delete_order" method="POST" class="w-full">
                  <input type="hidden" name="order_id" value="{{ order.id }}">
                  <button type="submit" onclick="return confirm('Точно удалить этот заказ?')" class="w-full bg-red-950/40 border border-red-900 hover:bg-red-900 text-red-200 text-xs font-bold py-2 transition-all">УДАЛИТЬ</button>
                </form>
              </div>
            </div>
          {% endfor %}
        </div>
      {% endif %}
    </div>

    <!-- Products Management Section -->
    <div>
      <h2 class="text-lg font-black tracking-wider text-zinc-200 mb-6 uppercase border-l-2 border-red-800 pl-3">Управление каталогом ({{ products|length }})</h2>

      <!-- Add Product Form -->
      <div class="bg-zinc-950 border border-zinc-800 p-6 mb-8">
        <h3 class="text-xs font-black tracking-widest text-zinc-400 mb-4 uppercase">Добавить новый товар</h3>
        <form action="/admin/add_product" method="POST" class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <input type="text" name="name" placeholder="Название товара" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">
          <select name="category" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">
            <option value="tshirts">Футболки (tshirts)</option>
            <option value="outerwear">Верхняя одежда (outerwear)</option>
            <option value="accessories">Аксессуары (accessories)</option>
          </select>
          <input type="number" name="price_eur" placeholder="Цена EUR" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">
          <input type="text" name="image" placeholder="URL картинки" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500 md:col-span-2">
          <input type="text" name="sizes" placeholder="Размеры через запятую (S, M, L)" class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">
          <textarea name="description" placeholder="Описание товара" class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500 md:col-span-3 h-20"></textarea>
          <button type="submit" class="bg-zinc-100 text-black font-black text-xs py-3 md:col-span-3 hover:bg-zinc-300 transition-all uppercase tracking-wider">ДОБАВИТЬ В КАТАЛОГ</button>
        </form>
      </div>

      <!-- Products List -->
      <div class="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-4 gap-4">
        {% for p in products %}
          <div class="bg-zinc-950 border border-zinc-900 p-4 flex flex-col justify-between">
            <div>
              <img src="{{ p.images[0] if p.images else '' }}" class="w-full h-48 object-cover border border-zinc-900 mb-3" alt="">
              <h4 class="text-xs font-black text-zinc-200 uppercase truncate">{{ p.name }}</h4>
              <p class="text-xs text-zinc-500 font-bold mt-1">{{ p.price_eur }} EUR</p>
              <p class="text-[10px] text-zinc-600 mt-1 uppercase">Категория: {{ p.category }}</p>
            </div>

            <div class="mt-4 space-y-2">
              <button onclick="openEditModal('{{ p.id }}', '{{ p.name }}', '{{ p.category }}', '{{ p.price_eur }}', '{{ p.images[0] if p.images else '' }}', '{{ p.sizes|join(', ') if p.sizes else '' }}', `{{ p.description or '' }}`)" class="w-full border border-zinc-700 text-zinc-300 hover:bg-zinc-800 text-[10px] font-bold py-2 transition-all uppercase">Редактировать</button>

              <form action="/admin/delete_product" method="POST">
                <input type="hidden" name="product_id" value="{{ p.id }}">
                <button type="submit" onclick="return confirm('Удалить товар из каталога?')" class="w-full border border-red-950 text-red-500 hover:bg-red-950/30 text-[10px] font-bold py-2 transition-all uppercase">Удалить</button>
              </form>
            </div>
          </div>
        {% endfor %}
      </div>
    </div>
  </div>

  <!-- EDIT MODAL -->
  <div id="editModal" class="fixed inset-0 bg-black/80 backdrop-blur-sm hidden items-center justify-center p-4 z-50">
    <div class="bg-zinc-950 border border-zinc-800 p-6 w-full max-w-xl relative">
      <h3 class="text-sm font-black tracking-widest text-zinc-200 mb-4 uppercase">Редактировать товар #<span id="edit_id_display"></span></h3>

      <form action="/admin/edit_product" method="POST" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <input type="hidden" name="product_id" id="edit_id">

        <input type="text" name="name" id="edit_name" placeholder="Название товара" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">

        <select name="category" id="edit_category" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">
          <option value="tshirts">Футболки (tshirts)</option>
          <option value="outerwear">Верхняя одежда (outerwear)</option>
          <option value="accessories">Аксессуары (accessories)</option>
        </select>

        <input type="number" name="price_eur" id="edit_price" placeholder="Цена EUR" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">

        <input type="text" name="image" id="edit_image" placeholder="URL картинки" required class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500">

        <input type="text" name="sizes" id="edit_sizes" placeholder="Размеры через запятую (S, M, L)" class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500 md:col-span-2">

        <textarea name="description" id="edit_description" placeholder="Описание товара" class="bg-zinc-900 border border-zinc-800 px-4 py-2.5 text-xs text-zinc-100 focus:outline-none focus:border-zinc-500 md:col-span-2 h-20"></textarea>

        <div class="flex gap-2 md:col-span-2">
          <button type="submit" class="flex-1 bg-zinc-100 text-black font-black text-xs py-3 hover:bg-zinc-300 transition-all uppercase tracking-wider">СОХРАНИТЬ ИЗМЕНЕНИЯ</button>
          <button type="button" onclick="closeEditModal()" class="bg-zinc-900 border border-zinc-700 text-zinc-400 px-6 text-xs font-bold hover:text-white uppercase">ОТМЕНА</button>
        </div>
      </form>
    </div>
  </div>

  <script>
    function openEditModal(id, name, category, price, image, sizes, description) {
      document.getElementById('edit_id').value = id;
      document.getElementById('edit_id_display').innerText = id;
      document.getElementById('edit_name').value = name;
      document.getElementById('edit_category').value = category;
      document.getElementById('edit_price').value = price;
      document.getElementById('edit_image').value = image;
      document.getElementById('edit_sizes').value = sizes;
      document.getElementById('edit_description').value = description;

      document.getElementById('editModal').classList.remove('hidden');
      document.getElementById('editModal').classList.add('flex');
    }

    function closeEditModal() {
      document.getElementById('editModal').classList.remove('flex');
      document.getElementById('editModal').classList.add('hidden');
    }
  </script>
</body>
</html>
"""

LOGIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <title>ADMIN LOGIN</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-black text-white h-screen flex items-center justify-center p-6">
  <form method="POST" action="/admin/login" class="bg-zinc-950 border border-zinc-800 p-8 w-full max-w-sm space-y-6">
    <h2 class="text-xl font-bold tracking-widest text-center uppercase">DEAD ARCHIVE // AUTH</h2>
    {% if error %}
      <p class="text-red-500 text-xs text-center font-bold">{{ error }}</p>
    {% endif %}
    <div>
      <label class="block text-[10px] text-zinc-500 uppercase font-bold mb-2">Пароль доступа</label>
      <input type="password" name="password" required class="w-full bg-zinc-900 border border-zinc-800 px-4 py-3 text-sm focus:outline-none focus:border-zinc-600">
    </div>
    <button type="submit" class="w-full bg-zinc-100 text-black py-3 text-xs font-black tracking-wider hover:bg-zinc-300 transition-all uppercase">ВОЙТИ</button>
  </form>
</body>
</html>
"""

@admin_bp.route('/')
def admin_dashboard():
    if not session.get('is_admin'):
        return render_template_string(LOGIN_TEMPLATE)

    orders = get_orders()
    products = get_products()

    return render_template_string(ADMIN_TEMPLATE, orders=orders, products=products)

@admin_bp.route('/login', methods=['POST'])
def admin_login():
    pwd = request.form.get('password')
    if pwd == ADMIN_PASSWORD:
        session['is_admin'] = True
        return redirect('/admin/')
    return render_template_string(LOGIN_TEMPLATE, error="Неверный пароль")

@admin_bp.route('/logout')
def admin_logout():
    session.pop('is_admin', None)
    return redirect('/admin/')

@admin_bp.route('/update_order_status', methods=['POST'])
def update_order_status():
    if not session.get('is_admin'):
        return redirect('/admin/')

    order_id = int(request.form.get('order_id', 0))
    new_status = request.form.get('status')

    orders = get_orders()
    for order in orders:
        if order.get('id') == order_id:
            order['status'] = new_status
            break

    save_orders(orders)
    return redirect('/admin/')

@admin_bp.route('/delete_order', methods=['POST'])
def delete_order():
    if not session.get('is_admin'):
        return redirect('/admin/')

    order_id = int(request.form.get('order_id', 0))
    orders = get_orders()
    updated_orders = [o for o in orders if o.get('id') != order_id]

    save_orders(updated_orders)
    return redirect('/admin/')

@admin_bp.route('/export_orders')
def export_orders():
    if not session.get('is_admin'):
        return redirect('/admin/')

    orders = get_orders()
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')

    writer.writerow(['ID Заказа', 'Статус', 'Telegram', 'Телефон', 'Товары', 'Итого (EUR)'])

    for order in orders:
        order_id = order.get('id')
        status = order.get('status')
        contacts = order.get('contacts', {})
        telegram = contacts.get('telegram', 'Не указан')
        phone = contacts.get('phone', 'Не указан')
        total = order.get('total', 0)

        items_list = order.get('items', [])
        items_str = ", ".join([f"{item.get('name')} ({item.get('price_eur')} EUR)" for item in items_list])

        writer.writerow([order_id, status, telegram, phone, items_str, total])

    output.seek(0)

    return Response(
        '\ufeff' + output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment;filename=dead_archive_orders.csv"}
    )

@admin_bp.route('/add_product', methods=['POST'])
def add_product():
    if not session.get('is_admin'):
        return redirect('/admin/')

    products = get_products()
    new_id = max([p['id'] for p in products], default=0) + 1

    sizes_raw = request.form.get('sizes', 'S, M, L')
    sizes_list = [s.strip() for s in sizes_raw.split(',') if s.strip()]
    price_eur = int(request.form.get('price_eur', 0))

    new_product = {
        "id": new_id,
        "name": request.form.get('name'),
        "category": request.form.get('category'),
        "price_eur": price_eur,
        "price_uah": price_eur * 50,
        "images": [request.form.get('image')],
        "description": request.form.get('description', ''),
        "sizes": sizes_list
    }

    products.append(new_product)
    save_products(products)
    return redirect('/admin/')

@admin_bp.route('/edit_product', methods=['POST'])
def edit_product():
    if not session.get('is_admin'):
        return redirect('/admin/')

    product_id = int(request.form.get('product_id', 0))
    products = get_products()

    sizes_raw = request.form.get('sizes', 'S, M, L')
    sizes_list = [s.strip() for s in sizes_raw.split(',') if s.strip()]
    price_eur = int(request.form.get('price_eur', 0))

    for p in products:
        if p.get('id') == product_id:
            p['name'] = request.form.get('name')
            p['category'] = request.form.get('category')
            p['price_eur'] = price_eur
            p['price_uah'] = price_eur * 50
            p['images'] = [request.form.get('image')]
            p['description'] = request.form.get('description', '')
            p['sizes'] = sizes_list
            break

    save_products(products)
    return redirect('/admin/')

@admin_bp.route('/delete_product', methods=['POST'])
def delete_product():
    if not session.get('is_admin'):
        return redirect('/admin/')

    product_id = int(request.form.get('product_id', 0))
    products = get_products()
    updated_products = [p for p in products if p.get('id') != product_id]

    save_products(updated_products)
    return redirect('/admin/')