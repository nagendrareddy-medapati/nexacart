from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import datetime, random, hashlib, os, re, secrets
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from bson import ObjectId
from bson.errors import InvalidId
import pymongo

# ═══════════════════════════════════════════════════════════
# APP SETUP
# ═══════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "nexacart_secure_key_2025")

@app.context_processor
def inject_globals():
    # Transparent 1px placeholder (no network request, no 404)
    _PLACEHOLDER = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='1' height='1'/%3E"

    def img_url(path_or_key):
        """Convert any image key/path to a proper URL. Handles all storage formats."""
        try:
            if not path_or_key:
                return _PLACEHOLDER   # no image uploaded — silent transparent placeholder
            s = str(path_or_key).strip()
            # Already a full URL (http/https)
            if s.startswith("http"):
                return s
            # Route path: "img/159/1" OR "/img/159/1"
            clean = s.lstrip("/")
            if clean.startswith("img/"):
                parts = clean.split("/")
                if len(parts) == 3:
                    return url_for("serve_product_image",
                                   product_id=int(parts[1]), slot=int(parts[2]))
            # GridFS key: "product_<id>_slot_<n>"
            if s.startswith("product_") and "_slot_" in s:
                slot_part = s.split("_slot_")[-1]
                id_part   = s.split("_slot_")[0].replace("product_", "")
                pid  = int(id_part)
                slot = int(slot_part)
                return url_for("serve_product_image", product_id=pid, slot=slot)
            # Local static file fallback
            return url_for("static", filename=s)
        except Exception:
            return _PLACEHOLDER

    # Get current user's avatar URL if logged in
    def user_avatar_url():
        uid = session.get("user_id")
        if uid:
            try:
                u = col("users").find_one({"seq_id": uid}, {"avatar": 1})
                if u and u.get("avatar"):
                    return f"/profile-picture/{uid}"
            except Exception:
                pass
        return None

    return dict(get_img=get_product_image, get_imgs=get_product_images, user_avatar_url=user_avatar_url,
                img_url=img_url, current_year=datetime.datetime.now().year)

@app.after_request
def add_security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options']        = 'SAMEORIGIN'
    resp.headers['X-XSS-Protection']       = '1; mode=block'
    resp.headers['Referrer-Policy']        = 'strict-origin-when-cross-origin'
    return resp

@app.errorhandler(404)
def page_not_found(e):
    return render_template('error.html', code=404,
        message="Page not found.", detail="The page you're looking for doesn't exist."), 404

@app.errorhandler(500)
def server_error(e):
    return render_template('error.html', code=500,
        message="Something went wrong.", detail="We're on it! Please try again in a moment."), 500

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "nexacart_admin_2025")
SUPER_ADMIN_USERNAME = os.environ.get("SUPER_ADMIN_USERNAME", "admin")
SUPER_ADMIN_PASSWORD = os.environ.get("SUPER_ADMIN_PASSWORD", "admin@9432")
STRIPE_PUBLISHABLE_KEY = os.environ.get("STRIPE_PUBLISHABLE_KEY", "pk_test_YOUR_PUBLISHABLE_KEY")
STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "sk_test_YOUR_SECRET_KEY")

PROMO_CODES = {
    "SAVE10":10,"MARKET20":20,"TECH15":15,"WELCOME5":5,"FASHION30":30,
    "BEAUTY15":15,"FOOD20":20,"SUMMER25":25,"WINTER20":20,"MONSOON15":15,"FIRST50":50,
}
GST_RATE = 0.09
PER_PAGE = 24

# ═══════════════════════════════════════════════════════════
# MONGODB CONNECTION
# ═══════════════════════════════════════════════════════════
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/nexacart")
_mongo_client = None

def get_mongo():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = pymongo.MongoClient(MONGO_URI)
    return _mongo_client

def get_db():
    return get_mongo()[os.environ.get("MONGO_DB", "nexacart")]

# Collection helpers — returns the collection
def col(name):
    return get_db()[name]

# ═══════════════════════════════════════════════════════════
# MONGODB GRIDFS — Image Storage (no external service needed)
# Images are stored directly in MongoDB as binary data.
# Served via /img/<product_id>/<slot> route.
# ═══════════════════════════════════════════════════════════
import gridfs
from PIL import Image as PILImage
import io

def get_gridfs():
    """Return a GridFS bucket for storing product images."""
    return gridfs.GridIn
    
def get_fs():
    return gridfs.GridFS(get_db(), collection="product_images")

def upload_image_to_gridfs(file_obj, product_id, slot):
    """Store an image in MongoDB GridFS. Returns the file_id string."""
    try:
        fs = get_fs()
        file_id_str = f"product_{product_id}_slot_{slot}"
        # Remove existing file with same id_str if present
        for existing in fs.find({"filename": file_id_str}):
            fs.delete(existing._id)
        # Read and optimise image with Pillow
        raw = file_obj.read()
        img = PILImage.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((800, 800), PILImage.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        buf.seek(0)
        fs.put(buf, filename=file_id_str,
               content_type="image/jpeg",
               product_id=product_id, slot=slot)
        return file_id_str   # used as image "URL" key
    except Exception as e:
        app.logger.error(f"GridFS upload failed: {e}")
        return None

def delete_product_images_gridfs(product_id):
    """Delete all GridFS images for a product."""
    try:
        fs = get_fs()
        for slot in range(1, 7):
            for f in fs.find({"filename": f"product_{product_id}_slot_{slot}"}):
                fs.delete(f._id)
    except Exception as e:
        app.logger.error(f"GridFS delete failed: {e}")

# ═══════════════════════════════════════════════════════════
# IMAGE HELPERS — read from MongoDB product document
# ═══════════════════════════════════════════════════════════
def get_product_images(product_id):
    """Return list of image route URLs for a product.
    Images are served from /img/<product_id>/<slot> which reads from GridFS."""
    try:
        pid = int(product_id)
    except (ValueError, TypeError):
        return []   # product_id was a name/ObjectId string — not a valid numeric id
    p = col("products").find_one({"seq_id": pid}, {"images": 1})
    if not p:
        return []
    stored = [s for s in (p.get("images") or []) if s]
    if stored:
        # Return keys as-is; img_url() in templates handles all formats
        urls = []
        for key in stored:
            if key.startswith("http") or (key.startswith("product_") and "_slot_" in key):
                urls.append(key)
            else:
                # legacy or unknown — keep as-is
                urls.append(key)
        return urls
    # Fallback: check local static folder
    folder = os.path.join(os.path.dirname(__file__), "static", "product_images", str(pid))
    local  = []
    for i in range(1, 7):
        for ext in ("jpg","jpeg","png","webp"):
            if os.path.exists(os.path.join(folder, f"{i}.{ext}")):
                local.append(f"product_images/{pid}/{i}.{ext}")
                break
    return local

def get_product_image(product_id, name="", category=""):
    """Return main image key/path, or None if no image uploaded."""
    try:
        imgs = get_product_images(product_id)
        return imgs[0] if imgs else None
    except Exception:
        return None

def batch_get_first_images(product_ids):
    """Fetch first image URL for a list of product IDs in a single DB query.
    Returns a dict {product_id: img_url_path_or_None}."""
    result = {}
    try:
        pids = [int(p) for p in product_ids if p]
        if not pids:
            return result
        docs = col("products").find(
            {"seq_id": {"$in": pids}},
            {"seq_id": 1, "images": 1}
        )
        for doc in docs:
            pid = doc.get("seq_id")
            stored = [s for s in (doc.get("images") or []) if s]
            if stored:
                key = stored[0]
                # Return raw key; img_url() in templates handles all formats
                result[pid] = key
            else:
                result[pid] = None
    except Exception as e:
        app.logger.error(f"batch_get_first_images error: {e}")
    return result

# ═══════════════════════════════════════════════════════════
# MONGODB DOCUMENT HELPERS
# ═══════════════════════════════════════════════════════════
def doc_to_dict(doc):
    """Convert a MongoDB document to a plain dict with id always as seq_id (int)."""
    if doc is None:
        return None
    d = dict(doc)
    seq = doc.get("seq_id")
    if seq is not None:
        d["id"] = int(seq)
    else:
        # Document has no seq_id — assign a safe fallback (won't match real routes)
        d["id"] = 0
    d["_id"] = str(d.get("_id",""))
    return d

def next_seq(name):
    """Auto-increment counter stored in MongoDB.
    Self-heals if the counter falls behind the actual max seq_id in the collection."""
    for attempt in range(3):
        result = get_db().counters.find_one_and_update(
            {"_id": name},
            {"$inc": {"seq": 1}},
            upsert=True,
            return_document=pymongo.ReturnDocument.AFTER
        )
        candidate = result["seq"]
        # Check for duplicate by looking at the actual collection max
        # Map counter name to collection name
        coll_name = {"products": "products", "users": "users",
                     "cart": "cart", "orders": "orders"}.get(name, name)
        try:
            coll = get_db()[coll_name]
            agg = list(coll.aggregate([{"$group": {"_id": None, "max": {"$max": "$seq_id"}}}]))
            actual_max = agg[0]["max"] if agg else 0
            if actual_max is not None and actual_max >= candidate:
                # Counter is behind — jump it ahead
                safe_val = actual_max + 1
                get_db().counters.update_one(
                    {"_id": name},
                    {"$set": {"seq": safe_val}},
                    upsert=True
                )
                candidate = safe_val
        except Exception:
            pass
        return candidate
    raise RuntimeError(f"next_seq({name}): could not get a unique id after 3 attempts")

# ═══════════════════════════════════════════════════════════
# DB INIT — create indexes and seed products
# ═══════════════════════════════════════════════════════════
def init_db():
    db = get_db()

    def safe_index(col, keys, **kw):
        """Create index; if it conflicts, drop all indexes and retry."""
        try:
            col.create_index(keys, **kw)
        except Exception:
            try: col.drop_indexes()
            except Exception: pass
            try: col.create_index(keys, **kw)
            except Exception: pass

    safe_index(db.users,            "username",                unique=True)
    safe_index(db.users,            "email",                   sparse=True)
    safe_index(db.users,            "phone",                   sparse=True)
    safe_index(db.products,         "seq_id",                  unique=True)
    safe_index(db.products,         "category")
    safe_index(db.cart,             [("user_id",1),("product_id",1)])
    safe_index(db.wishlist,         [("user_id",1),("product_id",1)], unique=True)
    safe_index(db.orders,           "order_ref",               unique=True)
    safe_index(db.orders,           "user_id")
    safe_index(db.recently_viewed,  [("user_id",1),("product_id",1)], unique=True)
    safe_index(db.password_resets,  "token",                   unique=True)
    try: db.products.create_index([("name","text"),("category","text")])
    except Exception: pass

    # ── Auto-clean old documents that lack seq_id (stale data) ──
    if db.products.count_documents({"seq_id":{"$exists":False}}) > 0:
        db.products.delete_many({"seq_id":{"$exists":False}})
        db.counters.delete_one({"_id":"products"})
        app.logger.warning("Cleaned old products without seq_id — will reseed")

    # ── Auto-assign seq_id to users that are missing it ──
    for u in list(db.users.find({"seq_id":{"$exists":False}})):
        db.users.update_one({"_id":u["_id"]},{"$set":{"seq_id":next_seq("users")}})

    # ── Sync counters to actual max seq_id in each collection (prevents DuplicateKeyError) ──
    for coll_name in ("products", "users"):
        try:
            agg = list(db[coll_name].aggregate([{"$group": {"_id": None, "max": {"$max": "$seq_id"}}}]))
            actual_max = int(agg[0]["max"]) if agg and agg[0]["max"] is not None else 0
            current = db.counters.find_one({"_id": coll_name})
            current_val = int(current["seq"]) if current else 0
            if actual_max > current_val:
                db.counters.update_one(
                    {"_id": coll_name},
                    {"$set": {"seq": actual_max}},
                    upsert=True
                )
                app.logger.info(f"Counter sync: {coll_name} counter fixed {current_val} -> {actual_max}")
        except Exception as e:
            app.logger.warning(f"Counter sync failed for {coll_name}: {e}")

    # ── Patch existing products that have empty images with seed URLs ──
    _SEED_IMG_PATCH = {
        "Sony Bravia 55-inch 4K TV": ["https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=600"],
        "LG OLED 65-inch TV": ["https://images.unsplash.com/photo-1601944179066-29786cb9d32a?w=600"],
        "Amazon Echo Dot 5th Gen": ["https://images.unsplash.com/photo-1543512214-318c7553f230?w=600"],
        "Google Nest Hub Max": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Xiaomi Smart TV 43-inch": ["https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=600"],
        "Apple TV 4K": ["https://images.unsplash.com/photo-1585792180666-f7347c490ee2?w=600"],
        "Epson Projector EH-TW750": ["https://images.unsplash.com/photo-1478720568477-152d9b164e26?w=600"],
        "Ring Video Doorbell Pro": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Philips Hue Starter Kit": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "TP-Link Archer AX73 Router": ["https://images.unsplash.com/photo-1562408590-e32931084e23?w=600"],
        "Chromecast with Google TV": ["https://images.unsplash.com/photo-1585792180666-f7347c490ee2?w=600"],
        "Blink Outdoor Camera": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "MacBook Pro 14-inch M3": ["https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=600"],
        "Dell XPS 15 OLED": ["https://images.unsplash.com/photo-1593642632559-0c6d3fc62b89?w=600"],
        "HP Pavilion 15": ["https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=600"],
        "Lenovo ThinkPad X1 Carbon": ["https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=600"],
        "ASUS ROG Zephyrus G14": ["https://images.unsplash.com/photo-1603302576837-37561b2e2302?w=600"],
        "Acer Swift 3": ["https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=600"],
        "MSI Creator Z16": ["https://images.unsplash.com/photo-1593642632559-0c6d3fc62b89?w=600"],
        "Mac Mini M2": ["https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=600"],
        "Logitech MX Keys Advanced": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "Samsung 27-inch QHD Monitor": ["https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=600"],
        "iPhone 15 Pro Max": ["https://images.unsplash.com/photo-1695048133142-1a20484d2569?w=600"],
        "Samsung Galaxy S24 Ultra": ["https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?w=600"],
        "OnePlus 12": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Google Pixel 8 Pro": ["https://images.unsplash.com/photo-1598327105666-5b89351aff97?w=600"],
        "Realme GT 5 Pro": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Xiaomi 14 Ultra": ["https://images.unsplash.com/photo-1570101945621-945409a6370f?w=600"],
        "Nothing Phone 2a": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "iQOO 12 5G": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Apple Watch Ultra 2": ["https://images.unsplash.com/photo-1551816230-ef5deaed4a26?w=600"],
        "Samsung Galaxy Watch 6": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Garmin Fenix 7": ["https://images.unsplash.com/photo-1508685096489-7aacd43bd3b1?w=600"],
        "Fitbit Charge 6": ["https://images.unsplash.com/photo-1575311373937-040b8e1fd5b6?w=600"],
        "Noise ColorFit Pro 4": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Mi Smart Band 8": ["https://images.unsplash.com/photo-1575311373937-040b8e1fd5b6?w=600"],
        "Sony WH-1000XM5": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "AirPods Pro 2nd Gen": ["https://images.unsplash.com/photo-1603351154351-5e2d0600bb77?w=600"],
        "Bose QuietComfort 45": ["https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=600"],
        "JBL Flip 6": ["https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=600"],
        "Sennheiser HD 450BT": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "Sony WF-1000XM5": ["https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=600"],
        "Marshall Stanmore III": ["https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=600"],
        "boAt Rockerz 558": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "Allen Solly Men's Formal Shirt": ["https://images.unsplash.com/photo-1620012253295-c15cc3e65df4?w=600"],
        "Levi's 511 Slim Jeans": ["https://images.unsplash.com/photo-1542272604-787c3835535d?w=600"],
        "Raymond Wool Blazer": ["https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=600"],
        "Nike Dri-FIT T-Shirt": ["https://images.unsplash.com/photo-1581655353564-df123a1eb820?w=600"],
        "Tommy Hilfiger Polo": ["https://images.unsplash.com/photo-1620012253295-c15cc3e65df4?w=600"],
        "Puma Tracksuit": ["https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?w=600"],
        "Biba Anarkali Kurta": ["https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600"],
        "Zara Floral Maxi Dress": ["https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600"],
        "Fabindia Silk Saree": ["https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600"],
        "AND Blazer Formal": ["https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=600"],
        "Libas Cotton Palazzo Set": ["https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600"],
        "Nike Air Max 270": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=600"],
        "Adidas Ultraboost 22": ["https://images.unsplash.com/photo-1608231387042-66d1773070a5?w=600"],
        "Woodland Trekking Boots": ["https://images.unsplash.com/photo-1520639888713-7851133b1ed0?w=600"],
        "Bata Formal Shoes": ["https://images.unsplash.com/photo-1533867617858-e7b97e060509?w=600"],
        "Crocs Classic Clogs": ["https://images.unsplash.com/photo-1606107557195-0e29a4b5b4aa?w=600"],
        "Skechers Memory Foam": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=600"],
        "Lakme Absolute Foundation": ["https://images.unsplash.com/photo-1522335789203-aabd1fc54bc9?w=600"],
        "Mamaearth Vitamin C Serum": ["https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=600"],
        "Dot & Key Sunscreen": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "The Ordinary Niacinamide": ["https://images.unsplash.com/photo-1556228453-efd6c1ff04f6?w=600"],
        "Minimalist AHA BHA Toner": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "SUGAR Cosmetics Lipstick": ["https://images.unsplash.com/photo-1631214500004-0f5f9f2ef5c4?w=600"],
        "Dyson Supersonic Hair Dryer": ["https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=600"],
        "TRESemmé Keratin Shampoo": ["https://images.unsplash.com/photo-1526947425960-945c6e72858f?w=600"],
        "Philips Hair Dryer BHD356": ["https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=600"],
        "Indulekha Bringha Hair Oil": ["https://images.unsplash.com/photo-1526947425960-945c6e72858f?w=600"],
        "Chanel No 5 EDP": ["https://images.unsplash.com/photo-1541643600914-78b084683702?w=600"],
        "Davidoff Cool Water EDT": ["https://images.unsplash.com/photo-1557170334-a9632e77c6e4?w=600"],
        "Fogg Black Series": ["https://images.unsplash.com/photo-1541643600914-78b084683702?w=600"],
        "Armaf Club De Nuit": ["https://images.unsplash.com/photo-1557170334-a9632e77c6e4?w=600"],
        "IKEA KALLAX Shelf Unit": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Sleepwell Ortho Pro Mattress": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Urban Ladder Floor Lamp": ["https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=600"],
        "IKEA Poäng Chair": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Milton Thermosteel Bottle": ["https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=600"],
        "Prestige Induction Cooktop": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Hawkins Pressure Cooker": ["https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=600"],
        "Nespresso Vertuo Next": ["https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=600"],
        "WMF Cutlery Set 30-Piece": ["https://images.unsplash.com/photo-1556909172-54557c7e4fb7?w=600"],
        "Borosil Glass Casserole Set": ["https://images.unsplash.com/photo-1556909172-54557c7e4fb7?w=600"],
        "LG 7kg Washing Machine": ["https://images.unsplash.com/photo-1626806787461-102c1bfaaea1?w=600"],
        "Samsung 253L Refrigerator": ["https://images.unsplash.com/photo-1571175443880-49e1d25b2bc5?w=600"],
        "Dyson V15 Detect Vacuum": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Philips Air Fryer HD9200": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Hitachi 1.5 Ton Split AC": ["https://images.unsplash.com/photo-1601560496309-d12c0a994f42?w=600"],
        "Eureka Forbes Water Purifier": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Bajaj Room Heater": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Tata Tea Premium 1kg": ["https://images.unsplash.com/photo-1544787219-7f47ccb76574?w=600"],
        "Amul Ghee 1kg": ["https://images.unsplash.com/photo-1609501676725-7186f017a4b7?w=600"],
        "India Gate Basmati Rice 5kg": ["https://images.unsplash.com/photo-1586201375761-83865001e31c?w=600"],
        "Cadbury Celebrations Gift Box": ["https://images.unsplash.com/photo-1549007994-cb92caebd54b?w=600"],
        "Haldiram's Mixture 400g": ["https://images.unsplash.com/photo-1536662788222-6927ce05daea?w=600"],
        "Patanjali Aloe Vera Juice 1L": ["https://images.unsplash.com/photo-1608571423902-eed4a5ad8108?w=600"],
        "Himalaya Ashwagandha Tablets": ["https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=600"],
        "Omron BP Monitor HEM-7120": ["https://images.unsplash.com/photo-1559757148-5c350d0d3c56?w=600"],
        "Apollo Life Vitamin D3": ["https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=600"],
        "Wellbeing Nutrition Probiotic": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "Boldfit Yoga Mat 6mm": ["https://images.unsplash.com/photo-1575052814086-f385e2e2ad1b?w=600"],
        "Vector X Cricket Bat": ["https://images.unsplash.com/photo-1531415074968-036ba1b575da?w=600"],
        "Nivia Badminton Set": ["https://images.unsplash.com/photo-1626224583764-f87db24ac4ea?w=600"],
        "Fitkit Resistance Bands": ["https://images.unsplash.com/photo-1598289431512-b97b0917affc?w=600"],
        "NutriTech Whey Protein 2kg": ["https://images.unsplash.com/photo-1593095948071-474c5cc2989d?w=600"],
        "Decathlon Camping Tent": ["https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?w=600"],
        "Atomic Habits — James Clear": ["https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=600"],
        "Rich Dad Poor Dad": ["https://images.unsplash.com/photo-1592496431122-2349e0fbc666?w=600"],
        "Casio Scientific Calculator": ["https://images.unsplash.com/photo-1587145820266-a5951ee6f620?w=600"],
        "LEGO City Police Station": ["https://images.unsplash.com/photo-1587654780291-39c9404d746b?w=600"],
        "PlayStation DualSense Controller": ["https://images.unsplash.com/photo-1606144042614-b2417e99c4e3?w=600"],
        "Hasbro Monopoly Classic": ["https://images.unsplash.com/photo-1610890716171-6b1bb98ffd09?w=600"],
        "Barbie Dreamhouse": ["https://images.unsplash.com/photo-1602132468813-46e19c21b42c?w=600"],
        "UNO Card Game": ["https://images.unsplash.com/photo-1610890716171-6b1bb98ffd09?w=600"],
        "IKEA HEMNES Bed Frame": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Urban Ladder Fabric Sofa": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Durian Ergonomic Office Chair": ["https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600"],
        "Pepperfry Study Table": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Pedigree Adult Dog Food 3kg": ["https://images.unsplash.com/photo-1601758174493-45d0a4d3e407?w=600"],
        "Whiskas Cat Food 1.2kg": ["https://images.unsplash.com/photo-1615789591457-74a63395c990?w=600"],
        "Furhaven Orthopedic Pet Bed": ["https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=600"],
        "Michelin Pilot Sport Tyre": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Vega Crux Helmet": ["https://images.unsplash.com/photo-1558981403-c5f9899a28bc?w=600"],
        "Instaauto Dashcam": ["https://images.unsplash.com/photo-1565043666747-69f6646db940?w=600"],
        "BBQ Grill Portable": ["https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=600"],
        "Solar Garden Lights 10-Pack": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Outdoor Hammock Cotton": ["https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?w=600"],
        "Pampers Pants Large 54-Count": ["https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600"],
        "Fisher-Price Baby Gym": ["https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=600"],
        "Chicco Baby Monitor": ["https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600"],
        "Tanishq Gold Necklace": ["https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=600"],
        "Malabar Gold Bangles Set": ["https://images.unsplash.com/photo-1573408301185-9519f94815f6?w=600"],
        "BlueStone Diamond Ring": ["https://images.unsplash.com/photo-1605100804763-247f67b3557e?w=600"],
        "Zaveri Pearls Necklace Set": ["https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=600"],
        "Johareez Kundan Necklace": ["https://images.unsplash.com/photo-1573408301185-9519f94815f6?w=600"],
        "Voylla Silver Earrings Set": ["https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=600"],
        "Yamaha Acoustic Guitar F310": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "Casio CT-S300 Keyboard": ["https://images.unsplash.com/photo-1520523839897-bd0b52f945a0?w=600"],
        "Pearl Export Drum Kit": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Harman Kardon Ukulele": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "Banjira Tabla Set": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Fender Squier Guitar": ["https://images.unsplash.com/photo-1525201548942-d8732f6617a0?w=600"],
        "Cajon Percussion Box": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Hohner Harmonica": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "HP LaserJet Pro Printer": ["https://images.unsplash.com/photo-1612815154858-60aa4c59eaa6?w=600"],
        "Epson L3252 InkTank Printer": ["https://images.unsplash.com/photo-1612815154858-60aa4c59eaa6?w=600"],
        "Wacom Drawing Tablet": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "AmazonBasics Office Chair": ["https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600"],
        "Navneet A4 Ruled Reams": ["https://images.unsplash.com/photo-1471107340929-a87cd0f5b5f3?w=600"],
        "Stapler Set with Pins": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "American Tourister Trolley 68cm": ["https://images.unsplash.com/photo-1565026057447-bc90a3dceb87?w=600"],
        "Safari Polycarbonate Trolley": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Wildcraft Backpack 45L": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Samsonite Carry-on Bag": ["https://images.unsplash.com/photo-1565026057447-bc90a3dceb87?w=600"],
        "Neck Pillow Memory Foam": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Passport Holder Leather": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Samsung Galaxy Tab S9": ["https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=600"],
        "Noise ColorFit Ultra 3": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Nivea Men Face Wash": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "Philips Air Fryer XL HD9270": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Fastrack Casual Watch": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "boAt Airdopes 141": ["https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=600"],
        "Wildcraft Trident Backpack": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Prestige Mixer Grinder 750W": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
    }
    patched = 0
    for name, imgs in _SEED_IMG_PATCH.items():
        result = db.products.update_one(
            {"name": name, "$or": [{"images": {"$exists": False}}, {"images": []}, {"images": None}]},
            {"$set": {"images": imgs}}
        )
        if result.modified_count:
            patched += 1
    if patched:
        app.logger.info(f"✅ Patched images for {patched} existing products")

    app.logger.info("✅ MongoDB ready")

# ═══════════════════════════════════════════════════════════
# CATEGORY META
# ═══════════════════════════════════════════════════════════
CATEGORY_META = {
    "Electronics":("⚡","bg-blue"),"Laptops & Computers":("💻","bg-indigo"),
    "Smartphones":("📱","bg-purple"),"Audio":("🎧","bg-violet"),
    "Wearables":("⌚","bg-blue"),"Clothing — Men":("👔","bg-green"),
    "Clothing — Women":("👗","bg-pink"),"Clothing — Kids":("👕","bg-yellow"),
    "Fashion Accessories":("👜","bg-rose"),"Footwear":("👟","bg-orange"),
    "Beauty & Skincare":("💄","bg-pink"),"Hair Care":("💇","bg-rose"),
    "Fragrances":("🌸","bg-violet"),"Home & Living":("🛋️","bg-green"),
    "Kitchen & Dining":("🍳","bg-amber"),"Appliances":("🏠","bg-blue"),
    "Groceries & Food":("🛒","bg-green"),"Health & Wellness":("💊","bg-teal"),
    "Sports & Fitness":("🏋️","bg-orange"),"Books & Stationery":("📚","bg-indigo"),
    "Toys & Games":("🎮","bg-purple"),"Furniture":("🪑","bg-amber"),
    "Pet Supplies":("🐾","bg-green"),"Automotive":("🚗","bg-blue"),
    "Outdoor & Garden":("🌿","bg-green"),"Baby & Maternity":("🍼","bg-pink"),
    "Jewellery":("💍","bg-amber"),"Musical Instruments":("🎸","bg-amber"),
    "Stationery & Office":("✏️","bg-indigo"),"Travel & Luggage":("✈️","bg-blue"),
}

# ═══════════════════════════════════════════════════════════
# CUSTOMIZATION OPTIONS
# ═══════════════════════════════════════════════════════════
def get_customization_options(category, name=""):
    nl = name.lower()
    if category == "Smartphones":
        return [{"label":"Storage","key":"storage","choices":["128 GB","256 GB","512 GB","1 TB"],"required":True},
                {"label":"Colour","key":"colour","choices":["Midnight Black","Pearl White","Deep Blue","Forest Green","Titanium"],"required":True}]
    if category == "Laptops & Computers":
        return [{"label":"RAM","key":"ram","choices":["8 GB RAM","16 GB RAM","32 GB RAM","64 GB RAM"],"required":True},
                {"label":"Storage","key":"storage","choices":["256 GB SSD","512 GB SSD","1 TB SSD","2 TB SSD"],"required":True}]
    if category == "Audio":
        return [{"label":"Colour","key":"colour","choices":["Black","White","Navy Blue","Silver","Rose Gold"],"required":False},
                {"label":"Connectivity","key":"connectivity","choices":["Bluetooth","Wired","Both"],"required":False}]
    if category == "Wearables":
        return [{"label":"Band Size","key":"size","choices":["Small (130–180 mm)","Medium (150–200 mm)","Large (170–220 mm)"],"required":True},
                {"label":"Colour","key":"colour","choices":["Black","Silver","Gold","Rose Gold","Midnight"],"required":False}]
    if category == "Electronics":
        return [{"label":"Colour","key":"colour","choices":["Black","White","Silver","Grey"],"required":False}]
    if category == "Clothing — Men":
        return [{"label":"Size","key":"size","choices":["XS","S","M","L","XL","XXL","3XL"],"required":True},
                {"label":"Colour","key":"colour","choices":["White","Black","Navy Blue","Grey","Olive","Maroon","Royal Blue"],"required":True}]
    if category == "Clothing — Women":
        return [{"label":"Size","key":"size","choices":["XS","S","M","L","XL","XXL"],"required":True},
                {"label":"Colour","key":"colour","choices":["White","Black","Pink","Beige","Red","Teal","Lavender","Mustard"],"required":True}]
    if category == "Clothing — Kids":
        return [{"label":"Age / Size","key":"size","choices":["1-2 Y","2-3 Y","3-4 Y","4-5 Y","5-6 Y","6-7 Y","8-9 Y","10-11 Y"],"required":True},
                {"label":"Colour","key":"colour","choices":["White","Blue","Pink","Yellow","Green","Red","Multicolour"],"required":False}]
    if category == "Footwear":
        return [{"label":"Size (UK)","key":"size","choices":["UK 3","UK 4","UK 5","UK 6","UK 7","UK 8","UK 9","UK 10","UK 11","UK 12"],"required":True},
                {"label":"Colour","key":"colour","choices":["Black","White","Brown","Navy","Grey","Tan"],"required":False}]
    if category == "Fashion Accessories":
        return [{"label":"Colour","key":"colour","choices":["Black","Brown","Tan","Navy","Burgundy","Olive","Nude"],"required":False}]
    if category == "Jewellery":
        return [{"label":"Metal","key":"metal","choices":["Gold (22K)","Gold (18K)","Rose Gold","Silver (925)","Platinum"],"required":True},
                {"label":"Size","key":"size","choices":["Free Size","Size 6","Size 8","Size 10","Size 12","Size 14","Size 16"],"required":False}]
    if category in ("Beauty & Skincare","Hair Care"):
        return [{"label":"Pack Size","key":"pack","choices":["30 ml","50 ml","100 ml","150 ml","200 ml"],"required":False}]
    if category == "Fragrances":
        return [{"label":"Size","key":"size","choices":["25 ml","50 ml","75 ml","100 ml","150 ml"],"required":True}]
    if category == "Furniture":
        return [{"label":"Finish / Colour","key":"colour","choices":["Natural Wood","Dark Walnut","White","Grey","Black"],"required":True}]
    if category == "Appliances":
        caps = {"washing":["6 kg","7 kg","8 kg","9 kg","10 kg"],"fridge":["180 L","253 L","320 L","450 L"],
                "ac":["0.75 Ton","1 Ton","1.5 Ton","2 Ton"],"default":["Standard","Large","XL"]}
        choices = (caps["washing"] if "wash" in nl else caps["fridge"] if "fridge" in nl or "refrigerator" in nl
                   else caps["ac"] if " ac" in nl or "conditioner" in nl else caps["default"])
        return [{"label":"Capacity","key":"capacity","choices":choices,"required":True}]
    if category == "Sports & Fitness":
        if any(w in nl for w in ["mat","rug"]):
            return [{"label":"Thickness","key":"thickness","choices":["4 mm","6 mm","8 mm","10 mm"],"required":False}]
        if any(w in nl for w in ["shoe","boot","sneaker","running"]):
            return [{"label":"Size (UK)","key":"size","choices":["UK 6","UK 7","UK 8","UK 9","UK 10","UK 11"],"required":True}]
        if any(w in nl for w in ["weight","dumbbell","kg"]):
            return [{"label":"Weight","key":"weight","choices":["1 kg","2 kg","5 kg","10 kg","15 kg","20 kg"],"required":True}]
        return []
    if category in ("Books & Stationery","Stationery & Office"):
        return [{"label":"Pack of","key":"pack","choices":["1","2","3","5","10"],"required":False}]
    if category == "Groceries & Food":
        return [{"label":"Pack Size","key":"pack","choices":["250 g","500 g","1 kg","2 kg","5 kg"],"required":False}]
    if category == "Pet Supplies":
        return [{"label":"Pack Size","key":"pack","choices":["500 g","1 kg","3 kg","7 kg","15 kg"],"required":False},
                {"label":"Flavour","key":"flavour","choices":["Original","Chicken","Beef","Lamb","Salmon","Vegetarian"],"required":False}]
    if category == "Travel & Luggage":
        return [{"label":"Colour","key":"colour","choices":["Black","Navy Blue","Red","Grey","Olive Green","Rose Gold"],"required":False},
                {"label":"Size","key":"size","choices":['Cabin (18"–20")', 'Medium (24"–26")', 'Large (28"–32")'],"required":False}]
    return []

def get_variants(category):
    opts = get_customization_options(category)
    size_opt = next((o for o in opts if o["key"]=="size"), None)
    return size_opt["choices"] if size_opt else []

# ═══════════════════════════════════════════════════════════
# PRODUCT FEATURES
# ═══════════════════════════════════════════════════════════
def _get_product_features(category, name):
    feats = {
        "Electronics":["Smart home compatible","Energy Star certified","2-year warranty","Easy setup","Remote & app control"],
        "Laptops & Computers":["Latest generation processor","Fast SSD storage","Backlit keyboard","Multiple I/O ports","MIL-SPEC durability"],
        "Smartphones":["5G connectivity","AMOLED display","Fast charging","Multi-camera system","Expandable storage"],
        "Audio":["Active Noise Cancellation","30-hour battery","Bluetooth 5.3","Foldable design","Carrying case included"],
        "Wearables":["Health & fitness tracking","GPS tracking","Water resistant (5ATM)","7-day battery","Sleep monitoring"],
        "Clothing — Men":["100% premium cotton","Pre-shrunk","Machine washable","Regular fit","Multiple colours"],
        "Clothing — Women":["Soft breathable fabric","Wrinkle resistant","Easy care","Contemporary design","True-to-size fit"],
        "Clothing — Kids":["Child-safe materials","Easy-on design","Durable stitching","Colourfast dyes","Hypoallergenic"],
        "Fashion Accessories":["Premium material","Handcrafted finish","Multiple compartments","Adjustable strap","Dust bag included"],
        "Footwear":["Cushioned insole","Slip-resistant sole","Breathable upper","True-to-size","1-year warranty"],
        "Beauty & Skincare":["Dermatologist tested","Paraben-free","Cruelty-free","SPF protection","All skin types"],
        "Hair Care":["Sulphate-free","Strengthens hair","Controls frizz","Colour-safe","Nourishes & hydrates"],
        "Fragrances":["Long-lasting 8+ hours","Eau de Parfum","Nature-inspired","Premium glass bottle","Unique notes"],
        "Home & Living":["Premium materials","Easy assembly","Stain resistant","Modern design","1-year warranty"],
        "Kitchen & Dining":["Food-grade materials","Dishwasher safe","Heat resistant","BPA-free","Easy to clean"],
        "Appliances":["5-star energy rating","1-year warranty","Easy installation","Auto shut-off","Low noise"],
        "Groceries & Food":["100% natural","No preservatives","Hygienically packed","Source verified","Ready to use"],
        "Health & Wellness":["Clinically tested","No side effects","Natural formula","GMP certified","Doctor recommended"],
        "Sports & Fitness":["Professional grade","Ergonomic design","Sweat resistant","Durable","All fitness levels"],
        "Books & Stationery":["Bestselling","Original edition","Quality paper","Durable binding","Index included"],
        "Toys & Games":["Child-safe","Age-appropriate","Educational value","Durable","Guidelines included"],
        "Furniture":["Solid wood","Easy assembly","Weight tested","5-year warranty","Scratch-resistant"],
        "Pet Supplies":["Vet approved","Natural ingredients","No chemicals","All breeds","Hypoallergenic"],
        "Automotive":["OEM compatible","All-weather","Easy installation","Vehicle-specific","1-year warranty"],
        "Outdoor & Garden":["Weather resistant","UV protected","Eco-friendly","Easy setup","Indian climate ready"],
        "Baby & Maternity":["BPA-free","Dermatologist tested","Gentle on skin","Easy to clean","Certified safe"],
        "Jewellery":["BIS hallmarked","Certified gemstones","Ethically sourced","Handcrafted","Certificate of authenticity"],
        "Musical Instruments":["Professional grade","Standard tuning","Beginner accessories","All levels","Quality resonance"],
        "Stationery & Office":["Premium quality","Long-lasting","Energy efficient","Office & home use","Compatible accessories"],
        "Travel & Luggage":["TSA-approved lock","360° spinner wheels","Expandable","Lightweight","10-year warranty"],
    }
    return feats.get(category, ["Premium quality","1-year warranty","30-day returns","Free delivery","Genuine product"])

# ═══════════════════════════════════════════════════════════
# SEASON PICKS
# ═══════════════════════════════════════════════════════════
def get_season():
    m = datetime.datetime.now().month
    return ("Summer" if m in (4,5,6) else "Monsoon" if m in (7,8,9)
            else "Winter" if m in (10,11,12) else "Spring")

SEASON_CATS = {
    "Summer": ["Footwear","Sports & Fitness","Beauty & Skincare"],
    "Monsoon": ["Clothing — Men","Clothing — Women","Footwear"],
    "Winter": ["Clothing — Men","Clothing — Women","Wearables"],
    "Spring": ["Home & Living","Kitchen & Dining","Beauty & Skincare"],
}

# ═══════════════════════════════════════════════════════════
# AUTH DECORATORS
# ═══════════════════════════════════════════════════════════
def login_required(f):
    @wraps(f)
    def decorated(*a,**kw):
        if "user" not in session: return redirect(url_for("login"))
        return f(*a,**kw)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*a,**kw):
        if not session.get("is_admin"): return redirect(url_for("admin_login"))
        return f(*a,**kw)
    return decorated

