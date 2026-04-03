# 🖼️ How Product Images Work in Nexacart

## Short Answer

**Admin uploads images → Flask stores them in MongoDB GridFS → served at `/img/<id>/<slot>`**

No local files. No external service. One MongoDB connection handles everything.

---

## Step-by-Step (Adding Images as Admin)

1. Log in to admin panel → `/admin/login`
2. Go to **Products** → click **Edit** on any product
3. Scroll to **Product Images** section
4. Click **Replace** or **Upload** on any slot (1–6)
5. Select a JPG, PNG, or WEBP image (max 5 MB)
6. The image appears in the slot preview immediately
7. Click **Save All Changes**

The image is now stored in MongoDB GridFS and visible to customers.

---

## Adding Images for a New Product

1. Go to **Add Product**
2. Fill in the product details
3. Click **Choose #1** through **Choose #6** to pick images
4. A live preview strip shows all selected images at the bottom
5. Click **Clear** on any slot to deselect before saving
6. Click **Add Product**

---

## Remove an Image

On the Edit Product page, each uploaded image has a red **Remove** button.
Clicking it:
- Immediately deletes the image from MongoDB GridFS
- Removes the key from the product document
- Reloads the edit page

---

## Where Are Images Stored?

Images are stored in **MongoDB GridFS** inside your Atlas cluster:

| Collection | What's there |
|-----------|-------------|
| `product_images.files` | Metadata: filename, size, upload date, content type |
| `product_images.chunks` | Binary data: the actual image bytes in 255 KB chunks |

### Key format
```
product_<product_id>_slot_<slot_number>
Example: product_5_slot_1
```

### To view in MongoDB Atlas
1. Go to cloud.mongodb.com → Collections
2. Open `product_images.files`
3. Each document = one image, with `filename`, `length` (bytes), `uploadDate`

### To view in MongoDB Compass
Connect to your cluster → select `nexacart` database → `product_images.files`

---

## How Images Are Served

The Flask route `/img/<product_id>/<slot>` reads from GridFS and returns the image:

```
Customer visits product page
         ↓
Browser requests /img/5/1
         ↓
Flask reads from MongoDB GridFS (key: product_5_slot_1)
         ↓
Returns JPEG bytes with Content-Type: image/jpeg
         ↓
Image displays in browser
```

Images are cached in the browser for 1 day (`max-age=86400`).

---

## Image Processing

When you upload an image, Flask automatically:
1. Reads the file bytes
2. Opens with **Pillow** (Python image library)
3. Converts to RGB (handles PNG transparency)
4. Resizes to max **800×800 pixels** (preserves aspect ratio)
5. Saves as **JPEG at 85% quality** (reduces file size)
6. Stores compressed version in GridFS

A 5 MB PNG typically becomes ~100–200 KB after processing.

---

## Troubleshooting

**Image shows as broken/missing on admin products page?**
→ The image is stored correctly but the URL generation failed. Check that the product has images in its `images` array in MongoDB.

**Upload button does nothing?**
→ File may be too large (max 5 MB) or wrong format. Only JPG, PNG, WEBP accepted.

**Images visible on user side but not admin edit page?**
→ This was a bug that is now fixed. The edit page uses `img_url()` which handles all GridFS key formats.
