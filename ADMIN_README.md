# ⚙️ Nexacart Admin Panel Reference

## Accessing the Admin Panel

| Step | Action |
|------|--------|
| 1 | Run `python app.py` |
| 2 | Open `http://127.0.0.1:5000/admin/login` |
| 3 | Enter your `ADMIN_SECRET` password |
| 4 | Default password: `nexacart_admin_2025` |

> **Change the password** by setting the `ADMIN_SECRET` environment variable. Never use the default in production.

---

## Admin Sections

| Section | URL | What you can do |
|---------|-----|-----------------|
| Dashboard | `/admin` | Revenue, orders, users, product stats at a glance |
| Products | `/admin/products` | Search, view, edit, delete all products with real image thumbnails |
| Add Product | `/admin/products/add` | Add new products with up to 6 images, live preview strip |
| Edit Product | `/admin/products/edit/<id>` | Edit details, replace images, remove images, set options |
| Orders | `/admin/orders` | View all orders, update order status |
| Users | `/admin/users` | View registered customers, roles, order counts, spending |
| Profile | `/admin/profile` | Admin profile, stats summary, permissions overview |

---

## Managing Products

### Adding a Product
1. Go to **Add Product** → fill in name, price, category, badge, rating, stock
2. Upload up to **6 images** (JPG, PNG, WEBP, max 5 MB each)
3. Images are stored in **MongoDB GridFS** — no external service needed
4. Click **Add Product**

### Editing a Product
1. Go to **Products** → click **Edit** on any row
2. Update any field — name, price, category, badge, stock, trending
3. **Product Options** section shows category-specific variants (sizes, colours, etc.)
4. **Images section:**
   - Click **Replace** to upload a new image for that slot
   - Click **Remove** to permanently delete that image from MongoDB
   - Empty slots show a "+" placeholder — click **Upload** to add
5. Click **Save All Changes**

### Deleting a Product
- Click **Del** button on the products list, or
- Click **Delete Product** in the Danger Zone on the Edit page
- This permanently removes the product AND all its GridFS images from MongoDB

---

## Product Images — How They Work

Images are stored in **MongoDB GridFS**, not on the server disk.

| Item | Detail |
|------|--------|
| Storage location | MongoDB `product_images` collection (GridFS) |
| Key format | `product_<id>_slot_<n>` (e.g. `product_5_slot_1`) |
| Served at | `/img/<product_id>/<slot>` |
| Max per product | 6 images |
| Accepted formats | JPG, PNG, WEBP |
| Auto-resize | Yes — Pillow resizes to max 800×800 JPEG before storing |
| View in MongoDB | Atlas → Collections → `product_images.files` |

---

## Order Status Flow

```
Confirmed → Processing → Shipped → Delivered
                                 ↘ Cancelled
```

To update: **Orders** page → select new status from dropdown → **Update**.

---

## User Account Types

| Type | Can do |
|------|--------|
| Customer (`buyer`) | Shop, add to cart, place orders, write reviews |
| Admin | All customer actions + full admin panel access |

Users choose their type at registration. No invite code is required.

---

## Login System

Users can log in with any of:
- **Username** (e.g. `priya_k`)
- **Email address** (e.g. `priya@gmail.com`)
- **Phone number** (e.g. `+91 98765 43210`)

Brute-force protection: after 5 failed attempts, login is blocked for that session.

---

## Forgot Password Flow

1. User goes to `/forgot-password`
2. Enters username / email / phone
3. System generates a time-limited reset token (valid 2 hours)
4. **Demo mode:** reset link shown directly on screen
5. **Production:** configure an email/SMS service to send the link

---

## Promo Codes

| Code | Discount |
|------|----------|
| SAVE10 | 10% |
| MARKET20 | 20% |
| TECH15 | 15% |
| WELCOME5 | 5% |
| FASHION30 | 30% |
| BEAUTY15 | 15% |
| FOOD20 | 20% |
| SUMMER25 | 25% |
| WINTER20 | 20% |
| MONSOON15 | 15% |
| FIRST50 | 50% |

---

## Admin Panel on Mobile

The admin panel is fully responsive:
- On screens ≤768px, the sidebar is hidden by default
- Tap the **☰ hamburger button** (top-left) to open the sidebar
- Tap outside the sidebar or click any link to close it
- Table columns collapse on small screens to show the most important data

---

## Viewing Data in MongoDB Atlas

1. Log in to [cloud.mongodb.com](https://cloud.mongodb.com)
2. Click your cluster → **Browse Collections**
3. Select the `nexacart` database
4. Browse these collections:

| Collection | What's inside |
|-----------|---------------|
| `products` | All product documents with `seq_id`, name, price, category, `images` array |
| `product_images.files` | GridFS image metadata (filename, size, upload date) |
| `product_images.chunks` | GridFS image binary data |
| `users` | All registered user accounts |
| `orders` | All placed orders |
| `order_items` | Line items for each order |
| `cart` | Active cart items |
| `wishlist` | Saved wishlist items |
| `reviews` | Product reviews |
| `recently_viewed` | User browsing history |
| `counters` | Auto-increment ID counters |

---

## Security Checklist

- [ ] Change `ADMIN_SECRET` from the default before going live
- [ ] Set a strong random `SECRET_KEY`
- [ ] Use MongoDB Atlas IP whitelist (not 0.0.0.0/0) for production
- [ ] Set `FLASK_DEBUG=false` in production
- [ ] Do not commit `.env` to GitHub
