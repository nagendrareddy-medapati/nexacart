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
            s = str(path_or_key)
            # Already a full URL (http/https)
            if s.startswith("http"):
                return s
            # Route path: "img/159/1"
            if s.startswith("img/"):
                parts = s.split("/")
                if len(parts) == 3:
                    return url_for("serve_product_image",
                                   product_id=int(parts[1]), slot=int(parts[2]))
            # GridFS key: "product_159_slot_1"
            if s.startswith("product_") and "_slot_" in s:
                parts = s.split("_")  # ["product","159","slot","1"]
                pid  = int(parts[1])
                slot = int(parts[-1])
                return url_for("serve_product_image", product_id=pid, slot=slot)
            # Local static file fallback
            return url_for("static", filename=s)
        except Exception:
            return _PLACEHOLDER

    return dict(get_img=get_product_image, get_imgs=get_product_images,
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
        # Convert GridFS keys to URL routes
        urls = []
        for key in stored:
            # key format: "product_<id>_slot_<n>"  or legacy http URL
            if key.startswith("http"):
                urls.append(key)
            else:
                try:
                    slot = int(key.split("_slot_")[-1])
                    urls.append(f"img/{pid}/{slot}")
                except Exception:
                    urls.append(f"img/{pid}/1")
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
    """Auto-increment counter stored in MongoDB (replaces AUTOINCREMENT)."""
    result = get_db().counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=pymongo.ReturnDocument.AFTER
    )
    return result["seq"]

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
            "price": "$product.price",
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
    ]
    docs = []
    for i,(name,price,cat,icon,badge,rating,reviews,trending) in enumerate(products,1):
        docs.append({
            "seq_id":i,"name":name,"price":price,"category":cat,
            "icon":icon,"badge":badge,"rating":rating,"reviews":reviews,
            "trending":trending,"stock":100,"variants":"","images":[]
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
                    uid = next_seq("users")
                    col("users").insert_one({
                        "seq_id":uid,"username":u,
                        "password":generate_password_hash(pw),
                        "email":email or None,"phone":phone_full,
                        "country_code":country_code,"is_verified":1,
                        "role":account_type,"joined":datetime.datetime.utcnow(),
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
    return render_template("home.html",
        username=session["user"], cart_count=get_cart_count(uid),
        season=season, trending=trending, season_picks=season_picks,
        deals=deals, recently=recently, cat_counts=cat_counts,
        categories=list(CATEGORY_META.keys()), super_cats={},
        cat_meta=CATEGORY_META)

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
    sort_opts = sort_map.get(sort_f,[("rating",-1)])

    total_count = col("products").count_documents(filt)
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
        super_cats={}, cat_counts={})

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
    product_images = p.get("images") or get_product_images(pid)

    return render_template("product_detail.html",
        p=p, related=related, user_revs=user_revs, rating_dist=rating_dist,
        total_rev_count=total_rev_count, already_reviewed=already_reviewed,
        discount_pct=discount_pct, original_price=original_price,
        variants=variants, features=features, in_wishlist=in_wishlist,
        review_msg=review_msg, product_images=product_images,
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
    existing = col("cart").find_one({"user_id":uid,"product_id":pid})
    if existing:
        col("cart").update_one({"_id":existing["_id"]},
            {"$inc":{"quantity":qty},"$set":{"variant":variant}})
    else:
        seq = next_seq("cart")
        col("cart").insert_one({
            "seq_id":seq,"user_id":uid,"product_id":pid,
            "quantity":qty,"variant":variant,"added_at":datetime.datetime.utcnow()
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
@app.route("/profile", methods=["GET","POST"])
@login_required
def profile():
    uid=get_user_id(); msg=None
    if request.method=="POST":
        action=request.form.get("action")
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
    product_images=p.get("images") or get_product_images(pid)
    # Build absolute URL for the first image (used in OG meta tags)
    if product_images:
        first = product_images[0]
        if first.startswith("http"):
            share_img_url = first
        elif first.startswith("product_") and "_slot_" in first:
            parts = first.split("_")
            share_img_url = url_for("serve_product_image",
                product_id=int(parts[1]), slot=int(parts[-1]), _external=True)
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
    from flask import send_file, Response
    try:
        fs = get_fs()
        filename = f"product_{product_id}_slot_{slot}"
        f = fs.find_one({"filename": filename})
        if f:
            buf = io.BytesIO(f.read())
            buf.seek(0)
            return send_file(buf, mimetype="image/jpeg",
                             max_age=86400,   # cache 1 day
                             etag=False)
    except Exception as e:
        app.logger.error(f"GridFS serve error: {e}")
    # Fallback: local static file
    local_folder = os.path.join(os.path.dirname(__file__), "static", "product_images", str(product_id))
    for ext in ("jpg","jpeg","png","webp"):
        path = os.path.join(local_folder, f"{slot}.{ext}")
        if os.path.exists(path):
            return send_file(path, mimetype=f"image/{ext if ext!='jpg' else 'jpeg'}", max_age=86400)
    # Final fallback: 1x1 transparent PNG
    import base64
    px = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")
    return Response(px, mimetype="image/png")


# ═══════════════════════════════════════════════════════════
# ADMIN
# ═══════════════════════════════════════════════════════════
@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    if request.method=="POST":
        if request.form.get("secret")==ADMIN_SECRET:
            session["is_admin"]=True
            return redirect(url_for("admin_dashboard"))
        return render_template("admin_login.html",error="Invalid admin password.")
    return render_template("admin_login.html",error=None)

@app.route("/admin/profile")
@admin_required
def admin_profile():
    """Admin profile page — shows admin details and activity summary."""
    stats = {
        "products": col("products").count_documents({}),
        "orders":   col("orders").count_documents({}),
        "users":    col("users").count_documents({}),
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


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/admin")
@admin_required
def admin_dashboard():
    stats={
        "users":  col("users").count_documents({}),
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
    cat_revenue=list(col("order_items").aggregate([
        {"$lookup":{"from":"products","localField":"product_id","foreignField":"seq_id","as":"p"}},
        {"$unwind":"$p"},
        {"$group":{"_id":"$p.category","items":{"$sum":"$quantity"},"rev":{"$sum":{"$multiply":["$price","$quantity"]}}}},
        {"$sort":{"rev":-1}},{"$limit":8}
    ]))
    return render_template("admin_dashboard.html",stats=stats,
        recent_orders=recent_orders,top_products=top_products,cat_revenue=cat_revenue)

@app.route("/admin/products")
@admin_required
def admin_products():
    q=request.args.get("q",""); page=max(1,int(request.args.get("page",1)))
    filt={"name":{"$regex":q,"$options":"i"}} if q else {}
    total=col("products").count_documents(filt)
    items=[doc_to_dict(p) for p in col("products").find(filt).sort([("category",1),("name",1)]).skip((page-1)*30).limit(30)]
    total_pages=max(1,(total+29)//30)
    return render_template("admin_products.html",products=items,q=q,
        page=page,total_pages=total_pages,total=total,get_img=get_product_image)

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
        for opt in copts:
            val=request.form.get(f"opt_{opt['key']}","").strip()
            if val: var_parts.append(f"{opt['label']}: {val}")
        variants_str=" | ".join(var_parts)

        update={
            "name":new_name,"price":float(request.form["price"]),"category":new_cat,
            "badge":request.form.get("badge","") or None,"rating":float(request.form["rating"]),
            "stock":int(request.form.get("stock",100)),"trending":int(request.form.get("trending",0)),
            "variants":variants_str
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
        col("products").insert_one({
            "seq_id":new_pid,"name":request.form["name"],
            "price":float(request.form["price"]),"category":request.form["category"],
            "icon":request.form.get("icon","📦"),
            "badge":request.form.get("badge","") or None,
            "rating":float(request.form.get("rating",4.0)),
            "reviews":0,"trending":int(request.form.get("trending",0)),
            "stock":int(request.form.get("stock",100)),
            "variants":"","images":images
        })
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

@app.route("/admin/users")
@admin_required
def admin_users():
    pipeline=[
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
    return render_template("admin_users.html",users=users,pending_requests=[])

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
    import urllib.request, json as _json
    data=request.get_json(silent=True) or {}
    user_msg=(data.get("message","")).strip()[:500]
    history=data.get("history",[])[-10:]
    if not user_msg: return jsonify({"reply":"Please type a message."}),400
    cats_str=", ".join(CATEGORY_META.keys())
    system_prompt=(
        "You are Nexa, a friendly AI shopping assistant for Nexacart, an Indian e-commerce site. "
        "Help customers find products and answer questions about orders, delivery, returns, payments. "
        "Store details: 326 products across categories: "+cats_str+". "
        "Free delivery on all orders. 30-day returns. UPI + card payments. GST 9%. Delivery 3-5 days. "
        "Promo codes: SAVE10 MARKET20 FIRST50. Be concise, warm and helpful in 1-3 sentences."
    )
    messages=[]
    for h in history:
        if h.get("role") in ("user","assistant") and h.get("content"):
            messages.append({"role":h["role"],"content":str(h["content"])[:400]})
    messages.append({"role":"user","content":user_msg})
    api_key=os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        ml=user_msg.lower()
        if any(w in ml for w in ["return","refund"]): reply="30-day easy returns! Visit your Orders page to request a return."
        elif any(w in ml for w in ["deliver","ship"]): reply="Free delivery on all orders, arriving in 3-5 business days."
        elif any(w in ml for w in ["promo","code","discount"]): reply="Use SAVE10 (10% off), MARKET20 (20% off), or FIRST50 (50% off first order)!"
        elif any(w in ml for w in ["pay","upi","gpay","paytm"]): reply="We accept UPI (GPay, PhonePe, Paytm, BHIM) and Credit/Debit Cards."
        elif any(w in ml for w in ["hello","hi","hey"]): reply="Hello! I am Nexa, your Nexacart assistant. How can I help?"
        else: reply="I can help with products, delivery, returns and payments. What are you looking for?"
        return jsonify({"reply":reply})
    try:
        payload=_json.dumps({"model":"claude-haiku-4-5-20251001","max_tokens":200,"system":system_prompt,"messages":messages}).encode()
        req=urllib.request.Request("https://api.anthropic.com/v1/messages",data=payload,
            headers={"Content-Type":"application/json","x-api-key":api_key,"anthropic-version":"2023-06-01"},method="POST")
        with urllib.request.urlopen(req,timeout=12) as resp:
            result=_json.loads(resp.read())
        reply=result["content"][0]["text"]
    except Exception: reply="Sorry, having a brief issue! Try again shortly."
    return jsonify({"reply":reply})

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
# STARTUP
# ═══════════════════════════════════════════════════════════
with app.app_context():
    try:
        init_db()
        insert_sample_products()
    except Exception as e:
        app.logger.warning(f"Startup DB init: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG","false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)