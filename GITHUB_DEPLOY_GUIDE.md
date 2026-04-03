# 📤 Step-by-Step: Push to GitHub & Deploy

A beginner-friendly guide. Follow each step in order.

---

## PART 1 — Push Code to GitHub

### Step 1: Install Git (skip if already installed)
Download from **https://git-scm.com/download/win** → install with defaults.

### Step 2: Open terminal in your project folder

**Windows:** Right-click the `market` folder → "Open in Terminal" (or PowerShell)

**Mac/Linux:** Open Terminal → `cd path/to/market`

### Step 3: Initialize Git repository
```bash
git init
git add .
git commit -m "Nexacart v1.0 — MongoDB GridFS edition"
```

### Step 4: Create a GitHub repository
1. Go to **https://github.com** → sign in (or sign up free)
2. Click **+** (top right) → **New repository**
3. Name: `nexacart`
4. Visibility: Public or Private (either works)
5. **Do NOT** check "Add a README file" — you already have one
6. Click **Create repository**

### Step 5: Push to GitHub
GitHub will show you commands — copy and run them. They look like:
```bash

```

✅ Your code is now on GitHub!

---

## PART 2 — Set Up MongoDB Atlas (Required)

This app uses MongoDB — **not SQLite** — so you must set this up before deploying.

### Step 1: Create free account
Go to **https://www.mongodb.com/atlas** → Sign up free (Google sign-in works)

### Step 2: Create a cluster
- Click **Build a Database** → choose **M0 Free**
- Select any region (Singapore or Mumbai for India)
- Click **Create**

### Step 3: Create a database user
- Click **Database Access** → **Add New Database User**
- Authentication: Password
- Username: `nexacartuser`
- Password: choose something secure, save it
- Role: **Read and write to any database**
- Click **Add User**

### Step 4: Allow network access
- Click **Network Access** → **Add IP Address**
- Click **Allow Access from Anywhere** (adds `0.0.0.0/0`)
- Click **Confirm**

### Step 5: Get connection string
- Click **Database** → **Connect** → **Drivers**
- Copy the connection string — looks like:
  ```
  mongodb+srv://nexacartuser:<password>@cluster0.xxxxx.mongodb.net/?retryWrites=true&w=majority
  ```
- Replace `<password>` with the password you set in Step 3

Save this string — you'll need it in the next part.

---

## PART 3 — Deploy on Vercel (Free, Recommended)

### Step 1: Sign up on Vercel
Go to **https://vercel.com** → **Sign Up** → choose **Continue with GitHub**

### Step 2: Import your project
- Click **Add New Project**
- Find your `nexacart` repository → click **Import**
- Framework Preset: **Other**
- Root Directory: leave blank
- Click **Environment Variables** (expand it)

### Step 3: Add environment variables
Click **+ Add** for each row:

| Name | Value |
|------|-------|
| `SECRET_KEY` | any random text like `nxc8f2k9p3m7q1r4s6t` |
| `MONGO_URI` | your Atlas connection string from Part 2 Step 5 |
| `MONGO_DB` | `nexacart` |
| `ADMIN_SECRET` | your chosen admin panel password |
| `MERCHANT_UPI_ID` | your UPI ID like `yourname@paytm` |
| `MERCHANT_NAME` | `Nexacart` |

### Step 4: Deploy
Click **Deploy** → git remote add origin https://github.com/YOUR_USERNAME/nexacart.git
git branch -M main
git push -u origin mainwait ~2 minutes → ✅ Live!

Your URL: `https://nexacart-yourusername.vercel.app`

---

## PART 4 — Deploy on Render.com (Alternative)

Use Render if you want a persistent server (better for file uploads, etc.)

### Step 1: Sign up
**https://render.com** → Sign Up with GitHub

### Step 2: Create Web Service
1. **New +** → **Web Service**
2. Connect your `nexacart` GitHub repo
3. Fill in:
   - **Name:** nexacart
   - **Region:** Singapore (closest for India)
   - **Branch:** main
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn app:app`
   - **Instance Type:** Free

### Step 3: Environment variables
Click **Environment** tab → add the same variables from Part 3 Step 3.

### Step 4: Deploy
Click **Create Web Service** → wait 3-5 minutes → ✅ Live!

Your URL: `https://nexacart.onrender.com`

> ⚠️ **Free tier note:** The app sleeps after 15 minutes of no traffic. First visitor after sleep waits ~30 seconds.

---

## PART 5 — After Deployment

### First-time setup
1. Visit your app URL → `/register`
2. Choose **Admin** account type
3. Register with your details
4. Go to `/admin/login` → enter your `ADMIN_SECRET` password
5. Start adding products with images!

### Products auto-seed
On first startup the app automatically adds 159 sample products to MongoDB. You can delete them from the admin panel and add your own real products.

### Updating the app
Whenever you change code locally, push to GitHub:
```bash
git add .
git commit -m "describe what you changed"
git push
```
Vercel and Render automatically redeploy. ✅

---

## ⚠️ Important Notes

| Topic | Note |
|-------|------|
| Database | MongoDB Atlas — NOT SQLite. Must be configured. |
| Images | Stored in MongoDB GridFS — no external service needed |
| Secrets | Never commit `.env` to GitHub — `.gitignore` already excludes it |
| Admin password | Change `ADMIN_SECRET` from default before going live |
| Free tier | Both Vercel and MongoDB Atlas have generous free tiers for small stores |

---

## 🆘 Troubleshooting

**"ModuleNotFoundError" on deploy**
→ Make sure `requirements.txt` has all packages. Run `pip freeze > requirements.txt` locally.

**"ServerSelectionTimeoutError" (MongoDB)**
→ Check `MONGO_URI` is set correctly. Check MongoDB Atlas Network Access allows `0.0.0.0/0`.

**Images not showing**
→ Check that `MONGO_URI` is correct. Images are served from MongoDB via `/img/<id>/<slot>`.

**Admin login not working**
→ Check `ADMIN_SECRET` environment variable matches what you type.

**Products not showing**
→ First startup seeds products automatically. Check MongoDB Atlas Collections for the `products` collection.
