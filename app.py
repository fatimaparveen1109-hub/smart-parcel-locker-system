import streamlit as st
import sqlite3
import hashlib
import hmac
import secrets
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

try:
    import qrcode
    from io import BytesIO
    QR_AVAILABLE = True
except ImportError:
    QR_AVAILABLE = False

APP_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PARCEL_DB_PATH", str(APP_DIR / "parcel_locker.db")))
APP_NAME = "Privacy Parcel Locker"
ROLES = ["Customer", "Seller", "Rider", "Admin"]
SIZES = {"S": 1, "M": 2, "L": 3}
STATUS = ["Placed", "Accepted", "Packed", "With rider", "In locker", "Ready for pickup", "Collected", "Rejected", "Awaiting locker"]

st.set_page_config(page_title=APP_NAME, page_icon="📦", layout="wide", initial_sidebar_state="expanded")

def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def connect():
    con = sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 180_000)
    return salt.hex() + "$" + digest.hex()

def verify_password(password, stored):
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 180_000).hex()
        return hmac.compare_digest(candidate, digest_hex)
    except Exception:
        return False

def init_db():
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, user_code TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT DEFAULT '', area TEXT DEFAULT '',
            password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, emoji TEXT DEFAULT '📦',
            price INTEGER NOT NULL, size TEXT NOT NULL, stock INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS hubs(
            id INTEGER PRIMARY KEY AUTOINCREMENT, hub_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, area TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS lockers(
            id INTEGER PRIMARY KEY AUTOINCREMENT, hub_id INTEGER NOT NULL REFERENCES hubs(id),
            locker_code TEXT NOT NULL, size TEXT NOT NULL, order_id INTEGER UNIQUE, active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(hub_id, locker_code)
        );
        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT UNIQUE NOT NULL,
            customer_id INTEGER NOT NULL REFERENCES users(id), customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL,
            customer_area TEXT NOT NULL, hub1_id INTEGER NOT NULL REFERENCES hubs(id), hub2_id INTEGER NOT NULL REFERENCES hubs(id),
            selected_hub_id INTEGER REFERENCES hubs(id), locker_id INTEGER REFERENCES lockers(id),
            total INTEGER NOT NULL, size TEXT NOT NULL, payment_choice TEXT NOT NULL, paid INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Placed', pickup_token TEXT UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS order_items(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id), product_name TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id),
            amount INTEGER NOT NULL, method TEXT NOT NULL, status TEXT NOT NULL, reference TEXT UNIQUE, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_code TEXT UNIQUE NOT NULL, order_id INTEGER NOT NULL REFERENCES orders(id),
            customer_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL, details TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Open', decision TEXT DEFAULT '', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id),
            message TEXT NOT NULL, created_at TEXT NOT NULL, seen INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER REFERENCES orders(id),
            sender_id INTEGER NOT NULL REFERENCES users(id), recipient_role TEXT NOT NULL,
            message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, action TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            con.execute("INSERT INTO users(role,user_code,name,email,phone,area,password_hash,created_at) VALUES(?,?,?,?,?,?,?,?)",
                ("Admin", "ADM-4001", "System Admin", "admin@demo.pk", "", "", hash_password("demo123"), now_text()))
        if con.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            con.executemany("INSERT INTO products(name,emoji,price,size,stock) VALUES(?,?,?,?,?)", [
                ("Running shoes", "👟", 8900, "M", 60),
                ("Wireless earbuds", "🎧", 6500, "S", 60),
                ("Cotton kurta", "👕", 2400, "S", 60),
                ("Backpack", "🎒", 3200, "M", 40),
            ])
        if con.execute("SELECT COUNT(*) FROM hubs").fetchone()[0] == 0:
            hub_rows = [("H1", "Gulberg Hub", "Gulberg"), ("H2", "DHA Hub", "DHA"), ("H3", "Saddar Hub", "Saddar")]
            con.executemany("INSERT INTO hubs(hub_code,name,area) VALUES(?,?,?)", hub_rows)
        if con.execute("SELECT COUNT(*) FROM lockers").fetchone()[0] == 0:
            for hub in con.execute("SELECT id FROM hubs").fetchall():
                hid = hub["id"]
                for code, size in [("S1","S"),("S2","S"),("S3","S"),("S4","S"),("M1","M"),("M2","M"),("M3","M"),("L1","L")]:
                    con.execute("INSERT INTO lockers(hub_id,locker_code,size) VALUES(?,?,?)", (hid, code, size))

init_db()

def audit(actor, action):
    with connect() as con:
        con.execute("INSERT INTO audit(actor,action,created_at) VALUES(?,?,?)", (actor, action, now_text()))

def notify(user_id, message):
    if user_id:
        with connect() as con:
            con.execute("INSERT INTO notifications(user_id,message,created_at) VALUES(?,?,?)", (user_id, message, now_text()))

def money(value):
    return f"Rs {int(value):,}"

def get_user_by_email(email, role=None):
    with connect() as con:
        if role:
            return con.execute("SELECT * FROM users WHERE lower(email)=lower(?) AND role=?", (email.strip(), role)).fetchone()
        return con.execute("SELECT * FROM users WHERE lower(email)=lower(?)", (email.strip(),)).fetchone()

def current_user():
    uid = st.session_state.get("user_id")
    if not uid:
        return None
    with connect() as con:
        return con.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

def all_hubs():
    with connect() as con:
        return con.execute("SELECT * FROM hubs WHERE active=1 ORDER BY id").fetchall()

def hub_name(hub_id):
    if not hub_id:
        return "Not assigned"
    with connect() as con:
        row = con.execute("SELECT name FROM hubs WHERE id=?", (hub_id,)).fetchone()
        return row["name"] if row else "Unknown hub"

def order_items(order_id):
    with connect() as con:
        return con.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY id", (order_id,)).fetchall()

def order_row(order_id):
    with connect() as con:
        return con.execute("""SELECT o.*, h1.name AS hub1_name, h2.name AS hub2_name, hs.name AS selected_hub_name,
            l.locker_code FROM orders o
            LEFT JOIN hubs h1 ON o.hub1_id=h1.id LEFT JOIN hubs h2 ON o.hub2_id=h2.id
            LEFT JOIN hubs hs ON o.selected_hub_id=hs.id LEFT JOIN lockers l ON o.locker_id=l.id
            WHERE o.id=?""", (order_id,)).fetchone()

def my_orders(user_id):
    with connect() as con:
        return con.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC", (user_id,)).fetchall()

def order_for_delivery(delivery_id):
    with connect() as con:
        return con.execute("SELECT * FROM orders WHERE delivery_id=?", (delivery_id,)).fetchone()

def status_badge(status, paid):
    if status == "Collected":
        return "🟢 Collected"
    if status == "Ready for pickup":
        return "🟢 Ready for pickup"
    if status == "In locker" and not paid:
        return "🟠 Awaiting payment"
    if status in ("Rejected", "Awaiting locker"):
        return "🔴 " + status
    return "🔵 " + status

def make_qr_bytes(token):
    if not QR_AVAILABLE:
        return None
    img = qrcode.make(token)
    out = BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()

def create_account(role, name, email, password, phone=""):
    email = email.strip().lower()
    if not name.strip() or not email or len(password) < 8:
        return False, "Enter your name/email and use a password of at least 8 characters."
    if role == "Customer" and not phone.strip():
        return False, "Customers must enter a phone number for private contact."
    if get_user_by_email(email):
        return False, "This email is already registered. Use another email or log in."
    with connect() as con:
        count = con.execute("SELECT COUNT(*) FROM users WHERE role=?", (role,)).fetchone()[0]
        prefix = {"Customer":"CUS", "Seller":"SEL", "Rider":"RID"}[role]
        code = f"{prefix}-{1001 + count}"
        while con.execute("SELECT 1 FROM users WHERE user_code=?", (code,)).fetchone():
            code = f"{prefix}-{secrets.randbelow(8000)+1000}"
        con.execute("""INSERT INTO users(role,user_code,name,email,phone,password_hash,created_at)
            VALUES(?,?,?,?,?,?,?)""", (role, code, name.strip(), email, phone.strip(), hash_password(password), now_text()))
    audit("Public signup", f"New {role} account {code}")
    return True, f"Account created. Your ID is {code}. You can now log in."

def log_in(role, email, password):
    user = get_user_by_email(email, role)
    if not user or not verify_password(password, user["password_hash"]):
        return False, "Email or password is incorrect."
    if not user["active"]:
        return False, "This account is suspended. Contact the administrator."
    st.session_state.user_id = user["id"]
    st.session_state.role = user["role"]
    audit(user["user_code"], "Logged in")
    return True, "Welcome back."

def logout():
    user = current_user()
    if user:
        audit(user["user_code"], "Logged out")
    for key in ("user_id", "role", "cart"):
        st.session_state.pop(key, None)
    st.rerun()

def pay_order(order_id, method):
    with connect() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return False, "Order not found."
        if o["paid"]:
            return True, "Payment is already verified."
        ref = "SIM-" + secrets.token_hex(5).upper()
        con.execute("UPDATE orders SET paid=1, updated_at=? WHERE id=?", (now_text(), order_id))
        con.execute("INSERT INTO payments(order_id,amount,method,status,reference,created_at) VALUES(?,?,?,?,?,?)",
                    (order_id, o["total"], method, "Verified (simulated)", ref, now_text()))
        if o["status"] == "In locker":
            con.execute("UPDATE orders SET status='Ready for pickup', pickup_token=?, updated_at=? WHERE id=?",
                        ("PICKUP-" + secrets.token_urlsafe(18), now_text(), order_id))
        customer_id = o["customer_id"]
        delivery_id = o["delivery_id"]
    notify(customer_id, f"Payment verified for {delivery_id}. Your pickup QR is active only now.")
    audit("Payment simulator", f"Verified payment for {delivery_id}; reference {ref}")
    return True, "Payment verified in demo mode. No real money was charged."

def place_order(user, cart, name, phone, area, hub1, hub2, payment_choice):
    if not cart:
        return False, "Your cart is empty."
    if hub1 == hub2:
        return False, "Choose two different preferred hubs."
    if not name.strip() or not phone.strip() or not area.strip():
        return False, "Fill in your name, phone and area."
    with connect() as con:
        total = 0
        max_size = "S"
        chosen = []
        for pid, qty in cart.items():
            p = con.execute("SELECT * FROM products WHERE id=? AND active=1", (int(pid),)).fetchone()
            if not p or qty < 1:
                return False, "One product is no longer available."
            if p["stock"] < qty:
                return False, f"Not enough stock for {p['name']}."
            total += p["price"] * qty
            if SIZES[p["size"]] > SIZES[max_size]:
                max_size = p["size"]
            chosen.append((p, qty))
        delivery_id = f"DLV-{datetime.now().strftime('%y%m%d')}-{secrets.randbelow(90000)+10000}"
        while con.execute("SELECT 1 FROM orders WHERE delivery_id=?", (delivery_id,)).fetchone():
            delivery_id = f"DLV-{datetime.now().strftime('%y%m%d')}-{secrets.randbelow(90000)+10000}"
        cur = con.execute("""INSERT INTO orders(delivery_id,customer_id,customer_name,customer_phone,customer_area,
            hub1_id,hub2_id,total,size,payment_choice,paid,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (delivery_id, user["id"], name.strip(), phone.strip(), area.strip(), hub1, hub2, total, max_size,
             payment_choice, 0, "Placed", now_text(), now_text()))
        oid = cur.lastrowid
        for p, qty in chosen:
            con.execute("INSERT INTO order_items(order_id,product_id,product_name,quantity,unit_price) VALUES(?,?,?,?,?)",
                        (oid, p["id"], p["name"], qty, p["price"]))
            con.execute("UPDATE products SET stock=stock-? WHERE id=?", (qty, p["id"]))
    notify(user["id"], f"Order {delivery_id} placed successfully.")
    if payment_choice == "now":
        pay_order(oid, "Pay now (simulated)")
    with connect() as con:
        seller_ids = [r["id"] for r in con.execute("SELECT id FROM users WHERE role='Seller' AND active=1").fetchall()]
    for sid in seller_ids:
        notify(sid, f"New order {delivery_id} is waiting for review.")
    audit(user["user_code"], f"Placed order {delivery_id}")
    return True, delivery_id

def seller_update(order_id, new_status):
    with connect() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return False
        if new_status == "Accepted" and o["status"] != "Placed":
            return False
        if new_status == "Packed" and o["status"] != "Accepted":
            return False
        if new_status == "With rider" and o["status"] != "Packed":
            return False
        con.execute("UPDATE orders SET status=?, updated_at=? WHERE id=?", (new_status, now_text(), order_id))
        if new_status == "Rejected":
            items = con.execute("SELECT product_id,quantity FROM order_items WHERE order_id=?", (order_id,)).fetchall()
            for item in items:
                con.execute("UPDATE products SET stock=stock+? WHERE id=?", (item["quantity"], item["product_id"]))
    o = order_row(order_id)
    notify(o["customer_id"], f"Order {o['delivery_id']} status updated to {new_status}.")
    if new_status == "With rider":
        with connect() as con:
            riders = con.execute("SELECT id FROM users WHERE role='Rider' AND active=1").fetchall()
        for r in riders:
            notify(r["id"], f"Parcel {o['delivery_id']} is ready for rider handover. Size {o['size']}.")
    audit("Seller", f"{o['delivery_id']} changed to {new_status}")
    return True

def deposit_order(order_id):
    with connect() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o or o["status"] not in ("With rider", "Awaiting locker"):
            return False, "This parcel is not ready for locker deposit."
        chosen = None
        selected_hub = None
        used_backup = False
        for idx, hid in enumerate((o["hub1_id"], o["hub2_id"])):
            locker = con.execute("""SELECT * FROM lockers WHERE hub_id=? AND active=1 AND order_id IS NULL
                AND CASE size WHEN 'S' THEN 1 WHEN 'M' THEN 2 ELSE 3 END >= ?
                ORDER BY CASE size WHEN 'S' THEN 1 WHEN 'M' THEN 2 ELSE 3 END, id LIMIT 1""",
                (hid, SIZES[o["size"]])).fetchone()
            if locker:
                chosen, selected_hub = locker, hid
                used_backup = idx == 1
                break
        if not chosen:
            con.execute("UPDATE orders SET status='Awaiting locker', updated_at=? WHERE id=?", (now_text(), order_id))
            customer_id = o["customer_id"]
            delivery_id = o["delivery_id"]
            no_space = True
        else:
            token = ("PICKUP-" + secrets.token_urlsafe(18)) if o["paid"] else None
            con.execute("UPDATE lockers SET order_id=? WHERE id=?", (order_id, chosen["id"]))
            con.execute("""UPDATE orders SET selected_hub_id=?, locker_id=?, status=?, pickup_token=?, updated_at=? WHERE id=?""",
                (selected_hub, chosen["id"], "Ready for pickup" if o["paid"] else "In locker", token, now_text(), order_id))
            customer_id = o["customer_id"]
            delivery_id = o["delivery_id"]
            no_space = False
    if no_space:
        notify(customer_id, f"{delivery_id}: both preferred hubs are full for this parcel size. We will retry later.")
        audit("Rider", f"{delivery_id} waiting for compatible locker")
        return False, "No compatible locker at either preferred hub. Parcel marked Awaiting locker."
    notify(customer_id, f"{delivery_id}: parcel placed at {hub_name(selected_hub)}, locker {chosen['locker_code']}.")
    if o["paid"]:
        notify(customer_id, f"{delivery_id}: payment is verified. Your pickup QR is active.")
    else:
        notify(customer_id, f"{delivery_id}: payment is required before pickup QR activation.")
    audit("Rider", f"{delivery_id} deposited in {hub_name(selected_hub)} locker {chosen['locker_code']}")
    if used_backup:
        return True, f"Deposited in backup hub {hub_name(selected_hub)}, locker {chosen['locker_code']}."
    return True, f"Deposited in {hub_name(selected_hub)}, locker {chosen['locker_code']}."

def collect_order(order_id, token):
    with connect() as con:
        o = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not o:
            return False, "Order not found."
        if o["status"] != "Ready for pickup" or not o["paid"] or not o["pickup_token"]:
            return False, "Parcel is not ready. Payment must be verified first."
        if not hmac.compare_digest(o["pickup_token"], token):
            return False, "Pickup QR/token is invalid."
        if o["locker_id"]:
            con.execute("UPDATE lockers SET order_id=NULL WHERE id=?", (o["locker_id"],))
        con.execute("UPDATE orders SET status='Collected', pickup_token=NULL, updated_at=? WHERE id=?", (now_text(), order_id))
        customer_id, delivery_id = o["customer_id"], o["delivery_id"]
    notify(customer_id, f"{delivery_id}: parcel collected successfully.")
    audit("Customer", f"{delivery_id} collected")
    return True, "Parcel collected. Locker is now free."

def tickets_for_user(user_id, kind=None):
    with connect() as con:
        sql = """SELECT t.*, o.delivery_id FROM tickets t JOIN orders o ON t.order_id=o.id WHERE t.customer_id=?"""
        args = [user_id]
        if kind:
            sql += " AND t.kind=?"; args.append(kind)
        return con.execute(sql + " ORDER BY t.id DESC", args).fetchall()

def customer_dashboard(user):
    st.title("Customer dashboard")
    tabs = st.tabs(["Shop & checkout", "My orders", "Pickup locker", "Messages", "Returns & complaints", "Notifications"])
    with tabs[0]:
        st.subheader("Products")
        with connect() as con:
            products = con.execute("SELECT * FROM products WHERE active=1 ORDER BY id").fetchall()
        cart = st.session_state.setdefault("cart", {})
        cols = st.columns(2)
        for i, p in enumerate(products):
            with cols[i % 2]:
                with st.container(border=True):
                    st.markdown(f"### {p['emoji']} {p['name']}")
                    st.write(f"**{money(p['price'])}** · Size {p['size']} · Stock: {p['stock']}")
                    qty = st.number_input("Quantity", min_value=0, max_value=max(0, min(20, p["stock"])), value=cart.get(str(p["id"]), 0), key=f"qty_{p['id']}")
                    if qty:
                        cart[str(p["id"])] = int(qty)
                    else:
                        cart.pop(str(p["id"]), None)
        st.subheader("Checkout")
        if not cart:
            st.info("Add at least one product to your cart.")
        else:
            with connect() as con:
                total = 0
                for pid, qty in cart.items():
                    p = con.execute("SELECT * FROM products WHERE id=?", (int(pid),)).fetchone()
                    if p:
                        total += p["price"] * qty
                        st.write(f"{p['name']} × {qty} — {money(p['price'] * qty)}")
            st.markdown(f"### Total: {money(total)}")
            hubs = all_hubs()
            hub_opts = {f"{h['name']} ({h['area']})": h["id"] for h in hubs}
            with st.form("checkout_form"):
                name = st.text_input("Full name", value=user["name"])
                phone = st.text_input("Phone number (private)")
                area = st.text_input("Your area/location (private; not shared with seller or rider)")
                c1, c2 = st.columns(2)
                with c1:
                    hub1_label = st.selectbox("First preferred hub", list(hub_opts))
                with c2:
                    hub2_label = st.selectbox("Backup hub", list(hub_opts), index=min(1, len(hub_opts)-1))
                payment_choice = st.radio("Payment option", ["Pay now at checkout", "Pay after parcel is deposited in locker"], help="Demo payments only. Cash on delivery is not available.")
                submitted = st.form_submit_button("Place order", type="primary", use_container_width=True)
                if submitted:
                    choice = "now" if payment_choice.startswith("Pay now") else "later"
                    ok, message = place_order(user, cart.copy(), name, phone, area, hub_opts[hub1_label], hub_opts[hub2_label], choice)
                    if ok:
                        st.session_state.cart = {}
                        st.success(f"Order placed: {message}")
                        st.rerun()
                    else:
                        st.error(message)
    with tabs[1]:
        st.subheader("Your orders")
        orders = my_orders(user["id"])
        if not orders:
            st.info("No orders yet.")
        for o in orders:
            full = order_row(o["id"])
            with st.container(border=True):
                st.markdown(f"**{o['delivery_id']}** — {status_badge(o['status'], o['paid'])}")
                st.write(f"Total: {money(o['total'])} · Payment: {'Verified' if o['paid'] else 'Not paid'} · Size: {o['size']}")
                st.write(f"Preferred hubs: {full['hub1_name']} → {full['hub2_name']}")
                its = order_items(o["id"])
                st.write("Items: " + ", ".join(f"{x['product_name']} × {x['quantity']}" for x in its))
                if o["status"] == "In locker" and not o["paid"]:
                    if st.button(f"Pay {money(o['total'])} and activate QR (simulated)", key=f"pay_order_{o['id']}"):
                        _, msg = pay_order(o["id"], "After locker deposit (simulated)")
                        st.success(msg); st.rerun()
                if o["status"] == "Ready for pickup" and o["paid"] and o["pickup_token"]:
                    st.success(f"Pickup location: {full['selected_hub_name']} · Locker {full['locker_code']}")
                    qr_bytes = make_qr_bytes(o["pickup_token"])
                    if qr_bytes:
                        st.image(qr_bytes, caption="Pickup QR — demo token")
                        st.download_button("Download pickup QR", qr_bytes, file_name=f"{o['delivery_id']}_pickup_qr.png", mime="image/png", key=f"qr_download_{o['id']}")
                    with st.expander("Simulate scanning the pickup QR"):
                        entered = st.text_input("Enter/scan pickup token for this demo", key=f"token_{o['id']}", type="password")
                        if st.button("Verify QR and collect parcel", key=f"collect_{o['id']}", type="primary"):
                            ok, msg = collect_order(o["id"], entered)
                            (st.success if ok else st.error)(msg)
                            if ok: st.rerun()
                if o["status"] == "Collected":
                    st.caption("Collected. You can request a return or open a complaint below.")
    with tabs[2]:
        st.subheader("Locker status")
        orders = my_orders(user["id"])
        active = [o for o in orders if o["status"] not in ("Collected", "Rejected")]
        if not active:
            st.info("No active parcels.")
        for o in active:
            full = order_row(o["id"])
            st.write(f"**{o['delivery_id']}** — {status_badge(o['status'], o['paid'])}")
            if full["selected_hub_name"]:
                st.write(f"Hub: {full['selected_hub_name']} · Locker: {full['locker_code']}")
            if o["status"] == "In locker" and not o["paid"]:
                st.warning("Parcel is stored. Payment is required before pickup QR activation.")
    with tabs[3]:
        st.subheader("Messages")
        show_messages(user, "Seller")
    with tabs[4]:
        st.subheader("Returns and complaints")
        collected = [o for o in my_orders(user["id"]) if o["status"] == "Collected"]
        with st.form("ticket_form"):
            eligible = {o["delivery_id"]: o["id"] for o in collected}
            selected = st.selectbox("Collected order", list(eligible) if eligible else ["No collected orders"])
            kind = st.selectbox("Request type", ["Return", "Complaint"])
            details = st.text_area("Reason / issue details")
            submitted = st.form_submit_button("Submit request")
            if submitted:
                if not eligible:
                    st.error("A return/complaint request can be linked only to a collected order in this demo.")
                elif not details.strip():
                    st.error("Please explain the reason.")
                else:
                    with connect() as con:
                        code = ("RET-" if kind == "Return" else "TKT-") + secrets.token_hex(3).upper()
                        con.execute("INSERT INTO tickets(ticket_code,order_id,customer_id,kind,details,created_at) VALUES(?,?,?,?,?,?)",
                                    (code, eligible[selected], user["id"], kind, details.strip(), now_text()))
                    notify(user["id"], f"{kind} request {code} submitted.")
                    audit(user["user_code"], f"Submitted {kind} {code}")
                    st.success(f"{kind} request submitted: {code}")
                    st.rerun()
        for t in tickets_for_user(user["id"]):
            st.write(f"**{t['ticket_code']}** · {t['kind']} · {t['delivery_id']} · {t['status']}")
            st.caption(t["details"] + (f" — {t['decision']}" if t["decision"] else ""))
    with tabs[5]:
        show_notifications(user)

def show_messages(user, other_role):
    with connect() as con:
        if user["role"] == "Customer":
            rows = con.execute("""SELECT m.*, o.delivery_id, u.name AS sender_name FROM messages m
                LEFT JOIN orders o ON m.order_id=o.id JOIN users u ON m.sender_id=u.id
                WHERE (m.sender_id=? OR (m.recipient_role=? AND (m.order_id IS NULL OR o.customer_id=?)))
                ORDER BY m.id DESC LIMIT 100""", (user["id"], user["role"], user["id"])).fetchall()
            order_rows = con.execute("SELECT id,delivery_id FROM orders WHERE customer_id=? ORDER BY id DESC", (user["id"],)).fetchall()
        else:
            rows = con.execute("""SELECT m.*, o.delivery_id, u.name AS sender_name FROM messages m
                LEFT JOIN orders o ON m.order_id=o.id JOIN users u ON m.sender_id=u.id
                WHERE m.recipient_role=? OR m.sender_id=? ORDER BY m.id DESC LIMIT 100""", (user["role"], user["id"])).fetchall()
            order_rows = con.execute("SELECT id,delivery_id FROM orders ORDER BY id DESC").fetchall()
    for m in rows:
        st.write(f"**{m['sender_name']}** → {m['recipient_role']} · {m['delivery_id'] or 'General'}")
        st.caption(m["message"])
    order_opts = {"General message": None}
    for o in order_rows:
        order_opts[o["delivery_id"]] = o["id"]
    with st.form(f"message_form_{user['id']}"):
        selected = st.selectbox("Related delivery (optional)", list(order_opts))
        message = st.text_area("Message")
        submitted = st.form_submit_button("Send message")
        if submitted:
            if not message.strip():
                st.error("Write a message first.")
            else:
                recipient = "Seller" if user["role"] == "Customer" else "Customer"
                with connect() as con:
                    con.execute("INSERT INTO messages(order_id,sender_id,recipient_role,message,created_at) VALUES(?,?,?,?,?)",
                                (order_opts[selected], user["id"], recipient, message.strip(), now_text()))
                if recipient == "Customer" and order_opts[selected]:
                    with connect() as con:
                        customer_id = con.execute("SELECT customer_id FROM orders WHERE id=?", (order_opts[selected],)).fetchone()["customer_id"]
                    notify(customer_id, f"New message about {selected}.")
                st.success("Message sent.")
                st.rerun()

def show_notifications(user):
    with connect() as con:
        rows = con.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],)).fetchall()
        unread = con.execute("SELECT COUNT(*) FROM notifications WHERE user_id=? AND seen=0", (user["id"],)).fetchone()[0]
    st.caption(f"{unread} unread notification(s)")
    if not rows:
        st.info("No notifications yet.")
    for n in rows:
        st.write(("🔔 " if not n["seen"] else "✓ ") + n["message"])
        st.caption(n["created_at"])
    if unread and st.button("Mark notifications as read"):
        with connect() as con:
            con.execute("UPDATE notifications SET seen=1 WHERE user_id=?", (user["id"],))
        st.rerun()

def seller_dashboard(user):
    st.title("Seller dashboard")
    tabs = st.tabs(["Incoming orders", "Products & stock", "Messages", "Returns", "Notifications"])
    with tabs[0]:
        with connect() as con:
            orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
        if not orders:
            st.info("No orders yet. Place a test order through the Customer portal.")
        for o in orders:
            with st.container(border=True):
                st.markdown(f"**{o['delivery_id']}** — {status_badge(o['status'], o['paid'])}")
                items = order_items(o["id"])
                st.write("Products: " + ", ".join(f"{x['product_name']} × {x['quantity']}" for x in items))
                st.write(f"Size {o['size']} · Hubs: {hub_name(o['hub1_id'])} / {hub_name(o['hub2_id'])} · {'Paid' if o['paid'] else 'Unpaid'}")
                st.caption("Privacy: customer phone number and area are hidden from the seller.")
                if o["status"] == "Placed":
                    c1, c2 = st.columns(2)
                    if c1.button("Accept order", key=f"accept_{o['id']}"):
                        seller_update(o["id"], "Accepted"); st.rerun()
                    if c2.button("Reject order", key=f"reject_{o['id']}"):
                        seller_update(o["id"], "Rejected"); notify(o["customer_id"], f"Order {o['delivery_id']} was rejected; stock has been returned."); st.rerun()
                elif o["status"] == "Accepted":
                    if st.button("Mark packed", key=f"packed_{o['id']}"):
                        seller_update(o["id"], "Packed"); st.rerun()
                elif o["status"] == "Packed":
                    if st.button("Hand over to rider", key=f"handover_{o['id']}"):
                        seller_update(o["id"], "With rider"); st.rerun()
    with tabs[1]:
        st.subheader("Product inventory")
        with connect() as con:
            products = con.execute("SELECT * FROM products ORDER BY id").fetchall()
        for p in products:
            with st.container(border=True):
                st.write(f"**{p['emoji']} {p['name']}** · {money(p['price'])} · Size {p['size']} · Stock {p['stock']}")
                c1, c2, c3 = st.columns([2,1,1])
                delta = c1.number_input("Stock adjustment", min_value=-1000, max_value=1000, value=0, key=f"stock_delta_{p['id']}")
                if c2.button("Apply", key=f"stock_apply_{p['id']}"):
                    with connect() as con:
                        con.execute("UPDATE products SET stock=MAX(0,stock+?) WHERE id=?", (int(delta), p["id"]))
                    audit(user["user_code"], f"Stock adjustment {p['name']}: {delta}")
                    st.rerun()
                if c3.button("Toggle active", key=f"product_active_{p['id']}"):
                    with connect() as con:
                        con.execute("UPDATE products SET active=1-active WHERE id=?", (p["id"],))
                    st.rerun()
    with tabs[2]:
        show_messages(user, "Customer")
    with tabs[3]:
        with connect() as con:
            rows = con.execute("""SELECT t.*,o.delivery_id FROM tickets t JOIN orders o ON t.order_id=o.id
                WHERE t.kind='Return' ORDER BY t.id DESC""").fetchall()
        if not rows:
            st.info("No return requests.")
        for t in rows:
            with st.container(border=True):
                st.write(f"**{t['ticket_code']}** · {t['delivery_id']} · {t['status']}")
                st.write(t["details"])
                if t["status"] == "Open":
                    c1, c2 = st.columns(2)
                    if c1.button("Approve", key=f"approve_{t['id']}"):
                        resolve_ticket(t["id"], "Approved"); st.rerun()
                    if c2.button("Reject", key=f"reject_ticket_{t['id']}"):
                        resolve_ticket(t["id"], "Rejected"); st.rerun()
    with tabs[4]:
        show_notifications(user)

def resolve_ticket(ticket_id, decision):
    with connect() as con:
        t = con.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not t: return
        con.execute("UPDATE tickets SET status='Resolved', decision=? WHERE id=?", (decision, ticket_id))
        customer_id, code = t["customer_id"], t["ticket_code"]
    notify(customer_id, f"Your request {code} was {decision.lower()}.")
    audit("Seller/Admin", f"{code} {decision}")

def rider_dashboard(user):
    st.title("Rider dashboard")
    st.caption("Rider view shows delivery ID, parcel size and hub information only — no customer address, phone or product details.")
    tabs = st.tabs(["Assigned parcels", "Locker map", "Notifications"])
    with tabs[0]:
        with connect() as con:
            orders = con.execute("SELECT * FROM orders WHERE status IN ('With rider','Awaiting locker') ORDER BY id DESC").fetchall()
        if not orders:
            st.info("No parcels assigned. Seller must hand over an order first.")
        for o in orders:
            with st.container(border=True):
                st.markdown(f"**{o['delivery_id']}** — {status_badge(o['status'], o['paid'])}")
                st.write(f"Parcel size: {o['size']} · First hub: {hub_name(o['hub1_id'])} · Backup hub: {hub_name(o['hub2_id'])}")
                if st.button("Find compatible locker & confirm deposit", key=f"deposit_{o['id']}"):
                    ok, msg = deposit_order(o["id"])
                    (st.success if ok else st.warning)(msg)
                    st.rerun()
        st.subheader("Recent deposited parcels")
        with connect() as con:
            done = con.execute("SELECT * FROM orders WHERE selected_hub_id IS NOT NULL ORDER BY id DESC LIMIT 20").fetchall()
        for o in done:
            full = order_row(o["id"])
            st.write(f"{o['delivery_id']} · {full['selected_hub_name']} · Locker {full['locker_code']} · {o['status']}")
    with tabs[1]:
        show_locker_map()
    with tabs[2]:
        show_notifications(user)

def show_locker_map():
    hubs = all_hubs()
    for h in hubs:
        st.subheader(h["name"])
        with connect() as con:
            lockers = con.execute("""SELECT l.*, o.delivery_id FROM lockers l LEFT JOIN orders o ON l.order_id=o.id
                WHERE l.hub_id=? ORDER BY l.id""", (h["id"],)).fetchall()
        cols = st.columns(4)
        for i, l in enumerate(lockers):
            with cols[i % 4]:
                label = f"{l['locker_code']} · {l['size']}"
                st.metric(label, "Occupied" if l["order_id"] else "Free", help=l["delivery_id"] or "No parcel")

def admin_dashboard(user):
    st.title("Admin dashboard")
    with connect() as con:
        total_orders = con.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        revenue = con.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status LIKE 'Verified%'").fetchone()[0]
        used = con.execute("SELECT COUNT(*) FROM lockers WHERE order_id IS NOT NULL").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM lockers WHERE active=1").fetchone()[0]
        open_tickets = con.execute("SELECT COUNT(*) FROM tickets WHERE status='Open'").fetchone()[0]
        users = con.execute("SELECT * FROM users ORDER BY role,name").fetchall()
        orders = con.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
        payments = con.execute("""SELECT p.*,o.delivery_id FROM payments p JOIN orders o ON p.order_id=o.id ORDER BY p.id DESC""").fetchall()
        tickets = con.execute("""SELECT t.*,o.delivery_id FROM tickets t JOIN orders o ON t.order_id=o.id ORDER BY t.id DESC""").fetchall()
    m1,m2,m3,m4 = st.columns(4)
    m1.metric("Orders", total_orders)
    m2.metric("Verified demo payments", money(revenue))
    m3.metric("Lockers occupied", f"{used}/{total}")
    m4.metric("Open tickets", open_tickets)
    tabs = st.tabs(["Orders & payments", "Hubs & lockers", "Users", "Tickets", "Audit log", "System notes"])
    with tabs[0]:
        st.subheader("Orders")
        if orders:
            st.dataframe([{"Delivery ID":o["delivery_id"],"Status":o["status"],"Paid": "Yes" if o["paid"] else "No","Total":money(o["total"]),"Hub":hub_name(o["selected_hub_id"]),"Locker":(order_row(o["id"])["locker_code"] or "-")} for o in orders], use_container_width=True, hide_index=True)
        else: st.info("No orders yet.")
        st.subheader("Payment records (simulated)")
        if payments:
            st.dataframe([{"Delivery ID":p["delivery_id"],"Amount":money(p["amount"]),"Method":p["method"],"Status":p["status"],"Reference":p["reference"],"Time":p["created_at"]} for p in payments], use_container_width=True, hide_index=True)
        else: st.info("No payment records yet.")
    with tabs[1]:
        show_locker_map()
        st.subheader("Hub management")
        with connect() as con:
            hub_rows = con.execute("SELECT * FROM hubs ORDER BY id").fetchall()
        for h in hub_rows:
            st.write(f"{h['hub_code']} · {h['name']} · {h['area']} · {'Active' if h['active'] else 'Inactive'}")
        with st.form("add_hub_form"):
            st.write("Add a new hub")
            code = st.text_input("Hub code", placeholder="H4")
            name = st.text_input("Hub name")
            area = st.text_input("Hub area")
            if st.form_submit_button("Add hub"):
                if not code.strip() or not name.strip() or not area.strip():
                    st.error("Fill all hub fields.")
                else:
                    try:
                        with connect() as con:
                            cur = con.execute("INSERT INTO hubs(hub_code,name,area) VALUES(?,?,?)", (code.strip().upper(),name.strip(),area.strip()))
                            hid = cur.lastrowid
                            for locker_code, size in [("S1","S"),("S2","S"),("S3","S"),("S4","S"),("M1","M"),("M2","M"),("M3","M"),("L1","L")]:
                                con.execute("INSERT INTO lockers(hub_id,locker_code,size) VALUES(?,?,?)", (hid,locker_code,size))
                        st.success("Hub added with demo lockers."); st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Hub code already exists.")
    with tabs[2]:
        st.subheader("Accounts")
        for u in users:
            with st.container(border=True):
                st.write(f"**{u['role']}** · {u['user_code']} · {u['name']} · {u['email']} · {'Active' if u['active'] else 'Suspended'}")
                if u["role"] != "Admin":
                    if st.button("Suspend" if u["active"] else "Activate", key=f"user_toggle_{u['id']}"):
                        with connect() as con:
                            con.execute("UPDATE users SET active=1-active WHERE id=?", (u["id"],))
                        audit(user["user_code"], f"Toggled user {u['user_code']}")
                        st.rerun()
    with tabs[3]:
        if not tickets: st.info("No tickets.")
        for t in tickets:
            with st.container(border=True):
                st.write(f"**{t['ticket_code']}** · {t['kind']} · {t['delivery_id']} · {t['status']}")
                st.write(t["details"])
                if t["decision"]: st.caption("Decision: " + t["decision"])
                if t["status"] == "Open":
                    if st.button("Resolve ticket", key=f"admin_resolve_{t['id']}"):
                        resolve_ticket(t["id"], "Resolved by admin"); st.rerun()
    with tabs[4]:
        with connect() as con:
            logs = con.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
        if logs:
            st.dataframe([{"Time":r["created_at"],"Actor":r["actor"],"Action":r["action"]} for r in logs], use_container_width=True, hide_index=True)
        else: st.info("No audit activity yet.")
    with tabs[5]:
        st.warning("This is a university prototype. Payments, physical locker unlocking and QR-room access are simulated.")
        st.write("Pickup QR is generated only after payment is marked verified. No cash on delivery is offered.")
        st.write("For production, configure persistent database storage, HTTPS, secure session management, rate limiting, backups, real payment gateway verification, and authenticated locker-controller/QR APIs.")
        st.write(f"Database file: `{DB_PATH.name}`")
        if st.button("Reset demo orders, tickets, payments and locker occupancy"):
            with connect() as con:
                con.execute("DELETE FROM messages")
                con.execute("DELETE FROM notifications")
                con.execute("DELETE FROM payments")
                con.execute("DELETE FROM tickets")
                con.execute("DELETE FROM order_items")
                con.execute("DELETE FROM orders")
                con.execute("UPDATE lockers SET order_id=NULL")
                con.execute("UPDATE products SET stock=60 WHERE name IN ('Running shoes','Wireless earbuds','Cotton kurta')")
                con.execute("UPDATE products SET stock=40 WHERE name='Backpack'")
            audit(user["user_code"], "Admin reset demo orders and stock")
            st.success("Demo orders and related records cleared."); st.rerun()

def landing():
    st.title("📦 Privacy Parcel Locker")
    st.subheader("Receive parcels without sharing your home address.")
    st.write("The seller and rider work with a Delivery ID and parcel details. The parcel is stored at a selected smart-locker hub. The pickup QR becomes active only after payment is verified.")
    c1,c2,c3 = st.columns(3)
    c1.metric("Demo hubs", "3")
    c2.metric("Locker size types", "S · M · L")
    c3.metric("Payment modes", "2")
    st.divider()
    st.subheader("How it works")
    st.markdown("1. Customer selects products and two preferred hubs.  \n2. Seller accepts, packs and hands the parcel to a rider.  \n3. Rider allocates a compatible locker at the first or backup hub.  \n4. Customer pays at checkout or after locker deposit.  \n5. After payment verification, the customer gets an active pickup QR.")
    st.subheader("Choose a portal")
    cols = st.columns(4)
    for i, role in enumerate(ROLES):
        with cols[i]:
            st.markdown(f"### {role}")
            st.write({"Customer":"Shop, order, pay, track and collect.","Seller":"Process orders and stock.","Rider":"Deposit parcels in compatible lockers.","Admin":"Manage users, hubs, tickets and records."}[role])
            if st.button(f"Open {role}", key=f"open_{role}", use_container_width=True):
                st.session_state.auth_role = role
                st.rerun()
    st.info("Demo only: QR scanning, locker unlocking and payment are simulated. Do not enter real payment details.")

def auth_page(role):
    st.title(f"{role} portal")
    login_tab, signup_tab = st.tabs(["Log in", "Create account"])
    with login_tab:
        with st.form(f"login_{role}"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
            if submitted:
                ok, msg = log_in(role, email, password)
                if ok:
                    st.success(msg); st.rerun()
                else: st.error(msg)
        if role == "Admin":
            st.caption("Demo admin login: admin@demo.pk · Password: demo123")
    with signup_tab:
        if role == "Admin":
            st.info("Admin accounts are invitation-only. Use the demo admin login for this prototype.")
        else:
            with st.form(f"signup_{role}"):
                name = st.text_input("Full name")
                email = st.text_input("Email address")
                phone = st.text_input("Phone number" if role == "Customer" else "Phone number (optional)")
                password = st.text_input("Create password (minimum 8 characters)", type="password")
                confirm = st.text_input("Confirm password", type="password")
                submitted = st.form_submit_button("Create account", use_container_width=True)
                if submitted:
                    if password != confirm:
                        st.error("Passwords do not match.")
                    else:
                        ok, msg = create_account(role, name, email, password, phone)
                        (st.success if ok else st.error)(msg)

def main():
    st.markdown("""
    <style>
    .stApp { background: #f4f7f7; }
    [data-testid="stSidebar"] { background: #eaf1f1; }
    div[data-testid="stMetric"] { background: white; border: 1px solid #d7e2e2; padding: 12px; border-radius: 12px; }
    .block-container { padding-top: 2rem; padding-bottom: 3rem; max-width: 1250px; }
    </style>
    """, unsafe_allow_html=True)
    user = current_user()
    with st.sidebar:
        st.markdown("## 📦 Parcel Locker")
        if user:
            st.write(f"**{user['name']}**")
            st.caption(f"{user['role']} · {user['user_code']}")
            if st.button("Log out", use_container_width=True):
                logout()
            if st.button("Back to home", use_container_width=True):
                st.session_state.pop("auth_role", None)
                st.rerun()
        else:
            st.caption("Privacy-first delivery demo")
            if st.button("Home", use_container_width=True):
                st.session_state.pop("auth_role", None)
                st.rerun()
    if user:
        if user["role"] == "Customer": customer_dashboard(user)
        elif user["role"] == "Seller": seller_dashboard(user)
        elif user["role"] == "Rider": rider_dashboard(user)
        elif user["role"] == "Admin": admin_dashboard(user)
        return
    role = st.session_state.get("auth_role")
    if role in ROLES:
        if st.button("← Back to role selection"):
            st.session_state.pop("auth_role", None); st.rerun()
        auth_page(role)
    else:
        landing()

if __name__ == "__main__":
    main()
