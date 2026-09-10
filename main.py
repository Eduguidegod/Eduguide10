# ============================================================
# EduGuide Telegram Bot - main.py
# ============================================================
# Features:
# - Telegram registration
# - PostgreSQL persistent database
# - Razorpay Payment Links
# - Secure Razorpay Webhook
# - Idempotent payment processing
# - ₹50 per PDF
# - Maximum 20 successful purchases per Telegram user
# - One unique coupon per successful purchase
# - Unique referral/share link for every customer
# - Referral wallet bonus
# - Wallet + withdrawal request
# - Order history
# - Coupon history
# - PDF delivery
# - Render compatible Flask health server
#
# Required Environment Variables:
# ADMIN_CHAT_ID
# DATABASE_URL
# RAZORPAY_KEY_ID
# RAZORPAY_KEY_SECRET
# RAZORPAY_WEBHOOK_SECRET
# TELEGRAM_BOT_TOKEN
# ============================================================

import os
import re
import hmac
import hashlib
import secrets
import threading
from datetime import datetime
from io import BytesIO
from urllib.parse import quote

import psycopg2
import psycopg2.extras
import razorpay
import telebot

from flask import Flask, request, jsonify
from telebot import types
from fpdf import FPDF


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()

RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.environ.get(
    "RAZORPAY_WEBHOOK_SECRET", ""
).strip()

GUIDE_PRICE = 50
MAX_PURCHASES = 20
REFERRAL_BONUS = 10

PORT = int(os.environ.get("PORT", "10000"))

if not BOT_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is missing")

if not RAZORPAY_KEY_ID:
    raise RuntimeError("RAZORPAY_KEY_ID is missing")

if not RAZORPAY_KEY_SECRET:
    raise RuntimeError("RAZORPAY_KEY_SECRET is missing")

if not RAZORPAY_WEBHOOK_SECRET:
    raise RuntimeError("RAZORPAY_WEBHOOK_SECRET is missing")


# ============================================================
# CLIENTS
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

app = Flask(__name__)

db_lock = threading.RLock()


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )
    conn.autocommit = False
    return conn


