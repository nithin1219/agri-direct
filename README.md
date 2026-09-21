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

Open `http://localhost:8501` on the same computer. The repository includes `.streamlit/config.toml`, which binds Streamlit to `0.0.0.0` on port `8501` so the app can also be opened from another device on the same network.

To find the host computer's private IP address:

```powershell
# Windows
ipconfig
# Use the IPv4 Address, for example 192.168.1.25
```

Then open `http://<host-ip>:8501` from each phone, laptop, or desktop on the same Wi-Fi/LAN. For the current host network, the link is `http://172.20.10.2:8501`. Every device uses the same link, but each browser gets its own Streamlit session and login. If Windows Firewall asks, allow Python/Streamlit on the **Private networks** profile. Do not expose this development server directly to the public internet; use Streamlit Community Cloud or a properly secured reverse proxy for public access.

The single seeded administrator account is:

| Login | Password |
| --- | --- |
| `admin` or `admin@agridirect.local` | `admin123` |

Use that account from any device after opening the shared LAN link to manage users, FRS controls, products, and orders. Change this demo password before using the app for real users.

### Production admin account

For a real administrator, configure secrets on the deployed app; do not commit the password:

```toml
[admin]
email = "admin@your-domain.com"
username = "your-admin-username"
password = "use-a-long-unique-password"
```

The equivalent environment variables are `ADMIN_EMAIL`, `ADMIN_USERNAME`, and `ADMIN_PASSWORD`. On the next app start, this account is created or updated as an `Admin` account, its password is stored as a PBKDF2 hash in SQLite, and it can sign in from the local LAN link or the public deployment. The demo admin remains only as a local fallback when production admin secrets are not configured.

The app stores registrations and marketplace snapshots (products, orders, and uploaded image bytes) in `agridirect_users.db` (or the path in `AGRIDIRECT_DATABASE`). This means a new account or listing is available to later Streamlit sessions on the same deployment, and a saved account is not created again. Configure `AGRI_S3_BUCKET` plus AWS credentials for best-effort shared snapshots across multiple app replicas/devices; without shared storage, all replicas must use the same SQLite file. Cart contents remain browser-session scoped.

## Deploy on Streamlit Community Cloud

1. Push this repository to GitHub.
2. In [Streamlit Community Cloud](https://share.streamlit.io/), select the repository and the `main` branch.
3. Set **Main file path** to `streamlit_app.py`.
4. Deploy. Community Cloud installs the root `requirements.txt` automatically.

The public Community Cloud address remains the deployment URL assigned by Streamlit; the local IP address is only for a computer running Streamlit itself.

### Always-on access

Use the deployed Community Cloud address for access from any device at any time:

`https://agri-direct-nithin-1.streamlit.app/`

`localhost` and private IP links are not public hosting. They work only while the host computer is powered on, connected to the network, and running Streamlit; they cannot provide guaranteed 24/7 access after the computer sleeps, shuts down, loses its network connection, or closes the Streamlit process. For a permanent custom domain or guaranteed uptime, deploy the same app on an always-on server or managed hosting service.

## Included marketplace flows

- Customer, farmer, and admin workspace selection
- Farmer Registration System (FRS) with username login, farmer registration, hashed passwords, and sign-out
- Registered email addresses, usernames, roles, and hashed passwords are stored in SQLite for later logins
- Immediate account creation after valid registration details
- Farmer FRS profile photo enrollment and farmer-only profile details
- Daily farmer verification with camera access when optional face matching is available
- Admin-only farmer FRS verification status and reset controls
- Admin FRS camera capture and verification for a selected farmer
- FRS activation controls for farmers and the administrator, with daily camera access
- Admin account review, product moderation/removal, and order-status controls
- Product browsing, search, category and organic filters
- Newly published farmer listings become visible to every customer marketplace view automatically every second; SQLite persistence works locally and optional S3 snapshots synchronize separate replicas
- Farmer product image uploads for JPG, JPEG, PNG, WEBP, GIF, BMP, TIF, and TIFF, plus public image URLs
- Cart quantity management and Cash on Delivery checkout
- Welcome message at sign-in and thank-you confirmation after completed purchases
- Customer order history and fulfillment status
- Farmer product creation and inventory view
- Admin inventory, order, and session-revenue dashboard
- Seeded sample data with persistent SQLite storage and an administrator reset action
- Multilingual crop/listing assistant (English, Telugu, Hindi, Tamil, Kannada, Malayalam, Bengali, and Marathi) with microphone transcription when optional support is installed and a text fallback
- Top-level AI Voice Mode button with step-by-step microphone, language, transcription, and review instructions
- AI voice assistance button on the login page for multilingual sign-in and registration guidance
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

Farmer registration requires a clear FRS profile photo and shows only the signed-in farmer's profile in the farmer dashboard. Farmer password sign-in is followed by a camera face match; a different face or missing enrollment is rejected before the farmer session opens. The optional `face-recognition` package must be installed for this strict biometric login; if it is unavailable, farmer sign-in is blocked with a clear setup message rather than silently accepting an unverified face.

### Optional voice and face features

The base install intentionally has no fragile native dependencies. `st.audio_input` is used when provided by the installed Streamlit version. To transcribe recorded WAV audio, optionally install `SpeechRecognition` and provide the audio service it uses; otherwise use the transcript/text box. The listing assistant is a reviewable, lightweight field-prefill foundation, not a guarantee of translation or medical/agronomic advice. Install `face-recognition` only when its native build dependencies are available; its absence enables the documented secure fallback.

When `AGRI_S3_BUCKET` and AWS credentials are configured, the app restores users, hashed passwords, product listings, uploaded image bytes, orders, and verification dates from `agridirect/state.json`, then snapshots changes back to the same bucket. Do not store production credentials in source control; configure them as Streamlit secrets or deployment environment variables.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
