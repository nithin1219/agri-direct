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
- Optional consent-based Face ID verification using camera capture; enrolled users must pass face verification at sign-in
- Product browsing, search, category and organic filters
- Cart quantity management and Cash on Delivery checkout
- Customer order history and fulfillment status
- Farmer product creation and inventory view
- Admin inventory, order, and session-revenue dashboard
- Seeded sample data with local `st.session_state` persistence

### Demo sign-in accounts

The zero-database demo seeds these accounts for local testing:

| Role | Email | Password |
| --- | --- | --- |
| Customer | `customer` or `customer@agridirect.local` | `customer123` |
| Farmer | `greenvalley` or `farmer@agridirect.local` | `farmer123` |
| Farmer | `sunrise` or `orchard@agridirect.local` | `orchard123` |
| Admin | `admin` or `admin@agridirect.local` | `admin123` |

New customer and farmer accounts can be registered from the sign-in screen. Signed-in farmers can publish listings with price, stock, description, and image URL; those images appear in the customer product cards. Customer orders are scoped to the signed-in account, while farmer listings are scoped to the signed-in farmer.

### Online storage and privacy

For one-bucket online persistence, add an `AGRI_S3_BUCKET` Streamlit secret or environment variable plus normal AWS credentials. The app writes `agridirect/state.json` to that S3-compatible bucket after registrations, orders, product publishing, and Face ID enrollment. Without cloud credentials it safely uses browser session state.

Face ID is opt-in and requires explicit camera consent. Face encodings are stored for the account and never displayed as photos. For production, use a managed identity/biometric provider, encrypted storage, retention limits, and required legal consent notices.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