def init_db():
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                language TEXT DEFAULT 'gu',
                referred_by BIGINT,
                wallet_balance NUMERIC(12,2) DEFAULT 0,
                referral_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                razorpay_link_id TEXT UNIQUE NOT NULL,
                reference_id TEXT UNIQUE NOT NULL,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'INR',
                status TEXT DEFAULT 'created',
                processed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                razorpay_link_id TEXT UNIQUE NOT NULL,
                razorpay_payment_id TEXT UNIQUE,
                amount INTEGER NOT NULL,
                currency TEXT DEFAULT 'INR',
                status TEXT DEFAULT 'created',
                processed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                paid_at TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                id BIGSERIAL PRIMARY KEY,
                coupon_code TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                order_id TEXT UNIQUE NOT NULL,
                is_winner BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount NUMERIC(12,2) NOT NULL,
                upi_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed_at TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id BIGSERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_user_id BIGINT UNIQUE NOT NULL,
                bonus NUMERIC(12,2) DEFAULT 10,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_user_id
            ON orders(user_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_payments_user_id
            ON payments(user_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_coupons_user_id
            ON coupons(user_id)
        """)

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# ============================================================
# USER HELPERS
# ============================================================

def get_user(user_id):
    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute(
            "SELECT * FROM users WHERE user_id = %s",
            (user_id,)
        )

        return cur.fetchone()

    finally:
        conn.close()


def create_or_update_user(
    user_id,
    name=None,
    phone=None,
    language=None,
    referred_by=None
):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO users
                (user_id, name, phone, language, referred_by)
            VALUES
                (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET
                name = COALESCE(EXCLUDED.name, users.name),
                phone = COALESCE(EXCLUDED.phone, users.phone),
                language = COALESCE(EXCLUDED.language, users.language),
                referred_by = COALESCE(users.referred_by,
                                       EXCLUDED.referred_by),
                updated_at = CURRENT_TIMESTAMP
        """, (
            user_id,
            name,
            phone,
            language or "gu",
            referred_by
        ))

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_purchase_count(user_id):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE user_id = %s
              AND status = 'paid'
              AND processed = TRUE
        """, (user_id,))

        return cur.fetchone()[0]

    finally:
        conn.close()


def get_wallet(user_id):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT wallet_balance FROM users WHERE user_id = %s",
            (user_id,)
        )

        row = cur.fetchone()

        return float(row[0]) if row else 0

    finally:
        conn.close()


# ============================================================
# COUPON
# ============================================================

def generate_coupon():
    while True:
        code = (
            "EDU-"
            + secrets.token_hex(3).upper()
            + "-"
            + secrets.token_hex(2).upper()
        )

        conn = get_db()

        try:
            cur = conn.cursor()

            cur.execute(
                "SELECT 1 FROM coupons WHERE coupon_code = %s",
                (code,)
            )

            exists = cur.fetchone()

        finally:
            conn.close()

        if not exists:
            return code


# ============================================================
# ORDER ID
# ============================================================

def generate_order_id():
    return "EDU" + datetime.utcnow().strftime("%Y%m%d%H%M%S") + secrets.token_hex(3).upper()


def generate_reference_id(user_id):
    return (
        f"EDUGUIDE_{user_id}_"
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_"
        f"{secrets.token_hex(3).upper()}"
    )


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    kb.row(
        "📘 Buy Guide",
        "🎟 My Coupons"
    )

    kb.row(
        "👥 Referral",
        "📤 Share"
    )

    kb.row(
        "💰 Wallet",
        "📦 My Orders"
    )

    kb.row(
        "🎁 Lucky Draw",
        "ℹ️ Help"
    )

    return kb


# ============================================================
# START
# ============================================================

@bot.message_handler(commands=["start"])
def start_handler(message):

    user_id = message.from_user.id

    args = message.text.split(maxsplit=1)

    referred_by = None

    if len(args) > 1:
        start_param = args[1].strip()

        if start_param.startswith("ref_"):
            try:
                referred_by = int(start_param[4:])

                if referred_by == user_id:
                    referred_by = None

            except ValueError:
                referred_by = None

    existing = get_user(user_id)

    if not existing:

        create_or_update_user(
            user_id=user_id,
            name=message.from_user.first_name or "",
            referred_by=referred_by
        )

        if referred_by:
            save_referral(
                referred_by,
                user_id
            )

        language_keyboard = types.InlineKeyboardMarkup()

        language_keyboard.add(
            types.InlineKeyboardButton(
                "ગુજરાતી 🇮🇳",
                callback_data="lang_gu"
            )
        )

        language_keyboard.add(
            types.InlineKeyboardButton(
                "English 🇬🇧",
                callback_data="lang_en"
            )
        )

        bot.send_message(
            user_id,
            "🙏 <b>Welcome to EduGuide</b>\n\n"
            "કૃપા કરીને તમારી ભાષા પસંદ કરો.",
            reply_markup=language_keyboard
        )

    else:

        count = get_purchase_count(user_id)
        wallet = get_wallet(user_id)

        bot.send_message(
            user_id,
            "👋 <b>Welcome Back!</b>\n\n"
            f"📘 Purchased PDFs: <b>{count}/{MAX_PURCHASES}</b>\n"
            f"💰 Wallet: <b>₹{wallet:.2f}</b>\n\n"
            "નીચેમાંથી વિકલ્પ પસંદ કરો.",
            reply_markup=main_menu()
        )


# ============================================================
# LANGUAGE
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data in ["lang_gu", "lang_en"]
)
def language_handler(call):

    user_id = call.from_user.id

    lang = "gu" if call.data == "lang_gu" else "en"

    create_or_update_user(
        user_id=user_id,
        language=lang
    )

    bot.answer_callback_query(call.id)

    bot.send_message(
        user_id,
        "📝 <b>તમારું નામ લખો:</b>"
    )

    bot.register_next_step_handler(
        call.message,
        receive_name
    )


def receive_name(message):

    user_id = message.from_user.id

    name = message.text.strip()

    if not name:
        bot.send_message(
            user_id,
            "કૃપા કરીને સાચું નામ લખો."
        )

        bot.register_next_step_handler(
            message,
            receive_name
        )

        return

    create_or_update_user(
        user_id=user_id,
        name=name
    )

    bot.send_message(
        user_id,
        "📱 <b>તમારો મોબાઇલ નંબર શેર કરો.</b>\n\n"
        "નીચેનું <b>Share Contact</b> બટન દબાવો.",
        reply_markup=phone_keyboard()
    )


# ============================================================
# PHONE
# ============================================================

def phone_keyboard():

    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        one_time_keyboard=True
    )

    button = types.KeyboardButton(
        "📱 Share Contact",
        request_contact=True
    )

    kb.add(button)

    return kb


@bot.message_handler(content_types=["contact"])
def contact_handler(message):

    user_id = message.from_user.id

    contact = message.contact

    if contact.user_id and contact.user_id != user_id:
        bot.send_message(
            user_id,
            "❌ કૃપા કરીને તમારો પોતાનો contact શેર કરો."
        )
        return

    phone = contact.phone_number

    phone = re.sub(
        r"\D",
        "",
        phone
    )

    if phone.startswith("91") and len(phone) == 12:
        phone = phone[-10:]

    if len(phone) != 10 or phone[0] not in "6789":

        bot.send_message(
            user_id,
            "❌ મોબાઇલ નંબર યોગ્ય નથી."
        )

        return

    create_or_update_user(
        user_id=user_id,
        phone=phone
    )

    bot.send_message(
        user_id,
        "✅ <b>Registration Complete!</b>\n\n"
        "હવે તમે EduGuide નો ઉપયોગ કરી શકો છો.",
        reply_markup=main_menu()
    )


# ============================================================
# BUY GUIDE
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "📘 Buy Guide"
)
def buy_guide(message):

    user_id = message.from_user.id

    count = get_purchase_count(user_id)

    if count >= MAX_PURCHASES:

        bot.send_message(
            user_id,
            "🚫 <b>Purchase Limit Reached</b>\n\n"
            f"તમે મહત્તમ {MAX_PURCHASES} PDF ખરીદી શકો છો."
        )

        return

    user = get_user(user_id)

    if not user or not user.get("phone"):

        bot.send_message(
            user_id,
            "📱 પહેલા તમારો mobile number verify કરો."
        )

        return

    order_id = generate_order_id()
    reference_id = generate_reference_id(user_id)

    try:

        payment_link = razorpay_client.payment_link.create({
            "amount": GUIDE_PRICE * 100,
            "currency": "INR",
            "accept_partial": False,
            "description": "Career Guidance Guide - Gujarati PDF",
            "reference_id": reference_id,
            "expire_by": int(datetime.utcnow().timestamp()) + 86400,
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": False,
            "notes": {
                "order_id": order_id,
                "user_id": str(user_id)
            }
        })

        link_id = payment_link["id"]

        conn = get_db()

        try:

            cur = conn.cursor()

            cur.execute("""
                INSERT INTO orders
                    (
                        order_id,
                        user_id,
                        razorpay_link_id,
                        reference_id,
                        amount,
                        currency,
                        status
                    )
                VALUES
                    (%s, %s, %s, %s, %s, %s, 'created')
            """, (
                order_id,
                user_id,
                link_id,
                reference_id,
                GUIDE_PRICE,
                "INR"
            ))

            cur.execute("""
                INSERT INTO payments
                    (
                        order_id,
                        user_id,
                        razorpay_link_id,
                        amount,
                        currency,
                        status
                    )
                VALUES
                    (%s, %s, %s, %s, %s, 'created')
            """, (
                order_id,
                user_id,
                link_id,
                GUIDE_PRICE,
                "INR"
            ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        kb = types.InlineKeyboardMarkup()

        kb.add(
            types.InlineKeyboardButton(
                "💳 Pay ₹50",
                url=payment_link["short_url"]
            )
        )

        kb.add(
            types.InlineKeyboardButton(
                "🔄 Check Payment",
                callback_data=f"check_{order_id}"
            )
        )

        bot.send_message(
            user_id,
            "📘 <b>Career Guidance Guide</b>\n\n"
            "🎓 અભ્યાસ, સરકારી નોકરી, Career Options અને "
            "Exam Preparation માટે ઉપયોગી ગુજરાતી PDF.\n\n"
            "💳 Payment કર્યા પછી નીચેનું "
            "<b>Check Payment</b> બટન દબાવો.\n\n"
            f"🧾 Order ID: <code>{order_id}</code>",
            reply_markup=kb
        )

    except Exception as e:

        print("RAZORPAY CREATE ERROR:", repr(e))

        bot.send_message(
            user_id,
            "❌ Payment link બનાવવામાં સમસ્યા આવી છે.\n"
            "થોડીવાર પછી ફરી પ્રયાસ કરો."
        )


# ============================================================
# CHECK PAYMENT
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("check_")
)
def check_payment_handler(call):

    user_id = call.from_user.id

    order_id = call.data.replace(
        "check_",
        "",
        1
    )

    bot.answer_callback_query(
        call.id,
        "Payment check થઈ રહ્યું છે..."
    )

    try:

        conn = get_db()

        try:

            cur = conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            )

            cur.execute("""
                SELECT *
                FROM orders
                WHERE order_id = %s
                  AND user_id = %s
                FOR UPDATE
            """, (
                order_id,
                user_id
            ))

            order = cur.fetchone()

            if not order:

                conn.rollback()

                bot.send_message(
                    user_id,
                    "❌ Order મળી નથી."
                )

                return

            if order["processed"]:

                conn.rollback()

                bot.send_message(
                    user_id,
                    "✅ આ payment પહેલેથી process થઈ ગઈ છે."
                )

                return

            link = razorpay_client.payment_link.fetch(
                order["razorpay_link_id"]
            )

            if not is_valid_paid_link(
                link,
                order
            ):

                conn.rollback()

                bot.send_message(
                    user_id,
                    "⏳ Payment હજુ confirm થઈ નથી.\n\n"
                    "Payment successful થયા પછી ફરી "
                    "Check Payment દબાવો."
                )

                return

            payment_id = extract_payment_id(link)

            process_paid_order_locked(
                conn,
                order,
                payment_id
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

    except Exception as e:

        print("CHECK PAYMENT ERROR:", repr(e))

        bot.send_message(
            user_id,
            "❌ Payment verify કરવામાં સમસ્યા આવી.\n"
            "જો પૈસા કપાયા હોય તો ફરી payment ન કરશો."
        )


# ============================================================
# PAYMENT VALIDATION
# ============================================================

def is_valid_paid_link(link, order):

    status = str(
        link.get("status", "")
    ).lower()

    currency = str(
        link.get("currency", "")
    ).upper()

    amount = int(
        link.get("amount", 0) or 0
    )

    amount_paid = int(
        link.get("amount_paid", 0) or 0
    )

    link_id = link.get("id")

    reference_id = link.get(
        "reference_id"
    )

    return (
        status == "paid"
        and currency == "INR"
        and amount == GUIDE_PRICE * 100
        and amount_paid >= GUIDE_PRICE * 100
        and link_id == order["razorpay_link_id"]
        and reference_id == order["reference_id"]
    )


def extract_payment_id(link):

    payments = link.get(
        "payments"
    )

    if isinstance(payments, dict):

        items = payments.get(
            "items",
            []
        )

        if items:

            return items[0].get(
                "id"
            )

    return None


# ============================================================
# PROCESS PAID ORDER
# ============================================================

def process_paid_order_locked(
    conn,
    order,
    payment_id=None
):

    cur = conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    )

    # Lock user row
    cur.execute("""
        SELECT *
        FROM users
        WHERE user_id = %s
        FOR UPDATE
    """, (
        order["user_id"],
    ))

    user = cur.fetchone()

    if not user:
        raise RuntimeError(
            "User not found"
        )

    # Lock order again
    cur.execute("""
        SELECT *
        FROM orders
        WHERE id = %s
        FOR UPDATE
    """, (
        order["id"],
    ))

    locked_order = cur.fetchone()

    if locked_order["processed"]:
        return False

    # Double-check purchase limit
    cur.execute("""
        SELECT COUNT(*)
        FROM orders
        WHERE user_id = %s
          AND status = 'paid'
          AND processed = TRUE
    """, (
        order["user_id"],
    ))

    purchase_count = cur.fetchone()[0]

    if purchase_count >= MAX_PURCHASES:

        cur.execute("""
            UPDATE orders
            SET status = 'paid_limit_reached',
                paid_at = CURRENT_TIMESTAMP
            WHERE id = %s
        """, (
            order["id"],
        ))

        cur.execute("""
            UPDATE payments
            SET status = 'paid_limit_reached',
                paid_at = CURRENT_TIMESTAMP
            WHERE order_id = %s
        """, (
            order["order_id"],
        ))

        return False

    coupon_code = generate_coupon()

    cur.execute("""
        INSERT INTO coupons
            (
                coupon_code,
                user_id,
                order_id
            )
        VALUES
            (%s, %s, %s)
    """, (
        coupon_code,
        order["user_id"],
        order["order_id"]
    ))

    cur.execute("""
        UPDATE orders
        SET status = 'paid',
            processed = TRUE,
            paid_at = CURRENT_TIMESTAMP
        WHERE id = %s
          AND processed = FALSE
    """, (
        order["id"],
    ))

    if cur.rowcount != 1:
        raise RuntimeError(
            "Order processing race detected"
        )

    cur.execute("""
        UPDATE payments
        SET status = 'paid',
            processed = TRUE,
            razorpay_payment_id = %s,
            paid_at = CURRENT_TIMESTAMP
        WHERE order_id = %s
    """, (
        payment_id,
        order["order_id"]
    ))

    # Referral bonus - only once
    cur.execute("""
        SELECT *
        FROM referrals
        WHERE referred_user_id = %s
        FOR UPDATE
    """, (
        order["user_id"],
    ))

    referral = cur.fetchone()

    if referral and referral["status"] == "pending":

        cur.execute("""
            SELECT user_id
            FROM users
            WHERE user_id = %s
            FOR UPDATE
        """, (
            referral["referrer_id"],
        ))

        referrer = cur.fetchone()

        if referrer:

            cur.execute("""
                UPDATE users
                SET wallet_balance =
                        wallet_balance + %s,
                    referral_count =
                        referral_count + 1,
                    updated_at =
                        CURRENT_TIMESTAMP
                WHERE user_id = %s
            """, (
                REFERRAL_BONUS,
                referral["referrer_id"]
            ))

            cur.execute("""
                UPDATE referrals
                SET status = 'credited'
                WHERE id = %s
            """, (
                referral["id"],
            ))

    return coupon_code


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/razorpay/webhook",
    methods=["POST"]
)
def razorpay_webhook():

    raw_body = request.get_data()

    received_signature = request.headers.get(
        "X-Razorpay-Signature",
        ""
    )

    if not received_signature:
        return jsonify({
            "error": "missing signature"
        }), 400

    expected_signature = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode(),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(
        expected_signature,
        received_signature
    ):
        return jsonify({
            "error": "invalid signature"
        }), 400

    try:

        payload = request.get_json(
            force=True
        )

    except Exception:

        return jsonify({
            "error": "invalid json"
        }), 400

    event = payload.get(
        "event"
    )

    if event != "payment_link.paid":

        return jsonify({
            "status": "ignored"
        }), 200

    try:

        entity = (
            payload
            .get("payload", {})
            .get("payment_link", {})
            .get("entity", {})
        )

        link_id = entity.get(
            "id"
        )

        reference_id = entity.get(
            "reference_id"
        )

        amount = int(
            entity.get(
                "amount",
                0
            ) or 0
        )

        amount_paid = int(
            entity.get(
                "amount_paid",
                0
            ) or 0
        )

        currency = str(
            entity.get(
                "currency",
                ""
            )
        ).upper()

        status = str(
            entity.get(
                "status",
                ""
            )
        ).lower()

        if not link_id:
            return jsonify({
                "error": "missing link id"
            }), 400

        if status != "paid":
            return jsonify({
                "status": "not paid"
            }), 200

        if currency != "INR":
            return jsonify({
                "error": "invalid currency"
            }), 400

        if amount != GUIDE_PRICE * 100:
            return jsonify({
                "error": "invalid amount"
            }), 400

        if amount_paid < GUIDE_PRICE * 100:
            return jsonify({
                "error": "invalid amount paid"
            }), 400

        conn = get_db()

        try:

            cur = conn.cursor(
                cursor_factory=psycopg2.extras.RealDictCursor
            )

            cur.execute("""
                SELECT *
                FROM orders
                WHERE razorpay_link_id = %s
                FOR UPDATE
            """, (
                link_id,
            ))

            order = cur.fetchone()

            if not order:

                conn.rollback()

                return jsonify({
                    "error": "order not found"
                }), 404

            if reference_id != order["reference_id"]:
                conn.rollback()

                return jsonify({
                    "error": "reference mismatch"
                }), 400

            if order["processed"]:

                conn.rollback()

                return jsonify({
                    "status": "already processed"
                }), 200

            payment_id = None

            payment_entity = (
                payload
                .get("payload", {})
                .get("payment", {})
                .get("entity", {})
            )

            if payment_entity:
                payment_id = payment_entity.get(
                    "id"
                )

            process_paid_order_locked(
                conn,
                order,
                payment_id
            )

            conn.commit()

        except Exception:
            conn.rollback()
            raise

        finally:
            conn.close()

        # Delivery happens after DB commit
        try:
            send_purchase_success(
                order["user_id"],
                order["order_id"]
            )

        except Exception as delivery_error:

            print(
                "DELIVERY ERROR:",
                repr(delivery_error)
            )

        return jsonify({
            "status": "ok"
        }), 200

    except Exception as e:

        print(
            "WEBHOOK ERROR:",
            repr(e)
        )

        return jsonify({
            "error": "processing error"
        }), 500


# ============================================================
# PURCHASE SUCCESS / PDF
# ============================================================

def send_purchase_success(
    user_id,
    order_id
):

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        )

        cur.execute("""
            SELECT
                o.order_id,
                o.user_id,
                c.coupon_code
            FROM orders o
            JOIN coupons c
              ON c.order_id = o.order_id
            WHERE o.order_id = %s
              AND o.user_id = %s
        """, (
            order_id,
            user_id
        ))

        row = cur.fetchone()

    finally:
        conn.close()

    if not row:
        return

    pdf = generate_pdf()

    bot.send_document(
        user_id,
        pdf,
        caption=(
            "🎉 <b>Payment Successful!</b>\n\n"
            "📘 તમારો Career Guidance Guide તૈયાર છે.\n\n"
            f"🎟 <b>તમારો Unique Coupon:</b>\n"
            f"<code>{row['coupon_code']}</code>\n\n"
            "🍀 આ coupon Lucky Draw માટે સાચવી રાખો."
        )
    )


# ============================================================
# PDF GENERATOR
# ============================================================

def generate_pdf():

    pdf = FPDF()

    pdf.set_auto_page_break(
        auto=True,
        margin=15
    )

    pdf.add_page()

    # If a Gujarati Unicode font is available in the project,
    # use it. Otherwise use standard PDF font.
    font_candidates = [
        "Gujarati.ttf",
        "NotoSansGujarati-Regular.ttf",
        "NotoSansGujarati[wght].ttf",
        "/usr/share/fonts/truetype/noto/NotoSansGujarati-Regular.ttf"
    ]

    gujarati_font = None

    for path in font_candidates:

        if os.path.exists(path):
            gujarati_font = path
            break

    if gujarati_font:

        try:

            pdf.add_font(
                "Gujarati",
                "",
                gujarati_font
            )

            pdf.set_font(
                "Gujarati",
                size=14
            )

            pdf.multi_cell(
                0,
                10,
                "Career Guidance Guide"
            )

            pdf.ln(3)

            content = (
                "આ માર્ગદર્શિકામાં અભ્યાસ, Career Options, "
                "સરકારી નોકરી, પરીક્ષાની તૈયારી અને "
                "ભવિષ્યના કારકિર્દી વિકલ્પોની માહિતી આપવામાં "
                "આવી છે.\n\n"
                "વિદ્યાર્થીએ પોતાની રુચિ, ક્ષમતા અને "
                "લક્ષ્ય પ્રમાણે યોગ્ય ક્ષેત્ર પસંદ કરવું જોઈએ.\n\n"
                "સરકારી નોકરી માટે યોગ્ય ભરતીની સત્તાવાર "
                "જાહેરાત, લાયકાત, ઉંમર મર્યાદા અને "
                "પરીક્ષા પેટર્ન હંમેશા તપાસવી.\n\n"
                "Career planning માટે નિયમિત અભ્યાસ, "
                "સમયનું આયોજન અને સતત practice જરૂરી છે."
            )

            pdf.set_font(
                "Gujarati",
                size=12
            )

            pdf.multi_cell(
                0,
                8,
                content
            )

        except Exception:

            pdf.set_font(
                "Helvetica",
                size=14
            )

            pdf.cell(
                0,
                10,
                "Career Guidance Guide",
                ln=True
            )

            pdf.ln(5)

            pdf.multi_cell(
                0,
                8,
                "Career guidance PDF."
            )

    else:

        pdf.set_font(
            "Helvetica",
            size=16
        )

        pdf.cell(
            0,
            10,
            "Career Guidance Guide",
            ln=True,
            align="C"
        )

        pdf.ln(8)

        pdf.set_font(
            "Helvetica",
            size=12
        )

        text = (
            "Career Guidance Guide\n\n"
            "Study planning\n"
            "Government jobs\n"
            "Career options\n"
            "Exam preparation\n"
            "Diploma and ITI options\n"
            "Skill development\n\n"
            "Note: For Gujarati Unicode PDF rendering, "
            "place a Gujarati Unicode TTF font such as "
            "NotoSansGujarati-Regular.ttf in the project."
        )

        pdf.multi_cell(
            0,
            8,
            text
        )

    output = pdf.output(
        dest="S"
    )

    if isinstance(output, str):
        output = output.encode(
            "latin-1"
        )

    bio = BytesIO(output)

    bio.name = "Career_Guidance_Guide.pdf"

    bio.seek(0)

    return bio


# ============================================================
# REFERRAL
# ============================================================

def save_referral(
    referrer_id,
    referred_user_id
):

    if referrer_id == referred_user_id:
        return

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            INSERT INTO referrals
                (
                    referrer_id,
                    referred_user_id,
                    bonus,
                    status
                )
            VALUES
                (%s, %s, %s, 'pending')
            ON CONFLICT (referred_user_id)
            DO NOTHING
        """, (
            referrer_id,
            referred_user_id,
            REFERRAL_BONUS
        ))

        conn.commit()

    except Exception:

        conn.rollback()

    finally:

        conn.close()


