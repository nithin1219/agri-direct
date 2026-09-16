# AgriDirect

AgriDirect is a self-contained Streamlit marketplace that connects farmers directly with customers. The Streamlit app is the supported runtime; it needs no Flask server, JavaScript build, database server, or environment variables.

## Run locally

From the repository root:

```bash
python -m venv .venv
# Windows
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run streamlit_app.py
# macOS/Linux
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run streamlit_app.py
```

Open the URL printed by Streamlit (normally `http://localhost:8501`). The app seeds sample products and stores cart, orders, listings, and status updates in the current browser session.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), select the repository and the `main` branch.
3. Set **Main file path** to `streamlit_app.py`.
4. Deploy. Community Cloud installs the root `requirements.txt` automatically.

## Included marketplace flows

- Customer, farmer, and admin workspace selection
- Farmer Registration System (FRS) with username login, farmer registration, hashed passwords, and sign-out
- Registered email addresses, usernames, roles, and hashed passwords are stored in SQLite for later logins
- Email OTP verification before a new account is created
- Farmer FRS profile photo enrollment and farmer-only profile details
- Daily farmer verification with camera access when optional face matching is available
- Admin-only farmer FRS verification status and reset controls
- Admin FRS camera capture and verification for a selected farmer
- Admin account review, product moderation/removal, and order-status controls
- Product browsing, search, category and organic filters
- Newly published farmer listings become visible to customers after marketplace refresh when shared bucket storage is configured
- Farmer product image uploads for JPG, JPEG, PNG, WEBP, GIF, BMP, TIF, and TIFF, plus public image URLs
- Cart quantity management and Cash on Delivery checkout
- Welcome message at sign-in and thank-you confirmation after completed purchases
- Customer order history and fulfillment status
- Farmer product creation and inventory view
- Admin inventory, order, and session-revenue dashboard
- Seeded sample data with local `st.session_state` persistence
- Farm-and-plants visual theme on the public home screen

### Demo sign-in accounts

The app seeds these accounts for local testing:

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer` or `customer@agridirect.local` | `customer123` |
| Farmer | `greenvalley` or `farmer@agridirect.local` | `farmer123` |
| Farmer | `sunrise` or `orchard@agridirect.local` | `orchard123` |
| Admin | `admin` or `admin@agridirect.local` | `admin123` |

The seeded administrator can review farmer FRS profile-photo status, face-matching availability, and the last daily verification date, then require a farmer to verify again.

New customer and farmer accounts can be registered from the sign-in screen. Signed-in farmers can publish listings with price, stock, description, and image URL; those images appear in the customer product cards. Customer orders are scoped to the signed-in account, while farmer listings are scoped to the signed-in farmer.

Customer orders are shown only to the signed-in customer, and farmer listings are shown only to the signed-in farmer. Registered accounts are stored in `agridirect_users.db` (or the path in `AGRIDIRECT_DATABASE`) with PBKDF2 password hashes, so a user registers once and can sign in later with the same email/username and password. If `AGRI_S3_BUCKET` and AWS credentials are configured, the app also makes best-effort JSON snapshots to `agridirect/state.json`; a missing or unavailable bucket never breaks the site.

### Email OTP setup

Set these Streamlit secrets or deployment environment variables for real email delivery: `SMTP_HOST`, `SMTP_PORT` (normally `587`), `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_SENDER`. Codes expire after 10 minutes and are stored only as a hash until verification. If SMTP is not configured, the app remains usable in demo mode and displays the one-time code on the registration screen instead of failing.

Farmer registration requires a clear FRS profile photo and shows only the signed-in farmer's profile in the farmer dashboard. If a deployment provides the optional `face-recognition` package, the enrolled farmer receives a daily camera verification gate; otherwise the app safely falls back to username/password login without failing.

When `AGRI_S3_BUCKET` and AWS credentials are configured, the app restores users, hashed passwords, product listings, uploaded image bytes, orders, and verification dates from `agridirect/state.json`, then snapshots changes back to the same bucket. Do not store production credentials in source control; configure them as Streamlit secrets or deployment environment variables.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