# ═══════════════════════════════════════════════════════════
# DB HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════
def get_user_id():
    if "user_id" in session:
        return session["user_id"]
    user = col("users").find_one({"username": session["user"]})
    if user:
        uid = user.get("seq_id")
        if uid is None:
            # Old document without seq_id — assign one now
            uid = next_seq("users")
            col("users").update_one({"_id": user["_id"]}, {"$set": {"seq_id": uid}})
        session["user_id"] = uid
        return uid
    return None

def calc_totals(subtotal, discount_pct):
    d    = round(subtotal * discount_pct / 100, 2)
    disc = subtotal - d
    gst  = round(disc * GST_RATE, 2)
    return dict(discount_amt=d, discounted=disc, gst_amt=gst, grand_total=round(disc+gst,2))

def track_recently_viewed(user_id, product_id):
    now = datetime.datetime.utcnow()
    col("recently_viewed").update_one(
        {"user_id": user_id, "product_id": product_id},
        {"$set": {"viewed_at": now}},
        upsert=True
    )
    # Keep only latest 12
    docs = list(col("recently_viewed").find(
        {"user_id": user_id}, {"_id":1}
    ).sort("viewed_at", -1).skip(12))
    if docs:
        col("recently_viewed").delete_many({"_id": {"$in": [d["_id"] for d in docs]}})

def get_cart_items(user_id):
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$lookup": {"from":"products","localField":"product_id","foreignField":"seq_id","as":"product"}},
        {"$unwind": "$product"},
        {"$project": {
            "cart_id": "$seq_id",
            "quantity": 1, "variant": 1,
            "id": "$product.seq_id",
            "name": "$product.name",
            "price": {"$ifNull": ["$price", "$product.price"]},
            "category": "$product.category",
            "icon": "$product.icon",
            "badge": "$product.badge",
            "rating": "$product.rating",
        }},
        {"$sort": {"added_at": -1}}
    ]
    return list(col("cart").aggregate(pipeline))

def get_cart_count(user_id):
    result = col("cart").aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": None, "total": {"$sum": "$quantity"}}}
    ])
    r = list(result)
    return r[0]["total"] if r else 0

