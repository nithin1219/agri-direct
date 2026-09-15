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
- Product browsing, search, category and organic filters
- Cart quantity management and Cash on Delivery checkout
- Customer order history and fulfillment status
- Farmer product creation and inventory view
- Admin inventory, order, and session-revenue dashboard
- Seeded sample data with local `st.session_state` persistence
- Farm-and-plants visual theme on the public home screen

### Demo sign-in accounts

The zero-database demo seeds these accounts for local testing:

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer` or `customer@agridirect.local` | `customer123` |
| Farmer | `greenvalley` or `farmer@agridirect.local` | `farmer123` |
| Farmer | `sunrise` or `orchard@agridirect.local` | `orchard123` |
| Admin | `admin` or `admin@agridirect.local` | `admin123` |

New customer and farmer accounts can be registered from the sign-in screen. Signed-in farmers can publish listings with price, stock, description, and image URL; those images appear in the customer product cards. Customer orders are scoped to the signed-in account, while farmer listings are scoped to the signed-in farmer.

Customer orders are shown only to the signed-in customer, and farmer listings are shown only to the signed-in farmer. The base app is session-local and deploys without cloud credentials. If `AGRI_S3_BUCKET` and AWS credentials are configured, the app makes best-effort JSON snapshots to `agridirect/state.json`; a missing or unavailable bucket never breaks the site.

Biometric face recognition is not enabled in the base deployment because native biometric packages can fail Streamlit Cloud builds and require explicit consent, retention, and legal controls. Use the secure username/password FRS login in this deployment, or add a managed identity provider before enabling biometrics.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
