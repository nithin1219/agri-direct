# AgriDirect

AgriDirect is a Streamlit marketplace for real farmer listings and customer orders. The Streamlit app is the supported runtime; it needs no Flask server or JavaScript build. It does not create demo accounts, pretend products, or sample orders. A production deployment must configure an administrator, verified-email delivery, and durable storage before account registration and marketplace changes are enabled.

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

Open `http://localhost:8501` on the same computer. The repository includes `.streamlit/config.toml`, which binds Streamlit to `0.0.0.0` on port `8501` so the app can also be opened from another device on the same network.

To find the host computer's private IP address:

```powershell
# Windows
ipconfig
# Use the IPv4 Address, for example 192.168.1.25
```

Then open `http://<host-ip>:8501` from each phone, laptop, or desktop on the same Wi-Fi/LAN, replacing `<host-ip>` with the host computer's current private IPv4 address. Every device uses the same link, but each browser gets its own Streamlit session and login. If Windows Firewall asks, allow Python/Streamlit on the **Private networks** profile. Do not expose this development server directly to the public internet; use Streamlit Community Cloud or a properly secured reverse proxy for public access.

### Required production configuration

In Streamlit Community Cloud, open **App settings → Secrets** and configure a unique administrator account and the external services below. Never commit credentials:

```toml
AGRI_S3_BUCKET = "your-private-agridirect-state-bucket"
AWS_REGION = "ap-south-1"
AWS_ACCESS_KEY_ID = "least-privilege-access-key"
AWS_SECRET_ACCESS_KEY = "least-privilege-secret-key"

[admin]
email = "admin@your-domain.com"
username = "your-admin-username"
password = "replace-with-a-unique-password-of-at-least-12-characters"

[email]
host = "smtp.your-provider.example"
port = 587
username = "smtp-account"
password = "smtp-app-password"
from_address = "AgriDirect <noreply@your-domain.com>"
```

Admin settings may alternatively be provided through the `ADMIN_EMAIL`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD` environment variables. The configured account becomes the only Admin; its password is stored as a PBKDF2 hash. There is deliberately no built-in or fallback admin password.

Customer and Farmer registration requires a working SMTP account: a one-time email code is required before an account is created. Password recovery also requires a one-time email code; Admin passwords are changed only through deployment secrets. The app stores salted PBKDF2-HMAC-SHA256 password hashes (never plaintext) in SQLite and synchronizes marketplace state to the configured private S3 bucket using server-side encryption. Conditional ETag writes reject stale concurrent updates rather than silently overwriting another session's orders or inventory. On Streamlit Cloud, the app refuses to report hosted data changes as saved when S3 storage is not configured or a write conflicts/fails. Give the AWS identity only the bucket/object permissions this app needs. Do not use a public bucket for account records, addresses, or face data.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), select the repository and the `main` branch.
3. Set **Main file path** to `streamlit_app.py`.
4. Deploy. Community Cloud installs the root `requirements.txt`, including prebuilt `dlib-bin` wheels for supported Linux Python versions, so it does not need to compile dlib or install build-tool system packages.

If Farmer/Admin sign-in reports that face matching is unavailable after deployment, confirm that the deployed branch includes the updated `requirements.txt`, then use **Manage app → Reboot** after installation finishes. Do not bypass face verification: Farmer/Admin sign-in remains blocked until the face-matching runtime is available.

The public Community Cloud address is `https://agri-direct-nithin-1.streamlit.app/`. In **App settings**, set viewer access to the intended audience. A healthy `/healthz` endpoint does not verify app access or deployment success; check the app page and Streamlit Cloud logs after each deployment.

### Always-on access

Use the deployed Community Cloud address for access from any device at any time:

`https://agri-direct-nithin-1.streamlit.app/`

`localhost` and private IP links are not public hosting. They work only while the host computer is powered on, connected to the network, and running Streamlit; they cannot provide guaranteed 24/7 access after the computer sleeps, shuts down, loses its network connection, or closes the Streamlit process. For a permanent custom domain or guaranteed uptime, deploy the same app on an always-on server or managed hosting service.

## Included marketplace flows