def get_bot_username():

    try:
        return bot.get_me().username

    except Exception:
        return None


def get_share_link(user_id):

    username = get_bot_username()

    if not username:
        return None

    return (
        f"https://t.me/{username}"
        f"?start=ref_{user_id}"
    )


# ============================================================
# REFERRAL BUTTON
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "👥 Referral"
)
def referral_handler(message):

    user_id = message.from_user.id

    link = get_share_link(
        user_id
    )

    if not link:

        bot.send_message(
            user_id,
            "❌ Referral link બનાવી શકાયી નથી."
        )

        return

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                referral_count,
                wallet_balance
            FROM users
            WHERE user_id = %s
        """, (
            user_id,
        ))

        row = cur.fetchone()

    finally:
        conn.close()

    count = row[0] if row else 0
    wallet = float(row[1]) if row else 0

    bot.send_message(
        user_id,
        "👥 <b>Your Referral</b>\n\n"
        f"👤 Successful referrals: <b>{count}</b>\n"
        f"💰 Wallet: <b>₹{wallet:.2f}</b>\n\n"
        f"🔗 <b>Your Unique Share Link:</b>\n"
        f"<code>{link}</code>\n\n"
        "આ link તમારા મિત્રો સાથે share કરો."
    )


# ============================================================
# SHARE BUTTON
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "📤 Share"
)
def share_handler(message):

    user_id = message.from_user.id

    link = get_share_link(
        user_id
    )

    if not link:

        bot.send_message(
            user_id,
            "❌ Share link બનાવી શકાયી નથી."
        )

        return

    share_text = (
        "📘 Career Guidance Guide – ગુજરાતી PDF\n"
        "🎓 અભ્યાસ, સરકારી નોકરી, Career Options અને "
        "Exam Preparation માટે ઉપયોગી માર્ગદર્શિકા.\n\n"
        "📥 વધુ માહિતી માટે અહીં જોડાઓ:"
    )

    share_url = (
        "https://t.me/share/url"
        "?url=" + quote(link, safe="")
        "&text=" + quote(share_text, safe="")
    )

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "📤 Share Now",
            url=share_url
        )
    )

    bot.send_message(
        user_id,
        "📤 <b>Share Career Guide</b>\n\n"
        "નીચેનું Share Now બટન દબાવીને "
        "તમારી Unique Share Link share કરો.\n\n"
        "શેર મેસેજમાં PDFની કિંમત બતાવવામાં આવશે નહીં.",
        reply_markup=kb
    )


# ============================================================
# WALLET
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "💰 Wallet"
)
def wallet_handler(message):

    user_id = message.from_user.id

    wallet = get_wallet(
        user_id
    )

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*)
            FROM referrals
            WHERE referrer_id = %s
              AND status = 'credited'
        """, (
            user_id,
        ))

        referral_count = cur.fetchone()[0]

    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup()

    kb.add(
        types.InlineKeyboardButton(
            "🏦 Withdraw",
            callback_data="withdraw"
        )
    )

    bot.send_message(
        user_id,
        "💰 <b>Your Wallet</b>\n\n"
        f"💵 Balance: <b>₹{wallet:.2f}</b>\n"
        f"👥 Successful Referrals: <b>{referral_count}</b>\n\n"
        f"Minimum withdrawal: <b>₹{REFERRAL_BONUS}</b>",
        reply_markup=kb
    )


