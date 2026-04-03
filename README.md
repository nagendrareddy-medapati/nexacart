# 🛍️ Nexacart — E-Commerce Web App

A full-featured Indian e-commerce platform built with **Python Flask** + **MongoDB Atlas** + **MongoDB GridFS** for images. Ready to deploy on Vercel with zero external image services.

---

## ✨ Features

### Customer Side
- 159+ products across 30 categories
- Browse by category grid on home page
- Login with username, email, or phone number
- Customer & Admin account types on registration
- Forgot password / reset password flow
- DB-backed cart, wishlist, and order history
- Product pages with real reviews, image gallery (up to 6 images), variant/size selector
- UPI payments (GPay, PhonePe, Paytm, BHIM, Amazon Pay) + Stripe card payments
- AI-powered product recommendations
- AI chatbot (Nexa) — uses Claude API if key set, else rule-based fallback
- Promo codes, GST calculation, seasonal deals
- Shareable product links (WhatsApp, Telegram, Email, Twitter, Facebook)
- Recently viewed products
- Rewards & loyalty points
- Mobile responsive — works on phones, tablets, desktop

### Admin Panel (`/admin`)
- Dashboard with revenue, orders, users, product stats
- Product management — add, edit, delete with up to 6 images each
- **Images stored in MongoDB GridFS** — no Cloudinary, no AWS, no external service
- Remove / Replace individual product images
- Orders management — update status (Confirmed → Processing → Shipped → Delivered)
- Users management — view accounts, roles, spending
- Admin profile page with permissions overview
- Mobile-friendly sidebar with hamburger menu

---

## 🚀 Quick Start (Local)

### 1. Prerequisites
- Python 3.10+
- MongoDB Atlas account (free) — or local MongoDB

### 2. Clone and install
```bash
git clone https://github.com/YOUR_USERNAME/nexacart.git
cd nexacart
python -m venv venv

# Windows
venv\Scripts\activate
# Mac/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Set environment variables
Create a `.env` file or set these in your terminal:

```env
SECRET_KEY=your-random-secret-key
MONGO_URI=mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true&w=majority
MONGO_DB=nexacart
ADMIN_SECRET=your-admin-password
MERCHANT_UPI_ID=yourname@upi
MERCHANT_NAME=Nexacart
```

### 4. Run
```bash
python app.py
```

Open **http://127.0.0.1:5000**

> **First run:** The app auto-seeds 159 products into MongoDB. No manual setup needed.

---

## 🗄️ Database — MongoDB Atlas (Free)

This project uses **MongoDB Atlas** (free M0 cluster) for everything:

| Data | Collection |
|------|-----------|
| Products, users, orders, cart | Normal MongoDB collections |
| Product images (binary) | `product_images` (GridFS) |
| Counters for auto-increment IDs | `counters` |

**Images are served via** `/img/<product_id>/<slot>` — no static files, no external CDN.

To wipe all data and start fresh, see **WIPE_DATABASE.md**.

---

## 🔑 Admin Panel

| Item | Value |
|------|-------|
| URL | `http://127.0.0.1:5000/admin/login` |
| Default password | `nexacart_admin_2025` |
| Change it | Set `ADMIN_SECRET` environment variable |
| Profile page | `http://127.0.0.1:5000/admin/profile` |

---

## ☁️ Deploy to Vercel (Recommended)

See **DEPLOY.md** for the complete step-by-step guide.