- Customer, farmer, and admin workspace selection
- Farmer Registration System (FRS) with username login, farmer registration, hashed passwords, and sign-out
- Registered email addresses must pass a one-time SMTP email code before the account is created
- Password recovery requires a one-time email code and durable storage; Admin passwords are managed through deployment secrets
- Farmer FRS profile photo enrollment and farmer-only profile details
- Farmer and Admin accounts use password plus face verification: first successful password login enrolls and saves a camera face, and later logins require a face match; Customers sign in with their password only and do not enroll or store face data
- Face verification is required at sign-in only; Farmer and Admin dashboards do not require an additional daily camera check
- Successful face enrollment and verification captures are saved with the shared account state; the enrolled profile photo remains unchanged
- Admin-only farmer FRS verification status
- Admin FRS camera capture and verification for a selected farmer
- Admin account review, product moderation/removal, delivery-status, and COD collection controls
- Product browsing, search, category and organic filters
- Newly published farmer listings refresh in customer views automatically; hosted changes require successful writes to the configured private S3 snapshot
- Farmer product image uploads for JPG, JPEG, PNG, WEBP, GIF, BMP, TIF, and TIFF, plus public image URLs
- Working add-to-cart buttons with immediate cart-badge refresh, visible cart item summary, quantity management, address checkout, Cash on Delivery orders, a non-payment order reference, immediate cancellation for Placed/Confirmed/Preparing orders, and order history
- Welcome message at sign-in and thank-you confirmation after completed purchases
- Customer order history and fulfillment status
- Live order notifications refresh every five seconds in Admin and Farmer dashboards; each farmer receives only the items belonging to their listings, with customer, quantity, delivery, payment, and status details
- Complete order records are saved to durable hosted storage before checkout is confirmed and are shown with item-level farmer, quantity, and price details in Customer, Farmer, and Admin dashboards
- Farmer product creation and inventory view
- Farmer listings include farm location, optional coordinates, crop details, and nearby-farm visibility for customers
- Checkout captures the selected farmer/listing, location, distance, ETA estimate, and no-key Google Maps/OpenStreetMap route links
- Admin inventory, gross order-value summaries (distinct from collected money), manually recorded COD collection, full order history, and fulfillment-status dashboard
- Persistent listings, account, and order state backed by a private S3 snapshot in hosted deployments
- Multilingual crop/listing assistant (English, Telugu, Hindi, Tamil, Kannada, Malayalam, Bengali, and Marathi) with microphone transcription when optional support is installed and a text fallback
- Top-level AI Voice Mode button with step-by-step microphone, language, transcription, and review instructions
- AI voice assistance button on the login page for multilingual sign-in and registration guidance
- AI account assistant can fill spoken/transcribed email, username, and account type on login/registration; passwords always remain manual
- Farm-and-plants visual theme on the public home screen

New Customer and Farmer accounts are created only after SMTP email verification and a successful write to durable storage. There are no built-in test accounts or listings. Farmers publish their own real inventory; customer order history is scoped to the signed-in verified account.

Farmer listings and uploaded images are stored in the configured private S3 snapshot. If hosted durable storage is unavailable, the app does not confirm new account, listing, or order writes as successful.

Farmers can add a farm/town location, optional latitude/longitude, and crop details to each listing. Customers see those details in the marketplace and select a listing as the purchase context at checkout. If both the farmer and customer provide coordinates, distance is calculated locally with the Haversine formula; otherwise customers can enter an approximate distance or continue without geolocation. ETA is clearly labeled as an estimate using a 20–30 minute baseline plus a small distance adjustment. Route links use Google Maps and OpenStreetMap directly and do not require API keys.

Orders, including farmer/location/distance/ETA metadata, are included in the shared S3 snapshot. Distances and delivery-time values are estimates, not carrier dispatch or guaranteed delivery schedules.

Checkout uses Cash on Delivery only. Each accepted order receives an order number and non-payment order reference, clears the cart, reserves stock, and appears in customer order history and Admin order reports. The reference is not a payment transaction, and the app does not claim to process or collect money. The farmer/operator must arrange actual delivery and confirm collection outside the app.

Order placement is confirmed only after the complete order and updated inventory are saved to durable hosted storage; if the write fails, inventory and cart contents are restored and checkout reports an error. Each order preserves the customer, all ordered items, item-level farmer ownership, quantities and amounts, address, payment method, ETA, and status for customer history, per-farmer notifications, and Admin reports.

If a user forgets a password, select **Forgot password?** and complete the one-time code sent to the registered email address. SMTP delivery and durable storage must both be working. Admin passwords are changed through protected Streamlit deployment secrets, not the self-service flow.

Customers can cancel an order while it is Placed, Confirmed, or Preparing. Cancellation immediately marks the order as Cancelled, restores the reserved quantities to marketplace stock, records the cancellation time, and shows the confirmation in order history. Orders already Out for delivery or Delivered cannot be cancelled from the customer dashboard.

Customer orders are shown only to the signed-in customer, and farmer listings are shown only to the signed-in farmer. The local SQLite database supports development; hosted deployments require the configured S3 snapshot for durable, shared state.

Farmer registration requires a verified email and a clear FRS profile photo. Farmer and Admin accounts require password and camera face verification; face templates and verification captures are stored in the account snapshot. Customers use email/password only. Farmer and Admin sign-in remains blocked if face matching or durable storage is unavailable; there is no password-only fallback. The requirements file installs the prebuilt dlib runtime and matching face models on Windows and supported Linux Python versions. On Windows, install manually with:

```powershell
python -m pip install dlib-bin==20.0.1 face-recognition-models==0.3.0
```

If face matching is unavailable, farmer login is blocked with a clear setup message rather than silently accepting an unverified face.

### Voice and face features

Face-matching uses the prebuilt `dlib-bin` runtime and `face-recognition-models` on supported platforms; startup loads the native runtime only when a Farmer/Admin face action needs it. `st.audio_input` is used when provided by the installed Streamlit version. To transcribe recorded WAV audio, optionally install `SpeechRecognition` and provide the audio service it uses; otherwise use the transcript/text box. The listing assistant is a reviewable, lightweight field-prefill foundation, not a guarantee of translation or medical/agronomic advice.

When `AGRI_S3_BUCKET` and AWS credentials are configured, the app restores and writes users, hashed passwords, listings, uploaded image bytes, orders, and verification dates at `agridirect/state.json`. Do not store production credentials in source control; configure them as Streamlit secrets or deployment environment variables.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
