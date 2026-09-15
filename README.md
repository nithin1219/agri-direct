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
- Session authentication with hashed passwords, registration, sign-in, and sign-out
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
| Customer | `customer@agridirect.local` | `customer123` |
| Farmer | `farmer@agridirect.local` | `farmer123` |
| Admin | `admin@agridirect.local` | `admin123` |

New customer and farmer accounts can be registered from the sign-in screen. Authentication is intentionally session-local for this self-contained demo; use an external identity provider and persistent database before production use.

## Repository notes

`frontend/` and `backend/` are retained as historical/reference implementations only. They are not required to install, run, or deploy AgriDirect. The supported entry point is the root `streamlit_app.py`; the legacy Flask and React dependency manifests are intentionally not part of the Streamlit setup.
