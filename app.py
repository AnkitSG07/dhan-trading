from brokers.factory import get_broker_class
from brokers.zerodha import ZerodhaBroker
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash
from dhanhq import dhanhq
import sqlite3
import os
import json
import pandas as pd
from flask_cors import CORS
import io
from datetime import datetime, timedelta, date
import requests
from bs4 import BeautifulSoup
from apscheduler.schedulers.background import BackgroundScheduler
import tempfile
import shutil
from functools import wraps
from werkzeug.utils import secure_filename
import random
import string
from models import db, User, Account, Trade, WebhookLog, SystemLog, Setting
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "change-me"
CORS(app)
DB_PATH = os.path.join("/tmp", "quantbot.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quantbot.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)
start_time = datetime.utcnow()
SYMBOL_MAP = {
    "RELIANCE": "2885", "TCS": "11536", "INFY": "10999", "ADANIPORTS": "15083", "IDEA": "532822", "HDFCBANK": "1333",
    "SBIN": "3045", "ICICIBANK": "4963", "AXISBANK": "1343", "ITC": "1660", "HINDUNILVR": "1394",
    "KOTAKBANK": "1922", "LT": "11483", "BAJFINANCE": "317", "HCLTECH": "7229", "ASIANPAINT": "236",
    "MARUTI": "1095", "M&M": "2031", "SUNPHARMA": "3046", "TATAMOTORS": "3432", "WIPRO": "3787",
    "ULTRACEMCO": "11532", "TITAN": "3506", "NESTLEIND": "11262", "BAJAJFINSV": "317",
    "POWERGRID": "14977", "NTPC": "2886", "JSWSTEEL": "11723", "HDFCLIFE": "11915",
    "DRREDDY": "881", "TECHM": "11534", "BRITANNIA": "293", "TATASTEEL": "3505", "CIPLA": "694",
    "SBILIFE": "11916", "BAJAJ-AUTO": "317", "HINDALCO": "1393", "DIVISLAB": "881",
    "GRASIM": "1147", "ADANIENT": "15083", "COALINDIA": "694", "INDUSINDBK": "1393",
    "TATACONSUM": "3505", "EICHERMOT": "881", "SHREECEM": "1147", "HEROMOTOCO": "15083",
    "BAJAJHLDNG": "694", "SBICARD": "1393", "DLF": "3505", "DMART": "881", "UPL": "1147",
    "ICICIPRULI": "15083", "HDFCAMC": "694", "HDFC": "1393", "GAIL": "3505", "HAL": "881",
    "TATAPOWER": "1147", "VEDL": "15083", "BPCL": "694", "IOC": "1393", "ONGC": "3505",
    "LICHSGFIN": "881", "BANKBARODA": "1147", "PNB": "15083", "CANBK": "694", "UNIONBANK": "1393",
    "IDFCFIRSTB": "3505", "BANDHANBNK": "881", "FEDERALBNK": "1147", "RBLBANK": "15083",
    "YESBANK": "694", "IGL": "1393", "PETRONET": "3505", "GUJGASLTD": "881", "MGL": "1147",
    "TORNTPHARM": "15083", "LUPIN": "694", "AUROPHARMA": "1393", "BIOCON": "3505",
    "GLENMARK": "881", "CADILAHC": "1147", "ALKEM": "15083", "APOLLOHOSP": "694",
    "MAXHEALTH": "1393", "FORTIS": "3505", "JUBLFOOD": "881", "UBL": "1147", "MCDOWELL-N": "15083",
    "COLPAL": "694", "DABUR": "1393", "GODREJCP": "3505", "MARICO": "881", "EMAMILTD": "1147",
    "PGHH": "15083", "GILLETTE": "694", "TATACHEM": "1393", "PIDILITIND": "3505",
    "BERGEPAINT": "881", "KANSAINER": "1147", "JSWENERGY": "15083", "ADANIGREEN": "694",
    "ADANITRANS": "1393", "NHPC": "3505", "SJVN": "881", "RECLTD": "1147", "PFC": "15083"
}

BROKER_STATUS_URLS = {
    "dhan": "https://api.dhan.co",
    "zerodha": "https://api.kite.trade",
    "aliceblue": "https://ant.aliceblueonline.com",
    "finvasia": "https://api.shoonya.com",
    "fyers": "https://api.fyers.in",
}

def safe_write_json(path, data):
    dirpath = os.path.dirname(path) or '.'
    with tempfile.NamedTemporaryFile('w', delete=False, dir=dirpath) as tmp:
        json.dump(data, tmp, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
    shutil.move(tmp.name, path)

def safe_read_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"Error reading {path}: {e}")
        return {}

def broker_api(obj):
    broker = obj.get("broker", "Unknown").lower()
    credentials = obj.get("credentials", {})
    client_id = obj.get("client_id")
    access_token = credentials.get("access_token")
    BrokerClass = get_broker_class(broker)
    # Remove access_token from credentials dict to avoid duplication
    rest = {k: v for k, v in credentials.items() if k != "access_token"}
    return BrokerClass(client_id, access_token, **rest)

# Utility loaders for admin dashboard
def load_users():
    return User.query.all()

def load_accounts():
    return Account.query.all()

def load_trades():
    return Trade.query.all()

def load_logs():
    return WebhookLog.query.all(), SystemLog.query.all()

def load_settings():
    # Return all key/value settings stored in the DB
    result = {'trading_enabled': True}
    for s in Setting.query.all():
        if s.key == 'trading_enabled':
            result['trading_enabled'] = s.value.lower() == 'true'
        else:
            result[s.key] = s.value
    return result

def save_settings(settings):
    for key, value in settings.items():
        s = Setting.query.filter_by(key=key).first()
        if not s:
            s = Setting(key=key, value=str(value))
            db.session.add(s)
        else:
            s.value = str(value)
    db.session.commit()

def format_uptime():
    delta = datetime.utcnow() - start_time
    hours, remainder = divmod(int(delta.total_seconds()), 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{hours}h {minutes}m"


def check_api(url: str) -> bool:
    try:
        resp = requests.get(url, timeout=3)
        return resp.ok
    except Exception:
        return False

def seed_dummy_data():
    if User.query.count() == 0:
        data = safe_read_json('users.json')
        for uid, info in data.items():
            user = User(
                email=info.get('email', uid),
                name=info.get('name'),
                phone=info.get('phone'),
                plan=info.get('plan'),
                last_login=info.get('last_login'),
                subscription_start=info.get('subscription_start'),
                subscription_end=info.get('subscription_end'),
                payment_status=info.get('payment_status'),
            )
            user.set_password(info.get('password', 'pass'))
            db.session.add(user)
        admin_email = os.environ.get('ADMIN_EMAIL', 'admin@example.com')
        admin_password = os.environ.get('ADMIN_PASSWORD', 'admin123')
        admin = User(email=admin_email, name='Admin', plan='Admin', is_admin=True)
        admin.set_password(admin_password)
        db.session.add(admin)

    if Account.query.count() == 0:
        data = safe_read_json('accounts.json')
        for acc in data.get('accounts', []):
            account = Account(
                user_id=int(acc.get('user_id')),
                broker=acc.get('broker'),
                client_id=acc.get('client_id'),
                token_expiry=acc.get('token_expiry'),
                status=acc.get('status'),
            )
            db.session.add(account)

    if Trade.query.count() == 0:
        data = safe_read_json('trades.json')
        for t in data.get('trades', []):
            trade = Trade(
                user_id=1,
                symbol=t.get('symbol'),
                action=t.get('action'),
                qty=t.get('qty'),
                price=t.get('price'),
                status=t.get('status'),
                timestamp=datetime.now().isoformat(),
            )
            db.session.add(trade)

    if WebhookLog.query.count() == 0:
        data = safe_read_json('logs.json')
        for log in data.get('webhook', []):
            db.session.add(WebhookLog(status=log.get('status'), time=log.get('time'), reason=log.get('reason')))
        for log in data.get('system', []):
            db.session.add(SystemLog(type=log.get('type'), time=log.get('time'), details=log.get('details')))

    if Setting.query.count() == 0:
        data = safe_read_json('settings.json')
        for k, v in data.items():
            db.session.add(Setting(key=k, value=str(v)))

    db.session.commit()


def find_account_by_client_id(accounts, client_id):
    """
    Returns (account_object, parent_master_object or None) for a given client_id, or (None, None) if not found.
    """
    for master in accounts.get("masters", []):
        if master.get("client_id") == client_id:
            return master, None
        for child in master.get("children", []):
            if child.get("client_id") == client_id:
                return child, master
    return None, None


def clean_response_message(response):
    if isinstance(response, dict):
        remarks = response.get("remarks")
        if isinstance(remarks, dict):
            return remarks.get("errorMessage") or remarks.get("error_message") or str(remarks)
        return str(remarks) or str(response.get("status")) or str(response)
    return str(response)

# === Initialize SQLite DB ===
def init_db():
    conn = sqlite3.connect("tradelogs.db")
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            user_id TEXT,
            symbol TEXT,
            action TEXT,
            quantity INTEGER,
            status TEXT,
            response TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# === Save logs ===
def save_log(user_id, symbol, action, quantity, status, response):
    conn = sqlite3.connect("tradelogs.db")
    c = conn.cursor()
    c.execute("""
        INSERT INTO logs (timestamp, user_id, symbol, action, quantity, status, response)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (datetime.now().isoformat(), user_id, symbol, action, quantity, status, response))
    conn.commit()
    conn.close()

def save_order_mapping(master_order_id, child_order_id, master_id, master_broker, child_id, child_broker, symbol):
    path = "order_mappings.json"
    mappings = []

    if os.path.exists(path):
        with open(path, "r") as f:
            mappings = json.load(f)

    mappings.append({
        "master_order_id": master_order_id,
        "child_order_id": child_order_id,
        "master_client_id": master_id,
        "master_broker": master_broker,
        "child_client_id": child_id,
        "child_broker": child_broker,
        "symbol": symbol,
        "status": "ACTIVE"
    })

    with open(path, "w") as f:
        json.dump(mappings, f, indent=2)


def record_trade(user_email, symbol, action, qty, price, status):
    """Persist a trade record to the database."""
    user = User.query.filter_by(email=user_email).first()
    trade = Trade(
        user_id=user.id if user else None,
        symbol=symbol,
        action=action,
        qty=int(qty),
        price=float(price or 0),
        status=status,
        timestamp=datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    )
    db.session.add(trade)
    db.session.commit()


# Authentication decorator
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

# Admin authentication
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@example.com")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

def admin_login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped
    
def poll_and_copy_trades():
    print("🔄 poll_and_copy_trades() triggered...")

    try:
        if not os.path.exists("accounts.json"):
            print("⚠️ No accounts.json file found.")
            return

        with open("accounts.json", "r") as f:
            db = json.load(f)

        all_accounts = db.get("accounts", [])
        # Find all masters
        masters = [acc for acc in all_accounts if acc.get("role") == "master"]

        if not masters:
            print("⚠️ No master accounts configured.")
            return

        for master in masters:
            master_id = master.get("client_id")
            master_broker = master.get("broker", "Unknown").lower()
            credentials = master.get("credentials", {})
            BrokerClass = get_broker_class(master_broker)
            try:
                rest = {k: v for k, v in credentials.items() if k != "access_token"}
                master_api = BrokerClass(
                    client_id=master.get("client_id"),
                    access_token=credentials.get("access_token"),
                    **rest
                )
            except Exception as e:
                print(f"❌ Could not initialize master API ({master_broker}): {e}")
                continue

            last_copied_key = f"last_copied_trade_id_{master_id}"
            last_copied_trade_id = db.get(last_copied_key)
            new_last_trade_id = None

            # Get master orders using standard interface
            try:
                orders_resp = master_api.get_order_list()
                order_list = orders_resp.get("data", orders_resp.get("orders", []))
            except Exception as e:
                print(f"❌ Error fetching orders for master {master_id}: {e}")
                continue

            if not order_list:
                print(f"ℹ️ No orders found for master {master_id}.")
                continue

            order_list = sorted(order_list, key=lambda x: x.get("orderTimestamp", x.get("order_time", "")), reverse=True)

            # Find all children linked to this master
            children = [acc for acc in all_accounts if acc.get("role") == "child" and acc.get("linked_master_id") == master_id]

            for order in order_list:
                order_id = order.get("orderId") or order.get("order_id")
                if not order_id:
                    continue

                if order_id == last_copied_trade_id:
                    print(f"✅ [{master_id}] Reached last copied trade. Stopping here.")
                    break

                order_status = order.get("orderStatus") or order.get("status") or ""
                if order_status.upper() not in ["TRADED", "FILLED", "COMPLETE"]:
                    print(f"⏩ [{master_id}] Skipping order {order_id} (Status: {order_status})")
                    continue

                print(f"✅ [{master_id}] New TRADED/FILLED order: {order_id}")
                new_last_trade_id = new_last_trade_id or order_id
                base_qty = (
                    order.get("quantity") or
                    order.get("orderQuantity") or
                    order.get("qty") or 1
                )
                price = float(order.get("price") or order.get("orderPrice") or order.get("avg_price") or 0)
                transaction_type = (
                    order.get("transactionType") or
                    order.get("transaction_type") or
                    order.get("side") or
                    "BUY"
                ).upper()
                symbol = order.get("tradingSymbol") or order.get("symbol") or order.get("stock") or "UNKNOWN"
                master_owner = master.get("owner")
                record_trade(master_owner, symbol, transaction_type, base_qty, price, order_status.upper())

                if not children:
                    print(f"ℹ️ [{master_id}] No children to copy trades to.")
                    continue

                for child in children:
                    if child.get("copy_status") != "On":
                        print(f"➡️ Skipping child {child['client_id']} (copy_status is Off)")
                        continue

                    child_broker = child.get("broker", "Unknown").lower()
                    child_credentials = child.get("credentials", {})
                    try:
                        ChildBrokerClass = get_broker_class(child_broker)
                        rest_child = {k: v for k, v in child_credentials.items() if k != "access_token"}
                        child_api = ChildBrokerClass(
                            client_id=child.get("client_id"),
                            access_token=child_credentials.get("access_token"),
                            **rest_child
                        )
                    except Exception as e:
                        print(f"❌ Could not initialize child API ({child_broker}): {e}")
                        continue

                    multiplier = float(child.get("multiplier", 1))
                    copied_qty = max(1, int(float(base_qty) * multiplier))

                    symbol = order.get("tradingSymbol") or order.get("symbol") or order.get("stock") or "UNKNOWN"
                    exchange = order.get("exchange") or order.get("exchangeSegment") or order.get("exchange_segment") or "NSE"
                    transaction_type = (
                        order.get("transactionType") or
                        order.get("transaction_type") or
                        order.get("side") or
                        "BUY"
                    ).upper()
                    order_type = (
                        order.get("orderType") or
                        order.get("order_type") or
                        "MARKET"
                    ).upper()
                    product_type = (
                        order.get("productType") or
                        order.get("ProductType") or
                        order.get("product_type") or
                        "INTRADAY"
                    ).upper()
                    price = float(order.get("price") or order.get("orderPrice") or order.get("avg_price") or 0)

                    try:
                        if child_broker == "dhan":
                            security_id = order.get("securityId") or order.get("security_id") or SYMBOL_MAP.get(symbol.upper())
                            response = child_api.place_order(
                                security_id=security_id,
                                exchange_segment=exchange,
                                transaction_type=transaction_type,
                                quantity=copied_qty,
                                order_type=order_type,
                                product_type=product_type,
                                price=price or 0
                            )
                        else:
                            response = child_api.place_order(
                                tradingsymbol=symbol,
                                exchange=exchange,
                                transaction_type=transaction_type,
                                quantity=copied_qty,
                                order_type=order_type,
                                product=product_type,
                                price=price or 0  # Always pass price, even if 0 for MARKET
                            )
                        
                        if isinstance(response, dict) and response.get("status") == "failure":
                            error_msg = response.get("error") or response.get("remarks") or "Unknown error"
                            print(f"❌ Trade FAILED for {child['client_id']} (Reason: {error_msg})")
                            save_log(child['client_id'], symbol, transaction_type, copied_qty, "FAILED", error_msg)
                            record_trade(child.get('owner'), symbol, transaction_type, copied_qty, price, 'FAILED')
                        else:
                            order_id_child = response.get("order_id") or response.get("orderId")
                            print(f"✅ Copied to {child['client_id']} (Order ID: {order_id_child})")
                            save_log(child['client_id'], symbol, transaction_type, copied_qty, "SUCCESS", str(response))
                            save_order_mapping(
                                master_order_id=order_id,
                                child_order_id=order_id_child,
                                master_id=master_id,
                                master_broker=master_broker,
                                child_id=child["client_id"],
                                child_broker=child_broker,
                                symbol=symbol
                            )
                            record_trade(child.get('owner'), symbol, transaction_type, copied_qty, price, 'SUCCESS')
                    except Exception as e:
                        print(f"❌ Error copying to {child['client_id']}: {e}")
                        save_log(child['client_id'], symbol, transaction_type, copied_qty, "FAILED", str(e))

            if new_last_trade_id:
                print(f"✅ Updating last_copied_trade_id for {master_id} to {new_last_trade_id}")
                db[last_copied_key] = new_last_trade_id

        safe_write_json("accounts.json", db)

    except Exception as e:
        print(f"❌ poll_and_copy_trades encountered an error: {e}")


def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=poll_and_copy_trades, trigger="interval", seconds=10)
    scheduler.start()
    print("✅ Background copy trader scheduler is running...")
    return scheduler

@app.route("/connect-zerodha", methods=["POST"])
def connect_zerodha():
    data = request.json
    broker = ZerodhaBroker(
        client_id=data.get("client_id"),
        api_key=data.get("api_key"),
        api_secret=data.get("api_secret"),
        password=data.get("password"),
        totp_secret=data.get("totp_secret"),
    )
    return jsonify({"access_token": broker.access_token})

# --- Order Book Endpoint ---
@app.route('/api/order-book/<client_id>', methods=['GET'])
def get_order_book(client_id):
    try:
        with open("accounts.json", "r") as f:
            db = json.load(f)
        masters = [acc for acc in db.get("accounts", []) if acc.get("role") == "master"]
        master = next((m for m in masters if m.get("client_id") == client_id), None)
        if not master:
            return jsonify({"error": "Master not found"}), 404

        api = broker_api(master)
        orders_resp = api.get_order_list()
        orders = orders_resp.get("data", orders_resp.get("orders", []))

        formatted = []
        for order in orders:
            formatted.append({
                "order_id": order.get("orderId") or order.get("order_id"),
                "side": order.get("transactionType", order.get("side", "NA")),
                "status": order.get("orderStatus", order.get("status", "NA")),
                "symbol": order.get("tradingSymbol", order.get("symbol", "—")),
                "product_type": order.get("productType", order.get("product", "—")),
                "placed_qty": order.get("orderQuantity", order.get("qty", 0)),
                "filled_qty": order.get("filledQuantity", order.get("filled_qty", 0)),
                "avg_price": order.get("averagePrice", order.get("avg_price", 0)),
                "order_time": order.get("orderTimestamp", order.get("order_time", "")).replace("T", " ").split(".")[0],
                "remarks": order.get("remarks", "—")
            })

        return jsonify(formatted), 200

    except Exception as e:
        print(f"❌ Error in get_order_book(): {e}")
        return jsonify({"error": str(e)}), 500


# === Webhook to place orders using stored user credentials ===
@app.route("/webhook/<user_id>", methods=["POST"])
def webhook(user_id):
    try:
        data = request.get_json(force=True)
    except Exception:
        data = {}

    # ALERT HANDLING (unchanged)
    if isinstance(data, str):
        return jsonify({"status": "Alert logged", "message": data}), 200
    if "message" in data:
        return jsonify({"status": "Alert logged", "message": data["message"]}), 200

    symbol = data.get("symbol")
    action = data.get("action")
    quantity = data.get("quantity")

    if not all([symbol, action, quantity]):
        return jsonify({"error": "Missing required fields (symbol, action, quantity)"}), 400

    # Load users.json (or however you store users)
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except FileNotFoundError:
        return jsonify({"error": "User database not found"}), 500

    if user_id not in users:
        return jsonify({"error": "Invalid webhook ID"}), 403

    user = users[user_id]
    broker_name = user.get("broker", "dhan")
    client_id = user["client_id"]
    access_token = user["access_token"]

    # === Dynamic Broker Adapter ===
    try:
        BrokerClass = get_broker_class(broker_name)
        broker_api = BrokerClass(client_id, access_token)
    except Exception as e:
        return jsonify({"error": f"Could not initialize broker: {e}"}), 500

    # === Broker-agnostic order parameter builder ===
    order_params = {}
    if broker_name.lower() == "dhan":
        security_id = SYMBOL_MAP.get(symbol.strip().upper())
        if not security_id:
            return jsonify({"error": f"Symbol '{symbol}' not found in symbol map."}), 400
        order_params = dict(
            security_id=security_id,
            exchange_segment=broker_api.NSE,
            transaction_type=broker_api.BUY if action.upper() == "BUY" else broker_api.SELL,
            quantity=int(quantity),
            order_type=broker_api.MARKET,
            product_type=broker_api.INTRA,
            price=0
        )
    elif broker_name.lower() == "zerodha":
        order_params = dict(
            tradingsymbol=symbol,
            exchange="NSE",
            transaction_type=action.upper(),
            quantity=int(quantity),
            order_type="MARKET",
            product="MIS",  # Or "CNC"
            price=None,
        )
    # Add more brokers here...

    # === Place order ===
    try:
        response = broker_api.place_order(**order_params)
        if isinstance(response, dict) and response.get("status") == "failure":
            status = "FAILED"
            reason = (
                response.get("remarks") or response.get("error_message") or
                response.get("errorMessage") or response.get("error") or "Unknown error"
            )
            record_trade(user_id, symbol, action.upper(), quantity, order_params.get('price'), status)
            return jsonify({"status": status, "reason": reason}), 400

        # If order was SUCCESSFUL, trigger instant copying for all children!
        poll_and_copy_trades()
        status = "SUCCESS"

        success_msg = response.get("remarks", "Trade placed successfully")
        record_trade(user_id, symbol, action.upper(), quantity, order_params.get('price'), status)
        return jsonify({"status": status, "result": str(success_msg)}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/master-squareoff', methods=['POST'])
def master_squareoff():
    data = request.json
    master_order_id = data.get("master_order_id")
    if not master_order_id:
        return jsonify({"error": "Missing master_order_id"}), 400

    try:
        with open("order_mappings.json", "r") as f:
            mappings = json.load(f)
        targets = [m for m in mappings if m["master_order_id"] == master_order_id and m["status"] == "ACTIVE"]
        if not targets:
            return jsonify({"message": "No active child orders found for this master order."}), 200

        results = []
        with open("accounts.json", "r") as f:
            accounts = json.load(f)

        for mapping in targets:
            child_id = mapping["child_client_id"]
            symbol = mapping["symbol"]
            found = None
            for master in accounts.get("masters", []):
                for child in master.get("children", []):
                    if child["client_id"] == child_id:
                        found = child
                        break
                if found: break

            if not found:
                results.append(f"{child_id} → ❌ Credentials not found")
                continue

            try:
                api = broker_api(found)
                positions_resp = api.get_positions()
                positions = positions_resp.get("data", positions_resp.get("positions", []))
                match = next((p for p in positions if (p.get("tradingSymbol") or p.get("symbol", "")).upper() == symbol.upper() and int(p.get("netQty", p.get("net_quantity", 0))) != 0), None)
                if not match:
                    results.append(f"{child_id} → ℹ️ No open position in {symbol}")
                    continue

                direction = "SELL" if match.get("netQty", match.get("net_quantity", 0)) > 0 else "BUY"
                response = api.place_order(
                    security_id=match.get("securityId", match.get("security_id")),
                    exchange_segment=match.get("exchangeSegment", match.get("exchange_segment")),
                    transaction_type=direction,
                    quantity=abs(int(match.get("netQty", match.get("net_quantity", 0)))),
                    order_type="MARKET",
                    product_type="INTRADAY",
                    price=0
                )
                if isinstance(response, dict) and response.get("status") == "failure":
                    results.append(f"{child_id} → ❌ Square-off failed: {response.get('remarks', response.get('error', 'Unknown error'))}")
                else:
                    mapping["status"] = "SQUARED_OFF"
                    results.append(f"{child_id} → ✅ Square-off done")

            except Exception as e:
                results.append(f"{child_id} → ❌ ERROR: {str(e)}")

        with open("order_mappings.json", "w") as f:
            json.dump(mappings, f, indent=2)

        return jsonify({"message": "Square-off complete", "details": results}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/master-orders', methods=['GET'])
def get_master_orders():
    try:
        path = "order_mappings.json"
        if not os.path.exists(path):
            return jsonify([]), 200

        with open(path, "r") as f:
            mappings = json.load(f)

        master_id_filter = request.args.get("master_id")

        master_summary = {}

        for entry in mappings:
            master_id = entry["master_client_id"]
            if master_id_filter and master_id != master_id_filter:
                continue  # ⛔ skip non-matching masters

            mid = entry["master_order_id"]
            if mid not in master_summary:
                master_summary[mid] = {
                    "master_order_id": mid,
                    "symbol": entry["symbol"],
                    "master_client_id": master_id,
                    "master_broker": entry.get("master_broker", "Unknown"),
                    "status": "ACTIVE",
                    "total_children": 0,
                    "child_statuses": [],
                    "timestamp": entry.get("timestamp", "—")
                }

            master_summary[mid]["total_children"] += 1
            master_summary[mid]["child_statuses"].append(entry["status"])

        for summary in master_summary.values():
            if all(s != "ACTIVE" for s in summary["child_statuses"]):
                summary["status"] = "CANCELLED"

        return jsonify(list(master_summary.values())), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500



@app.route('/api/square-off', methods=['POST'])
def square_off():
    data = request.json
    client_id = data.get("client_id")
    symbol = data.get("symbol")
    is_master = data.get("is_master", False)

    if not client_id or not symbol:
        return jsonify({"error": "Missing client_id or symbol"}), 400

    try:
        with open("accounts.json", "r") as f:
            accounts = json.load(f)
    except Exception:
        return jsonify({"error": "Failed to load accounts file"}), 500

    found, parent = find_account_by_client_id(accounts, client_id)
    if not found:
        return jsonify({"error": "Client not found"}), 404
    if parent is None:
        master = found
    else:
        master = parent

    if is_master and parent is None:
        # Square off master only
        api = broker_api(master)
        try:
            positions_resp = api.get_positions()
            positions = positions_resp.get("data", [])
            match = next((p for p in positions if p.get("tradingSymbol", "").upper() == symbol.upper()), None)
            if not match or int(match.get("netQty", 0)) == 0:
                return jsonify({"message": f"Master → No active position in {symbol} (already squared off)"}), 200

            qty = abs(int(match["netQty"]))
            direction = "SELL" if match["netQty"] > 0 else "BUY"

            resp = api.place_order(
                security_id=match["securityId"],
                exchange_segment=match["exchangeSegment"],
                transaction_type=direction,
                quantity=qty,
                order_type="MARKET",
                product_type="INTRADAY",
                price=0
            )
            save_log(master["client_id"], symbol, "SQUARE_OFF", qty, "SUCCESS", str(resp))
            return jsonify({"message": "✅ Master square-off placed", "details": str(resp)}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    else:
        # Square off all children under master (parent==None means master, else parent)
        results = []
        for child in master.get("children", []):
            if child.get("copy_status") != "On":
                results.append(f"Child {child['client_id']} → Skipped (copy OFF)")
                continue

            try:
                api = broker_api(child)
                positions_resp = api.get_positions()
                positions = positions_resp.get('data', [])
                match = next((p for p in positions if p.get('tradingSymbol', '').upper() == symbol.upper()), None)

                if not match or int(match.get('netQty', 0)) == 0:
                    results.append(f"Child {child['client_id']} → Skipped (no active position in {symbol})")
                    continue

                security_id = match['securityId']
                exchange_segment = match['exchangeSegment']
                quantity = abs(int(match['netQty']))
                direction = "SELL" if match['netQty'] > 0 else "BUY"

                response = api.place_order(
                    security_id=security_id,
                    exchange_segment=exchange_segment,
                    transaction_type=direction,
                    quantity=quantity,
                    order_type="MARKET",
                    product_type="INTRADAY",
                    price=0
                )

                if isinstance(response, dict) and response.get("status") == "failure":
                    msg = response.get("remarks", "Unknown error")
                    results.append(f"Child {child['client_id']} → FAILED: {msg}")
                    save_log(child['client_id'], symbol, "SQUARE_OFF", quantity, "FAILED", msg)
                else:
                    results.append(f"Child {child['client_id']} → SUCCESS")
                    save_log(child['client_id'], symbol, "SQUARE_OFF", quantity, "SUCCESS", str(response))

            except Exception as e:
                error_msg = str(e)
                results.append(f"Child {child['client_id']} → ERROR: {error_msg}")
                save_log(child['client_id'], symbol, "SQUARE_OFF", 0, "ERROR", error_msg)

        return jsonify({"message": "🔁 Square-off for all children completed", "details": results}), 200

@app.route('/api/order-mappings', methods=['GET'])
def get_order_mappings():
    try:
        path = "order_mappings.json"
        if not os.path.exists(path):
            return jsonify([]), 200

        with open(path, "r") as f:
            mappings = json.load(f)

        return jsonify(mappings), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

# --- Cancel Order Endpoint ---
@app.route('/api/cancel-order', methods=['POST'])
def cancel_order():
    data = request.json
    master_order_id = data.get("master_order_id")
    if not master_order_id:
        return jsonify({"error": "Missing master_order_id"}), 400

    try:
        if not os.path.exists("order_mappings.json"):
            return jsonify({"error": "No order mappings found"}), 404

        with open("order_mappings.json", "r") as f:
            mappings = json.load(f)

        relevant = [m for m in mappings if m["master_order_id"] == master_order_id and m["status"] == "ACTIVE"]
        if not relevant:
            return jsonify({"message": "No active child orders found for this master order."}), 200

        results = []
        with open("accounts.json", "r") as f:
            accounts = json.load(f)

        for mapping in relevant:
            child_id = mapping["child_client_id"]
            child_order_id = mapping["child_order_id"]
            found = None
            for m in accounts.get("masters", []):
                for c in m.get("children", []):
                    if c["client_id"] == child_id:
                        found = c
                        break
                if found: break

            if not found:
                results.append(f"{child_id} → ❌ Client not found")
                continue

            try:
                api = broker_api(found)
                cancel_resp = api.cancel_order(child_order_id)

                if isinstance(cancel_resp, dict) and cancel_resp.get("status") == "failure":
                    results.append(f"{child_id} → ❌ Cancel failed: {cancel_resp.get('remarks', cancel_resp.get('error', 'Unknown error'))}")
                else:
                    results.append(f"{child_id} → ✅ Cancelled")
                    mapping["status"] = "CANCELLED"

            except Exception as e:
                results.append(f"{child_id} → ❌ ERROR: {str(e)}")

        with open("order_mappings.json", "w") as f:
            json.dump(mappings, f, indent=2)

        return jsonify({"message": "Cancel process completed", "details": results}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/change-master', methods=['POST'])
def change_master():
    data = request.json
    child_id = data.get("child_id")
    new_master_id = data.get("new_master_id")
    if not child_id or not new_master_id:
        return jsonify({"error": "Missing child_id or new_master_id"}), 400
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "No accounts file found"}), 500
    found = None
    for acc in db["accounts"]:
        if acc["client_id"] == child_id:
            acc["linked_master_id"] = new_master_id
            found = acc
    if not found:
        return jsonify({"error": "Child not found."}), 404
    safe_write_json("accounts.json", db)

    return jsonify({"message": f"Child {child_id} now linked to master {new_master_id}."}), 200

@app.route('/api/remove-child', methods=['POST'])
def remove_child():
    data = request.json
    client_id = data.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "No accounts file found"}), 500
    found = None
    for acc in db["accounts"]:
        if acc["client_id"] == client_id and acc.get("role") == "child":
            acc["role"] = None
            acc["linked_master_id"] = None
            acc["copy_status"] = "Off"
            acc["multiplier"] = 1
            found = acc
    if not found:
        return jsonify({"error": "Child not found."}), 404
    safe_write_json("accounts.json", db)

    return jsonify({"message": f"Child {client_id} removed from master."}), 200


# PATCH for /api/update-multiplier
@app.route('/api/update-multiplier', methods=['POST'])
def update_multiplier():
    data = request.json
    client_id = data.get("client_id")
    new_multiplier = data.get("multiplier")
    if not client_id or new_multiplier is None:
        return jsonify({"error": "Missing required fields"}), 400
    try:
        new_multiplier = float(new_multiplier)
        if new_multiplier < 0.1:
            return jsonify({"error": "Multiplier must be at least 0.1"}), 400
    except ValueError:
        return jsonify({"error": "Invalid multiplier format"}), 400
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "No accounts found"}), 400
    found = None
    for acc in db["accounts"]:
        if acc["client_id"] == client_id:
            acc["multiplier"] = new_multiplier
            found = acc
    if not found:
        return jsonify({"error": "Child account not found"}), 404
    safe_write_json("accounts.json", db)

    return jsonify({"message": f"Multiplier updated to {new_multiplier} for {client_id}"}), 200


@app.route("/marketwatch")
def market_watch():
    return render_template("marketwatch.html")

@app.route('/api/add-account', methods=['POST'])
@login_required
def add_account():
    user = session.get("user")
    data = request.json
    broker = data.get('broker')
    client_id = data.get('client_id')
    username = data.get('username')
    
    credentials = {k: v for k, v in data.items() if k not in ('broker', 'client_id', 'username')}
    
    if not broker or not client_id or not username:
        return jsonify({'error': 'Missing broker, client_id or username'}), 400

    # Load DB
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            accounts_data = json.load(f)
    else:
        accounts_data = {"accounts": []}

    # Check for duplicates
    for acc in accounts_data["accounts"]:
        if (
            acc.get("client_id") == client_id
            and acc.get("broker") == broker
            and acc.get("owner") == user
        ):
            return jsonify({'error': 'Account already exists'}), 400

    # Add new account
        accounts_data["accounts"].append({
        "broker": broker,
        "client_id": client_id,
        "username": username,
        "credentials": credentials,
        "owner": user,
        "status": "Connected",
        "auto_login": True,
        "last_login": datetime.now().isoformat(),
        "role": None,
        "linked_master_id": None,
        "multiplier": 1,
        "copy_status": "Off"
    })
    db_user = User.query.filter_by(email=user).first()
    if db_user:
        existing = Account.query.filter_by(user_id=db_user.id, client_id=client_id).first()
        if not existing:
            account_obj = Account(
                user_id=db_user.id,
                broker=broker,
                client_id=client_id,
                token_expiry=credentials.get('token_expiry'),
                status='Connected'
            )
            db.session.add(account_obj)
        else:
            existing.broker = broker
            existing.token_expiry = credentials.get('token_expiry')
            existing.status = 'Connected'
        db.session.commit()

    safe_write_json("accounts.json", accounts_data)
    return jsonify({'message': f"✅ Account {username} ({broker}) added."}), 200

@app.route('/api/accounts')
@login_required
def get_accounts():
    try:
        if os.path.exists("accounts.json"):
            with open("accounts.json", "r") as f:
                db = json.load(f)
            if "accounts" not in db or not isinstance(db["accounts"], list):
                raise ValueError("Corrupt accounts.json: missing 'accounts' list")
        else:
            db = {'accounts': []}

        user = session.get("user")
        accounts = [a for a in db["accounts"] if a.get("owner") == user]

        masters = []
        for acc in accounts:
            if acc.get("role") == "master":
                # Attach children to each master
                children = [child for child in accounts if child.get("role") == "child" and child.get("linked_master_id") == acc.get("client_id")]
                acc_copy = dict(acc)
                acc_copy["children"] = children
                masters.append(acc_copy)
        return jsonify({
            "masters": masters,
            "accounts": accounts
        })
    except Exception as e:
        print(f"❌ Error in /api/accounts: {str(e)}")
        return jsonify({"error": str(e)}), 500
@app.route('/api/groups', methods=['GET'])
@login_required
def get_groups():
    """Return all account groups for the logged-in user."""
    user = session.get("user")
    groups_path = "groups.json"
    if os.path.exists(groups_path):
        with open(groups_path, "r") as f:
            db = json.load(f)
    else:
        db = {"groups": []}
    groups = [g for g in db.get("groups", []) if g.get("owner") == user]
    return jsonify(groups)


@app.route('/api/create-group', methods=['POST'])
@login_required
def create_group():
    """Create a new account group."""
    data = request.json
    name = data.get("name")
    members = data.get("members", [])
    if not name:
        return jsonify({"error": "Missing group name"}), 400

    groups_path = "groups.json"
    if os.path.exists(groups_path):
        with open(groups_path, "r") as f:
            db = json.load(f)
    else:
        db = {"groups": []}

    user = session.get("user")
    for g in db.get("groups", []):
        if g.get("name") == name and g.get("owner") == user:
            return jsonify({"error": "Group already exists"}), 400

    db.setdefault("groups", []).append({
        "name": name,
        "owner": user,
        "members": members
    })
    safe_write_json(groups_path, db)
    return jsonify({"message": f"Group '{name}' created"})


@app.route('/api/groups/<group_name>/add', methods=['POST'])
@login_required
def add_account_to_group(group_name):
    """Add an account to an existing group."""
    client_id = request.json.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    groups_path = "groups.json"
    if os.path.exists(groups_path):
        with open(groups_path, "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "Group database not found"}), 500

    user = session.get("user")
    for g in db.get("groups", []):
        if g.get("name") == group_name and g.get("owner") == user:
            if client_id not in g.get("members", []):
                g.setdefault("members", []).append(client_id)
                safe_write_json(groups_path, db)
                return jsonify({"message": f"Added {client_id} to {group_name}"})
            return jsonify({"message": "Account already in group"})

    return jsonify({"error": "Group not found"}), 404


@app.route('/api/groups/<group_name>/remove', methods=['POST'])
@login_required
def remove_account_from_group(group_name):
    """Remove an account from a group."""
    client_id = request.json.get("client_id")
    if not client_id:
        return jsonify({"error": "Missing client_id"}), 400

    groups_path = "groups.json"
    if os.path.exists(groups_path):
        with open(groups_path, "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "Group database not found"}), 500

    user = session.get("user")
    for g in db.get("groups", []):
        if g.get("name") == group_name and g.get("owner") == user:
            if client_id in g.get("members", []):
                g["members"].remove(client_id)
                safe_write_json(groups_path, db)
                return jsonify({"message": f"Removed {client_id} from {group_name}"})
            return jsonify({"error": "Account not in group"}), 400

    return jsonify({"error": "Group not found"}), 404


@app.route('/api/group-order', methods=['POST'])
@login_required
def place_group_order():
    """Place the same order across all accounts in a group."""
    data = request.json
    group_name = data.get("group_name")
    symbol = data.get("symbol")
    action = data.get("action")
    quantity = data.get("quantity")

    if not all([group_name, symbol, action, quantity]):
        return jsonify({"error": "Missing required fields"}), 400

    groups_path = "groups.json"
    if os.path.exists(groups_path):
        with open(groups_path, "r") as f:
            groups_db = json.load(f)
    else:
        return jsonify({"error": "No groups configured"}), 400

    group = None
    user = session.get("user")
    for g in groups_db.get("groups", []):
        if g.get("name") == group_name and g.get("owner") == user:
            group = g
            break
    if not group:
        return jsonify({"error": "Group not found"}), 404

    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            accounts_db = json.load(f)
    else:
        return jsonify({"error": "No accounts configured"}), 500

    accounts = [a for a in accounts_db.get("accounts", []) if a.get("client_id") in group.get("members", [])]

    results = []
    for acc in accounts:
        try:
            api = broker_api(acc)
            broker_name = acc.get("broker", "dhan").lower()
            order_params = {}
            if broker_name == "dhan":
                security_id = SYMBOL_MAP.get(symbol.upper())
                order_params = dict(
                    security_id=security_id,
                    exchange_segment=api.NSE,
                    transaction_type=api.BUY if action.upper() == "BUY" else api.SELL,
                    quantity=int(quantity),
                    order_type=api.MARKET,
                    product_type=api.INTRA,
                    price=0
                )
            else:
                order_params = dict(
                    tradingsymbol=symbol,
                    exchange="NSE",
                    transaction_type=action.upper(),
                    quantity=int(quantity),
                    order_type="MARKET",
                    product="MIS",
                    price=None,
                )
            resp = api.place_order(**order_params)
            if isinstance(resp, dict) and resp.get("status") == "failure":
                status = "FAILED"
                results.append({"client_id": acc.get("client_id"), "status": status, "reason": clean_response_message(resp)})
            else:
                status = "SUCCESS"
                results.append({"client_id": acc.get("client_id"), "status": status})

            record_trade(user, symbol, action.upper(), quantity, order_params.get('price'), status)
        except Exception as e:
            results.append({"client_id": acc.get("client_id"), "status": "ERROR", "reason": str(e)})

    return jsonify(results)
    
# Set master account
@app.route('/api/set-master', methods=['POST'])
def set_master():
    try:
        client_id = request.json.get('client_id')
        if not client_id:
            return jsonify({"error": "Missing client_id"}), 400

        if os.path.exists("accounts.json"):
            with open("accounts.json", "r") as f:
                db = json.load(f)
        else:
            db = {"accounts": []}
            
        user = session.get("user")
        found = False
        for acc in db["accounts"]:
            if acc.get("client_id") == client_id and acc.get("owner") == user:
                acc["role"] = "master"
                acc.pop("linked_master_id", None)
                acc["copy_status"] = "Off"
                acc["multiplier"] = 1
                found = True    # <-- This line was missing!

        if not found:
            return jsonify({"error": "Account not found"}), 404

        safe_write_json("accounts.json", db)

        return jsonify({"message": "Set as master successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/set-child', methods=['POST'])
@login_required
def set_child():
    try:
        client_id = request.json.get('client_id')
        linked_master_id = request.json.get('linked_master_id')
        if not client_id or not linked_master_id:
            return jsonify({"error": "Missing client_id or linked_master_id"}), 400

        if os.path.exists("accounts.json"):
            with open("accounts.json", "r") as f:
                db = json.load(f)
        else:
            db = {"accounts": []}

        user = session.get("user")
        found = False
        for acc in db["accounts"]:
            if acc.get("client_id") == client_id and acc.get("owner") == user:
                acc["role"] = "child"
                acc["linked_master_id"] = linked_master_id
                acc["copy_status"] = "Off"
                acc["multiplier"] = 1
                found = True   # <-- This line was missing!

        if not found:
            return jsonify({"error": "Account not found"}), 404

        safe_write_json("accounts.json", db)

        return jsonify({"message": "Set as child successfully"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Start copying for a child account
@app.route('/api/start-copy', methods=['POST'])
@login_required
def start_copy():
    data = request.json
    client_id = data.get("client_id")
    master_id = data.get("master_id")
    if not client_id or not master_id:
        return jsonify({"error": "Missing client_id or master_id"}), 400
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "No accounts file found"}), 500

    user = session.get("user")

    found = False
    for acc in db["accounts"]:
        if acc["client_id"] == client_id and acc.get("owner") == user:
            acc["role"] = "child"
            acc["linked_master_id"] = master_id
            acc["copy_status"] = "On"
            found = True
    if not found:
        return jsonify({"error": "Child account not found."}), 404

    safe_write_json("accounts.json", db)
    return jsonify({'message': f"✅ Started copying for {client_id} under master {master_id}."})


@app.route('/api/stop-copy', methods=['POST'])
@login_required
def stop_copy():
    data = request.json
    client_id = data.get("client_id")
    master_id = data.get("master_id")
    if not client_id or not master_id:
        return jsonify({"error": "Missing client_id or master_id"}), 400
    if os.path.exists("accounts.json"):
        with open("accounts.json", "r") as f:
            db = json.load(f)
    else:
        return jsonify({"error": "No accounts file found"}), 500

    user = session.get("user")
    found = False
    for acc in db["accounts"]:
        if acc["client_id"] == client_id and acc.get("linked_master_id") == master_id and acc.get("owner") == user:
            acc["copy_status"] = "Off"
            found = True
    if not found:
        return jsonify({"error": "Child account not found."}), 404

    safe_write_json("accounts.json", db)
    return jsonify({'message': f"🛑 Stopped copying for {client_id} under master {master_id}."})

# === Endpoint to fetch passive alert logs ===
@app.route("/api/alerts")
def get_alerts():
    user_id = request.args.get("user_id")
    conn = sqlite3.connect("tradelogs.db")
    c = conn.cursor()
    c.execute("SELECT timestamp, response FROM logs WHERE user_id = ? AND status = 'ALERT' ORDER BY id DESC LIMIT 20", (user_id,))
    rows = c.fetchall()
    conn.close()

    alerts = [{"time": row[0], "message": row[1]} for row in rows]
    return jsonify(alerts)



# === API to save new user from login form ===
@app.route("/register", methods=["POST"])
def register_user():
    data = request.json
    user_id = data.get("user_id")
    client_id = data.get("client_id")
    access_token = data.get("access_token")

    if not all([user_id, client_id, access_token]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except FileNotFoundError:
        users = {}

    users[user_id] = {
        "client_id": client_id,
        "access_token": access_token
    }

    with open("users.json", "w") as f:
        json.dump(users, f, indent=2)

    return jsonify({"status": "User registered successfully", "webhook": f"/webhook/{user_id}"})

# === API to fetch logs for a user ===
@app.route("/logs")
def get_logs():
    user_id = request.args.get("user_id")
    conn = sqlite3.connect("tradelogs.db")
    c = conn.cursor()
    c.execute("SELECT * FROM logs WHERE user_id = ? ORDER BY id DESC LIMIT 100", (user_id,))
    rows = c.fetchall()
    conn.close()

    logs = []
    for row in rows:
        logs.append({
            "timestamp": row[1],
            "user_id": row[2],
            "symbol": row[3],
            "action": row[4],
            "quantity": row[5],
            "status": row[6],
            "response": row[7]
        })

    return jsonify(logs)

# === API to get live portfolio snapshot (holdings) ===
@app.route("/api/portfolio/<user_id>")
def get_portfolio(user_id):
    # Check users.json (external registered users)
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except:
        users = {}

    if user_id in users:
        client_id = users[user_id]["client_id"]
        access_token = users[user_id]["access_token"]
        dhan = dhanhq(client_id, access_token)
        try:
            positions_resp = dhan.get_positions()
            return jsonify(positions_resp)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Check in accounts.json using utility (for dashboard accounts)
    try:
        with open("accounts.json", "r") as f:
            accounts = json.load(f)
        found, _ = find_account_by_client_id(accounts, user_id)
        if not found:
            return jsonify({"error": "Invalid user ID"}), 403
        api = broker_api(found)
        positions_resp = api.get_positions()
        return jsonify(positions_resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# === API to get trade summary and open orders ===
@app.route("/api/orders/<user_id>")
def get_orders(user_id):
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except Exception as e:
        print(f"❌ Failed to load users.json: {str(e)}")
        return jsonify({"error": "User DB not found"}), 500

    if user_id not in users:
        return jsonify({"error": "Invalid user ID"}), 403

    user = users[user_id]
    dhan = dhanhq(user["client_id"], user["access_token"])

    try:
        resp = dhan.get_order_list()
        print(f"👉 Full Dhan API response for {user_id}: {resp}")

        # Defensive check: is it the expected dict?
        if not isinstance(resp, dict) or "data" not in resp:
            return jsonify({"error": "Unexpected response format", "details": resp}), 500

        orders = resp["data"]  # ✅ the real list of orders now

        total_trades = len(orders)
        last_order = orders[0] if orders else {}
        total_qty = sum(int(o.get("quantity", 0)) for o in orders)

        return jsonify({
            "orders": orders,
            "summary": {
                "total_trades": total_trades,
                "last_status": last_order.get("orderStatus", "N/A"),
                "total_quantity": total_qty
            }
        })
    except Exception as e:
        print(f"❌ Error while fetching orders for {user_id}: {str(e)}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/account/<user_id>")
def get_account_stats(user_id):
    try:
        with open("users.json", "r") as f:
            users = json.load(f)
    except:
        return jsonify({"error": "User DB not found"}), 500

    if user_id not in users:
        return jsonify({"error": "Invalid user ID"}), 403

    user = users[user_id]
    dhan = dhanhq(user["client_id"], user["access_token"])

    try:
        stats_resp = dhan.get_fund_limits()
        print(f"👉 Fund stats for {user_id}: {stats_resp}")

        if not isinstance(stats_resp, dict) or "data" not in stats_resp:
            return jsonify({"error": "Unexpected response format", "details": stats_resp}), 500

        stats = stats_resp["data"]

        # Map to clean keys:
        mapped_stats = {
            "total_funds": stats.get("availabelBalance", 0),
            "available_margin": stats.get("withdrawableBalance", 0),
            "used_margin": stats.get("utilizedAmount", 0)
        }
        return jsonify(mapped_stats)

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/users", methods=["GET", "POST"])
@login_required
def user_profile():
    username = session.get("user")
    users = {}
    if os.path.exists("users.json"):
        with open("users.json", "r") as f:
            try:
                users = json.load(f)
            except Exception:
                users = {}

    user = users.get(username, {})
    message = ""

    if request.method == "POST":
        action = request.form.get("action")

        if action == "save_profile":
            first_name = request.form.get("first_name", "")
            last_name = request.form.get("last_name", "")

            user["first_name"] = first_name
            user["last_name"] = last_name

            file = request.files.get("profile_image")
            if file and file.filename:
                image_dir = os.path.join("static", "profile_images")
                os.makedirs(image_dir, exist_ok=True)
                filename = secure_filename(username + "_" + file.filename)
                file.save(os.path.join(image_dir, filename))
                user["profile_image"] = os.path.join("profile_images", filename)
            message = "Profile updated"

        elif action == "send_otp" and not user.get("mobile"):
            mobile = request.form.get("mobile")
            if mobile:
                otp = "".join(random.choices(string.digits, k=6))
                user["pending_mobile"] = mobile
                user["otp"] = otp
                print(f"OTP for {mobile}: {otp}")
                message = f"OTP sent to {mobile}"

        elif action == "verify_otp" and user.get("pending_mobile"):
            otp_input = request.form.get("otp")
            if otp_input == user.get("otp"):
                user["mobile"] = user.get("pending_mobile")
                user.pop("pending_mobile", None)
                user.pop("otp", None)
                user["mobile_verified"] = True
                message = "Mobile number verified"
            else:
                message = "Invalid OTP"

        users[username] = user
        with open("users.json", "w") as f:
            json.dump(users, f, indent=2)

    profile_data = {
        "email": username,
        "first_name": user.get("first_name", ""),
        "last_name": user.get("last_name", ""),
        "plan": user.get("plan", "Free"),
        "profile_image": user.get("profile_image", "user.png"),
        "mobile": user.get("mobile"),
        "pending_mobile": user.get("pending_mobile"),
        "mobile_verified": user.get("mobile_verified", False),
    }

    return render_template("user.html", user=profile_data, message=message)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("email") or request.form.get("username")
        password = request.form.get("password")
        user = User.query.filter_by(email=username).first()
        if user and user.check_password(password):
            session["user"] = user.email
            user.last_login = datetime.now().strftime('%Y-%m-%d')
            db.session.commit()
            return redirect(url_for("summary"))
        return render_template("log-in.html", error="Invalid credentials")
    return render_template("log-in.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("email") or request.form.get("username")
        password = request.form.get("password")
        if User.query.filter_by(email=username).first():
            return render_template("sign-up.html", error="User already exists")
        user = User(email=username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        session["user"] = user.email
        return redirect(url_for("summary"))
    return render_template("sign-up.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))



# === Page routes ===
@app.route('/')
def home():
    return render_template("index.html")

@app.route('/dhan-dashboard')
@login_required
def dhan_dashboard():
    return render_template("dhan-dashboard.html")

@app.route("/Summary")
@login_required
def summary():
    return render_template("Summary.html")  # or "Summary.html" if that's your file name

@app.route("/copy-trading")
@login_required
def copytrading():
    return render_template("copy-trading.html")

@app.route("/Add-Account")
@login_required
def AddAccount():
    return render_template("Add-Account.html")

@app.route("/groups")
@login_required
def groups_page():
    return render_template("groups.html")

# === Admin routes ===
@app.route('/adminlogin', methods=['GET', 'POST'])
def admin_login():
    import os
    error = None

    if request.method == 'POST':
        input_email = request.form.get('email')
        input_password = request.form.get('password')

        # Compare with Render env vars
        admin_email = os.environ.get('ADMIN_EMAIL')
        admin_password = os.environ.get('ADMIN_PASSWORD')

        if input_email == admin_email and input_password == admin_password:
            session['admin'] = admin_email
            return redirect(url_for('admin_dashboard'))  # Replace with your dashboard route
        else:
            error = 'Invalid credentials'

    return render_template('login.html', error=error)


@app.route('/adminlogout')
def admin_logout():
    session.pop('admin', None)
    return redirect(url_for('admin_login'))

@app.route('/admindashboard')
@admin_login_required
def admin_dashboard():
    users = load_users()
    accounts = load_accounts()
    unique_brokers = {acc.broker for acc in accounts if acc.broker}

    today = date.today()
    start_today = today.strftime('%Y-%m-%d')
    end_today = (today + timedelta(days=1)).strftime('%Y-%m-%d')
    trades_today = Trade.query.filter(Trade.timestamp >= start_today,
                                     Trade.timestamp < end_today).count()
    metrics = {
        'total_users': len(users),
        'total_accounts': len(accounts),
        'brokers_connected': len(unique_brokers),
        'trades_today': trades_today,
        'uptime': format_uptime()
    }

    labels = []
    trade_counts = []
    signup_counts = []
    for i in range(5):
        day = today - timedelta(days=4 - i)
        start = day.strftime('%Y-%m-%d')
        end = (day + timedelta(days=1)).strftime('%Y-%m-%d')
        labels.append(day.strftime('%a'))
        trade_counts.append(Trade.query.filter(Trade.timestamp >= start,
                                               Trade.timestamp < end).count())
        signup_counts.append(User.query.filter(User.subscription_start >= start,
                                              User.subscription_start < end).count())

    trade_chart = {'labels': labels, 'data': trade_counts}
    signup_chart = {'labels': labels, 'data': signup_counts}

    broker_list = sorted({acc.broker.lower() for acc in accounts if acc.broker})
    api_status = []
    for name in broker_list:
        url = BROKER_STATUS_URLS.get(name)
        online = check_api(url) if url else False
        api_status.append({'name': name.title(), 'online': online})
    return render_template('dashboard.html', metrics=metrics, api_status=api_status, trade_chart=trade_chart, signup_chart=signup_chart)

@app.route('/adminusers')
@admin_login_required
def admin_users():
    users = load_users()
    return render_template('users.html', users=users)

@app.route('/adminbrokers')
@admin_login_required
def admin_brokers():
    accounts = load_accounts()
    broker_names = sorted({acc.broker for acc in accounts if acc.broker})
    return render_template('brokers.html', accounts=accounts, broker_names=broker_names)

@app.route('/admintrades')
@admin_login_required
def admin_trades():
    trades = load_trades()
    return render_template('trades.html', trades=trades)

@app.route('/adminsubscriptions')
@admin_login_required
def admin_subscriptions():
    users = load_users()
    subs = [u for u in users]
    return render_template('subscriptions.html', subscriptions=subs)

@app.route('/adminlogs')
@admin_login_required
def admin_logs():
    webhook_logs, system_logs = load_logs()
    return render_template('logs.html', webhook_logs=webhook_logs, system_logs=system_logs)

@app.route('/adminsettings', methods=['GET', 'POST'])
@admin_login_required
def admin_settings():
    settings = load_settings()
    if request.method == 'POST':
        settings['trading_enabled'] = bool(request.form.get('trading_enabled'))
        for key, value in request.form.items():
            if key == 'trading_enabled':
                continue
            settings[key] = value
        save_settings(settings)
    return render_template('settings.html', settings=settings)

@app.route('/adminprofile')
@admin_login_required
def admin_profile():
    return render_template('profile.html', admin={'email': session.get('admin')})

# ---- Admin action routes ----

@app.route('/adminusers/<int:user_id>/suspend', methods=['POST'])
@admin_login_required
def admin_suspend_user(user_id):
    user = User.query.get_or_404(user_id)
    user.plan = 'Suspended'
    db.session.commit()
    flash(f'User {user.email} suspended.')
    return redirect(url_for('admin_users'))


@app.route('/adminusers/<int:user_id>/reset', methods=['POST'])
@admin_login_required
def admin_reset_password(user_id):
    user = User.query.get_or_404(user_id)
    new_pass = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
    user.set_password(new_pass)
    db.session.commit()
    flash(f'New password for {user.email}: {new_pass}')
    return redirect(url_for('admin_users'))


@app.route('/adminusers/<int:user_id>')
@admin_login_required
def admin_view_user(user_id):
    user = User.query.get_or_404(user_id)
    return render_template('user_detail.html', user=user)


@app.route('/adminbrokers/<int:account_id>/revoke', methods=['POST'])
@admin_login_required
def admin_revoke_account(account_id):
    account = Account.query.get_or_404(account_id)
    account.status = 'Revoked'
    db.session.commit()
    flash('Account revoked')
    return redirect(url_for('admin_brokers'))


@app.route('/admintrades/<int:trade_id>/retry', methods=['POST'])
@admin_login_required
def admin_retry_trade(trade_id):
    trade = Trade.query.get_or_404(trade_id)
    trade.status = 'Pending'
    db.session.commit()
    flash('Trade marked for retry')
    return redirect(url_for('admin_trades'))


@app.route('/adminsubscriptions/<int:user_id>/change', methods=['POST'])
@admin_login_required
def admin_change_subscription(user_id):
    user = User.query.get_or_404(user_id)
    user.plan = 'Pro' if user.plan != 'Pro' else 'Free'
    db.session.commit()
    flash(f'Plan updated to {user.plan} for {user.email}')
    return redirect(url_for('admin_subscriptions'))

with app.app_context():
    db.create_all()

scheduler = start_scheduler()

if __name__ == '__main__':
    app.run(debug=True)