# ═══════════════════════════════════════════════════════════
# SEED PRODUCTS
# ═══════════════════════════════════════════════════════════
def insert_sample_products():
    # Check if products already have seq_id (proper new-format data)
    if col("products").count_documents({"seq_id": {"$exists": True}}) > 0:
        return
    # Drop any old products without seq_id (from a previous broken seed)
    col("products").delete_many({"seq_id": {"$exists": False}})
    # Reset counter
    get_db().counters.delete_one({"_id": "products"})
    # Initialise counter
    get_db().counters.update_one({"_id":"products"},{"$setOnInsert":{"seq":0}},upsert=True)
    products = [
        ("Sony Bravia 55-inch 4K TV",64990,"Electronics","📺","Best Seller",4.5,12300,1),
        ("LG OLED 65-inch TV",189990,"Electronics","📺","Premium",4.8,5670,1),
        ("Amazon Echo Dot 5th Gen",4499,"Electronics","🔊","Best Seller",4.4,45600,0),
        ("Google Nest Hub Max",14999,"Electronics","🖥️",None,4.3,8900,0),
        ("Xiaomi Smart TV 43-inch",29999,"Electronics","📺",None,4.3,23400,0),
        ("Apple TV 4K",19900,"Electronics","📺","Premium",4.7,6780,0),
        ("Epson Projector EH-TW750",79990,"Electronics","📽️",None,4.5,2340,0),
        ("Ring Video Doorbell Pro",14999,"Electronics","🔔",None,4.4,8760,0),
        ("Philips Hue Starter Kit",12999,"Electronics","💡","Smart Home",4.6,5670,0),
        ("TP-Link Archer AX73 Router",9999,"Electronics","📡",None,4.5,4320,0),
        ("Chromecast with Google TV",6999,"Electronics","📺","Budget Pick",4.3,18900,0),
        ("Blink Outdoor Camera",5999,"Electronics","📷","Budget Pick",4.2,12300,0),
        ("MacBook Pro 14-inch M3",199900,"Laptops & Computers","💻","Premium",4.9,8760,1),
        ("Dell XPS 15 OLED",179990,"Laptops & Computers","💻","Premium",4.8,6780,1),
        ("HP Pavilion 15",52990,"Laptops & Computers","💻","Budget Pick",4.3,23400,0),
        ("Lenovo ThinkPad X1 Carbon",154990,"Laptops & Computers","💻","Premium",4.7,5670,0),
        ("ASUS ROG Zephyrus G14",119990,"Laptops & Computers","💻","Gaming",4.8,8760,1),
        ("Acer Swift 3",49990,"Laptops & Computers","💻","Budget Pick",4.4,18900,0),
        ("MSI Creator Z16",189990,"Laptops & Computers","💻","Premium",4.7,2340,0),
        ("Mac Mini M2",74900,"Laptops & Computers","🖥️",None,4.8,4320,0),
        ("Logitech MX Keys Advanced",9499,"Laptops & Computers","⌨️","Best Seller",4.7,12300,0),
        ("Samsung 27-inch QHD Monitor",34990,"Laptops & Computers","🖥️",None,4.6,8760,0),
        ("iPhone 15 Pro Max",159900,"Smartphones","📱","Best Seller",4.8,8730,1),
        ("Samsung Galaxy S24 Ultra",134999,"Smartphones","📱","Best Seller",4.8,7650,1),
        ("OnePlus 12",64999,"Smartphones","📱","Best Seller",4.7,9870,1),
        ("Google Pixel 8 Pro",106999,"Smartphones","📱",None,4.7,5670,0),
        ("Realme GT 5 Pro",39999,"Smartphones","📱","Budget Pick",4.5,12300,0),
        ("Xiaomi 14 Ultra",99999,"Smartphones","📱","Premium",4.7,4320,0),
        ("Nothing Phone 2a",23999,"Smartphones","📱","New",4.5,8760,0),
        ("iQOO 12 5G",52999,"Smartphones","📱","Gaming",4.7,6780,0),
        ("Apple Watch Ultra 2",89900,"Wearables","⌚","Premium",4.9,4320,1),
        ("Samsung Galaxy Watch 6",34999,"Wearables","⌚","Best Seller",4.7,8760,0),
        ("Garmin Fenix 7",89990,"Wearables","⌚","Sports",4.8,3210,0),
        ("Fitbit Charge 6",14999,"Wearables","⌚",None,4.5,12300,0),
        ("Noise ColorFit Pro 4",3999,"Wearables","⌚","Budget Pick",4.3,34500,0),
        ("Mi Smart Band 8",3499,"Wearables","⌚","Budget Pick",4.2,45600,0),
        ("Sony WH-1000XM5",29990,"Audio","🎧","Best Seller",4.8,12300,1),
        ("AirPods Pro 2nd Gen",24900,"Audio","🎧","Premium",4.8,9870,1),
        ("Bose QuietComfort 45",29990,"Audio","🎧","Premium",4.7,8760,0),
        ("JBL Flip 6",11999,"Audio","🔊","Best Seller",4.6,23400,0),
        ("Sennheiser HD 450BT",8990,"Audio","🎧",None,4.6,6780,0),
        ("Sony WF-1000XM5",19990,"Audio","🎧","Premium",4.7,5670,0),
        ("Marshall Stanmore III",34999,"Audio","🔊","Premium",4.7,3210,0),
        ("boAt Rockerz 558",1999,"Audio","🎧","Budget Pick",4.3,45600,0),
        ("Allen Solly Men's Formal Shirt",1299,"Clothing — Men","👔",None,4.3,18900,0),
        ("Levi's 511 Slim Jeans",3999,"Clothing — Men","👖","Best Seller",4.5,34500,0),
        ("Raymond Wool Blazer",8999,"Clothing — Men","🧥","Premium",4.6,5670,0),
        ("Nike Dri-FIT T-Shirt",2499,"Clothing — Men","👕","Sports",4.5,23400,0),
        ("Tommy Hilfiger Polo",4499,"Clothing — Men","👕","Premium",4.6,12300,0),
        ("Puma Tracksuit",3999,"Clothing — Men","🏃","Sports",4.4,18900,0),
        ("Biba Anarkali Kurta",2499,"Clothing — Women","👗","Best Seller",4.5,23400,0),
        ("Zara Floral Maxi Dress",5999,"Clothing — Women","👗","New",4.6,8760,0),
        ("Fabindia Silk Saree",7999,"Clothing — Women","🥻","Traditional",4.7,6780,0),
        ("AND Blazer Formal",4999,"Clothing — Women","🧥","Premium",4.5,5670,0),
        ("Libas Cotton Palazzo Set",1999,"Clothing — Women","👗","Budget Pick",4.3,34500,0),
        ("Nike Air Max 270",10995,"Footwear","👟","Best Seller",4.7,18900,1),
        ("Adidas Ultraboost 22",12999,"Footwear","👟","Premium",4.8,12300,1),
        ("Woodland Trekking Boots",4999,"Footwear","🥾","Best Seller",4.5,23400,0),
        ("Bata Formal Shoes",2499,"Footwear","👞","Budget Pick",4.3,34500,0),
        ("Crocs Classic Clogs",3999,"Footwear","👡","Best Seller",4.5,45600,0),
        ("Skechers Memory Foam",5999,"Footwear","👟",None,4.6,18900,0),
        ("Lakme Absolute Foundation",999,"Beauty & Skincare","💄","Best Seller",4.4,45600,0),
        ("Mamaearth Vitamin C Serum",699,"Beauty & Skincare","🧴",None,4.4,67800,0),
        ("Dot & Key Sunscreen",599,"Beauty & Skincare","☀️","Best Seller",4.5,56700,0),
        ("The Ordinary Niacinamide",699,"Beauty & Skincare","🧴",None,4.6,34500,0),
        ("Minimalist AHA BHA Toner",499,"Beauty & Skincare","🧴","Budget Pick",4.5,23400,0),
        ("SUGAR Cosmetics Lipstick",599,"Beauty & Skincare","💄",None,4.3,45600,0),
        ("Dyson Supersonic Hair Dryer",39900,"Hair Care","💇","Premium",4.8,5670,0),
        ("TRESemmé Keratin Shampoo",399,"Hair Care","🧴","Best Seller",4.4,89700,0),
        ("Philips Hair Dryer BHD356",2499,"Hair Care","💇","Budget Pick",4.4,23400,0),
        ("Indulekha Bringha Hair Oil",399,"Hair Care","🌿","Traditional",4.5,56700,0),
        ("Chanel No 5 EDP",18999,"Fragrances","🌸","Premium",4.8,3210,0),
        ("Davidoff Cool Water EDT",2999,"Fragrances","💧",None,4.5,18900,0),
        ("Fogg Black Series",549,"Fragrances","🌸","Best Seller",4.3,89700,0),
        ("Armaf Club De Nuit",2799,"Fragrances","🌸",None,4.6,12300,0),
        ("IKEA KALLAX Shelf Unit",9990,"Home & Living","🗄️",None,4.5,12300,0),
        ("Sleepwell Ortho Pro Mattress",14999,"Home & Living","🛏️","Best Seller",4.6,8760,0),
        ("Urban Ladder Floor Lamp",6999,"Home & Living","💡","Premium",4.5,5670,0),
        ("IKEA Poäng Chair",12990,"Home & Living","🪑",None,4.6,9870,0),
        ("Milton Thermosteel Bottle",599,"Kitchen & Dining","🍶","Best Seller",4.5,45600,0),
        ("Prestige Induction Cooktop",3499,"Kitchen & Dining","🍳","Budget Pick",4.4,34500,0),
        ("Hawkins Pressure Cooker",1499,"Kitchen & Dining","🍲","Best Seller",4.6,56700,0),
        ("Nespresso Vertuo Next",19999,"Kitchen & Dining","☕","Premium",4.7,8760,0),
        ("WMF Cutlery Set 30-Piece",7999,"Kitchen & Dining","🍴","Premium",4.7,5670,0),
        ("Borosil Glass Casserole Set",1899,"Kitchen & Dining","🫕",None,4.5,8760,0),
        ("LG 7kg Washing Machine",30990,"Appliances","🫧","Best Seller",4.6,12300,0),
        ("Samsung 253L Refrigerator",28990,"Appliances","❄️","Best Seller",4.5,8760,0),
        ("Dyson V15 Detect Vacuum",52900,"Appliances","🌀","Premium",4.8,4320,0),
        ("Philips Air Fryer HD9200",7999,"Appliances","🍟","Best Seller",4.5,23400,1),
        ("Hitachi 1.5 Ton Split AC",42990,"Appliances","❄️","Best Seller",4.6,9870,0),
        ("Eureka Forbes Water Purifier",15999,"Appliances","💧",None,4.4,12300,0),
        ("Bajaj Room Heater",2499,"Appliances","🔥","Budget Pick",4.3,18900,0),
        ("Tata Tea Premium 1kg",420,"Groceries & Food","🍵","Best Seller",4.5,89700,0),
        ("Amul Ghee 1kg",650,"Groceries & Food","🧈","Best Seller",4.6,67800,0),
        ("India Gate Basmati Rice 5kg",699,"Groceries & Food","🌾",None,4.5,56700,0),
        ("Cadbury Celebrations Gift Box",499,"Groceries & Food","🍫","Gift",4.7,34500,0),
        ("Haldiram's Mixture 400g",199,"Groceries & Food","🥜","Budget Pick",4.4,89700,0),
        ("Patanjali Aloe Vera Juice 1L",199,"Groceries & Food","🌿","Budget Pick",4.2,45600,0),
        ("Himalaya Ashwagandha Tablets",299,"Health & Wellness","💊",None,4.4,23400,0),
        ("Omron BP Monitor HEM-7120",2490,"Health & Wellness","🩺","Best Seller",4.6,12300,0),
        ("Apollo Life Vitamin D3",499,"Health & Wellness","💊",None,4.4,18900,0),
        ("Wellbeing Nutrition Probiotic",899,"Health & Wellness","🌿",None,4.5,8760,0),
        ("Boldfit Yoga Mat 6mm",799,"Sports & Fitness","🧘","Budget Pick",4.4,34500,0),
        ("Vector X Cricket Bat",3999,"Sports & Fitness","🏏","Best Seller",4.5,12300,0),
        ("Nivia Badminton Set",2499,"Sports & Fitness","🏸","Best Seller",4.4,18900,0),
        ("Fitkit Resistance Bands",999,"Sports & Fitness","💪","Budget Pick",4.3,23400,0),
        ("NutriTech Whey Protein 2kg",3499,"Sports & Fitness","💪","Best Seller",4.5,12300,0),
        ("Decathlon Camping Tent",8999,"Sports & Fitness","⛺",None,4.5,5670,0),
        ("Atomic Habits — James Clear",499,"Books & Stationery","📚","Best Seller",4.8,45600,1),
        ("Rich Dad Poor Dad",399,"Books & Stationery","📚","Best Seller",4.7,56700,0),
        ("Casio Scientific Calculator",895,"Books & Stationery","🔢","Best Seller",4.6,34500,0),
        ("LEGO City Police Station",8999,"Toys & Games","🏗️","Best Seller",4.7,8760,0),
        ("PlayStation DualSense Controller",6290,"Toys & Games","🎮","Best Seller",4.8,12300,0),
        ("Hasbro Monopoly Classic",1299,"Toys & Games","🎲","Best Seller",4.6,23400,0),
        ("Barbie Dreamhouse",12999,"Toys & Games","🏠","Best Seller",4.7,8760,0),
        ("UNO Card Game",349,"Toys & Games","🃏","Budget Pick",4.5,45600,0),
        ("IKEA HEMNES Bed Frame",29990,"Furniture","🛏️",None,4.6,5670,0),
        ("Urban Ladder Fabric Sofa",34999,"Furniture","🛋️","Premium",4.6,4320,0),
        ("Durian Ergonomic Office Chair",24999,"Furniture","🪑","Premium",4.7,6780,0),
        ("Pepperfry Study Table",8999,"Furniture","📚",None,4.5,8760,0),
        ("Pedigree Adult Dog Food 3kg",1299,"Pet Supplies","🐾","Best Seller",4.6,23400,0),
        ("Whiskas Cat Food 1.2kg",899,"Pet Supplies","🐱","Best Seller",4.5,18900,0),
        ("Furhaven Orthopedic Pet Bed",2499,"Pet Supplies","🛏️",None,4.5,8760,0),
        ("Michelin Pilot Sport Tyre",8999,"Automotive","🔧","Premium",4.7,5670,0),
        ("Vega Crux Helmet",1999,"Automotive","⛑️","Best Seller",4.4,23400,0),
        ("Instaauto Dashcam",4999,"Automotive","📷","Best Seller",4.5,12300,0),
        ("BBQ Grill Portable",3499,"Outdoor & Garden","🔥",None,4.4,8760,0),
        ("Solar Garden Lights 10-Pack",1299,"Outdoor & Garden","☀️","Budget Pick",4.3,18900,0),
        ("Outdoor Hammock Cotton",2999,"Outdoor & Garden","😴","Best Seller",4.5,12300,0),
        ("Pampers Pants Large 54-Count",999,"Baby & Maternity","👶","Best Seller",4.6,34500,0),
        ("Fisher-Price Baby Gym",2499,"Baby & Maternity","🧸","Best Seller",4.7,12300,0),
        ("Chicco Baby Monitor",8999,"Baby & Maternity","📷",None,4.5,5670,0),
        ("Tanishq Gold Necklace",45000,"Jewellery","💍","Premium",4.8,890,0),
        ("Malabar Gold Bangles Set",35000,"Jewellery","💍","Premium",4.7,1560,0),
        ("BlueStone Diamond Ring",15000,"Jewellery","💍",None,4.6,2340,0),
        ("Zaveri Pearls Necklace Set",1499,"Jewellery","📿","Budget Pick",4.4,8760,0),
        ("Johareez Kundan Necklace",3499,"Jewellery","📿",None,4.5,4320,0),
        ("Voylla Silver Earrings Set",999,"Jewellery","📿","Budget Pick",4.3,12300,0),
        ("Yamaha Acoustic Guitar F310",10999,"Musical Instruments","🎸","Best Seller",4.7,3210,0),
        ("Casio CT-S300 Keyboard",6999,"Musical Instruments","🎹","Best Seller",4.6,5670,0),
        ("Pearl Export Drum Kit",45000,"Musical Instruments","🥁","Premium",4.8,890,0),
        ("Harman Kardon Ukulele",3999,"Musical Instruments","🪗",None,4.5,2340,0),
        ("Banjira Tabla Set",4999,"Musical Instruments","🪘","Traditional",4.7,1890,0),
        ("Fender Squier Guitar",25000,"Musical Instruments","🎸",None,4.7,1230,0),
        ("Cajon Percussion Box",5999,"Musical Instruments","🥁",None,4.6,1560,0),
        ("Hohner Harmonica",1499,"Musical Instruments","🎵","Budget Pick",4.4,4320,0),
        ("HP LaserJet Pro Printer",18000,"Stationery & Office","🖨️","Best Seller",4.6,5670,0),
        ("Epson L3252 InkTank Printer",13000,"Stationery & Office","🖨️",None,4.5,8760,0),
        ("Wacom Drawing Tablet",7999,"Stationery & Office","✏️",None,4.6,3210,0),
        ("AmazonBasics Office Chair",6999,"Stationery & Office","🪑","Budget Pick",4.3,9870,0),
        ("Navneet A4 Ruled Reams",699,"Stationery & Office","📄","Budget Pick",4.4,19800,0),
        ("Stapler Set with Pins",399,"Stationery & Office","📌",None,4.3,23400,0),
        ("American Tourister Trolley 68cm",6999,"Travel & Luggage","🧳","Best Seller",4.6,8760,1),
        ("Safari Polycarbonate Trolley",8999,"Travel & Luggage","🧳",None,4.5,6780,0),
        ("Wildcraft Backpack 45L",3499,"Travel & Luggage","🎒",None,4.5,9870,0),
        ("Samsonite Carry-on Bag",12000,"Travel & Luggage","🧳","Premium",4.7,4320,0),
        ("Neck Pillow Memory Foam",999,"Travel & Luggage","😴","Best Seller",4.5,18900,0),
        ("Passport Holder Leather",799,"Travel & Luggage","📋",None,4.4,14300,0),
        ("Samsung Galaxy Tab S9",74999,"Electronics","📱","Best Seller",4.7,9870,1),
        ("Noise ColorFit Ultra 3",7999,"Wearables","⌚","New",4.6,12300,1),
        ("Nivea Men Face Wash",299,"Beauty & Skincare","🧴","Best Seller",4.4,56700,1),
        ("Philips Air Fryer XL HD9270",12999,"Appliances","🍟","Premium",4.6,8760,1),
        ("Fastrack Casual Watch",1995,"Wearables","⌚","Budget Pick",4.3,23400,1),
        ("boAt Airdopes 141",1299,"Audio","🎧","Best Seller",4.4,67800,1),
        ("Wildcraft Trident Backpack",2499,"Travel & Luggage","🎒","Best Seller",4.5,14500,1),
        ("Prestige Mixer Grinder 750W",4299,"Appliances","🫙","Best Seller",4.5,34500,1),
    ]
    # Real product image URLs keyed by product name (free CDN images)
    SEED_IMAGES = {
        "Sony Bravia 55-inch 4K TV": ["https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=600"],
        "LG OLED 65-inch TV": ["https://images.unsplash.com/photo-1601944179066-29786cb9d32a?w=600"],
        "Amazon Echo Dot 5th Gen": ["https://images.unsplash.com/photo-1543512214-318c7553f230?w=600"],
        "Google Nest Hub Max": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Xiaomi Smart TV 43-inch": ["https://images.unsplash.com/photo-1593359677879-a4bb92f829d1?w=600"],
        "Apple TV 4K": ["https://images.unsplash.com/photo-1585792180666-f7347c490ee2?w=600"],
        "Epson Projector EH-TW750": ["https://images.unsplash.com/photo-1478720568477-152d9b164e26?w=600"],
        "Ring Video Doorbell Pro": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Philips Hue Starter Kit": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "TP-Link Archer AX73 Router": ["https://images.unsplash.com/photo-1562408590-e32931084e23?w=600"],
        "Chromecast with Google TV": ["https://images.unsplash.com/photo-1585792180666-f7347c490ee2?w=600"],
        "Blink Outdoor Camera": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "MacBook Pro 14-inch M3": ["https://images.unsplash.com/photo-1517336714731-489689fd1ca8?w=600"],
        "Dell XPS 15 OLED": ["https://images.unsplash.com/photo-1593642632559-0c6d3fc62b89?w=600"],
        "HP Pavilion 15": ["https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=600"],
        "Lenovo ThinkPad X1 Carbon": ["https://images.unsplash.com/photo-1588872657578-7efd1f1555ed?w=600"],
        "ASUS ROG Zephyrus G14": ["https://images.unsplash.com/photo-1603302576837-37561b2e2302?w=600"],
        "Acer Swift 3": ["https://images.unsplash.com/photo-1496181133206-80ce9b88a853?w=600"],
        "MSI Creator Z16": ["https://images.unsplash.com/photo-1593642632559-0c6d3fc62b89?w=600"],
        "Mac Mini M2": ["https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=600"],
        "Logitech MX Keys Advanced": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "Samsung 27-inch QHD Monitor": ["https://images.unsplash.com/photo-1527443224154-c4a3942d3acf?w=600"],
        "iPhone 15 Pro Max": ["https://images.unsplash.com/photo-1695048133142-1a20484d2569?w=600"],
        "Samsung Galaxy S24 Ultra": ["https://images.unsplash.com/photo-1610945415295-d9bbf067e59c?w=600"],
        "OnePlus 12": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Google Pixel 8 Pro": ["https://images.unsplash.com/photo-1598327105666-5b89351aff97?w=600"],
        "Realme GT 5 Pro": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Xiaomi 14 Ultra": ["https://images.unsplash.com/photo-1570101945621-945409a6370f?w=600"],
        "Nothing Phone 2a": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "iQOO 12 5G": ["https://images.unsplash.com/photo-1511707171634-5f897ff02aa9?w=600"],
        "Apple Watch Ultra 2": ["https://images.unsplash.com/photo-1551816230-ef5deaed4a26?w=600"],
        "Samsung Galaxy Watch 6": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Garmin Fenix 7": ["https://images.unsplash.com/photo-1508685096489-7aacd43bd3b1?w=600"],
        "Fitbit Charge 6": ["https://images.unsplash.com/photo-1575311373937-040b8e1fd5b6?w=600"],
        "Noise ColorFit Pro 4": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Mi Smart Band 8": ["https://images.unsplash.com/photo-1575311373937-040b8e1fd5b6?w=600"],
        "Sony WH-1000XM5": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "AirPods Pro 2nd Gen": ["https://images.unsplash.com/photo-1603351154351-5e2d0600bb77?w=600"],
        "Bose QuietComfort 45": ["https://images.unsplash.com/photo-1546435770-a3e426bf472b?w=600"],
        "JBL Flip 6": ["https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=600"],
        "Sennheiser HD 450BT": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "Sony WF-1000XM5": ["https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=600"],
        "Marshall Stanmore III": ["https://images.unsplash.com/photo-1608043152269-423dbba4e7e1?w=600"],
        "boAt Rockerz 558": ["https://images.unsplash.com/photo-1505740420928-5e560c06d30e?w=600"],
        "Allen Solly Men's Formal Shirt": ["https://images.unsplash.com/photo-1620012253295-c15cc3e65df4?w=600"],
        "Levi's 511 Slim Jeans": ["https://images.unsplash.com/photo-1542272604-787c3835535d?w=600"],
        "Raymond Wool Blazer": ["https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=600"],
        "Nike Dri-FIT T-Shirt": ["https://images.unsplash.com/photo-1581655353564-df123a1eb820?w=600"],
        "Tommy Hilfiger Polo": ["https://images.unsplash.com/photo-1620012253295-c15cc3e65df4?w=600"],
        "Puma Tracksuit": ["https://images.unsplash.com/photo-1515886657613-9f3515b0c78f?w=600"],
        "Biba Anarkali Kurta": ["https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600"],
        "Zara Floral Maxi Dress": ["https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600"],
        "Fabindia Silk Saree": ["https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600"],
        "AND Blazer Formal": ["https://images.unsplash.com/photo-1507679799987-c73779587ccf?w=600"],
        "Libas Cotton Palazzo Set": ["https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600"],
        "Nike Air Max 270": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=600"],
        "Adidas Ultraboost 22": ["https://images.unsplash.com/photo-1608231387042-66d1773070a5?w=600"],
        "Woodland Trekking Boots": ["https://images.unsplash.com/photo-1520639888713-7851133b1ed0?w=600"],
        "Bata Formal Shoes": ["https://images.unsplash.com/photo-1533867617858-e7b97e060509?w=600"],
        "Crocs Classic Clogs": ["https://images.unsplash.com/photo-1606107557195-0e29a4b5b4aa?w=600"],
        "Skechers Memory Foam": ["https://images.unsplash.com/photo-1542291026-7eec264c27ff?w=600"],
        "Lakme Absolute Foundation": ["https://images.unsplash.com/photo-1522335789203-aabd1fc54bc9?w=600"],
        "Mamaearth Vitamin C Serum": ["https://images.unsplash.com/photo-1620916566398-39f1143ab7be?w=600"],
        "Dot & Key Sunscreen": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "The Ordinary Niacinamide": ["https://images.unsplash.com/photo-1556228453-efd6c1ff04f6?w=600"],
        "Minimalist AHA BHA Toner": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "SUGAR Cosmetics Lipstick": ["https://images.unsplash.com/photo-1631214500004-0f5f9f2ef5c4?w=600"],
        "Dyson Supersonic Hair Dryer": ["https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=600"],
        "TRESemmé Keratin Shampoo": ["https://images.unsplash.com/photo-1526947425960-945c6e72858f?w=600"],
        "Philips Hair Dryer BHD356": ["https://images.unsplash.com/photo-1522338242992-e1a54906a8da?w=600"],
        "Indulekha Bringha Hair Oil": ["https://images.unsplash.com/photo-1526947425960-945c6e72858f?w=600"],
        "Chanel No 5 EDP": ["https://images.unsplash.com/photo-1541643600914-78b084683702?w=600"],
        "Davidoff Cool Water EDT": ["https://images.unsplash.com/photo-1557170334-a9632e77c6e4?w=600"],
        "Fogg Black Series": ["https://images.unsplash.com/photo-1541643600914-78b084683702?w=600"],
        "Armaf Club De Nuit": ["https://images.unsplash.com/photo-1557170334-a9632e77c6e4?w=600"],
        "IKEA KALLAX Shelf Unit": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Sleepwell Ortho Pro Mattress": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Urban Ladder Floor Lamp": ["https://images.unsplash.com/photo-1507473885765-e6ed057f782c?w=600"],
        "IKEA Poäng Chair": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Milton Thermosteel Bottle": ["https://images.unsplash.com/photo-1602143407151-7111542de6e8?w=600"],
        "Prestige Induction Cooktop": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Hawkins Pressure Cooker": ["https://images.unsplash.com/photo-1556909114-f6e7ad7d3136?w=600"],
        "Nespresso Vertuo Next": ["https://images.unsplash.com/photo-1495474472287-4d71bcdd2085?w=600"],
        "WMF Cutlery Set 30-Piece": ["https://images.unsplash.com/photo-1556909172-54557c7e4fb7?w=600"],
        "Borosil Glass Casserole Set": ["https://images.unsplash.com/photo-1556909172-54557c7e4fb7?w=600"],
        "LG 7kg Washing Machine": ["https://images.unsplash.com/photo-1626806787461-102c1bfaaea1?w=600"],
        "Samsung 253L Refrigerator": ["https://images.unsplash.com/photo-1571175443880-49e1d25b2bc5?w=600"],
        "Dyson V15 Detect Vacuum": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Philips Air Fryer HD9200": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Hitachi 1.5 Ton Split AC": ["https://images.unsplash.com/photo-1601560496309-d12c0a994f42?w=600"],
        "Eureka Forbes Water Purifier": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Bajaj Room Heater": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Tata Tea Premium 1kg": ["https://images.unsplash.com/photo-1544787219-7f47ccb76574?w=600"],
        "Amul Ghee 1kg": ["https://images.unsplash.com/photo-1609501676725-7186f017a4b7?w=600"],
        "India Gate Basmati Rice 5kg": ["https://images.unsplash.com/photo-1586201375761-83865001e31c?w=600"],
        "Cadbury Celebrations Gift Box": ["https://images.unsplash.com/photo-1549007994-cb92caebd54b?w=600"],
        "Haldiram's Mixture 400g": ["https://images.unsplash.com/photo-1536662788222-6927ce05daea?w=600"],
        "Patanjali Aloe Vera Juice 1L": ["https://images.unsplash.com/photo-1608571423902-eed4a5ad8108?w=600"],
        "Himalaya Ashwagandha Tablets": ["https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=600"],
        "Omron BP Monitor HEM-7120": ["https://images.unsplash.com/photo-1559757148-5c350d0d3c56?w=600"],
        "Apollo Life Vitamin D3": ["https://images.unsplash.com/photo-1584308666744-24d5c474f2ae?w=600"],
        "Wellbeing Nutrition Probiotic": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "Boldfit Yoga Mat 6mm": ["https://images.unsplash.com/photo-1575052814086-f385e2e2ad1b?w=600"],
        "Vector X Cricket Bat": ["https://images.unsplash.com/photo-1531415074968-036ba1b575da?w=600"],
        "Nivia Badminton Set": ["https://images.unsplash.com/photo-1626224583764-f87db24ac4ea?w=600"],
        "Fitkit Resistance Bands": ["https://images.unsplash.com/photo-1598289431512-b97b0917affc?w=600"],
        "NutriTech Whey Protein 2kg": ["https://images.unsplash.com/photo-1593095948071-474c5cc2989d?w=600"],
        "Decathlon Camping Tent": ["https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?w=600"],
        "Atomic Habits — James Clear": ["https://images.unsplash.com/photo-1544947950-fa07a98d237f?w=600"],
        "Rich Dad Poor Dad": ["https://images.unsplash.com/photo-1592496431122-2349e0fbc666?w=600"],
        "Casio Scientific Calculator": ["https://images.unsplash.com/photo-1587145820266-a5951ee6f620?w=600"],
        "LEGO City Police Station": ["https://images.unsplash.com/photo-1587654780291-39c9404d746b?w=600"],
        "PlayStation DualSense Controller": ["https://images.unsplash.com/photo-1606144042614-b2417e99c4e3?w=600"],
        "Hasbro Monopoly Classic": ["https://images.unsplash.com/photo-1610890716171-6b1bb98ffd09?w=600"],
        "Barbie Dreamhouse": ["https://images.unsplash.com/photo-1602132468813-46e19c21b42c?w=600"],
        "UNO Card Game": ["https://images.unsplash.com/photo-1610890716171-6b1bb98ffd09?w=600"],
        "IKEA HEMNES Bed Frame": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Urban Ladder Fabric Sofa": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Durian Ergonomic Office Chair": ["https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600"],
        "Pepperfry Study Table": ["https://images.unsplash.com/photo-1555041469-a586c61ea9bc?w=600"],
        "Pedigree Adult Dog Food 3kg": ["https://images.unsplash.com/photo-1601758174493-45d0a4d3e407?w=600"],
        "Whiskas Cat Food 1.2kg": ["https://images.unsplash.com/photo-1615789591457-74a63395c990?w=600"],
        "Furhaven Orthopedic Pet Bed": ["https://images.unsplash.com/photo-1583337130417-3346a1be7dee?w=600"],
        "Michelin Pilot Sport Tyre": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Vega Crux Helmet": ["https://images.unsplash.com/photo-1558981403-c5f9899a28bc?w=600"],
        "Instaauto Dashcam": ["https://images.unsplash.com/photo-1565043666747-69f6646db940?w=600"],
        "BBQ Grill Portable": ["https://images.unsplash.com/photo-1555396273-367ea4eb4db5?w=600"],
        "Solar Garden Lights 10-Pack": ["https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600"],
        "Outdoor Hammock Cotton": ["https://images.unsplash.com/photo-1504280390367-361c6d9f38f4?w=600"],
        "Pampers Pants Large 54-Count": ["https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600"],
        "Fisher-Price Baby Gym": ["https://images.unsplash.com/photo-1515488042361-ee00e0ddd4e4?w=600"],
        "Chicco Baby Monitor": ["https://images.unsplash.com/photo-1544367567-0f2fcb009e0b?w=600"],
        "Tanishq Gold Necklace": ["https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=600"],
        "Malabar Gold Bangles Set": ["https://images.unsplash.com/photo-1573408301185-9519f94815f6?w=600"],
        "BlueStone Diamond Ring": ["https://images.unsplash.com/photo-1605100804763-247f67b3557e?w=600"],
        "Zaveri Pearls Necklace Set": ["https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=600"],
        "Johareez Kundan Necklace": ["https://images.unsplash.com/photo-1573408301185-9519f94815f6?w=600"],
        "Voylla Silver Earrings Set": ["https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=600"],
        "Yamaha Acoustic Guitar F310": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "Casio CT-S300 Keyboard": ["https://images.unsplash.com/photo-1520523839897-bd0b52f945a0?w=600"],
        "Pearl Export Drum Kit": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Harman Kardon Ukulele": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "Banjira Tabla Set": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Fender Squier Guitar": ["https://images.unsplash.com/photo-1525201548942-d8732f6617a0?w=600"],
        "Cajon Percussion Box": ["https://images.unsplash.com/photo-1519892300165-cb5542fb47c7?w=600"],
        "Hohner Harmonica": ["https://images.unsplash.com/photo-1510915361894-db8b60106cb1?w=600"],
        "HP LaserJet Pro Printer": ["https://images.unsplash.com/photo-1612815154858-60aa4c59eaa6?w=600"],
        "Epson L3252 InkTank Printer": ["https://images.unsplash.com/photo-1612815154858-60aa4c59eaa6?w=600"],
        "Wacom Drawing Tablet": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "AmazonBasics Office Chair": ["https://images.unsplash.com/photo-1586023492125-27b2c045efd7?w=600"],
        "Navneet A4 Ruled Reams": ["https://images.unsplash.com/photo-1471107340929-a87cd0f5b5f3?w=600"],
        "Stapler Set with Pins": ["https://images.unsplash.com/photo-1587829741301-dc798b83add3?w=600"],
        "American Tourister Trolley 68cm": ["https://images.unsplash.com/photo-1565026057447-bc90a3dceb87?w=600"],
        "Safari Polycarbonate Trolley": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Wildcraft Backpack 45L": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Samsonite Carry-on Bag": ["https://images.unsplash.com/photo-1565026057447-bc90a3dceb87?w=600"],
        "Neck Pillow Memory Foam": ["https://images.unsplash.com/photo-1631049307264-da0ec9d70304?w=600"],
        "Passport Holder Leather": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Samsung Galaxy Tab S9": ["https://images.unsplash.com/photo-1544244015-0df4b3ffc6b0?w=600"],
        "Noise ColorFit Ultra 3": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "Nivea Men Face Wash": ["https://images.unsplash.com/photo-1556228720-195a672e8a03?w=600"],
        "Philips Air Fryer XL HD9270": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Fastrack Casual Watch": ["https://images.unsplash.com/photo-1523275335684-37898b6baf30?w=600"],
        "boAt Airdopes 141": ["https://images.unsplash.com/photo-1590658268037-6bf12165a8df?w=600"],
        "Wildcraft Trident Backpack": ["https://images.unsplash.com/photo-1553062407-98eeb64c6a62?w=600"],
        "Prestige Mixer Grinder 750W": ["https://images.unsplash.com/photo-1585837146751-a44118595680?w=600"],
        "Plum toner for skincare | niacinamide": ["https://images.unsplash.com/photo-1556228453-efd6c1ff04f6?w=600"],
    }
    docs = []
    for i,(name,price,cat,icon,badge,rating,reviews,trending) in enumerate(products,1):
        imgs = SEED_IMAGES.get(name, [])
        docs.append({
            "seq_id":i,"name":name,"price":price,"category":cat,
            "icon":icon,"badge":badge,"rating":rating,"reviews":reviews,
            "trending":trending,"stock":100,"variants":"","images":imgs
        })
    col("products").insert_many(docs)
    get_db().counters.update_one({"_id":"products"},{"$set":{"seq":len(products)}})
    app.logger.info(f"✅ Seeded {len(products)} products into MongoDB")


