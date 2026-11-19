import json
import os


def get_products_file_path(base_dir):
    return os.path.join(base_dir, "data", "products.json")


def load_products_from_file(base_dir):
    path = get_products_file_path(base_dir)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def save_products_to_file(base_dir, products_query_or_list):
    data_dir = os.path.join(base_dir, "data")
    os.makedirs(data_dir, exist_ok=True)
    path = get_products_file_path(base_dir)

    payload = []
    for idx, p in enumerate(products_query_or_list, start=1):
        payload.append({
            "name": p.name,
            "name_en": p.name_en or p.name,
            "category": p.category,
            "price_eur": int(p.price_eur),
            "price_uah": int(p.price_uah),
            "images_str": p.images_str,
            "colors_str": p.colors_str or "Черный",
            "description": p.description or "",
            "description_en": p.description_en or p.description or "",
            "sizes_str": p.sizes_str or "S, M, L, XL",
            "sort_order": int(p.sort_order or idx),
            "in_stock": bool(p.in_stock) if p.in_stock is not None else True,
        })

    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)
