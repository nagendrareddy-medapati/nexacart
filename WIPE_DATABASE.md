# 🗑️ How to Wipe All Data and Start Fresh

Use this when you want to reset Nexacart to a completely clean state — no products, no users, no orders, no images.

---

## Option A — MongoDB Atlas UI (Easiest)

1. Go to **https://cloud.mongodb.com** → sign in
2. Click your cluster → **Browse Collections**
3. Select your database (e.g. `nexacart`)
4. Click the **trash icon** next to the database name → **Drop Database**
5. Type the database name to confirm → **Drop**

Done. Restart the app — it auto-reseeds products on first startup.

---

## Option B — MongoDB Compass (Desktop)

1. Open MongoDB Compass → connect to your cluster
2. Find your database (`nexacart`) in the left panel
3. Click the **trash icon** next to it
4. Type the name to confirm → **Drop Database**

---

## Option C — Python Script (Run Once)

Create a file `wipe.py` inside the `market` folder:

```python
import os
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/nexacart")
MONGO_DB  = os.environ.get("MONGO_DB", "nexacart")

client = MongoClient(MONGO_URI)
client.drop_database(MONGO_DB)
print(f"✅ '{MONGO_DB}' database dropped completely")
client.close()
```

Run it:
```bash
python wipe.py
```

Then delete `wipe.py` — you don't need it anymore.

---

## Option D — Drop Individual Collections

If you only want to reset specific data (not everything):

```python
import os
from pymongo import MongoClient

client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017/nexacart"))
db = client[os.environ.get("MONGO_DB", "nexacart")]

# Drop only what you want:
db.products.drop()                  # All products
db.product_images.files.drop()      # Product image metadata (GridFS)
db.product_images.chunks.drop()     # Product image binary data (GridFS)
db.users.drop()                     # All user accounts
db.orders.drop()                    # All orders
db.order_items.drop()               # All order line items
db.cart.drop()                      # All cart items
db.wishlist.drop()                  # All wishlists
db.reviews.drop()                   # All reviews
db.recently_viewed.drop()           # Browsing history
db.password_resets.drop()           # Password reset tokens
db.counters.drop()                  # Auto-increment counters

client.close()
print("✅ Selected collections dropped")
```

---

## After Wiping — Restart the App

```bash
python app.py
```

On startup the app will:
1. ✅ Recreate all MongoDB indexes
2. ✅ Detect empty `products` collection → seed 159 products (IDs starting from 1)
3. ✅ Show clean state — no users, no orders, no images

Then register a new account and start fresh.

---

## What Data Lives Where

| Data | MongoDB Collection | Notes |
|------|--------------------|-------|
| Products | `products` | name, price, category, images array, etc. |
| Product images | `product_images.files` + `product_images.chunks` | GridFS binary storage |
| Users | `users` | accounts with seq_id |
| Orders | `orders` | order headers |
| Order items | `order_items` | line items per order |
| Cart | `cart` | active cart items |
| Wishlist | `wishlist` | saved items |
| Reviews | `reviews` | product reviews |
| Browsing history | `recently_viewed` | per-user |
| Auto-increment IDs | `counters` | reset this if resetting products/users |

**Nothing is stored on disk.** The `static/product_images/` folder is intentionally empty.