# ============================================================
# WITHDRAWAL
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data == "withdraw"
)
def withdraw_handler(call):

    user_id = call.from_user.id

    bot.answer_callback_query(
        call.id
    )

    wallet = get_wallet(
        user_id
    )

    if wallet < REFERRAL_BONUS:

        bot.send_message(
            user_id,
            f"❌ Withdrawal માટે ઓછામાં ઓછા "
            f"₹{REFERRAL_BONUS} હોવા જોઈએ."
        )

        return

    bot.send_message(
        user_id,
        "🏦 <b>તમારી UPI ID મોકલો.</b>\n\n"
        "ઉદાહરણ:\n"
        "<code>name@upi</code>"
    )

    bot.register_next_step_handler(
        call.message,
        receive_withdrawal_upi
    )


def receive_withdrawal_upi(message):

    user_id = message.from_user.id

    upi = message.text.strip()

    if not re.match(
        r"^[A-Za-z0-9._-]{2,}@[A-Za-z]{2,}$",
        upi
    ):

        bot.send_message(
            user_id,
            "❌ UPI ID યોગ્ય નથી.\n"
            "ફરીથી UPI ID મોકલો."
        )

        bot.register_next_step_handler(
            message,
            receive_withdrawal_upi
        )

        return

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT wallet_balance
            FROM users
            WHERE user_id = %s
            FOR UPDATE
        """, (
            user_id,
        ))

        row = cur.fetchone()

        if not row:
            conn.rollback()

            bot.send_message(
                user_id,
                "❌ User account મળ્યું નથી."
            )

            return

        balance = float(
            row[0]
        )

        if balance < REFERRAL_BONUS:

            conn.rollback()

            bot.send_message(
                user_id,
                "❌ Wallet balance પૂરતું નથી."
            )

            return

        amount = balance

        cur.execute("""
            INSERT INTO withdrawals
                (
                    user_id,
                    amount,
                    upi_id,
                    status
                )
            VALUES
                (%s, %s, %s, 'pending')
        """, (
            user_id,
            amount,
            upi
        ))

        cur.execute("""
            UPDATE users
            SET wallet_balance = 0,
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = %s
        """, (
            user_id,
        ))

        withdrawal_id = cur.fetchone() if False else None

        conn.commit()

    except Exception as e:

        conn.rollback()

        print(
            "WITHDRAW ERROR:",
            repr(e)
        )

        bot.send_message(
            user_id,
            "❌ Withdrawal requestમાં સમસ્યા આવી."
        )

        return

    finally:
        conn.close()

    bot.send_message(
        user_id,
        "✅ <b>Withdrawal Request Submitted</b>\n\n"
        f"💰 Amount: <b>₹{amount:.2f}</b>\n"
        f"🏦 UPI: <code>{upi}</code>\n\n"
        "Admin verification પછી payment process થશે."
    )

    if ADMIN_CHAT_ID:

        try:

            bot.send_message(
                int(ADMIN_CHAT_ID),
                "💰 <b>NEW WITHDRAWAL</b>\n\n"
                f"👤 User ID: <code>{user_id}</code>\n"
                f"💵 Amount: <b>₹{amount:.2f}</b>\n"
                f"🏦 UPI: <code>{upi}</code>"
            )

        except Exception as e:

            print(
                "ADMIN MESSAGE ERROR:",
                repr(e)
            )


# ============================================================
# COUPONS
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "🎟 My Coupons"
)
def coupons_handler(message):

    user_id = message.from_user.id

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                coupon_code,
                order_id,
                created_at,
                is_winner
            FROM coupons
            WHERE user_id = %s
            ORDER BY id DESC
        """, (
            user_id,
        ))

        rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:

        bot.send_message(
            user_id,
            "🎟 હજુ કોઈ coupon નથી."
        )

        return

    text = "🎟 <b>Your Coupons</b>\n\n"

    for index, row in enumerate(rows, 1):

        coupon = row[0]
        order_id = row[1]
        winner = row[3]

        text += (
            f"{index}. <code>{coupon}</code>\n"
            f"🧾 Order: <code>{order_id}</code>\n"
        )

        if winner:
            text += "🏆 <b>WINNER</b>\n"

        text += "\n"

    bot.send_message(
        user_id,
        text
    )