**Quick summary:**
1. Push to GitHub
2. Import repo on [vercel.com](https://vercel.com)
3. Set environment variables (see DEPLOY.md)
4. Click Deploy — live in ~2 minutes

---

## 📁 Project Structure

```
nexacart/
├── app.py                   ← Main Flask app (1700+ lines, all routes)
├── requirements.txt         ← Python dependencies
├── vercel.json              ← Vercel deployment config
├── Procfile                 ← For Render/Railway/Heroku
├── render.yaml              ← Render.com config
├── runtime.txt              ← Python version pin
├── .env.example             ← Environment variable template
├── README.md                ← This file
├── DEPLOY.md                ← Full Vercel deployment guide
├── ADMIN_README.md          ← Admin panel reference
├── GITHUB_DEPLOY_GUIDE.md   ← Step-by-step GitHub + deploy guide
├── WIPE_DATABASE.md         ← How to reset all MongoDB data
├── static/
│   ├── css/style.css        ← All styles (1500+ lines, fully responsive)
│   ├── js/main.js           ← Client-side JS
│   ├── assets/upi/          ← UPI payment app icons (GridFS)
│   └── favicon.ico
└── templates/               ← 32 Jinja2 HTML templates
    ├── base.html            ← Customer layout shell
    ├── home.html            ← Home page with category grid
    ├── products.html        ← Product listing with filters
    ├── product_detail.html  ← Product page with gallery
    ├── cart.html
    ├── checkout.html
    ├── upi_payment.html     ← UPI payment with real icons
    ├── login.html / register.html
    ├── profile.html / orders.html
    ├── admin_base.html      ← Admin layout (mobile responsive)
    ├── admin_dashboard.html
    ├── admin_products.html  ← Product list with real images
    ├── admin_edit_product.html  ← Edit with remove/replace images
    ├── admin_add_product.html   ← Add with live preview strip
    ├── admin_orders.html
    ├── admin_users.html
    ├── admin_profile.html   ← Admin profile + permissions
    └── ...
```

---

## 🛠️ Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11 + Flask 3.0 |
| Database | MongoDB Atlas (pymongo 4.7) |
| Image storage | MongoDB GridFS (Pillow for resize) |
| Frontend | Vanilla HTML/CSS/JS + Jinja2 |
| Payments | Stripe (cards) + UPI deep links |
| AI chat | Anthropic Claude API (optional) |
| Auth | Werkzeug password hashing |
| Server | Gunicorn (production) |

---

## 🔐 Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | ✅ Yes | Flask session secret (any random string) |
| `MONGO_URI` | ✅ Yes | MongoDB Atlas connection string |
| `MONGO_DB` | ✅ Yes | Database name (use `nexacart`) |
| `ADMIN_SECRET` | ✅ Yes | Admin panel password |
| `MERCHANT_UPI_ID` | Recommended | Your UPI ID for payments |
| `MERCHANT_NAME` | Recommended | Your store name |
| `STRIPE_PUBLISHABLE_KEY` | Optional | Stripe card payments |
| `STRIPE_SECRET_KEY` | Optional | Stripe card payments |
| `ANTHROPIC_API_KEY` | Optional | Powers the Nexa AI chatbot |
| `FLASK_DEBUG` | Optional | Set `true` for dev mode |

---

## 🖼️ How Product Images Work

1. Admin uploads image via **Edit Product** or **Add Product** page
2. Flask reads the bytes → Pillow resizes to max 800×800 JPEG
3. Stored in **MongoDB GridFS** under key `product_<id>_slot_<n>`
4. URL saved in product document `images` array
5. Served at `/img/<product_id>/<slot>` to customers and admin

**No files stored locally.** `static/product_images/` is intentionally empty.

To view images in MongoDB: **Atlas → Collections → `product_images.files`**

---

## 🏷️ Promo Codes

| Code | Discount |
|------|----------|
| SAVE10 | 10% off |
| MARKET20 | 20% off |
| TECH15 | 15% off |
| WELCOME5 | 5% off |
| FASHION30 | 30% off |
| FIRST50 | 50% off first order |
| SUMMER25 | 25% off |
| WINTER20 | 20% off |
| MONSOON15 | 15% off |

---

## ⚠️ Before Going Live

1. Set a strong random `SECRET_KEY`
2. Set a strong `ADMIN_SECRET` (not the default)
3. Use MongoDB Atlas with IP whitelist for production
4. Add real Stripe keys for live card payments
5. Set `FLASK_DEBUG=false`
6. Add real UPI ID for `MERCHANT_UPI_ID`

---

Built with ❤️ using Python Flask + MongoDB
