# 🚀 Nexacart — Deployment Guide

## Architecture

| Layer | Technology |
|-------|-----------|
| Web server | Vercel (serverless) or Render/Railway |
| Database | MongoDB Atlas (free M0 cluster) |
| Images | MongoDB GridFS (inside Atlas — no extra service) |

**No Cloudinary. No S3. No external image service.** Everything goes into MongoDB.

---

## Step 1 — Set Up MongoDB Atlas (Free)

1. Go to **https://www.mongodb.com/atlas** → Sign up free
2. Create a **Free Cluster** (M0, choose closest region to your users)
3. **Database Access** → Add Database User → set username + password
4. **Network Access** → Add IP Address → type `0.0.0.0/0` → Allow from anywhere
5. Click **Connect** → **Drivers** → copy the URI:
   ```
   mongodb+srv://<username>:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
   ```
6. Replace `<username>` and `<password>` with your credentials

---

## Step 2 — Push to GitHub

```bash
cd market
git init
git add .
git commit -m "Nexacart v1.0 — MongoDB GridFS edition"
```

Go to **github.com** → New repository → name it `nexacart` → no README → Create.

```bash
git remote add origin https://github.com/YOUR_USERNAME/nexacart.git
git branch -M main
git push -u origin main
```

---

## Step 3A — Deploy on Vercel (Recommended)

Vercel is serverless Python — works perfectly with MongoDB since there's no disk dependency.

1. Go to **https://vercel.com** → Sign up with GitHub
2. **Add New Project** → Import your `nexacart` repo
3. **Framework Preset**: Other
4. **Root Directory**: leave blank
5. Click **Environment Variables** and add:

| Variable | Value |
|----------|-------|
| `SECRET_KEY` | Any long random string |
| `MONGO_URI` | Your MongoDB Atlas connection string |
| `MONGO_DB` | `nexacart` |
| `ADMIN_SECRET` | Your chosen admin password |
| `MERCHANT_UPI_ID` | Your UPI ID (e.g. `yourname@upi`) |
| `MERCHANT_NAME` | `Nexacart` |
| `STRIPE_PUBLISHABLE_KEY` | From stripe.com (optional) |
| `STRIPE_SECRET_KEY` | From stripe.com (optional) |
| `ANTHROPIC_API_KEY` | From console.anthropic.com (optional, for AI chat) |

6. Click **Deploy** → live in ~2 minutes ✅

**Your app:** `https://nexacart.vercel.app`

### Vercel Free Tier Limits

| Limit | Free |
|-------|------|
| Serverless timeout | 10 seconds |
| Bandwidth | 100 GB/month |
| Deployments | Unlimited |
| Custom domain | ✅ Free |

---

## Step 3B — Deploy on Render.com (Alternative — has persistent server)

Render is better if you need long-running processes.

1. Go to **https://render.com** → Sign up with GitHub
2. **New +** → **Web Service** → connect your `nexacart` repo
3. Settings:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free
4. Add the same environment variables as Vercel (see table above)
5. Click **Create Web Service**

**Your app:** `https://nexacart.onrender.com`

> ⚠️ Free tier sleeps after 15 min inactivity. First request after sleep takes ~30s.

---

## Step 3C — Deploy on Railway.app (Alternative)

1. Go to **https://railway.app** → Login with GitHub
2. **New Project** → **Deploy from GitHub repo** → select `nexacart`
3. Add environment variables in the **Variables** tab
4. **Settings** → **Networking** → **Generate Domain**

Live in ~2 minutes ✅

---

## Step 4 — First-Time Setup

1. Visit your deployed URL → `/register`
2. Select **Admin** account type → create your account
3. Visit `/admin/login` → enter your `ADMIN_SECRET`
4. Start adding products with real images via `/admin/products/add`

> Products are **auto-seeded** (159 items) on first startup if the database is empty.

---

## How Images Work on Vercel

Vercel serverless functions have **no persistent disk** — so all images must be stored in MongoDB GridFS.

```
Admin uploads image
      ↓
Pillow resizes to max 800×800 JPEG
      ↓
Stored in MongoDB GridFS (product_images collection)
      ↓
Key "product_5_slot_1" saved in product document
      ↓
Customer requests /img/5/1
      ↓
Flask reads binary from GridFS → serves as image/jpeg
```

No disk. No CDN. No external service. One MongoDB connection handles everything.

---

## Updating Your Deployed App

```bash
git add .
git commit -m "describe your change"
git push
```

Vercel/Render/Railway automatically redeploys on every push to `main`. ✅

---

## MongoDB Atlas Storage Limits

| Plan | Storage | Enough for |
|------|---------|-----------|
| M0 Free | 512 MB | ~5,000 product images + millions of records |
| M2 ($9/mo) | 2 GB | ~40,000 product images |
| M5 ($25/mo) | 5 GB | ~100,000 product images |

Each product image is stored as a ~50–150 KB JPEG after Pillow compression.
512 MB free tier is plenty for a small-to-medium store.

---

## Wiping the Database

See **WIPE_DATABASE.md** for full instructions on how to reset all data.
