# Privacy Parcel Locker — Streamlit prototype

A multi-role, shared-database demo for the Privacy-Based Automated Parcel Locker Delivery System.

## Features
- Customer, Seller, Rider and Admin portals
- Signup/login with salted PBKDF2 password hashes
- SQLite data store shared by all roles in the same app instance
- Product shop, cart, two preferred hubs, stock checks and checkout
- Two payment options: pay now or pay after locker deposit
- Simulated payment verification; pickup QR is generated/activated only after verified payment
- Seller order processing and inventory
- Rider locker assignment with parcel-size compatibility and backup hub
- QR download and simulated QR verification/collection
- Notifications, messages, returns, complaints, audit log and admin controls
- No Cash on Delivery

## Run locally
Python 3.10+ recommended.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL shown by Streamlit.

## Demo admin
- Email: `admin@demo.pk`
- Password: `demo123`

Create separate Seller and Rider accounts from their role portals, and a Customer account to test the complete workflow.

## Deploy on Streamlit Community Cloud
1. Create a GitHub repository and upload `app.py`, `requirements.txt`, and `README.md`.
2. In Streamlit Community Cloud, choose the repository and `app.py` as the entry point.
3. Deploy.

**Important storage note:** SQLite is suitable for local testing and a single-instance prototype. Streamlit Community Cloud's local filesystem may be reset or ephemeral, and a local SQLite file is not a production-grade shared database. For a persistent, multi-user deployment, move the database to a managed service such as PostgreSQL/Supabase and configure secrets. Do not use this demo admin password in a public production deployment.

## Important limitations
This is a working prototype, not a production-ready secure delivery/payment system. Payment is simulated; it does not charge money. QR collection is simulated in the browser/app and does not operate physical locker hardware. Real deployment needs a verified payment gateway webhook, server-side authorization, HTTPS, rate limiting, secure session controls, backups, persistent database, and integration with the other group's QR/locker API.