# ═══════════════════════════════════════════════════════════
# ROUTES — HEALTH, AUTH
# ═══════════════════════════════════════════════════════════
@app.route("/health")
def health_check():
    try:
        get_mongo().admin.command("ping")
        return jsonify({"status":"ok","db":"mongodb connected"}), 200
    except Exception as e:
        return jsonify({"status":"error","detail":str(e)}), 500

@app.route("/", methods=["GET","POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("username","").strip()
        p = request.form.get("password","")
        fails = session.get("login_fails", 0)
        if fails >= 5:
            return render_template("login.html", error="Too many failed attempts. Please wait a few minutes.")
        user = col("users").find_one(
            {"$or": [{"username": identifier}, {"email": identifier.lower()}, {"phone": identifier}]}
        )
        if user and check_password_hash(user["password"], p):
            session.clear()
            session["user"] = user["username"]
            session["role"] = user.get("role","customer")
            # Grant super admin privileges if this is the super admin account
            if user["username"] == SUPER_ADMIN_USERNAME:
                session["is_super_admin"] = True
            uid = user.get("seq_id")
            if uid is None:
                # Old user without seq_id — assign one
                uid = next_seq("users")
                col("users").update_one({"_id": user["_id"]}, {"$set": {"seq_id": uid}})
            session["user_id"] = uid
            if user.get("role") == "admin":
                session["is_admin"] = True
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("home"))
        session["login_fails"] = fails + 1
        return render_template("login.html", error="Incorrect credentials. Try username, email, or phone.")
    return render_template("login.html", error=None)

@app.route("/register", methods=["GET","POST"])
def register():
    error = None; form_data = {}
    if request.method == "POST":
        u            = request.form.get("username","").strip()
        pw           = request.form.get("password","").strip()
        pw2          = request.form.get("password2","").strip()
        email        = request.form.get("email","").strip().lower()
        country_code = request.form.get("country_code","+91").strip()
        phone_raw    = request.form.get("phone","").strip()
        phone        = re.sub(r"[^0-9]","", phone_raw)
        account_type = request.form.get("account_type","customer")
        form_data    = {"username":u,"email":email,"country_code":country_code,
                        "phone":phone_raw,"account_type":account_type}
        if not u: error="Username is required."
        elif len(u)<3: error="Username must be at least 3 characters."
        elif not pw: error="Password is required."
        elif len(pw)<6: error="Password must be at least 6 characters."
        elif pw!=pw2: error="Passwords do not match."
        elif not email and not phone: error="Please provide at least one: email address or mobile number."
        elif email and not re.match(r'^[\w.+\-]+@[\w\-]+\.[\w.]+$',email): error="Please enter a valid email address."
        elif phone and len(phone)<7: error="Please enter a valid mobile number (min 7 digits)."
        else:
            if col("users").find_one({"username":u}): error="This username is already taken."
            elif email and col("users").find_one({"email":email}): error="An account with this email already exists."
            else:
                try:
                    phone_full = (country_code+phone) if phone else None
                    if account_type not in ("customer","admin"): account_type="customer"
                    # Admin accounts go through super-admin approval flow
                    if account_type == "admin":
                        existing_req = col("admin_requests").find_one({"email": email, "status": "pending"})
                        if existing_req:
                            error = "A pending admin request already exists for this email. Please wait for super admin approval."
                        else:
                            col("admin_requests").insert_one({
                                "name":    u,
                                "email":   email or "",
                                "phone":   phone_full or "",
                                "reason":  f"Registered via signup page as admin account.",
                                "status":  "pending",
                                "requested_at": datetime.datetime.utcnow(),
                            })
                            return render_template("register.html", error=None,
                                form_data=form_data, admin_request_sent=True)
                    else:
                        uid = next_seq("users")
                        col("users").insert_one({
                            "seq_id":uid,"username":u,
                            "password":generate_password_hash(pw),
                            "email":email or None,"phone":phone_full,
                            "country_code":country_code,"is_verified":1,
                            "role":"customer","joined":datetime.datetime.utcnow(),
                            "address":None,"city":None,"pincode":None
                        })
                        return redirect(url_for("login"))
                except Exception as ex:
                    error="Registration failed. Please try again."
    return render_template("register.html", error=error, form_data=form_data)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ═══════════════════════════════════════════════════════════
# HOME
# ═══════════════════════════════════════════════════════════
@app.route("/home")
@login_required
def home():
    uid     = get_user_id()
    season  = get_season()
    sc      = SEASON_CATS.get(season, [])
    # Category filter / search params (used when user clicks a category pill)
    active_cat = request.args.get("cat", "")
    sort_f     = request.args.get("sort_f", "rating")
    min_price  = request.args.get("min_price", "")
    max_price  = request.args.get("max_price", "")
    min_rating = request.args.get("min_rating", "")
    # Only load products that have a proper seq_id (excludes any old/corrupt data)
    trending = [doc_to_dict(p) for p in col("products").find({"trending":1,"seq_id":{"$exists":True}}).sort([("rating",-1),("reviews",-1)]).limit(12)]
    trending = [p for p in trending if p.get("id",0) > 0]
    season_picks = []
    if sc:
        season_picks = [doc_to_dict(p) for p in col("products").find({"category":{"$in":sc},"seq_id":{"$exists":True}}).sort("rating",-1).limit(8)]
        season_picks = [p for p in season_picks if p.get("id",0) > 0]
    deals_pool = [doc_to_dict(p) for p in col("products").find({"badge":{"$ne":None},"seq_id":{"$exists":True}}).sort("reviews",-1).limit(20)]
    deals_pool = [p for p in deals_pool if p.get("id",0) > 0]
    deals = random.sample(deals_pool, min(5,len(deals_pool)))
    cat_counts = {}
    for r in col("products").aggregate([{"$group":{"_id":"$category","cnt":{"$sum":1}}}]):
        cat_counts[r["_id"]] = r["cnt"]
    recently = []
    rv_docs = list(col("recently_viewed").find({"user_id":uid}).sort("viewed_at",-1).limit(8))
    if rv_docs:
        pids = [r["product_id"] for r in rv_docs]
        rp = {p["seq_id"]:doc_to_dict(p) for p in col("products").find({"seq_id":{"$in":pids}})}
        recently = [rp[pid] for pid in pids if pid in rp]

    # Build cat_products when a category pill is clicked
    cat_products = []
    if active_cat:
        filt = {"seq_id": {"$exists": True}}
        if active_cat != "All":
            filt["category"] = active_cat
        if min_price:
            filt["price"] = {"$gte": float(min_price)}
        if max_price:
            filt.setdefault("price", {})["$lte"] = float(max_price)
        if min_rating:
            filt["rating"] = {"$gte": float(min_rating)}
        sort_map = {
            "rating":     [("rating", -1)],
            "popular":    [("reviews", -1)],
            "price_asc":  [("price", 1)],
            "price_desc": [("price", -1)],
            "name":       [("name", 1)],
        }
        sort_opts = sort_map.get(sort_f, [("rating", -1)])
        cat_products = [doc_to_dict(p) for p in
                        col("products").find(filt).sort(sort_opts).limit(48)]
        cat_products = [p for p in cat_products if p.get("id", 0) > 0]

    # Build image map — include ALL product lists so every card gets its image
    all_for_imgs = trending + season_picks + deals + recently + cat_products
    product_img_map = batch_get_first_images([p["id"] for p in all_for_imgs])

    return render_template("home.html",
        username=session["user"], cart_count=get_cart_count(uid),
        season=season, trending=trending, season_picks=season_picks,
        deals=deals, recently=recently, cat_counts=cat_counts,
        categories=list(CATEGORY_META.keys()), super_cats={},
        cat_meta=CATEGORY_META,
        active_cat=active_cat, cat_products=cat_products,
        sort_f=sort_f, min_price=min_price, max_price=max_price,
        min_rating=min_rating,
        product_img_map=product_img_map)

# ═══════════════════════════════════════════════════════════
# PRODUCTS
# ═══════════════════════════════════════════════════════════
@app.route("/products")
@login_required
def products():
    uid = get_user_id()
    q         = request.args.get("q","").strip()
    active_cat= request.args.get("cat","All")
    sort_f    = request.args.get("sort","rating")
    min_price = request.args.get("min_price","")
    max_price = request.args.get("max_price","")
    min_rating= request.args.get("min_rating","")
    brands_sel= request.args.getlist("brand")
    page      = max(1, int(request.args.get("page",1)))

    filt = {"seq_id": {"$exists": True}}   # only properly-seeded products
    if q: filt["$or"] = [{"name":{"$regex":q,"$options":"i"}},{"category":{"$regex":q,"$options":"i"}}]
    if active_cat != "All": filt["category"] = active_cat
    if min_price: filt["price"] = {"$gte": float(min_price)}
    if max_price: filt.setdefault("price",{})["$lte"] = float(max_price)
    if min_rating: filt["rating"] = {"$gte": float(min_rating)}

    sort_map = {"rating":[("rating",-1)],"popular":[("reviews",-1)],"price_asc":[("price",1)],
                "price_desc":[("price",-1)],"name":[("name",1)]}

    # In "All Products" view with no filter/search: sort by category+name so
    # all products of each category are grouped together in the template.
    # Also remove the per-page limit so every product is shown.
    showing_all = (active_cat == "All" and not q and not min_price and not max_price and not min_rating)

    # For all-products view use rating sort by default so best products appear first
    sort_opts = sort_map.get(sort_f, [("rating",-1)])

    total_count = col("products").count_documents(filt)

    if showing_all:
        # Show ALL products grouped by category — no pagination
        total_pages = 1
        product_list = [doc_to_dict(p) for p in col("products").find(filt).sort(sort_opts)]
    else:
        total_pages = max(1, (total_count + PER_PAGE - 1) // PER_PAGE)
        product_list = [doc_to_dict(p) for p in col("products").find(filt).sort(sort_opts).skip((page-1)*PER_PAGE).limit(PER_PAGE)]

    wish_ids = set()
    if uid:
        wish_ids = {w["product_id"] for w in col("wishlist").find({"user_id":uid},{"product_id":1})}

    pr_result = list(col("products").aggregate([{"$match":filt},{"$group":{"_id":None,"mn":{"$min":"$price"},"mx":{"$max":"$price"}}}]))
    price_range = {"min": pr_result[0]["mn"], "max": pr_result[0]["mx"]} if pr_result else {"min":0,"max":200000}

    return render_template("products.html",
        products=product_list, active_cat=active_cat, search_q=q,
        sort_f=sort_f, categories=list(CATEGORY_META.keys()), cat_meta=CATEGORY_META,
        page=page, total_pages=total_pages, total_count=total_count,
        min_price=min_price, max_price=max_price, min_rating=min_rating,
        brands_sel=brands_sel, all_brands=[], price_range=price_range,
        wish_ids=wish_ids, username=session["user"], cart_count=get_cart_count(uid),
        super_cats={}, cat_counts={}, showing_all=showing_all,
        product_img_map=batch_get_first_images([p["id"] for p in product_list]))

# ═══════════════════════════════════════════════════════════
# PRODUCT DETAIL
# ═══════════════════════════════════════════════════════════
@app.route("/category/<path:cat_name>")
@login_required
def category_page(cat_name):
    """Show products filtered by a specific category."""
    return redirect(url_for("products", cat=cat_name))


@app.route("/product/<int:pid>", methods=["GET","POST"])
@login_required
def product_detail(pid):
    uid = get_user_id()
    p   = col("products").find_one({"seq_id": pid})
    if not p: return redirect(url_for("products"))
    p   = doc_to_dict(p)
    track_recently_viewed(uid, pid)

    review_msg = None
    if request.method=="POST" and "rating" in request.form:
        try:
            rv = int(request.form["rating"])
            col("reviews").update_one(
                {"product_id":pid,"user_id":uid},
                {"$set":{"rating":rv,"title":request.form.get("review_title","")[:120],
                          "body":request.form.get("review_body","")[:1000],
                          "created_at":datetime.datetime.utcnow()}},
                upsert=True
            )
            agg = list(col("reviews").aggregate([
                {"$match":{"product_id":pid}},
                {"$group":{"_id":None,"avg":{"$avg":"$rating"},"cnt":{"$sum":1}}}
            ]))
            if agg:
                col("products").update_one({"seq_id":pid},
                    {"$set":{"rating":round(agg[0]["avg"],1),"reviews":agg[0]["cnt"]}})
                p["rating"]  = round(agg[0]["avg"],1)
                p["reviews"] = agg[0]["cnt"]
            review_msg = "✅ Review submitted! Thank you."
        except Exception as e:
            review_msg = f"❌ Could not save review: {e}"

    related   = [doc_to_dict(r) for r in col("products").find({"category":p["category"],"seq_id":{"$ne":pid}}).sort("rating",-1).limit(6)]
    user_revs = list(col("reviews").aggregate([
        {"$match":{"product_id":pid}},
        {"$lookup":{"from":"users","localField":"user_id","foreignField":"seq_id","as":"u"}},
        {"$unwind":{"path":"$u","preserveNullAndEmptyArrays":True}},
        {"$project":{"rating":1,"title":1,"body":1,"created_at":1,"username":"$u.username"}},
        {"$sort":{"created_at":-1}},{"$limit":20}
    ]))
    rating_dist = {}
    for i in range(5,0,-1):
        rating_dist[i] = col("reviews").count_documents({"product_id":pid,"rating":i})
    total_rev_count  = sum(rating_dist.values())
    already_reviewed = col("reviews").find_one({"product_id":pid,"user_id":uid}) is not None
    in_wishlist      = col("wishlist").find_one({"user_id":uid,"product_id":pid}) is not None

    discount_pct   = 10 + (pid % 4) * 10
    original_price = round(p["price"] / (1 - discount_pct/100))
    variants       = get_customization_options(p["category"], p["name"])
    features       = _get_product_features(p["category"], p["name"])
    product_images = get_product_images(pid)

    spec_prices=p.get("spec_prices") or {}
    return render_template("product_detail.html",
        p=p, related=related, user_revs=user_revs, rating_dist=rating_dist,
        total_rev_count=total_rev_count, already_reviewed=already_reviewed,
        discount_pct=discount_pct, original_price=original_price,
        variants=variants, features=features, in_wishlist=in_wishlist,
        review_msg=review_msg, product_images=product_images,
        spec_prices=spec_prices,
        username=session["user"], cart_count=get_cart_count(uid),
        get_img=get_product_image)

# ═══════════════════════════════════════════════════════════
# CART
# ═══════════════════════════════════════════════════════════
@app.route("/add_to_cart/<int:pid>", methods=["GET","POST"])
@login_required
def add_to_cart(pid):
    uid = get_user_id()
    qty = int(request.form.get("qty",1))
    variant = request.form.get("variant","")
    # Resolve price: check spec_prices for selected choices
    prod = col("products").find_one({"seq_id":pid})
    resolved_price = prod["price"] if prod else 0
    if prod and prod.get("spec_prices"):
        sp = prod["spec_prices"]
        # Parse variant string "Label: Choice | Label2: Choice2"
        sel = {}
        for part in variant.split(" | "):
            if ":" in part:
                lbl,val = part.split(":",1)
                sel[lbl.strip().lower()] = val.strip()
        # Get customization opts to map label -> key
        copts = get_customization_options(prod.get("category",""), prod.get("name",""))
        for opt in copts:
            chosen = sel.get(opt["label"].lower())
            if chosen and opt["key"] in sp and chosen in sp[opt["key"]]:
                resolved_price = sp[opt["key"]][chosen]
                break
    existing = col("cart").find_one({"user_id":uid,"product_id":pid})
    if existing:
        col("cart").update_one({"_id":existing["_id"]},
            {"$inc":{"quantity":qty},"$set":{"variant":variant,"price":resolved_price}})
    else:
        seq = next_seq("cart")
        col("cart").insert_one({
            "seq_id":seq,"user_id":uid,"product_id":pid,
            "quantity":qty,"variant":variant,"price":resolved_price,
            "added_at":datetime.datetime.utcnow()
        })
    return redirect(request.referrer or url_for("cart"))

@app.route("/remove_from_cart/<int:cart_id>")
@login_required
def remove_from_cart(cart_id):
    uid = get_user_id()
    col("cart").delete_one({"seq_id":cart_id,"user_id":uid})
    return redirect(url_for("cart"))

@app.route("/update_cart/<int:cart_id>", methods=["POST"])
@login_required
def update_cart(cart_id):
    uid = get_user_id()
    qty = int(request.form.get("qty",1))
    if qty <= 0:
        col("cart").delete_one({"seq_id":cart_id,"user_id":uid})
    else:
        col("cart").update_one({"seq_id":cart_id,"user_id":uid},{"$set":{"quantity":min(qty,10)}})
    return redirect(url_for("cart"))

@app.route("/cart", methods=["GET","POST"])
@login_required
def cart():
    uid = get_user_id()
    promo_msg=None; promo_error=None
    if request.method=="POST":
        code = request.form.get("promo_code","").strip().upper()
        if code in PROMO_CODES:
            session["promo_code"]    = code
            session["discount_pct"]  = PROMO_CODES[code]
            promo_msg = f"Promo code {code} applied!"
        else:
            promo_error = "Invalid promo code."
    items    = get_cart_items(uid)
    subtotal = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct",0)
    applied_code = session.get("promo_code","")
    t = calc_totals(subtotal, discount_pct)
    return render_template("cart.html", items=items, subtotal=subtotal,
        discount_pct=discount_pct, applied_code=applied_code,
        promo_msg=promo_msg, promo_error=promo_error,
        available_promos=PROMO_CODES,
        username=session["user"], cart_count=get_cart_count(uid), **t)

@app.route("/remove_promo")
@login_required
def remove_promo():
    session.pop("promo_code",None); session.pop("discount_pct",None)
    return redirect(url_for("cart"))

# ═══════════════════════════════════════════════════════════
# WISHLIST
# ═══════════════════════════════════════════════════════════
@app.route("/wishlist")
@login_required
def wishlist():
    uid = get_user_id()
    pipeline = [
        {"$match":{"user_id":uid}},
        {"$lookup":{"from":"products","localField":"product_id","foreignField":"seq_id","as":"product"}},
        {"$unwind":"$product"},
        {"$replaceRoot":{"newRoot":"$product"}},
        {"$sort":{"rating":-1}}
    ]
    items = [doc_to_dict(p) for p in col("wishlist").aggregate(pipeline)]
    return render_template("wishlist.html", items=items,
        username=session["user"], cart_count=get_cart_count(uid),
        wishlist_count=len(items), get_img=get_product_image)

@app.route("/toggle_wishlist/<int:pid>")
@login_required
def toggle_wishlist(pid):
    uid = get_user_id()
    if col("wishlist").find_one({"user_id":uid,"product_id":pid}):
        col("wishlist").delete_one({"user_id":uid,"product_id":pid})
    else:
        col("wishlist").insert_one({"user_id":uid,"product_id":pid,"added_at":datetime.datetime.utcnow()})
    return redirect(request.referrer or url_for("wishlist"))

# ═══════════════════════════════════════════════════════════
# CHECKOUT & ORDERS
# ═══════════════════════════════════════════════════════════
@app.route("/checkout", methods=["GET","POST"])
@login_required
def checkout():
    uid   = get_user_id()
    items = get_cart_items(uid)
    if not items: return redirect(url_for("cart"))
    user  = col("users").find_one({"seq_id":uid})
    subtotal     = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct",0)
    applied_code = session.get("promo_code","")
    t = calc_totals(subtotal,discount_pct)
    return render_template("checkout.html", items=items, subtotal=subtotal,
        discount_pct=discount_pct, applied_code=applied_code,
        amount_paise=int(t["grand_total"]*100),
        user=doc_to_dict(user) if user else {}, username=session["user"],
        stripe_pub_key=STRIPE_PUBLISHABLE_KEY,
        cart_count=get_cart_count(uid), **t)

@app.route("/checkout/stripe-pay", methods=["POST"])
@login_required
def stripe_pay():
    """Handle Stripe card payment via API."""
    import json
    try:
        import stripe as stripe_lib
        stripe_lib.api_key = STRIPE_SECRET_KEY
    except ImportError:
        return jsonify({"error": "Stripe not installed. Run: pip install stripe"}), 500

    uid   = get_user_id()
    items = get_cart_items(uid)
    if not items:
        return jsonify({"error": "Cart is empty"}), 400

    subtotal     = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct", 0)
    t = calc_totals(subtotal, discount_pct)
    amount_paise = int(t["grand_total"] * 100)

    data = request.get_json(silent=True) or {}
    payment_method_id = data.get("payment_method_id","")
    name    = data.get("name","")
    address = data.get("address","")
    city    = data.get("city","")
    pin     = data.get("pin","")

    # Save address to user profile
    col("users").update_one(
        {"seq_id": uid},
        {"$set": {"address": address, "city": city, "pincode": pin}}
    )

    try:
        intent = stripe_lib.PaymentIntent.create(
            amount=amount_paise,
            currency="inr",
            payment_method=payment_method_id,
            confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            description=f"Nexacart order for {name}",
            receipt_email=data.get("email",""),
        )
        if intent.status == "requires_action":
            return jsonify({
                "requires_action": True,
                "payment_intent_client_secret": intent.client_secret
            })
        elif intent.status == "succeeded":
            # Create order
            _create_order(uid, t, subtotal, address, city, pin, "Stripe/Card", intent.id)
            return jsonify({"success": True})
        else:
            return jsonify({"error": f"Payment status: {intent.status}"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400

def _create_order(uid, t, subtotal, address, city, pin, method, txn_id=""):
    """Helper to create an order and clear cart."""
    items    = get_cart_items(uid)
    ref      = f"NXC-{random.randint(100000,999999)}"
    oid      = next_seq("orders")
    promo    = session.get("promo_code","")
    col("orders").insert_one({
        "seq_id":oid,"user_id":uid,"order_ref":ref,
        "total":t["grand_total"],"subtotal":subtotal,
        "discount_amt":t["discount_amt"],"gst_amt":t["gst_amt"],
        "promo_code":promo,"status":"Confirmed",
        "address":address,"city":city,"pincode":pin,
        "payment_method":method,"payment_txn_id":txn_id,
        "created_at":datetime.datetime.utcnow()
    })
    for i in items:
        col("order_items").insert_one({
            "order_id":oid,"product_id":i["id"],"name":i["name"],
            "price":i["price"],"quantity":i["quantity"],"variant":i.get("variant","")
        })
    col("cart").delete_many({"user_id":uid})
    session.pop("promo_code",None); session.pop("discount_pct",None)
    return ref


@app.route("/payment_success", methods=["GET","POST"])
@login_required
def payment_success():
    """Called after demo-mode payment or redirect from Stripe."""
    uid          = get_user_id()
    items        = get_cart_items(uid)
    if not items:
        # Order may already be created by stripe_pay — just show success
        return render_template("success.html", username=session["user"],
            order_ref="NXC-" + str(random.randint(100000,999999)),
            total=0, items=[], promo="", cart_count=0)
    subtotal     = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct", 0)
    applied_code = session.get("promo_code","")
    t   = calc_totals(subtotal, discount_pct)
    user = col("users").find_one({"seq_id":uid})
    ref = _create_order(uid, t, subtotal,
        user.get("address","") if user else "",
        user.get("city","")    if user else "",
        user.get("pincode","") if user else "",
        "Stripe/Card", "")
    return render_template("success.html", username=session["user"],
        order_ref=ref, total=t["grand_total"], items=items,
        promo=applied_code, cart_count=0)

@app.route("/save-shipping-and-upi", methods=["POST"])
@login_required
def save_shipping_and_upi():
    """Save shipping details from checkout then redirect to UPI payment page."""
    uid = get_user_id()
    address = request.form.get("address","").strip()
    city    = request.form.get("city","").strip()
    pin     = request.form.get("pin","").strip()
    # Save to user profile for convenience
    col("users").update_one(
        {"seq_id": uid},
        {"$set": {"address": address, "city": city, "pincode": pin}}
    )
    return redirect(url_for("upi_payment"))


@app.route("/upi-payment", methods=["GET","POST"])
@login_required
def upi_payment():
    uid   = get_user_id()
    items = get_cart_items(uid)
    if not items: return redirect(url_for("cart"))
    subtotal     = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct",0)
    applied_code = session.get("promo_code","")
    t = calc_totals(subtotal,discount_pct)
    return render_template("upi_payment.html", items=items, subtotal=subtotal,
        discount_pct=discount_pct, applied_code=applied_code,
        merchant_upi=os.environ.get("MERCHANT_UPI_ID","nexacart@upi"),
        merchant_name=os.environ.get("MERCHANT_NAME","Nexacart"),
        username=session["user"], cart_count=get_cart_count(uid), **t)

@app.route("/upi-verify", methods=["POST"])
@login_required
def upi_verify():
    uid      = get_user_id()
    upi_txn  = request.form.get("upi_txn_id","").strip()
    upi_app  = request.form.get("upi_app","UPI")
    items    = get_cart_items(uid)
    if not items: return redirect(url_for("cart"))
    subtotal     = sum(i["price"]*i["quantity"] for i in items)
    discount_pct = session.get("discount_pct",0)
    applied_code = session.get("promo_code","")
    t   = calc_totals(subtotal,discount_pct)
    ref = f"NXC-{random.randint(100000,999999)}"
    user = col("users").find_one({"seq_id":uid})
    oid  = next_seq("orders")
    col("orders").insert_one({
        "seq_id":oid,"user_id":uid,"order_ref":ref,
        "total":t["grand_total"],"subtotal":subtotal,
        "discount_amt":t["discount_amt"],"gst_amt":t["gst_amt"],
        "promo_code":applied_code,"status":"Confirmed",
        "address":user.get("address","") if user else "",
        "city":user.get("city","") if user else "",
        "pincode":user.get("pincode","") if user else "",
        "payment_method":upi_app,"payment_txn_id":upi_txn,
        "created_at":datetime.datetime.utcnow()
    })
    for i in items:
        col("order_items").insert_one({
            "order_id":oid,"product_id":i["id"],"name":i["name"],
            "price":i["price"],"quantity":i["quantity"],"variant":i.get("variant","")
        })
    col("cart").delete_many({"user_id":uid})
    session.pop("promo_code",None); session.pop("discount_pct",None)
    return render_template("success.html", username=session["user"],
        order_ref=ref, total=t["grand_total"], items=items,
        promo=applied_code, payment_method=upi_app, cart_count=0)

@app.route("/orders")
@login_required
def orders():
    uid = get_user_id()
    order_list = list(col("orders").find({"user_id":uid}).sort("created_at",-1))
    orders_with_items = []
    for o in order_list:
        its = list(col("order_items").find({"order_id":o["seq_id"]}))
        od = dict(o); od["id"]=o["seq_id"]; od["_id"]=str(o["_id"])
        od["created_at"] = o.get("created_at","")
        if hasattr(od["created_at"],"strftime"):
            od["created_at"] = od["created_at"].strftime("%Y-%m-%d %H:%M:%S")
        orders_with_items.append({"order":od,"items":its})
    return render_template("orders.html", orders=orders_with_items,
        username=session["user"], cart_count=get_cart_count(uid))

# ═══════════════════════════════════════════════════════════
# PROFILE
# ═══════════════════════════════════════════════════════════

def upload_profile_picture(file_obj, user_id):
    """Store a user profile picture in MongoDB GridFS."""
    try:
        fs = get_fs()
        filename = f"profile_{user_id}"
        for existing in fs.find({"filename": filename}):
            fs.delete(existing._id)
        raw = file_obj.read()
        img = PILImage.open(io.BytesIO(raw)).convert("RGB")
        img.thumbnail((300, 300), PILImage.LANCZOS)
        w, h = img.size
        m = min(w, h)
        img = img.crop(((w-m)//2, (h-m)//2, (w+m)//2, (h+m)//2))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        buf.seek(0)
        fs.put(buf, filename=filename, content_type="image/jpeg", user_id=user_id)
        return filename
    except Exception as e:
        app.logger.error(f"Profile picture upload failed: {e}")
        return None

@app.route("/profile-picture/<int:user_id>")
def serve_profile_picture(user_id):
    from flask import send_file, Response
    try:
        fs = get_fs()
        f = fs.find_one({"filename": f"profile_{user_id}"})
        if f:
            buf = io.BytesIO(f.read())
            buf.seek(0)
            return send_file(buf, mimetype="image/jpeg", max_age=3600)
    except Exception as e:
        app.logger.error(f"Profile picture serve error: {e}")
    import base64
    px = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    return Response(px, mimetype="image/png")

@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    uid=get_user_id(); msg=None
    if request.method=="POST":
        action=request.form.get("action")
        if action=="upload_avatar":
            f = request.files.get("avatar")
            if f and f.filename:
                ext = f.filename.rsplit(".",1)[-1].lower()
                if ext in ("jpg","jpeg","png","webp","gif"):
                    key = upload_profile_picture(f.stream, uid)
                    if key:
                        col("users").update_one({"seq_id": uid}, {"$set": {"avatar": key}})
                        msg = "Profile picture updated!"
                    else:
                        msg = "error:Could not save profile picture."
                else:
                    msg = "error:Please upload a JPG, PNG or WebP image."
        if action=="update_info":
            col("users").update_one({"seq_id":uid},{"$set":{
                "email":request.form.get("email",""),
                "phone":request.form.get("phone",""),
                "address":request.form.get("address",""),
                "city":request.form.get("city",""),
                "pincode":request.form.get("pincode","")
            }}); msg="Profile updated!"
        elif action=="change_password":
            user=col("users").find_one({"seq_id":uid})
            old,new,conf=request.form.get("old_password",""),request.form.get("new_password",""),request.form.get("confirm_password","")
            if not check_password_hash(user["password"],old): msg="error:Current password incorrect."
            elif new!=conf: msg="error:Passwords do not match."
            elif len(new)<6: msg="error:Min 6 characters."
            else:
                col("users").update_one({"seq_id":uid},{"$set":{"password":generate_password_hash(new)}})
                msg="Password changed!"
    user=col("users").find_one({"seq_id":uid})
    if not user: session.clear(); return redirect(url_for("login"))
    user=doc_to_dict(user)
    order_count=col("orders").count_documents({"user_id":uid})
    wish_count=col("wishlist").count_documents({"user_id":uid})
    return render_template("profile.html",user=user,msg=msg,
        username=session["user"],cart_count=get_cart_count(uid),
        order_count=order_count,wish_count=wish_count)

# ═══════════════════════════════════════════════════════════
# ANCILLARY PAGES
# ═══════════════════════════════════════════════════════════
@app.route("/rewards")
@login_required
def rewards():
    uid=get_user_id()
    agg=list(col("orders").aggregate([{"$match":{"user_id":uid}},{"$group":{"_id":None,"total":{"$sum":"$total"}}}]))
    total_spent=agg[0]["total"] if agg else 0
    reward_points=int(total_spent/100)
    return render_template("rewards.html",reward_points=reward_points,total_spent=total_spent,
        promo_codes=PROMO_CODES,username=session["user"],cart_count=get_cart_count(uid))

@app.route("/gift-cards")
@login_required
def gift_cards():
    uid=get_user_id()
    return render_template("gift_cards.html",username=session["user"],cart_count=get_cart_count(uid))

@app.route("/notifications")
@login_required
def notifications():
    uid=get_user_id()
    notifs=[{"icon":"🛍️","title":"New arrivals in Electronics","time":"2h ago","read":False},
            {"icon":"🏷️","title":"Use SAVE10 for 10% off","time":"1d ago","read":True}]
    return render_template("notifications.html",notifications=notifs,
        username=session["user"],cart_count=get_cart_count(uid))

@app.route("/help")
@login_required
def help_page():
    uid=get_user_id()
    faqs=[{"q":"How do I track my order?","a":"Go to Orders page to see your order status."},
          {"q":"What is the return policy?","a":"30-day easy returns on all products."},
          {"q":"How do I apply a promo code?","a":"Enter the code in your cart before checkout."}]
    return render_template("help.html",faqs=faqs,username=session["user"],cart_count=get_cart_count(uid))

@app.route("/about")
@login_required
def about():
    uid=get_user_id()
    return render_template("static_page.html",page_title="About Us",
        content="<p>Nexacart is your everyday marketplace.</p>",
        username=session["user"],cart_count=get_cart_count(uid))

@app.route("/careers")
@login_required
def careers():
    uid=get_user_id()
    return render_template("static_page.html",page_title="Careers",
        content="<p>We're hiring! Send your CV to careers@nexacart.com</p>",
        username=session["user"],cart_count=get_cart_count(uid))

@app.route("/terms")
@login_required
def terms():
    uid=get_user_id()
    return render_template("static_page.html",page_title="Terms & Conditions",
        content="<p>By using Nexacart you agree to our terms of service.</p>",
        username=session["user"],cart_count=get_cart_count(uid))

@app.route("/privacy")
@login_required
def privacy():
    uid=get_user_id()
    return render_template("static_page.html",page_title="Privacy Policy",
        content="<p>We respect your privacy and protect your data.</p>",
        username=session["user"],cart_count=get_cart_count(uid))

@app.route("/cancellation")
@login_required
def cancellation():
    uid=get_user_id()
    return render_template("static_page.html",page_title="Cancellation Policy",
        content="<p>Orders can be cancelled before shipping. Contact support for help.</p>",
        username=session["user"],cart_count=get_cart_count(uid))

# ═══════════════════════════════════════════════════════════
# PASSWORD RESET
# ═══════════════════════════════════════════════════════════
@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    msg=None
    if request.method=="POST":
        identifier=request.form.get("identifier","").strip()
        user=col("users").find_one({"$or":[{"username":identifier},{"email":identifier},{"phone":identifier}]})
        if user:
            token=secrets.token_urlsafe(32)
            expires=datetime.datetime.utcnow()+datetime.timedelta(hours=2)
            col("password_resets").delete_many({"user_id":user["seq_id"]})
            col("password_resets").insert_one({"user_id":user["seq_id"],"token":token,"expires_at":expires,"used":0})
            reset_url=url_for("reset_password",token=token,_external=True)
            msg=f"success:Reset link (demo): {reset_url}"
        else:
            msg="error:No account found with that username, email, or phone."
    return render_template("forgot_password.html",msg=msg)

@app.route("/reset-password/<token>", methods=["GET","POST"])
def reset_password(token):
    reset=col("password_resets").find_one({"token":token,"used":0})
    valid=reset and reset["expires_at"]>datetime.datetime.utcnow()
    msg=None
    if request.method=="POST" and valid:
        pw=request.form.get("password",""); conf=request.form.get("confirm_password","")
        if len(pw)<6: msg="error:Min 6 characters."
        elif pw!=conf: msg="error:Passwords do not match."
        else:
            col("users").update_one({"seq_id":reset["user_id"]},{"$set":{"password":generate_password_hash(pw)}})
            col("password_resets").update_one({"token":token},{"$set":{"used":1}})
            msg="success:Password reset! You can now sign in."
    return render_template("reset_password.html",valid=valid,token=token,msg=msg)

# ═══════════════════════════════════════════════════════════
# SHARE
# ═══════════════════════════════════════════════════════════
@app.route("/share/<int:pid>")
def share_product(pid):
    p=col("products").find_one({"seq_id":pid})
    if not p: return redirect(url_for("login"))
    p=doc_to_dict(p)
    product_url=url_for("product_detail",pid=pid,_external=True)
    product_images=get_product_images(pid)
    # Build absolute URL for the first image (used in OG meta tags)
    if product_images:
        first = product_images[0]
        if first.startswith("http"):
            share_img_url = first
        elif first.startswith("img/"):
            pts = first.split("/")
            share_img_url = url_for("serve_product_image",
                product_id=int(pts[1]), slot=int(pts[2]), _external=True)
        else:
            share_img_url = url_for("static", filename=first, _external=True)
    else:
        share_img_url = ""
    img_url = share_img_url
    direct_url=url_for("product_detail",pid=pid,_external=True)
    return render_template("share_preview.html",p=p,product_url=product_url,
        img_url=img_url,direct_url=direct_url,
        get_img=get_product_image,product_images=product_images)

# ═══════════════════════════════════════════════════════════
# IMAGE SERVING — MongoDB GridFS
# ═══════════════════════════════════════════════════════════
@app.route("/img/<int:product_id>/<int:slot>")
def serve_product_image(product_id, slot):
    """Serve a product image stored in MongoDB GridFS."""
    from flask import Response
    try:
        fs = get_fs()
        filename = f"product_{product_id}_slot_{slot}"
        f = fs.find_one({"filename": filename})
        if f:
            data = f.read()
            response = Response(data, mimetype="image/jpeg")
            response.headers["Cache-Control"] = "public, max-age=86400"
            response.headers["Content-Length"] = str(len(data))
            return response
    except Exception as e:
        app.logger.error(f"GridFS serve error: {e}")
    # Fallback: local static file
    local_folder = os.path.join(os.path.dirname(__file__), "static", "product_images", str(product_id))
    for ext in ("jpg","jpeg","png","webp"):
        path = os.path.join(local_folder, f"{slot}.{ext}")
        if os.path.exists(path):
            with open(path, "rb") as fh:
                data = fh.read()
            return Response(data,
                            mimetype=f"image/{'jpeg' if ext in ('jpg','jpeg') else ext}",
                            headers={"Cache-Control": "public, max-age=86400",
                                     "Content-Length": str(len(data))})
    # Final fallback: 1x1 transparent PNG
    import base64
    px = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    return Response(px, mimetype="image/png")


# ═══════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    """Two modes: super-admin login (username+password) and new-admin request."""
    if request.method == "POST":
        mode = request.form.get("mode", "request")  # "super" or "request"

        if mode == "super":
            # ── Super admin login ──────────────────────────────────
            uname = request.form.get("username", "").strip()
            pwd   = request.form.get("password", "").strip()
            if uname == SUPER_ADMIN_USERNAME and pwd == SUPER_ADMIN_PASSWORD:
                session["is_admin"]       = True
                session["is_super_admin"] = True
                session["admin_name"]     = uname
                return redirect(url_for("admin_dashboard"))
            return render_template("admin_login.html", error="Invalid super admin credentials.", tab="super")

        else:
            # ── New admin access request ───────────────────────────
            name   = request.form.get("req_name", "").strip()
            email  = request.form.get("req_email", "").strip()
            phone  = request.form.get("req_phone", "").strip()
            reason = request.form.get("req_reason", "").strip()
            secret = request.form.get("req_secret", "").strip()
            if not all([name, email, reason, secret]):
                return render_template("admin_login.html",
                    error="Please fill in all required fields.", tab="request")
            if secret != ADMIN_SECRET:
                return render_template("admin_login.html",
                    error="Invalid admin secret key.", tab="request")
            # Check if a pending request already exists for this email
            existing = col("admin_requests").find_one({"email": email, "status": "pending"})
            if existing:
                return render_template("admin_login.html",
                    error="A pending request already exists for this email. Please wait for super admin approval.",
                    tab="request")
            col("admin_requests").insert_one({
                "name":       name,
                "email":      email,
                "phone":      phone,
                "reason":     reason,
                "status":     "pending",
                "requested_at": datetime.datetime.utcnow(),
            })
            return render_template("admin_login.html",
                success="Your request has been sent to the super admin for approval. You will be notified once approved.",
                tab="request")

    return render_template("admin_login.html", error=None, tab="super")


@app.route("/admin/requests")
@admin_required
def admin_requests_page():
    """Super-admin only: view and manage new admin access requests."""
    if not session.get("is_super_admin"):
        return redirect(url_for("admin_dashboard"))
    raw = list(col("admin_requests").find({"status": "pending"}).sort("requested_at", -1))
    requests_list = []
    for r in raw:
        requested_at = r.get("requested_at")
        if hasattr(requested_at, "strftime"):
            requested_at = requested_at.strftime("%d %b %Y, %I:%M %p")
        requests_list.append({
            "id":           str(r["_id"]),
            "name":         r.get("name", "Unknown"),
            "email":        r.get("email", ""),
            "phone":        r.get("phone", ""),
            "reason":       r.get("reason", ""),
            "status":       r.get("status", "pending"),
            "requested_at": requested_at or "—",
        })
    return render_template("admin_requests.html", requests=requests_list)


@app.route("/admin/requests/<req_id>/action", methods=["POST"])
@admin_required
def admin_request_action(req_id):
    """Accept or decline an admin access request."""
    if not session.get("is_super_admin"):
        return jsonify({"ok": False, "msg": "Unauthorized"}), 403
    from bson import ObjectId
    action = request.form.get("action", "")  # "accept" or "decline"
    try:
        oid = ObjectId(req_id)
    except Exception:
        return jsonify({"ok": False, "msg": "Invalid request ID"}), 400
    req_doc = col("admin_requests").find_one({"_id": oid})
    if not req_doc:
        return jsonify({"ok": False, "msg": "Request not found"}), 404
    if action == "accept":
        col("admin_requests").update_one({"_id": oid},
            {"$set": {"status": "accepted", "actioned_at": datetime.datetime.utcnow()}})
        name_label = req_doc.get("name") or req_doc.get("username") or "Unknown"
        return jsonify({"ok": True, "msg": f"✅ Request from {name_label} has been accepted. They can now log in using the admin secret key."})
    elif action == "decline":
        col("admin_requests").update_one({"_id": oid},
            {"$set": {"status": "declined", "actioned_at": datetime.datetime.utcnow()}})
        name_label = req_doc.get("name") or req_doc.get("username") or "Unknown"
        return jsonify({"ok": True, "msg": f"❌ Request from {name_label} has been declined."})
    return jsonify({"ok": False, "msg": "Unknown action"}), 400


@app.route("/admin/requests/count")
@admin_required
def admin_requests_count():
    """Returns pending request count for sidebar badge (super admin only)."""
    if not session.get("is_super_admin"):
        return jsonify({"count": 0})
    count = col("admin_requests").count_documents({"status": "pending"})
    return jsonify({"count": count})

@app.route("/admin/profile")
@admin_required
def admin_profile():
    """Admin profile page — shows admin details and activity summary."""
    stats = {
        "products": col("products").count_documents({}),
        "orders":   col("orders").count_documents({}),
        "users":    col("users").count_documents({"is_fake_reviewer": {"$ne": True}}),
        "reviews":  col("reviews").count_documents({}),
    }
    rev_agg = list(col("orders").aggregate([{"$group":{"_id":None,"total":{"$sum":"$total"}}}]))
    stats["revenue"] = rev_agg[0]["total"] if rev_agg else 0
    recent_actions = list(col("orders").find().sort("created_at",-1).limit(5))
    for o in recent_actions:
        o["id"] = o.get("seq_id", str(o["_id"]))
        o["_id"] = str(o["_id"])
        if hasattr(o.get("created_at",""), "strftime"):
            o["created_at"] = o["created_at"].strftime("%Y-%m-%d %H:%M")
    return render_template("admin_profile.html", stats=stats, recent_actions=recent_actions,
                           admin_name="Nexacart Admin")



@app.route("/admin/profile-picture/<int:user_id>", methods=["POST"])
@admin_required
def admin_upload_profile_picture(user_id):
    f = request.files.get("avatar")
    if f and f.filename:
        ext = f.filename.rsplit(".",1)[-1].lower()
        if ext in ("jpg","jpeg","png","webp","gif"):
            key = upload_profile_picture(f.stream, user_id)
            if key:
                col("users").update_one({"seq_id": user_id}, {"$set": {"avatar": key}})
    return redirect(url_for("admin_profile"))

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    stats={
        "users":  col("users").count_documents({"is_fake_reviewer": {"$ne": True}}),
        "products":col("products").count_documents({}),
        "orders": col("orders").count_documents({}),
        "cart_items":sum(d.get("total",0) for d in col("cart").aggregate([{"$group":{"_id":None,"total":{"$sum":"$quantity"}}}])),
        "reviews":col("reviews").count_documents({}),
    }
    rev_agg=list(col("orders").aggregate([{"$group":{"_id":None,"total":{"$sum":"$total"}}}]))
    stats["revenue"]=rev_agg[0]["total"] if rev_agg else 0
    recent_orders=list(col("orders").aggregate([
        {"$sort":{"created_at":-1}},{"$limit":10},
        {"$lookup":{"from":"users","localField":"user_id","foreignField":"seq_id","as":"u"}},
        {"$unwind":{"path":"$u","preserveNullAndEmptyArrays":True}},
        {"$project":{"order_ref":1,"total":1,"status":1,"created_at":1,"username":"$u.username","seq_id":1}}
    ]))
    for o in recent_orders:
        o["id"]=o.get("seq_id",str(o["_id"]))
        o["_id"]=str(o["_id"])
        if hasattr(o.get("created_at",""),"strftime"):
            o["created_at"]=o["created_at"].strftime("%Y-%m-%d %H:%M")
    top_products=list(col("order_items").aggregate([
        {"$group":{"_id":"$product_id","items":{"$sum":"$quantity"}}},
        {"$sort":{"items":-1}},{"$limit":5},
        {"$lookup":{"from":"products","localField":"_id","foreignField":"seq_id","as":"p"}},
        {"$unwind":"$p"},
        {"$project":{"name":"$p.name","category":"$p.category","price":"$p.price","rating":"$p.rating","items":1}}
    ]))
    cat_revenue_raw = list(col("order_items").aggregate([
        {"$lookup":{"from":"products","localField":"product_id","foreignField":"seq_id","as":"p"}},
        {"$unwind":"$p"},
        {"$group":{"_id":"$p.category","items":{"$sum":"$quantity"},"rev":{"$sum":{"$multiply":["$price","$quantity"]}}}},
        {"$sort":{"rev":-1}},{"$limit":8}
    ]))
    # Normalise cat_revenue: _id → category
    cat_revenue = [{"category": r.get("_id","Unknown"), "rev": r.get("rev",0), "items": r.get("items",0)}
                   for r in cat_revenue_raw]

    # Normalise top_products: items → sold, convert ObjectId
    for p in top_products:
        p["sold"] = p.get("items", 0)
        p["_id"]  = str(p.get("_id",""))

    return render_template("admin_dashboard.html",stats=stats,
        recent_orders=recent_orders,top_products=top_products,cat_revenue=cat_revenue)

@app.route("/admin/products")
@admin_required
def admin_products():
    q           = request.args.get("q", "").strip()
    page        = max(1, int(request.args.get("page", 1)))
    cat_filter  = request.args.get("cat", "").strip()
    badge_filter= request.args.get("badge", "").strip()
    stock_filter= request.args.get("stock", "").strip()
    sort_by     = request.args.get("sort", "cat_name").strip()

    # Build MongoDB filter
    filt = {"seq_id": {"$exists": True}}
    if q:
        filt["$or"] = [
            {"name":     {"$regex": q, "$options": "i"}},
            {"category": {"$regex": q, "$options": "i"}},
        ]
    if cat_filter:
        filt["category"] = cat_filter
    if badge_filter == "none":
        filt["badge"] = {"$in": [None, ""]}
    elif badge_filter:
        filt["badge"] = badge_filter
    if stock_filter == "low":
        filt["stock"] = {"$lt": 10}
    elif stock_filter == "medium":
        filt["stock"] = {"$gte": 10, "$lt": 30}
    elif stock_filter == "ok":
        filt["stock"] = {"$gte": 30}

    # Sort mapping
    sort_map = {
        "cat_name":   [("category", 1), ("name", 1)],
        "name":       [("name", 1)],
        "newest":     [("seq_id", -1)],
        "price_asc":  [("price", 1)],
        "price_desc": [("price", -1)],
        "rating":     [("rating", -1)],
        "stock_asc":  [("stock", 1)],
    }
    sort_order = sort_map.get(sort_by, sort_map["cat_name"])

    total = col("products").count_documents(filt)
    items = [doc_to_dict(p) for p in
             col("products").find(filt).sort(sort_order).skip((page-1)*30).limit(30)]
    total_pages = max(1, (total + 29) // 30)

    # Values for filter dropdowns
    all_cats   = sorted(col("products").distinct("category"))
    all_badges = sorted([b for b in col("products").distinct("badge") if b])

    return render_template("admin_products.html", products=items, q=q,
        page=page, total_pages=total_pages, total=total,
        cat_filter=cat_filter, badge_filter=badge_filter,
        stock_filter=stock_filter, sort_by=sort_by,
        all_cats=all_cats, all_badges=all_badges,
        get_img=get_product_image)

@app.route("/admin/products/edit/<int:pid>", methods=["GET","POST"])
@admin_required
def admin_edit_product(pid):
    p=col("products").find_one({"seq_id":pid})
    if not p: return redirect(url_for("admin_products"))
    msg=None
    if request.method=="POST":
        new_cat=request.form["category"]; new_name=request.form["name"]
        copts=get_customization_options(new_cat,new_name)
        var_parts=[]
        # Build spec_prices: {opt_key: {choice: price}}
        spec_prices={}
        for opt in copts:
            val=request.form.get(f"opt_{opt['key']}","").strip()
            if val: var_parts.append(f"{opt['label']}: {val}")
            choice_prices={}
            for choice in opt["choices"]:
                price_key=f"spec_price_{opt['key']}_{choice}"
                raw=request.form.get(price_key,"").strip()
                if raw:
                    try: choice_prices[choice]=float(raw)
                    except ValueError: pass
            if choice_prices:
                spec_prices[opt["key"]]=choice_prices
        variants_str=" | ".join(var_parts)
        base_price=float(request.form["price"])
        if spec_prices:
            all_sp=[v2 for cp in spec_prices.values() for v2 in cp.values()]
            if all_sp: base_price=min(all_sp)

        update={
            "name":new_name,"price":base_price,"category":new_cat,
            "badge":request.form.get("badge","") or None,"rating":float(request.form["rating"]),
            "stock":int(request.form.get("stock",100)),"trending":int(request.form.get("trending",0)),
            "variants":variants_str,"spec_prices":spec_prices
        }

        # Handle image uploads to MongoDB GridFS
        existing_images = list(p.get("images") or [])
        saved = 0
        for slot in range(1, 7):
            f = request.files.get(f"image_{slot}")
            if f and f.filename:
                ext = f.filename.rsplit(".",1)[-1].lower()
                if ext in ("jpg","jpeg","png","webp","gif"):
                    file_key = upload_image_to_gridfs(f.stream, pid, slot)
                    if file_key:
                        # Replace or insert at position slot-1
                        while len(existing_images) < slot:
                            existing_images.append(None)
                        existing_images[slot-1] = file_key
                        saved += 1
        # Remove trailing Nones
        while existing_images and not existing_images[-1]:
            existing_images.pop()
        update["images"] = existing_images

        col("products").update_one({"seq_id":pid},{"$set":update})
        msg=f"✅ Product updated!{f' {saved} image(s) saved to database.' if saved else ''}"
        p=col("products").find_one({"seq_id":pid})

    p=doc_to_dict(p)
    existing_imgs=p.get("images") or get_product_images(pid)
    custom_opts=get_customization_options(p["category"],p["name"])
    saved_variants={}
    if p.get("variants"):
        for part in str(p["variants"]).split(" | "):
            if ":" in part:
                k,v=part.split(":",1)
                saved_variants[k.strip().lower()]=v.strip()
    return render_template("admin_edit_product.html",p=p,msg=msg,
        categories=list(CATEGORY_META.keys()),
        existing_imgs=existing_imgs,custom_opts=custom_opts,
        saved_variants=saved_variants,get_img=get_product_image)

@app.route("/admin/products/add", methods=["GET","POST"])
@admin_required
def admin_add_product():
    msg=None; new_pid=None
    if request.method=="POST":
        new_pid=next_seq("products")
        images = []
        for slot in range(1, 7):
            f = request.files.get(f"image_{slot}")
            if f and f.filename:
                ext = f.filename.rsplit(".",1)[-1].lower()
                if ext in ("jpg","jpeg","png","webp","gif"):
                    file_key = upload_image_to_gridfs(f.stream, new_pid, slot)
                    if file_key:
                        images.append(file_key)
        # Retry with fresh seq_id if duplicate key (counter out of sync)
        for _attempt in range(5):
            try:
                new_cat2=request.form["category"]
                new_name2=request.form["name"]
                copts2=get_customization_options(new_cat2,new_name2)
                spec_prices2={}
                for opt2 in copts2:
                    cp2={}
                    for ch2 in opt2["choices"]:
                        pk2=f"spec_price_{opt2['key']}_{ch2}"
                        rv2=request.form.get(pk2,"").strip()
                        if rv2:
                            try: cp2[ch2]=float(rv2)
                            except ValueError: pass
                    if cp2: spec_prices2[opt2["key"]]=cp2
                base_price2=float(request.form["price"])
                if spec_prices2:
                    all_sp2=[v2 for cp in spec_prices2.values() for v2 in cp.values()]
                    if all_sp2: base_price2=min(all_sp2)
                col("products").insert_one({
                    "seq_id":new_pid,"name":new_name2,
                    "price":base_price2,"category":new_cat2,
                    "icon":request.form.get("icon","📦"),
                    "badge":request.form.get("badge","") or None,
                    "rating":float(request.form.get("rating",4.0)),
                    "reviews":0,"trending":int(request.form.get("trending",0)),
                    "stock":int(request.form.get("stock",100)),
                    "variants":"","spec_prices":spec_prices2,"images":images
                })
                break  # success
            except pymongo.errors.DuplicateKeyError:
                new_pid = next_seq("products")  # get next safe id and retry
        msg=f"✅ Product added!{f' {len(images)} image(s) saved to database.' if images else ''}"
    return render_template("admin_add_product.html",msg=msg,new_pid=new_pid,
        categories=list(CATEGORY_META.keys()))

@app.route("/admin/products/delete-image/<int:pid>/<int:slot>", methods=["POST"])
@admin_required
def admin_delete_image(pid, slot):
    """Remove a single image from GridFS and update product document."""
    try:
        fs = get_fs()
        filename = f"product_{pid}_slot_{slot}"
        for f in fs.find({"filename": filename}):
            fs.delete(f._id)
        # Remove key from product images list
        p = col("products").find_one({"seq_id": pid}, {"images": 1})
        if p:
            imgs = list(p.get("images") or [])
            key  = f"product_{pid}_slot_{slot}"
            imgs = [k for k in imgs if k != key]
            col("products").update_one({"seq_id": pid}, {"$set": {"images": imgs}})
    except Exception as e:
        app.logger.error(f"Delete image error: {e}")
    return redirect(url_for("admin_edit_product", pid=pid))


@app.route("/admin/products/delete/<int:pid>", methods=["POST"])
@admin_required
def admin_delete_product(pid):
    delete_product_images_gridfs(pid)
    col("products").delete_one({"seq_id":pid})
    return redirect(url_for("admin_products"))


@app.route("/admin/users/delete/<int:uid>", methods=["POST"])
@admin_required
def admin_delete_user(uid):
    """Delete a user — only super admin can do this."""
    if not session.get("is_super_admin"):
        return jsonify({"error": "Forbidden: Only super admin can delete users."}), 403
    # Prevent super admin from deleting themselves
    user = col("users").find_one({"seq_id": uid})
    if not user:
        return redirect(url_for("admin_users"))
    if user.get("username") == SUPER_ADMIN_USERNAME:
        return redirect(url_for("admin_users"))
    # Delete all user data
    col("cart").delete_many({"user_id": uid})
    col("wishlist").delete_many({"user_id": uid})
    col("recently_viewed").delete_many({"user_id": uid})
    col("reviews").delete_many({"user_id": uid})
    col("password_resets").delete_many({"user_id": uid})
    # Delete profile picture from GridFS
    try:
        fs = get_fs()
        for f in fs.find({"filename": f"profile_{uid}"}):
            fs.delete(f._id)
    except Exception:
        pass
    col("users").delete_one({"seq_id": uid})
    app.logger.info(f"Super admin deleted user id={uid} ({user.get('username')})")
    return redirect(url_for("admin_users"))

@app.route("/admin/orders")
@admin_required
def admin_orders():
    status=request.args.get("status","")
    filt={"status":status} if status else {}
    orders=list(col("orders").aggregate([
        {"$match":filt},{"$sort":{"created_at":-1}},{"$limit":50},
        {"$lookup":{"from":"users","localField":"user_id","foreignField":"seq_id","as":"u"}},
        {"$unwind":{"path":"$u","preserveNullAndEmptyArrays":True}},
        {"$project":{"order_ref":1,"total":1,"status":1,"promo_code":1,"created_at":1,
                     "username":"$u.username","seq_id":1,"_id":1}}
    ]))
    for o in orders:
        o["id"]=o.get("seq_id",str(o["_id"]))
        o["_id"]=str(o["_id"])
        if hasattr(o.get("created_at",""),"strftime"):
            o["created_at"]=o["created_at"].strftime("%Y-%m-%d %H:%M:%S")
    return render_template("admin_orders.html",orders=orders,status=status)

@app.route("/admin/orders/update/<int:oid>", methods=["POST"])
@admin_required
def admin_update_order(oid):
    new_status=request.form.get("status","Confirmed")
    col("orders").update_one({"seq_id":oid},{"$set":{"status":new_status}})
    return redirect(url_for("admin_orders"))


@app.route("/admin/orders/label/<int:oid>")
@admin_required
def admin_order_label(oid):
    """Generate a professional shipping label PDF (Amazon-style) for an order."""
    from flask import make_response
    import io, random

    order = col("orders").find_one({"seq_id": oid})
    if not order:
        return "Order not found", 404

    items   = list(col("order_items").find({"order_id": oid}))
    user    = col("users").find_one({"seq_id": order.get("user_id")})

    recipient_name = (user.get("username","Customer") if user else "Customer").title()
    email    = user.get("email","") if user else ""
    phone    = user.get("phone","") if user else ""
    address  = order.get("address") or (user.get("address","") if user else "")
    city     = order.get("city")    or (user.get("city","")    if user else "")
    pincode  = order.get("pincode") or (user.get("pincode","") if user else "")

    order_ref    = order.get("order_ref", f"NXC-{oid}")
    order_date   = order.get("created_at","")
    order_date_str = order_date.strftime("%d %b %Y, %I:%M %p") if hasattr(order_date,"strftime") else str(order_date)[:19]

    status         = order.get("status","Confirmed")
    total          = order.get("total", 0)
    subtotal       = order.get("subtotal", total)
    gst_amt        = order.get("gst_amt", 0)
    payment_method = order.get("payment_method","Online")
    promo          = order.get("promo_code","") or ""

    try:
        from reportlab.pdfgen import canvas as rl_canvas
        from reportlab.lib import colors
        from reportlab.lib.units import mm

        buf = io.BytesIO()
        W, H = 148*mm, 210*mm   # A5 portrait
        c = rl_canvas.Canvas(buf, pagesize=(W, H))

        forest = colors.HexColor("#1a3a0a")
        black  = colors.black
        white  = colors.white
        lgray  = colors.HexColor("#f5f5f5")
        gray   = colors.HexColor("#6b7280")
        dgray  = colors.HexColor("#374151")
        border = colors.HexColor("#bbbbbb")

        margin = 6*mm
        col_w  = W - 2*margin

        def box(x, y, w, h, fill=None, stroke_col=border, lw=0.5):
            c.setLineWidth(lw)
            c.setStrokeColor(stroke_col)
            if fill:
                c.setFillColor(fill)
                c.rect(x, y, w, h, fill=1, stroke=1)
            else:
                c.rect(x, y, w, h, fill=0, stroke=1)
            c.setFillColor(black); c.setStrokeColor(black)

        def txt(x, y, s, size=8, bold=False, color=black, align="left"):
            c.setFillColor(color)
            c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
            if align == "center": c.drawCentredString(x, y, str(s))
            elif align == "right": c.drawRightString(x, y, str(s))
            else: c.drawString(x, y, str(s))
            c.setFillColor(black)

        def hline(y, x0=None, x1=None, lw=0.4, col=border):
            c.setLineWidth(lw); c.setStrokeColor(col)
            c.line(x0 or margin, y, x1 or (margin+col_w), y)
            c.setStrokeColor(black)

        def barcode_strip(bx, by, bw, bh, seed_str):
            c.setFillColor(black)
            random.seed(hash(seed_str))
            patterns = [random.choice([1,2,3]) for _ in range(40)]
            bbar = bx
            for p in patterns:
                pw = p * 0.5*mm
                if bbar + pw > bx + bw - 2*mm: break
                c.rect(bbar, by, pw, bh, fill=1, stroke=0)
                bbar += pw + random.uniform(0.2,0.6)*mm
            c.setFillColor(black)

        y = H - margin   # current y (top-down)

        # ─────────────────────────────────────────────
        # SECTION 1: SHIP TO
        # ─────────────────────────────────────────────
        s1h = 48*mm
        s1y = y - s1h
        box(margin, s1y, col_w, s1h, fill=white, lw=1.2)

        txt(margin+3*mm, s1y+s1h-6.5*mm, "Ship To", size=9, bold=True, color=dgray)
        hline(s1y+s1h-9*mm, lw=0.5)

        ly = s1y + s1h - 15*mm
        txt(margin+3*mm, ly, recipient_name.upper(), size=11, bold=True)
        ly -= 6.5*mm
        for line in filter(None, [address, city, pincode]):
            txt(margin+3*mm, ly, line, size=8)
            ly -= 5.5*mm
        if phone:
            txt(margin+3*mm, ly, f"Phone No.: {phone}", size=8, bold=True)

        # Nexacart logo box (top right of ship-to)
        lbx = margin + col_w - 33*mm
        lby = s1y + s1h - 34*mm
        c.setFillColor(forest)
        c.roundRect(lbx, lby, 28*mm, 22*mm, 3*mm, fill=1, stroke=0)
        c.setFillColor(white)
        c.setFont("Helvetica-Bold", 16); c.drawCentredString(lbx+14*mm, lby+12*mm, "N")
        c.setFont("Helvetica-Bold", 5.5); c.drawCentredString(lbx+14*mm, lby+5*mm, "NEXACART")
        c.setFont("Helvetica", 4.5); c.setFillColor(colors.HexColor("#b8882a"))
        c.drawCentredString(lbx+14*mm, lby+2*mm, "Premium Shopping")
        c.setFillColor(black)

        y = s1y

        # ─────────────────────────────────────────────
        # SECTION 2: ORDER DETAILS + BARCODE
        # ─────────────────────────────────────────────
        s2h = 40*mm
        s2y = y - s2h
        box(margin, s2y, col_w, s2h, fill=white, lw=1)

        # Left: info table
        info = [
            ("Payment:",     payment_method.upper() if payment_method else "PREPAID"),
            ("ORDER TOTAL:", f"Rs. {total:,.2f}"),
            ("Order Date:",  order_date_str),
            ("Status:",      status.upper()),
            ("Promo Code:",  promo if promo else "—"),
        ]
        iy = s2y + s2h - 8*mm
        for lbl, val in info:
            txt(margin+3*mm, iy, lbl, size=7.5, color=gray)
            txt(margin+32*mm, iy, val, size=7.5, bold=True)
            iy -= 6*mm

        # Right: barcode
        bx = margin + col_w*0.55
        by = s2y + 4*mm
        bw = col_w*0.42
        bh = 22*mm
        barcode_strip(bx, by+5*mm, bw, bh, order_ref)
        txt(bx + bw/2, by+1.5*mm, order_ref, size=6.5, align="center")

        # Shipping method label
        c.setFillColor(lgray)
        c.rect(bx, by+bh+6*mm, bw, 7*mm, fill=1, stroke=0)
        txt(bx+bw/2, by+bh+8*mm, "NEXACART STANDARD DELIVERY", size=5.5, bold=True, align="center")
        c.setFillColor(black)

        y = s2y

        # ─────────────────────────────────────────────
        # SECTION 3: SHIPPED BY (return address)
        # ─────────────────────────────────────────────
        s3h = 36*mm
        s3y = y - s3h
        box(margin, s3y, col_w, s3h, fill=white, lw=1)

        txt(margin+3*mm, s3y+s3h-6.5*mm, "Shipped By", size=9, bold=True)
        c.setFont("Helvetica", 6); c.setFillColor(gray)
        c.drawString(margin+28*mm, s3y+s3h-6.5*mm, "(If undelivered, return to)")
        c.setFillColor(black)
        hline(s3y+s3h-9*mm, lw=0.5)

        sy = s3y + s3h - 14.5*mm
        for line, bold in [("Nexacart",True),("support@nexacart.com",False),
                            ("www.nexacart.com",False),("GSTIN: 29NEXACART001Z5",False)]:
            txt(margin+3*mm, sy, line, size=7.5, bold=bold)
            sy -= 5.5*mm

        # Right: order # + mini barcode + invoice info
        bx2  = margin + col_w*0.55
        by2  = s3y + 4*mm
        bw2  = col_w*0.42
        txt(bx2, by2+25*mm, f"Order #: {order_ref}", size=7, bold=True)
        barcode_strip(bx2, by2+9*mm, bw2, 14*mm, order_ref+"B")
        c.setFont("Helvetica", 6); c.setFillColor(dgray)
        c.drawString(bx2, by2+5*mm, f"Invoice No.: {order_ref.replace('NXC','INV')}")
        c.drawString(bx2, by2+1.5*mm, f"Invoice Date: {order_date_str[:10]}")
        c.setFillColor(black)

        y = s3y

        # ─────────────────────────────────────────────
        # SECTION 4: ITEMS TABLE
        # ─────────────────────────────────────────────
        max_items = min(len(items), 5)
        s4h = max_items * 9*mm + 14*mm
        s4y = y - s4h
        if s4y < margin + 14*mm:
            s4h = y - margin - 14*mm
            s4y = margin + 14*mm
        box(margin, s4y, col_w, s4h, fill=white, lw=1)

        # Header row
        th = 9*mm
        c.setFillColor(lgray)
        c.rect(margin, s4y+s4h-th, col_w, th, fill=1, stroke=0)
        c.setFillColor(black)
        hline(s4y+s4h-th, lw=0.5)
        hline(s4y+s4h, lw=0.5)

        cols = [("Product Name & SKU", 0.42),("Qty",0.07),
                ("Unit Price",0.16),("Taxable Value",0.16),("IGST",0.10),("Total",0.09)]
        hx = margin
        for hdr, frac in cols:
            hw = col_w*frac
            txt(hx+1.5*mm, s4y+s4h-th+2.5*mm, hdr, size=6.5, bold=True)
            hx += hw

        # Vertical dividers for header
        hx = margin
        for _, frac in cols[:-1]:
            hx += col_w*frac
            c.setLineWidth(0.3); c.setStrokeColor(border)
            c.line(hx, s4y, hx, s4y+s4h)

        ry = s4y + s4h - th
        for item in items[:max_items]:
            ry -= 9*mm
            if ry < s4y+1*mm: break
            nm   = item.get("name","Product")
            qty  = item.get("quantity",1)
            up   = item.get("price",0)
            itot = up * qty
            igst = round(itot*0.09,2)
            tax  = round(itot,2)
            var  = item.get("variant","")
            display_name = (nm[:30]+(f" / {var}" if var else ""))
            
            rx = margin
            txt(rx+1.5*mm, ry+2.5*mm, display_name[:34], size=6.5)
            rx += col_w*0.42
            txt(rx+1.5*mm, ry+2.5*mm, str(qty), size=7)
            rx += col_w*0.07
            txt(rx+1.5*mm, ry+2.5*mm, f"{up:,.2f}", size=7)
            rx += col_w*0.16
            txt(rx+1.5*mm, ry+2.5*mm, f"{tax:,.2f}", size=7)
            rx += col_w*0.16
            txt(rx+1.5*mm, ry+2.5*mm, f"{igst:,.2f}", size=7)
            rx += col_w*0.10
            txt(rx+1.5*mm, ry+2.5*mm, f"{itot:,.2f}", size=7)
            hline(ry, lw=0.3)

        if len(items) > max_items:
            txt(margin+3*mm, s4y+2*mm, f"+ {len(items)-max_items} more item(s)", size=6.5, color=gray)

        y = s4y

        # ─────────────────────────────────────────────
        # SECTION 5: DISCLAIMER
        # ─────────────────────────────────────────────
        s5h = 10*mm
        s5y = y - s5h
        if s5y >= margin + 8*mm:
            box(margin, s5y, col_w, s5h, fill=lgray, lw=0.5)
            disc = ("All disputes are subject to local jurisdiction only. "
                    "Goods once sold will only be taken back or exchanged "
                    "as per the store's exchange/return policy.")
            c.setFont("Helvetica",6); c.setFillColor(dgray)
            # wrap into 2 lines
            words = disc.split(); l1=[]; l2=[]
            for w in words:
                if c.stringWidth(" ".join(l1+[w]),"Helvetica",6) < col_w-6*mm: l1.append(w)
                else: l2.append(w)
            c.drawString(margin+3*mm, s5y+6*mm, " ".join(l1))
            c.drawString(margin+3*mm, s5y+2*mm, " ".join(l2))
            c.setFillColor(black)
            y = s5y

        # ─────────────────────────────────────────────
        # FOOTER BAR
        # ─────────────────────────────────────────────
        c.setFillColor(lgray)
        c.rect(margin, margin, col_w, 8*mm, fill=1, stroke=0)
        hline(margin+8*mm, lw=0.5)
        c.setFont("Helvetica",6); c.setFillColor(gray)
        c.drawString(margin+3*mm, margin+2.5*mm,
                     "THIS IS AN AUTO-GENERATED LABEL AND DOES NOT NEED SIGNATURE.")
        c.setFont("Helvetica-Bold",6.5); c.setFillColor(forest)
        c.drawRightString(margin+col_w-2*mm, margin+2.5*mm, "Powered By: NEXACART")
        c.setFillColor(black)

        # Outer border
        c.setStrokeColor(colors.HexColor("#222222"))
        c.setLineWidth(1.8)
        c.rect(margin-0.5*mm, margin-0.5*mm, col_w+1*mm, H-2*margin+1*mm, fill=0, stroke=1)

        c.save()
        buf.seek(0)
        resp = make_response(buf.read())
        resp.headers["Content-Type"]        = "application/pdf"
        resp.headers["Content-Disposition"] = f'attachment; filename="label_{order_ref}.pdf"'
        return resp

    except ImportError:
        # HTML fallback (auto-prints)
        items_html = "".join(
            f"<tr><td>{i.get('name','Product')[:40]}{' / '+i.get('variant','') if i.get('variant') else ''}</td>"
            f"<td>{i.get('quantity',1)}</td><td>Rs.{i.get('price',0):,.2f}</td>"
            f"<td>Rs.{i.get('price',0)*i.get('quantity',1)*0.09:,.2f}</td>"
            f"<td>Rs.{i.get('price',0)*i.get('quantity',1):,.2f}</td></tr>"
            for i in items
        )
        html = f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Label {order_ref}</title>
<style>*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:Arial,sans-serif;font-size:11px;padding:15px;max-width:500px;border:2px solid #333}}
.hdr{{background:#1a3a0a;color:#fff;padding:10px;text-align:center;font-size:14px;font-weight:bold;margin-bottom:0}}
.sec{{border:1px solid #ccc;padding:8px;margin-bottom:0;border-top:none}}
.sec-title{{font-weight:bold;font-size:10px;border-bottom:1px solid #eee;padding-bottom:3px;margin-bottom:5px}}
.name{{font-size:13px;font-weight:bold}}
.row{{display:flex;justify-content:space-between;padding:1px 0}}
.lbl{{color:#6b7280;min-width:90px}}
table{{width:100%;border-collapse:collapse;font-size:10px}}
th{{background:#f5f5f5;font-weight:bold;padding:4px;border:1px solid #ccc;text-align:left}}
td{{padding:3px 4px;border:1px solid #eee}}
.footer{{background:#f5f5f5;font-size:9px;color:#666;padding:5px;text-align:center;border-top:1px solid #ccc}}
</style></head><body onload="window.print()">
<div class="hdr">📦 NEXACART — DELIVERY LABEL</div>
<div class="sec"><div class="sec-title">Ship To</div>
<div class="name">{recipient_name.upper()}</div>
<div>{address}</div><div>{city} {pincode}</div><div><b>Phone: {phone}</b></div></div>
<div class="sec" style="display:flex;gap:10px">
<div style="flex:1"><div class="sec-title">Order Details</div>
<div class="row"><span class="lbl">Order Ref:</span><b>{order_ref}</b></div>
<div class="row"><span class="lbl">Date:</span>{order_date_str}</div>
<div class="row"><span class="lbl">Payment:</span><b>{payment_method}</b></div>
<div class="row"><span class="lbl">Total:</span><b>Rs. {total:,.2f}</b></div>
<div class="row"><span class="lbl">Status:</span>{status}</div></div>
<div style="flex:1"><div class="sec-title">Shipped By</div>
<b>Nexacart</b><div>support@nexacart.com</div><div>GSTIN: 29NEXACART001Z5</div>
<div>Invoice: {order_ref.replace('NXC','INV')}</div></div></div>
<div class="sec"><div class="sec-title">Items</div>
<table><tr><th>Product</th><th>Qty</th><th>Unit Price</th><th>GST</th><th>Total</th></tr>
{items_html}</table></div>
<div class="footer">THIS IS AN AUTO-GENERATED LABEL AND DOES NOT NEED SIGNATURE. | Powered By: NEXACART</div>
</body></html>"""
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html"
        return resp


@app.route("/admin/users")
@admin_required
def admin_users():
    pipeline=[
        {"$match":{"is_fake_reviewer":{"$ne":True}}},
        {"$lookup":{"from":"orders","localField":"seq_id","foreignField":"user_id","as":"orders"}},
        {"$project":{
            "seq_id":1,"username":1,"email":1,"phone":1,"city":1,
            "role":1,"joined":1,"is_verified":1,
            "order_count":{"$size":"$orders"},
            "total_spent":{"$sum":"$orders.total"}
        }},
        {"$sort":{"joined":-1}}
    ]
    users=[doc_to_dict(u) for u in col("users").aggregate(pipeline)]
    for u in users:
        if hasattr(u.get("joined",""),"strftime"):
            u["joined"]=u["joined"].strftime("%Y-%m-%d %H:%M")
    is_super_admin = session.get("is_super_admin", False)
    return render_template("admin_users.html", users=users, pending_requests=[], is_super_admin=is_super_admin)

# ═══════════════════════════════════════════════════════════
# API ROUTES
# ═══════════════════════════════════════════════════════════
@app.route("/api/search")
@login_required
def api_search():
    q=request.args.get("q","").strip()
    if len(q)<2: return jsonify([])
    rows=[doc_to_dict(p) for p in col("products").find(
        {"name":{"$regex":q,"$options":"i"}},
        {"seq_id":1,"name":1,"price":1,"category":1}
    ).limit(8)]
    return jsonify([{"id":p["id"],"name":p["name"],"price":p["price"],"category":p["category"]} for p in rows])

@app.route("/api/pincode/<pin>")
@login_required
def pincode_lookup(pin):
    PIN_MAP={"110001":"New Delhi","400001":"Mumbai","560001":"Bengaluru","600001":"Chennai",
             "700001":"Kolkata","500001":"Hyderabad","411001":"Pune","380001":"Ahmedabad",
             "226001":"Lucknow","302001":"Jaipur","533201":"Razole","521001":"Vijayawada",
             "533001":"Eluru","530001":"Visakhapatnam","533101":"Rajahmundry","600028":"Chennai"}
    city=PIN_MAP.get(pin,"")
    return jsonify({"city":city,"found":bool(city)})

@app.route("/api/categories")
@login_required
def api_categories():
    cats=list(col("products").aggregate([
        {"$group":{"_id":"$category","cnt":{"$sum":1},"avg_rating":{"$avg":"$rating"}}}
    ]))
    return jsonify([{"category":r["_id"],"count":r["cnt"],"avg_rating":round(r["avg_rating"],1)} for r in cats])

@app.route("/api/product/<int:pid>")
@login_required
def api_product(pid):
    p=col("products").find_one({"seq_id":pid})
    if not p: return jsonify({"error":"Not found"}),404
    p=doc_to_dict(p)
    discount_pct=10+(pid%4)*10
    return jsonify({**p,"discount_pct":discount_pct,
        "original_price":round(p["price"]/(1-discount_pct/100)),
        "image":get_product_image(pid)})

@app.route("/api/stock/<int:pid>")
@login_required
def api_stock(pid):
    p=col("products").find_one({"seq_id":pid},{"stock":1,"name":1})
    if not p: return jsonify({"error":"Not found"}),404
    return jsonify({"id":pid,"stock":p["stock"],"low":p["stock"]<10,"name":p["name"]})

@app.route("/api/short-link/<int:pid>")
def get_short_link(pid):
    return jsonify({"url":url_for("share_product",pid=pid,_external=True)})

@app.route("/api/chat", methods=["POST"])
@login_required
def api_chat():
    import urllib.request, urllib.error, json as _json
    data = request.get_json(silent=True) or {}
    user_msg = (data.get("message", "")).strip()[:500]
    history = data.get("history", [])[-10:]
    if not user_msg:
        return jsonify({"reply": "Please type a message."}), 400

    cats_str = ", ".join(CATEGORY_META.keys())
    system_prompt = (
        "You are Nexa, a friendly and knowledgeable AI shopping assistant for Nexacart, "
        "a premium Indian e-commerce platform. Your job is to help customers find products, "
        "answer questions about orders, delivery, returns, and payments. "
        "Store details: 160+ products across 30 categories: " + cats_str + ". "
        "Free delivery on all orders. 30-day easy returns. "
        "Payments: UPI (GPay, PhonePe, Paytm, BHIM, Amazon Pay), Credit/Debit Cards. "
        "GST: 9% included. Delivery: 3-5 business days. "
        "Active promo codes: SAVE10 (10% off), MARKET20 (20% off), FIRST50 (50% off first order), "
        "FASHION30 (30% off fashion), BEAUTY15 (15% off beauty). "
        "Always be concise, warm, and helpful. Reply in 1-3 sentences. "
        "If asked about a specific product, mention where to find it (category name)."
    )

    # Build messages array with proper history
    messages = []
    for h in history:
        role = h.get("role", "")
        msg_content = h.get("content", "")
        if role in ("user", "assistant") and msg_content:
            messages.append({"role": role, "content": str(msg_content)[:400]})
    messages.append({"role": "user", "content": user_msg})

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # Smart fallback when no API key
    if not api_key:
        ml = user_msg.lower()
        if any(w in ml for w in ["return", "refund", "exchange"]):
            reply = "We offer 30-day easy returns on all products! 🔄 Go to your Orders page and click 'Return' on the item you'd like to send back."
        elif any(w in ml for w in ["deliver", "ship", "track", "days"]):
            reply = "We offer free delivery on all orders, arriving in 3–5 business days. 🚚 Track your order from the Orders page!"
        elif any(w in ml for w in ["promo", "code", "discount", "coupon", "offer"]):
            reply = "Here are our active promo codes 🏷️: SAVE10 (10% off), MARKET20 (20% off), FIRST50 (50% off your first order), FASHION30 (30% off fashion), BEAUTY15 (15% off beauty)!"
        elif any(w in ml for w in ["pay", "payment", "upi", "gpay", "paytm", "card", "credit", "debit"]):
            reply = "We accept UPI (GPay, PhonePe, Paytm, BHIM, Amazon Pay) and Credit/Debit Cards. 💳 All payments are 100% secure!"
        elif any(w in ml for w in ["gst", "tax", "price"]):
            reply = "All our prices include 9% GST. The final price you see is exactly what you pay — no hidden charges! ✅"
        elif any(w in ml for w in ["hello", "hi", "hey", "namaste", "good"]):
            reply = "Hello! 👋 I'm Nexa, your Nexacart AI shopping assistant. I can help you find products, track orders, apply promo codes, and more! What are you looking for today?"
        elif any(w in ml for w in ["laptop", "computer", "macbook", "dell", "hp"]):
            reply = "We have a great range of laptops in the 'Laptops & Computers' category! 💻 From budget picks like HP Pavilion to premium options like MacBook Pro M3. Use promo code SAVE10 for 10% off!"
        elif any(w in ml for w in ["phone", "mobile", "iphone", "samsung", "oneplus"]):
            reply = "Check out our Smartphones category for the latest models! 📱 We have iPhone 15 Pro, Samsung Galaxy S24, OnePlus 12 and many more. Use SAVE10 for a discount!"
        elif any(w in ml for w in ["earphone", "headphone", "earbud", "speaker", "audio"]):
            reply = "Browse our Audio category for top headphones and speakers! 🎧 Sony WH-1000XM5, AirPods Pro, JBL Flip 6 — all available with free delivery."
        elif any(w in ml for w in ["fashion", "clothes", "shirt", "dress", "shoes"]):
            reply = "Explore our Fashion & Footwear collections! 👗👟 Use code FASHION30 for 30% off on clothing items. We have brands like Levi's, Nike, Adidas and more."
        elif any(w in ml for w in ["beauty", "skincare", "makeup", "skin"]):
            reply = "Discover our Beauty & Skincare collection! 💄 Brands like Mamaearth, Lakme, The Ordinary and more. Use BEAUTY15 for 15% off beauty products!"
        elif any(w in ml for w in ["help", "support", "problem", "issue"]):
            reply = "I'm here to help! 🤝 You can also visit our Help Centre for FAQs. What specific issue are you facing? I'll do my best to resolve it!"
        elif any(w in ml for w in ["cancel", "cancellation"]):
            reply = "Orders can be cancelled before they are shipped. 📦 Go to your Orders page and click 'Cancel' if the option is available. For shipped orders, use our 30-day return policy."
        elif any(w in ml for w in ["contact", "email", "phone number"]):
            reply = "You can reach our support team through the Help page. 📧 We typically respond within 24 hours. Is there something specific I can help you with right now?"
        else:
            reply = "I can help with finding products, delivery info, returns, payments, and promo codes! 🛍️ What are you looking for today?"
        return jsonify({"reply": reply})

    # Call Anthropic API
    try:
        payload = _json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 300,
            "system": system_prompt,
            "messages": messages
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = _json.loads(resp.read().decode("utf-8"))

        # Extract text from content array
        reply = ""
        if result.get("content") and isinstance(result["content"], list):
            for block in result["content"]:
                if block.get("type") == "text":
                    reply += block.get("text", "")
        if not reply:
            reply = "I'm here to help! What are you looking for today?"

    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="ignore")
        app.logger.error(f"Anthropic API HTTP error {e.code}: {error_body}")
        reply = "I'm having a brief moment! 😅 Please try again in a few seconds, or ask about delivery, returns, or promo codes."
    except Exception as e:
        app.logger.error(f"Chat API error: {e}")
        reply = "Connection hiccup! 🔄 Please try again shortly. In the meantime — use SAVE10 for 10% off your order!"

    return jsonify({"reply": reply})

@app.route("/api/recommendations")
@login_required
def api_recommendations():
    uid=get_user_id()
    order_cats=[r["_id"] for r in col("order_items").aggregate([
        {"$lookup":{"from":"orders","localField":"order_id","foreignField":"seq_id","as":"o"}},
        {"$unwind":"$o"},{"$match":{"o.user_id":uid}},
        {"$lookup":{"from":"products","localField":"product_id","foreignField":"seq_id","as":"p"}},
        {"$unwind":"$p"},{"$group":{"_id":"$p.category"}}
    ])]
    wish_cats=[r["_id"] for r in col("wishlist").aggregate([
        {"$match":{"user_id":uid}},
        {"$lookup":{"from":"products","localField":"product_id","foreignField":"seq_id","as":"p"}},
        {"$unwind":"$p"},{"$group":{"_id":"$p.category"}}
    ])]
    fav_cats=list(set(order_cats+wish_cats))
    if fav_cats:
        bought={r["product_id"] for r in col("order_items").aggregate([
            {"$lookup":{"from":"orders","localField":"order_id","foreignField":"seq_id","as":"o"}},
            {"$unwind":"$o"},{"$match":{"o.user_id":uid}},{"$group":{"_id":"$product_id","product_id":{"$first":"$product_id"}}}
        ])}
        rows=[doc_to_dict(p) for p in col("products").find(
            {"category":{"$in":fav_cats},"stock":{"$gt":0},"seq_id":{"$nin":list(bought)}}
        ).sort([("trending",-1),("rating",-1),("reviews",-1)]).limit(8)]
    else:
        rows=[doc_to_dict(p) for p in col("products").find({"stock":{"$gt":0}}).sort(
            [("trending",-1),("rating",-1),("reviews",-1)]).limit(8)]
    return jsonify([{"id":p["id"],"name":p["name"],"price":p["price"],"category":p["category"],
        "rating":p["rating"],"reviews":p["reviews"],"badge":p.get("badge") or "",
        "image":get_product_image(p["id"])} for p in rows])

# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# SEED REVIEWS
# ═══════════════════════════════════════════════════════════
def seed_fake_reviews():
    """Insert realistic fake reviews for every product. Safe to call multiple times."""
    import random, datetime

    FAKE_USERS = [
        "Aarav Sharma","Priya Nair","Rohan Mehta","Sneha Reddy","Vikram Iyer",
        "Ananya Patel","Karan Joshi","Divya Menon","Arjun Kapoor","Meera Pillai",
        "Rahul Gupta","Pooja Desai","Siddharth Rao","Kavya Krishnan","Aditya Singh",
        "Lakshmi Verma","Nikhil Bhat","Swati Choudhury","Manish Tiwari","Ritu Agarwal",
        "Deepak Nambiar","Sowmya Rajan","Harish Gowda","Preethi Shetty","Vivek Dubey",
        "Nandini Subramaniam","Akash Malhotra","Anjali Mishra","Sanjay Kulkarni","Pallavi Das",
        "Tarun Pandey","Bhavya Jain","Gaurav Chatterjee","Archana Thakur","Suresh Nayak",
        "Mithila Rao","Ramesh Pillai","Sunita Ghosh","Ashwin Menon","Kavitha Naik",
        "Praveen Kumar","Shilpa Yadav","Girish Hegde","Usha Patel","Santosh Varma",
        "Rekha Bose","Vinod Suresh","Chitra Raman","Balaji Murthy","Geeta Iyengar",
        "Isha Khanna","Mohit Bansal","Rashmi Nanda","Devendra Yadav","Smita Kulkarni",
        "Ritesh Agarwal","Nalini Krishnan","Abhijit Dey","Sarita Jha","Bhuvan Sharma",
        "Puja Srivastava","Surendra Nath","Harshita Garg","Lokesh Tiwari","Veena Menon",
        "Ajay Bhatt","Mamta Rawat","Sunil Pawar","Latha Murthy","Rohini Kapoor",
        "Prasad Iyer","Amrita Sinha","Nitesh Jain","Deepa Nair","Vijay Rathod",
        "Savita Desai","Sushant Sahoo","Padma Krishnaswamy","Jayesh Shah","Nisha Pandey",
        "Gopal Rao","Anita Bose","Ramakrishna Hegde","Varsha Patil","Sameer Qureshi",
        "Lalitha Subramaniam","Asif Khan","Durga Prasad","Meenakshi Iyer","Pranav Doshi",
        "Chandana Gowda","Arun Nair","Vidya Murthy","Sudhakar Rao","Bindu Thomas",
        "Omkar Joshi","Renuka Naik","Dilip Mishra","Kaveri Pillai","Tushar Mehta",
        "Shobha Krishnan","Pramod Nair","Sunanda Ravi","Rajesh Kulkarni","Girija Patel",
        "Abhishek Verma","Preeti Joshi","Nagaraj Swamy","Sandhya Iyer","Manoj Bhatia",
    ]

    TITLES_BY_RATING = {
        5: [
            "Absolutely love it!", "Best purchase this year", "Exceeded my expectations",
            "Outstanding quality", "Highly recommended!", "Perfect — no complaints",
            "Worth every rupee", "Superb product", "Amazing value for money", "Five stars easily",
            "Couldn't be happier", "Exactly as described", "Great buy!", "Top-notch quality",
            "Brilliant product!", "10/10 would buy again", "Just wow!", "Superb experience",
            "Mind-blowing quality", "Delivered on every promise", "Incredible value!",
            "Couldn't ask for more", "Premium feel at this price!", "Very impressed",
        ],
        4: [
            "Really good product", "Very satisfied", "Good value for money", "Impressed overall",
            "Works great", "Solid purchase", "Happy with this", "Nice product",
            "Good quality", "Almost perfect", "Quite good", "Mostly satisfied",
            "Recommended!", "Well worth buying", "Pleased with the purchase",
            "Very decent quality", "Does the job well", "Good all-round product",
        ],
        3: [
            "Decent product", "Average quality", "Could be better", "Okay for the price",
            "Mixed feelings", "Acceptable", "Does the job", "Room for improvement",
            "Moderate quality", "Not bad, not great", "It's fine I guess",
            "Average experience", "Neither here nor there", "Gets the work done",
        ],
        2: [
            "Bit disappointed", "Not as expected", "Below average", "Could do better",
            "Expected more", "Mediocre quality", "Slightly let down",
            "Not impressed", "Had higher hopes", "Quality could be better",
        ],
        1: [
            "Not happy", "Poor quality", "Disappointed", "Would not recommend",
            "Save your money", "Didn't meet expectations", "Very poor experience",
            "Complete letdown", "Regret buying this",
        ],
    }

    BODIES_BY_RATING = {
        5: [
            "The build quality is excellent and it works exactly as advertised. Delivery was super fast and packaging was secure.",
            "I've been using this for a few weeks now and I'm thoroughly impressed. Great value for money!",
            "Ordered this after reading multiple reviews and I'm not disappointed. Everything is perfect.",
            "Top-quality product. Very sturdy and well-made. Looks premium too. Will definitely buy again.",
            "Absolutely delighted with this purchase. Works flawlessly and looks exactly like the photos.",
            "Fantastic product! My family loves it. Arrived well before the expected date.",
            "Excellent quality for the price. Highly recommend to anyone looking for a reliable product.",
            "This exceeded my expectations. The finish is smooth, functions perfectly and looks great.",
            "Very happy with this purchase. Customer service was also very responsive when I had a query.",
            "Perfect product, zero defects. Matches the description exactly. Delivery was prompt.",
            "Truly one of the best purchases I've made online. Packaging was secure and the product inside was flawless.",
            "I gifted this to my mother and she absolutely loves it! Quality is outstanding for the price.",
            "Was skeptical at first but this product completely won me over. Five stars without any hesitation.",
            "Seamless ordering experience. The product quality justifies the price and then some.",
            "Works like a dream! Setup was easy and performance has been rock solid ever since.",
            "This is my second purchase from this seller and both times the quality has been impeccable.",
            "Highly impressed with the attention to detail. Feels premium and well-crafted.",
            "Received it in 2 days. Unboxed it and immediately loved it. Best online purchase this season!",
            "I researched for weeks before buying and this was the right call. Zero regrets.",
            "Excellent value. My friends keep asking where I got this from. Definitely recommending Nexacart!",
            "Smooth delivery, perfect packaging, flawless product. What more can you ask for?",
            "The quality blew me away. I've bought similar things before but this is clearly a step above.",
            "Very happy with this. The seller was honest about the product and it matched perfectly.",
            "Fast shipping, great quality, no issues whatsoever. Highly satisfied customer right here!",
        ],
        4: [
            "Good product overall. Does what it's supposed to do. Minor packaging could be better.",
            "Quite happy with the purchase. Quality is good. Took a couple of extra days to arrive.",
            "Works well for my needs. The product is solid and well-built. Just minor room for improvement.",
            "Very good value for money. Performance is reliable. Would buy again.",
            "Nice product, easy to use. Build quality is solid. One small complaint about the instructions.",
            "Good buy overall. Product functions as expected. The color is slightly different from photos.",
            "Happy with this purchase. Functional and well-made. Shipping was on time.",
            "Solid product. Does what it says. Only missing a small extra feature I hoped for.",
            "Really good quality. Took off one star because delivery was a day late but product itself is great.",
            "Positive experience overall. The product is well-built and the seller was responsive.",
            "Decent build quality. Not flawless but definitely worth the price.",
            "Great value for money. This is better than many pricier alternatives I've tried.",
            "Works perfectly. Delivery was quick and the packaging was adequate. Happy customer.",
            "Good purchase. Instructions were clear and product performance has been reliable.",
            "Nice product. Minor finishing issues but nothing major. Overall a great buy.",
            "Satisfied with the purchase. Delivery was prompt and the product quality is good.",
        ],
        3: [
            "Product is okay. Not the best quality but works for the price. Packaging was average.",
            "Decent product. Does the job but nothing extraordinary. Could use some improvements.",
            "Average experience. Product works but feels a bit cheaply made in some areas.",
            "Okay product. Shipping was fine. Quality could be better for this price range.",
            "It's acceptable. Not great, not terrible. Might look for alternatives next time.",
            "Mixed feelings. Some things I liked, some not so much. Average overall.",
            "Functional but not impressive. I expected a bit more given the reviews.",
            "Gets the work done but there are better options out there. Average experience.",
            "Not the best quality I've seen but it does what it's meant to do. Okay for now.",
            "Three stars feels right. It's neither great nor bad. Just an average product.",
        ],
        2: [
            "The product quality didn't meet my expectations. Feels flimsy and seems to have minor defects.",
            "Disappointed with the build quality. Looks different from the product images.",
            "Product arrived late and quality was below what I expected. Not great for the price.",
            "Had issues from day one. Customer support was helpful though.",
            "Expected much better based on the description. Quality is poor and finish is rough.",
            "The product looks nothing like the photos. Very disappointed.",
            "Cheap material. Felt like it would break very quickly. Very underwhelmed.",
        ],
        1: [
            "Very poor quality. Not what was shown in the images. Returning this.",
            "Completely disappointed. Product stopped working within a week. Would not recommend.",
            "Waste of money. Quality is terrible and doesn't match the description at all.",
            "Broke within days of use. Build quality is appalling. Don't buy this.",
            "Complete waste. The product arrived damaged and customer care was unhelpful.",
        ],
    }

    db = get_db()
    products = list(db.products.find({"seq_id": {"$exists": True}}, {"seq_id": 1, "name": 1, "category": 1, "rating": 1}))

    if not products:
        return 0

    # Create fake user records if they don't exist (use negative seq_ids to avoid conflicts)
    fake_user_map = {}  # username -> seq_id
    existing_fake = list(db.users.find({"is_fake_reviewer": True}, {"seq_id": 1, "username": 1}))
    for u in existing_fake:
        fake_user_map[u["username"]] = u["seq_id"]

    next_fake_id = min([-1] + [u["seq_id"] for u in existing_fake]) - 1
    for uname in FAKE_USERS:
        if uname not in fake_user_map:
            db.users.update_one(
                {"username": uname, "is_fake_reviewer": True},
                {"$setOnInsert": {"seq_id": next_fake_id, "username": uname,
                                  "email": uname.lower().replace(" ", ".") + "@nexacart.fake",
                                  "is_fake_reviewer": True}},
                upsert=True
            )
            fake_user_map[uname] = next_fake_id
            next_fake_id -= 1

    # Re-fetch to get actual seq_ids (upsert may have used existing)
    existing_fake = list(db.users.find({"is_fake_reviewer": True}, {"seq_id": 1, "username": 1}))
    fake_user_map = {u["username"]: u["seq_id"] for u in existing_fake}
    fake_user_ids = list(fake_user_map.values())

    total_inserted = 0
    now = datetime.datetime.utcnow()

    for prod in products:
        pid = prod["seq_id"]
        existing_count = db.reviews.count_documents({"product_id": pid})
        if existing_count >= 15:
            continue  # already has plenty of reviews

        # Decide how many reviews to add (12-25 per product)
        target = random.randint(12, 25)
        to_add = target - existing_count
        if to_add <= 0:
            continue

        # Weight ratings around the product's existing rating (biased toward 4-5)
        base = prod.get("rating", 4.0)
        weights = {5: 0, 4: 0, 3: 0, 2: 0, 1: 0}
        if base >= 4.5:
            weights = {5: 55, 4: 30, 3: 10, 2: 3, 1: 2}
        elif base >= 4.0:
            weights = {5: 35, 4: 40, 3: 15, 2: 7, 1: 3}
        elif base >= 3.5:
            weights = {5: 20, 4: 30, 3: 30, 2: 12, 1: 8}
        else:
            weights = {5: 15, 4: 20, 3: 30, 2: 20, 1: 15}

        rating_pool = []
        for r, w in weights.items():
            rating_pool.extend([r] * w)

        # Pick random users (avoid repeating same user for same product)
        existing_user_ids = {r["user_id"] for r in db.reviews.find({"product_id": pid}, {"user_id": 1})}
        available_users = [uid for uid in fake_user_ids if uid not in existing_user_ids]
        random.shuffle(available_users)

        reviews_to_insert = []
        for i in range(min(to_add, len(available_users))):
            rating = random.choice(rating_pool)
            days_ago = random.randint(1, 365)
            created = now - datetime.timedelta(days=days_ago, hours=random.randint(0, 23), minutes=random.randint(0, 59))
            reviews_to_insert.append({
                "product_id": pid,
                "user_id": available_users[i],
                "rating": rating,
                "title": random.choice(TITLES_BY_RATING[rating]),
                "body": random.choice(BODIES_BY_RATING[rating]),
                "created_at": created,
                "is_fake": True,
            })

        if reviews_to_insert:
            db.reviews.insert_many(reviews_to_insert)
            total_inserted += len(reviews_to_insert)

            # Update product rating & review count
            agg = list(db.reviews.aggregate([
                {"$match": {"product_id": pid}},
                {"$group": {"_id": None, "avg": {"$avg": "$rating"}, "cnt": {"$sum": 1}}}
            ]))
            if agg:
                db.products.update_one(
                    {"seq_id": pid},
                    {"$set": {"rating": round(agg[0]["avg"], 1), "reviews": agg[0]["cnt"]}}
                )

    return total_inserted


@app.route("/admin/seed-reviews", methods=["GET", "POST"])
@admin_required
def admin_seed_reviews():
    msg = None
    if request.method == "POST":
        try:
            count = seed_fake_reviews()
            msg = f"✅ Successfully added {count} fake reviews across all products!"
        except Exception as e:
            msg = f"❌ Error: {e}"
    return render_template("admin_seed_reviews.html", msg=msg)

# STARTUP
# ═══════════════════════════════════════════════════════════
with app.app_context():
    try:
        init_db()
        insert_sample_products()
        seed_fake_reviews()
    except Exception as e:
        app.logger.warning(f"Startup DB init: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG","false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)