# ============================================================
# ORDERS
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "📦 My Orders"
)
def orders_handler(message):

    user_id = message.from_user.id

    conn = get_db()

    try:

        cur = conn.cursor()

        cur.execute("""
            SELECT
                order_id,
                amount,
                status,
                created_at
            FROM orders
            WHERE user_id = %s
            ORDER BY id DESC
            LIMIT 20
        """, (
            user_id,
        ))

        rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:

        bot.send_message(
            user_id,
            "📦 હજુ કોઈ order નથી."
        )

        return

    text = "📦 <b>My Orders</b>\n\n"

    for row in rows:

        text += (
            f"🧾 <code>{row[0]}</code>\n"
            f"💵 ₹{row[1]}\n"
            f"📌 {row[2]}\n"
            f"📅 {row[3]}\n\n"
        )

    bot.send_message(
        user_id,
        text
    )


# ============================================================
# LUCKY DRAW
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "🎁 Lucky Draw"
)
def lucky_draw_handler(message):

    user_id = message.from_user.id

    bot.send_message(
        user_id,
        "🎁 <b>Lucky Draw</b>\n\n"
        "તમારા દરેક successful PDF purchase સાથે "
        "તમને અલગ Unique Coupon મળે છે.\n\n"
        "🎟 તમારા coupons જોવા માટે "
        "<b>My Coupons</b> દબાવો."
    )


