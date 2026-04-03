# How to Add Real Product Images to Nexacart

## Supported formats: JPG, JPEG, PNG

The website automatically checks for real photos before falling back to SVG placeholders.
Simply drop your image file into this folder (static/assets/) with the correct filename.

## Image filename reference

Replace any .svg file below with a .jpg, .jpeg, or .png of the same base name.

### Electronics
- tv_sony_55.jpg        → Sony Bravia 55" 4K TV
- tv_lg_oled.jpg        → LG OLED 65" TV
- echo_dot.jpg          → Amazon Echo Dot
- chromecast.jpg        → Chromecast with Google TV
- philips_hue.jpg       → Philips Hue Smart Bulb

### Laptops & Computers
- macbook_pro.jpg       → MacBook Pro 14" M3
- dell_xps.jpg          → Dell XPS 15
- hp_pavilion.jpg       → HP Pavilion 15
- lenovo_thinkpad.jpg   → Lenovo ThinkPad X1
- asus_rog.jpg          → ASUS ROG Strix G15

### Smartphones
- iphone_15_pro.jpg     → iPhone 15 Pro Max
- samsung_s24.jpg       → Samsung Galaxy S24 Ultra
- oneplus_12.jpg        → OnePlus 12
- nothing_phone.jpg     → Nothing Phone 2a
- pixel_8_pro.jpg       → Google Pixel 8 Pro

### Audio
- sony_wh1000.jpg       → Sony WH-1000XM5
- airpods_pro.jpg       → Apple AirPods Pro 2
- jbl_flip6.jpg         → JBL Flip 6 Speaker
- bose_qc45.jpg         → Bose QuietComfort 45

### Wearables
- apple_watch_ultra.jpg → Apple Watch Ultra 2
- samsung_watch6.jpg    → Samsung Galaxy Watch 6
- garmin_fenix.jpg      → Garmin Fenix 7X

### Clothing
- allen_solly_shirt.jpg → Allen Solly Shirt
- levis_511.jpg         → Levi's 511 Jeans
- biba_anarkali.jpg     → Biba Anarkali Kurta
- zara_dress.jpg        → Zara Floral Midi Dress
- nike_airmax.jpg       → Nike Air Max 270

### Beauty
- lakme_foundation.jpg  → Lakme Foundation
- mamaearth_serum.jpg   → Mamaearth Serum
- the_ordinary.jpg      → The Ordinary Hyaluronic

### Appliances
- lg_washer.jpg         → LG 7kg Washing Machine
- samsung_fridge.jpg    → Samsung 253L Fridge
- philips_airfryer.jpg  → Philips Air Fryer

### Category fallbacks (used when no specific image found)
- electronics.jpg       → All electronics without specific image
- smartphones.jpg       → All smartphones without specific image
- clothing_men.jpg      → Men's clothing fallback
- clothing_women.jpg    → Women's clothing fallback
- beauty.jpg            → Beauty products fallback
- groceries.jpg         → Groceries fallback
- health.jpg            → Health & wellness fallback
- sports.jpg            → Sports & fitness fallback

## Image size recommendation
- Minimum: 400×400 pixels
- Recommended: 800×800 pixels
- Format: Square images work best
- File size: Keep under 200KB for fast loading

## How it works
When you add macbook_pro.jpg, the website AUTOMATICALLY uses it instead of macbook_pro.svg.
No code changes needed — just drop the file in this folder and refresh!