# ============================================================
# HELP
# ============================================================

@bot.message_handler(
    func=lambda message: message.text == "ℹ️ Help"
)
def help_handler(message):

    bot.send_message(
        message.from_user.id,
        "ℹ️ <b>EduGuide Help</b>\n\n"
        "📘 Buy Guide → PDF ખરીદવા\n"
        "🎟 My Coupons → તમારા coupons\n"
        "👥 Referral → referral information\n"
        "📤 Share → તમારી unique share link\n"
        "💰 Wallet → wallet અને withdrawal\n"
        "📦 My Orders → order history\n"
        "🎁 Lucky Draw → draw information\n\n"
        "Payment થયા પછી PDF automatically મળે છે."
    )


# ============================================================
# FALLBACK TEXT
# ============================================================

@bot.message_handler(
    func=lambda message: True,
    content_types=["text"]
)
def fallback_handler(message):

    bot.send_message(
        message.from_user.id,
        "કૃપા કરીને નીચેના menuમાંથી વિકલ્પ પસંદ કરો.",
        reply_markup=main_menu()
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "service": "EduGuide Telegram Bot"
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# ERROR HANDLER
# ============================================================

@bot.message_handler(
    commands=["help"]
)
def command_help(message):

    help_handler(message)


# ============================================================
# START FLASK
# ============================================================

def run_flask():

    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )


# ============================================================
# BOT START
# ============================================================

def start_bot():

    print("Starting EduGuide Telegram Bot...")

    while True:

        try:

            bot.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )

        except Exception as e:

            print(
                "BOT POLLING ERROR:",
                repr(e)
            )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print("Initializing database...")

    init_db()

    print("Database ready.")

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )

    flask_thread.start()

    print(
        f"Health server started on port {PORT}"
    )

    start_bot()
