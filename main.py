import os
import re
import hmac
import hashlib
import secrets
import threading
import time
import logging
from decimal import Decimal, InvalidOperation
from io import BytesIO
from urllib.parse import quote
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

import razorpay
import telebot
from telebot import types

from flask import Flask, request, jsonify

from fpdf import FPDF
from fpdf.enums import XPos, YPos


# ============================================================
# EDU GUIDE - PRODUCTION MAIN.PY
# ============================================================

APP_NAME = "EduGuide"

GUIDE_PRICE = 50
MAX_PURCHASES = 20
REFERRAL_BONUS = Decimal("10.00")

PORT = int(os.getenv("PORT", "10000"))

ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(APP_NAME)


# ============================================================
# ENVIRONMENT VALIDATION
# ============================================================

required_env = {
    "ADMIN_CHAT_ID": ADMIN_CHAT_ID,
    "DATABASE_URL": DATABASE_URL,
    "RAZORPAY_KEY_ID": RAZORPAY_KEY_ID,
    "RAZORPAY_KEY_SECRET": RAZORPAY_KEY_SECRET,
    "RAZORPAY_WEBHOOK_SECRET": RAZORPAY_WEBHOOK_SECRET,
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
}

missing_env = [key for key, value in required_env.items() if not value]

if missing_env:
    raise RuntimeError(
        "Missing environment variables: " + ", ".join(missing_env)
    )

try:
    ADMIN_CHAT_ID_INT = int(ADMIN_CHAT_ID)
except ValueError:
    raise RuntimeError("ADMIN_CHAT_ID must be a numeric Telegram chat ID")


# ============================================================
# CLIENTS
# ============================================================

bot = telebot.TeleBot(
    TELEGRAM_BOT_TOKEN,
    parse_mode="HTML",
    threaded=True
)

razorpay_client = razorpay.Client(
    auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
)

app = Flask(__name__)


# ============================================================
# FIXED PRIZE LIST
# ============================================================

PRIZES = [
    {
        "from_rank": 1,
        "to_rank": 1,
        "amount": Decimal("500000"),
        "title": "🥇 પ્રથમ ઇનામ",
    },
    {
        "from_rank": 2,
        "to_rank": 2,
        "amount": Decimal("300000"),
        "title": "🥈 બીજું ઇનામ",
    },
    {
        "from_rank": 3,
        "to_rank": 3,
        "amount": Decimal("100000"),
        "title": "🥉 ત્રીજું ઇનામ",
    },
    {
        "from_rank": 4,
        "to_rank": 4,
        "amount": Decimal("50000"),
        "title": "🎁 ચોથું ઇનામ",
    },
    {
        "from_rank": 5,
        "to_rank": 5,
        "amount": Decimal("10000"),
        "title": "🎁 પાંચમું ઇનામ",
    },
    {
        "from_rank": 6,
        "to_rank": 10,
        "amount": Decimal("5000"),
        "title": "🎁 6 થી 10",
    },
    {
        "from_rank": 11,
        "to_rank": 50,
        "amount": Decimal("1500"),
        "title": "🎁 11 થી 50",
    },
    {
        "from_rank": 51,
        "to_rank": 100,
        "amount": Decimal("1000"),
        "title": "🎁 51 થી 100",
    },
    {
        "from_rank": 101,
        "to_rank": 500,
        "amount": Decimal("100"),
        "title": "🎁 101 થી 500",
    },
]


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = psycopg2.connect(
        DATABASE_URL,
        cursor_factory=RealDictCursor,
        connect_timeout=15
    )
    conn.autocommit = False
    return conn


def init_db():
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                name TEXT,
                phone TEXT,
                language TEXT DEFAULT 'gu',
                referred_by BIGINT,
                wallet_balance NUMERIC(14,2) NOT NULL DEFAULT 0,
                referral_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                currency TEXT NOT NULL DEFAULT 'INR',
                status TEXT NOT NULL DEFAULT 'created',
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                currency TEXT NOT NULL DEFAULT 'INR',
                status TEXT NOT NULL DEFAULT 'created',
                processed BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS coupons (
                id BIGSERIAL PRIMARY KEY,
                coupon_code TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                order_id TEXT UNIQUE NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                id BIGSERIAL PRIMARY KEY,
                referrer_id BIGINT NOT NULL,
                referred_user_id BIGINT UNIQUE NOT NULL,
                bonus NUMERIC(14,2) NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                credited_at TIMESTAMPTZ
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS withdrawals (
                id BIGSERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                amount NUMERIC(14,2) NOT NULL,
                upi_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                transaction_ref TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS prizes (
                id SERIAL PRIMARY KEY,
                from_rank INTEGER NOT NULL,
                to_rank INTEGER NOT NULL,
                prize_amount NUMERIC(14,2) NOT NULL,
                title TEXT NOT NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS delivery_queue (
                id BIGSERIAL PRIMARY KEY,
                order_id TEXT UNIQUE NOT NULL,
                user_id BIGINT NOT NULL,
                coupon_code TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_withdrawals_user_id
            ON withdrawals(user_id)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_delivery_status
            ON delivery_queue(status)
        """)

        cur.execute("SELECT COUNT(*) AS count FROM prizes")
        count = cur.fetchone()["count"]

        if count == 0:
            for prize in PRIZES:
                cur.execute("""
                    INSERT INTO prizes
                    (from_rank, to_rank, prize_amount, title)
                    VALUES (%s, %s, %s, %s)
                """, (
                    prize["from_rank"],
                    prize["to_rank"],
                    prize["amount"],
                    prize["title"]
                ))

        conn.commit()
        logger.info("Database initialized successfully")

    except Exception:
        conn.rollback()
        logger.exception("Database initialization failed")
        raise

    finally:
        conn.close()


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def money(value):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def format_rupees(value):
    value = money(value)
    return f"₹{value:,.2f}" if value % 1 else f"₹{int(value):,}"


def is_admin(user_id):
    return int(user_id) == ADMIN_CHAT_ID_INT


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def generate_random_code(length=12):
    return secrets.token_hex(16).upper()[:length]


def generate_order_id():
    return "EDU-" + secrets.token_hex(8).upper()


def generate_reference_id():
    return "EDUREF-" + secrets.token_hex(8).upper()


def generate_coupon_code(conn):
    cur = conn.cursor()

    for _ in range(20):
        code = "EDU-" + secrets.token_hex(6).upper()

        cur.execute(
            "SELECT 1 FROM coupons WHERE coupon_code=%s",
            (code,)
        )

        if not cur.fetchone():
            return code

    raise RuntimeError("Unable to generate unique coupon")


# ============================================================
# USER FUNCTIONS
# ============================================================

def get_user(user_id):
    conn = db_connect()

    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM users WHERE user_id=%s",
            (int(user_id),)
        )
        return cur.fetchone()

    finally:
        conn.close()


def create_or_update_user(
    user_id,
    name=None,
    phone=None,
    referred_by=None
):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute(
            "SELECT user_id, referred_by FROM users WHERE user_id=%s",
            (int(user_id),)
        )

        existing = cur.fetchone()

        if existing:
            updates = []
            values = []

            if name:
                updates.append("name=%s")
                values.append(name)

            if phone:
                updates.append("phone=%s")
                values.append(phone)

            if updates:
                updates.append("updated_at=NOW()")
                values.append(int(user_id))

                cur.execute(
                    f"""
                    UPDATE users
                    SET {", ".join(updates)}
                    WHERE user_id=%s
                    """,
                    values
                )

            conn.commit()
            return

        valid_referrer = None

        if referred_by:
            referred_by = int(referred_by)

            if referred_by != int(user_id):
                cur.execute(
                    "SELECT user_id FROM users WHERE user_id=%s",
                    (referred_by,)
                )

                if cur.fetchone():
                    valid_referrer = referred_by

        cur.execute("""
            INSERT INTO users
            (user_id, name, phone, referred_by)
            VALUES (%s, %s, %s, %s)
        """, (
            int(user_id),
            name,
            phone,
            valid_referrer
        ))

        if valid_referrer:
            cur.execute("""
                INSERT INTO referrals
                (referrer_id, referred_user_id, bonus, status)
                VALUES (%s, %s, %s, 'pending')
                ON CONFLICT (referred_user_id) DO NOTHING
            """, (
                valid_referrer,
                int(user_id),
                REFERRAL_BONUS
            ))

        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception("create_or_update_user failed")
        raise

    finally:
        conn.close()


def update_user_phone(user_id, phone):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET phone=%s, updated_at=NOW()
            WHERE user_id=%s
        """, (phone, int(user_id)))

        conn.commit()

    finally:
        conn.close()


def get_purchase_count(user_id, conn=None):
    own_conn = False

    if conn is None:
        conn = db_connect()
        own_conn = True

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE user_id=%s
              AND status='paid'
              AND processed=TRUE
        """, (int(user_id),))

        return int(cur.fetchone()["count"])

    finally:
        if own_conn:
            conn.close()


# ============================================================
# REFERRAL
# ============================================================

def credit_referral_if_needed(conn, referred_user_id):
    cur = conn.cursor()

    cur.execute("""
        SELECT id, referrer_id, bonus, status
        FROM referrals
        WHERE referred_user_id=%s
        FOR UPDATE
    """, (int(referred_user_id),))

    referral = cur.fetchone()

    if not referral:
        return False

    if referral["status"] == "credited":
        return False

    cur.execute("""
        SELECT user_id
        FROM users
        WHERE user_id=%s
        FOR UPDATE
    """, (int(referral["referrer_id"]),))

    referrer = cur.fetchone()

    if not referrer:
        return False

    cur.execute("""
        UPDATE users
        SET wallet_balance = wallet_balance + %s,
            referral_count = referral_count + 1,
            updated_at=NOW()
        WHERE user_id=%s
    """, (
        referral["bonus"],
        referral["referrer_id"]
    ))

    cur.execute("""
        UPDATE referrals
        SET status='credited',
            credited_at=NOW()
        WHERE id=%s
    """, (referral["id"],))

    return True


# ============================================================
# COUPON
# ============================================================

def get_coupon_for_order(conn, order_id):
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM coupons
        WHERE order_id=%s
        LIMIT 1
    """, (order_id,))

    return cur.fetchone()


def create_coupon_for_order(conn, user_id, order_id):
    existing = get_coupon_for_order(conn, order_id)

    if existing:
        return existing["coupon_code"]

    code = generate_coupon_code(conn)

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO coupons
        (coupon_code, user_id, order_id)
        VALUES (%s, %s, %s)
        RETURNING coupon_code
    """, (
        code,
        int(user_id),
        order_id
    ))

    return cur.fetchone()["coupon_code"]


# ============================================================
# PRIZE LIST
# ============================================================

def get_prize_list():
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT from_rank, to_rank, prize_amount, title
            FROM prizes
            WHERE is_active=TRUE
            ORDER BY from_rank ASC
        """)

        return cur.fetchall()

    finally:
        conn.close()


def prize_list_text():
    prizes = get_prize_list()

    lines = [
        "<b>🏆 EduGuide Prize List</b>",
        "",
    ]

    for prize in prizes:
        start = prize["from_rank"]
        end = prize["to_rank"]

        if start == end:
            rank = str(start)
        else:
            rank = f"{start} થી {end}"

        amount = format_rupees(prize["prize_amount"])

        lines.append(
            f"🎁 <b>{rank}</b> — {amount}"
        )

    lines.extend([
        "",
        "📌 કુલ ઇનામો: <b>500</b>",
    ])

    return "\n".join(lines)


# ============================================================
# ORDER CREATION
# ============================================================

def create_payment_link_for_user(user_id):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM users
            WHERE user_id=%s
            FOR UPDATE
        """, (int(user_id),))

        user = cur.fetchone()

        if not user:
            raise ValueError("User not found")

        count = get_purchase_count(user_id, conn)

        if count >= MAX_PURCHASES:
            conn.rollback()
            return {
                "success": False,
                "reason": "limit"
            }

        order_id = generate_order_id()
        reference_id = generate_reference_id()

        payment_link_data = {
            "amount": GUIDE_PRICE * 100,
            "currency": "INR",
            "accept_partial": False,
            "description": "EduGuide Career Guidance Guide",
            "reference_id": reference_id,
            "customer": {
                "name": user["name"] or "EduGuide Customer",
                "contact": user["phone"] or "",
            },
            "notify": {
                "sms": False,
                "email": False
            },
            "reminder_enable": False,
            "notes": {
                "order_id": order_id,
                "user_id": str(user_id)
            }
        }

        payment_link = razorpay_client.payment_link.create(
            payment_link_data
        )

        link_id = payment_link.get("id")
        short_url = payment_link.get("short_url")

        if not link_id or not short_url:
            raise RuntimeError("Razorpay Payment Link creation failed")

        cur.execute("""
            INSERT INTO orders
            (
                order_id,
                user_id,
                razorpay_link_id,
                reference_id,
                amount,
                currency,
                status,
                processed
            )
            VALUES (%s, %s, %s, %s, %s, 'INR', 'created', FALSE)
        """, (
            order_id,
            int(user_id),
            link_id,
            reference_id,
            GUIDE_PRICE * 100
        ))

        cur.execute("""
            INSERT INTO payments
            (
                order_id,
                user_id,
                razorpay_link_id,
                amount,
                currency,
                status,
                processed
            )
            VALUES (%s, %s, %s, %s, 'INR', 'created', FALSE)
        """, (
            order_id,
            int(user_id),
            link_id,
            GUIDE_PRICE * 100
        ))

        conn.commit()

        return {
            "success": True,
            "order_id": order_id,
            "link_id": link_id,
            "short_url": short_url
        }

    except Exception:
        conn.rollback()
        logger.exception("Payment link creation failed")
        raise

    finally:
        conn.close()


# ============================================================
# RAZORPAY VALIDATION
# ============================================================

def verify_webhook_signature(raw_body, signature):
    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(expected, signature or "")


def fetch_payment_link(link_id):
    return razorpay_client.payment_link.fetch(link_id)


def extract_payment_id(payment_link):
    payments = payment_link.get("payments")

    if not payments:
        return None

    if isinstance(payments, dict):
        items = payments.get("items", [])
    elif isinstance(payments, list):
        items = payments
    else:
        items = []

    for item in items:
        if item.get("status") == "captured":
            return item.get("id")

    for item in items:
        if item.get("id"):
            return item.get("id")

    return None


def validate_paid_payment_link(
    payment_link,
    expected_link_id,
    expected_reference_id
):
    if not payment_link:
        return False, None

    if payment_link.get("id") != expected_link_id:
        return False, None

    if payment_link.get("reference_id") != expected_reference_id:
        return False, None

    if payment_link.get("currency") != "INR":
        return False, None

    expected_amount = GUIDE_PRICE * 100

    amount = safe_int(payment_link.get("amount"), 0)
    amount_paid = safe_int(payment_link.get("amount_paid"), 0)

    if amount != expected_amount:
        return False, None

    if amount_paid < expected_amount:
        return False, None

    if payment_link.get("status") != "paid":
        return False, None

    payment_id = extract_payment_id(payment_link)

    return True, payment_id


# ============================================================
# PAYMENT PROCESSING
# ============================================================

def process_paid_order(
    order_id,
    payment_link=None,
    payment_id=None
):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders
            WHERE order_id=%s
            FOR UPDATE
        """, (order_id,))

        order = cur.fetchone()

        if not order:
            conn.rollback()
            return {
                "success": False,
                "reason": "order_not_found"
            }

        if order["processed"] and order["status"] == "paid":
            coupon = get_coupon_for_order(conn, order_id)

            conn.commit()

            return {
                "success": True,
                "already_processed": True,
                "coupon_code": coupon["coupon_code"] if coupon else None,
                "user_id": order["user_id"],
                "order_id": order_id
            }

        cur.execute("""
            SELECT *
            FROM payments
            WHERE order_id=%s
            FOR UPDATE
        """, (order_id,))

        payment = cur.fetchone()

        if not payment:
            conn.rollback()
            return {
                "success": False,
                "reason": "payment_record_missing"
            }

        if payment_link is not None:
            valid, extracted_id = validate_paid_payment_link(
                payment_link,
                order["razorpay_link_id"],
                order["reference_id"]
            )

            if not valid:
                conn.rollback()

                return {
                    "success": False,
                    "reason": "payment_validation_failed"
                }

            if payment_id is None:
                payment_id = extracted_id

        cur.execute("""
            SELECT *
            FROM users
            WHERE user_id=%s
            FOR UPDATE
        """, (order["user_id"],))

        user = cur.fetchone()

        if not user:
            conn.rollback()

            return {
                "success": False,
                "reason": "user_not_found"
            }

        purchase_count = get_purchase_count(
            order["user_id"],
            conn
        )

        if purchase_count >= MAX_PURCHASES:
            cur.execute("""
                UPDATE orders
                SET status='paid_limit_reached',
                    updated_at=NOW()
                WHERE order_id=%s
            """, (order_id,))

            cur.execute("""
                UPDATE payments
                SET status='paid_limit_reached',
                    razorpay_payment_id=COALESCE(
                        %s,
                        razorpay_payment_id
                    ),
                    updated_at=NOW()
                WHERE order_id=%s
            """, (
                payment_id,
                order_id
            ))

            conn.commit()

            return {
                "success": False,
                "reason": "limit_after_payment",
                "user_id": order["user_id"],
                "order_id": order_id,
                "requires_refund": True
            }

        coupon_code = create_coupon_for_order(
            conn,
            order["user_id"],
            order_id
        )

        cur.execute("""
            UPDATE orders
            SET status='paid',
                processed=TRUE,
                updated_at=NOW()
            WHERE order_id=%s
        """, (order_id,))

        cur.execute("""
            UPDATE payments
            SET status='captured',
                processed=TRUE,
                razorpay_payment_id=COALESCE(
                    %s,
                    razorpay_payment_id
                ),
                updated_at=NOW()
            WHERE order_id=%s
        """, (
            payment_id,
            order_id
        ))

        cur.execute("""
            INSERT INTO delivery_queue
            (
                order_id,
                user_id,
                coupon_code,
                status
            )
            VALUES (%s, %s, %s, 'pending')
            ON CONFLICT (order_id)
            DO UPDATE SET
                coupon_code=EXCLUDED.coupon_code,
                updated_at=NOW()
        """, (
            order_id,
            order["user_id"],
            coupon_code
        ))

        credit_referral_if_needed(
            conn,
            order["user_id"]
        )

        conn.commit()

        return {
            "success": True,
            "already_processed": False,
            "coupon_code": coupon_code,
            "user_id": order["user_id"],
            "order_id": order_id
        }

    except Exception:
        conn.rollback()
        logger.exception("process_paid_order failed")
        raise

    finally:
        conn.close()


# ============================================================
# PROFESSIONAL PDF (MERGED FROM SECOND CODE)
# ============================================================

class ProfessionalPDF(FPDF):
    def __init__(self):
        super().__init__(orientation='P', unit='mm', format='A4')
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=15)
        
        self.set_text_shaping(True)
            
        self.NAVY = (30, 58, 138)
        self.GOLD = (252, 211, 77)
        self.WHITE = (255, 255, 255)
        self.GRAY = (71, 85, 105)
        self.BLACK = (30, 41, 59)
        self.LIGHT_BG = (244, 246, 250)
        
        self.CONTENT_W = 180
        self.MARGIN = 15

    def rounded_box(self, x, y, w, h, fill_color, draw_color, radius):
        self.set_fill_color(*fill_color)
        self.set_draw_color(*draw_color)
        self.rect(x, y, w, h, style='DF', round_corners=True, corner_radius=radius)

    def footer(self):
        self.set_y(-15)
        self.set_font("Gujarati", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"પાનું {self.page_no()}", align="C")

    def info_card(self, title, text):
        self.ln(3)
        self.set_fill_color(*self.LIGHT_BG)
        self.set_draw_color(200, 210, 230)
        self.set_line_width(0.2)
        
        self.rect(self.get_x(), self.get_y(), self.CONTENT_W, 22, style='DF', round_corners=True, corner_radius=2)
        
        self.set_xy(self.get_x() + 4, self.get_y() + 3)
        self.set_font("Gujarati", "B", 10.5)
        self.set_text_color(*self.NAVY)
        self.cell(0, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        self.set_x(self.get_x() + 4)
        self.set_font("Gujarati", "", 9.5)
        self.set_text_color(*self.GRAY)
        self.multi_cell(170, 5, text)
        self.ln(6)

    def section_title(self, num, title):
        self.ln(4)
        self.set_font("Gujarati", "B", 12)
        self.set_text_color(*self.NAVY)
        self.cell(0, 8, f"{num}. {title}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        
        self.set_line_width(0.3)
        self.set_draw_color(*self.NAVY)
        self.line(self.get_x(), self.get_y(), self.get_x() + self.CONTENT_W, self.get_y())
        self.ln(3)

    def bullet(self, label, text):
        full_text = f"{label}{text}" if label else text
        
        start_x = self.get_x() + 2
        start_y = self.get_y()
        
        self.set_xy(start_x, start_y)
        self.set_font("Gujarati", "", 10)
        self.set_text_color(*self.NAVY)
        self.cell(4, 5, "•")
        
        self.set_xy(start_x + 4, start_y)
        self.set_font("Gujarati", "", 9.5)
        self.set_text_color(*self.GRAY)
        self.multi_cell(self.CONTENT_W - 6, 5, full_text)
        self.ln(1.5)

    def stream_card(self, title, items, height=45):
        self.ln(3)
        start_y = self.get_y()
        
        if start_y + height > 275:
            self.add_page()
            start_y = self.get_y()

        self.set_fill_color(255, 255, 255)
        self.set_draw_color(*self.NAVY)
        self.set_line_width(0.3)
        self.rect(self.get_x(), start_y, self.CONTENT_W, height, style='D', round_corners=True, corner_radius=2)
        
        self.set_xy(self.get_x() + 4, start_y + 3)
        self.set_font("Gujarati", "B", 11)
        self.set_text_color(*self.NAVY)
        self.cell(0, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)
        
        for label, text in items:
            full_text = f"{label}{text}" if label else text
            bx = self.MARGIN + 4
            by = self.get_y()
            
            self.set_xy(bx, by)
            self.set_font("Gujarati", "", 10)
            self.set_text_color(*self.NAVY)
            self.cell(4, 5, "•")
            
            self.set_xy(bx + 4, by)
            self.set_font("Gujarati", "", 9.5)
            self.set_text_color(*self.GRAY)
            self.multi_cell(self.CONTENT_W - 12, 5, full_text)
            self.ln(1)
            
        self.set_y(start_y + height + 2)


def generate_career_pdf(lang='gu'):
    pdf = ProfessionalPDF()
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(BASE_DIR, 'Gujarati.ttf')
    
    try:
        pdf.add_font("Gujarati", "", font_path)
        pdf.add_font("Gujarati", "B", font_path)
    except Exception as e:
        print("Font Error:", e)
        return None

    pdf.add_page()
    
    x = pdf.MARGIN
    y = pdf.get_y()
    w = pdf.CONTENT_W
    h = 35

    pdf.rounded_box(x, y, w, h, pdf.NAVY, pdf.NAVY, 3)

    pdf.set_xy(x + 5, y + 6)
    pdf.set_font("Gujarati", "B", 15.0)
    pdf.set_text_color(*pdf.WHITE)
    pdf.multi_cell(w - 10, 7, "સંપૂર્ણ કારકિર્દી માર્ગદર્શિકા અને સરકારી નોકરી રોડમેપ", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Gujarati", "", 9)
    pdf.set_text_color(225, 232, 249)
    pdf.multi_cell(w - 10, 5.3, "ધોરણ ૧૦ અને ૧૨ પછી શ્રેષ્ઠ પ્રવાહ પસંદગી, ઉચ્ચ અભ્યાસ અને સ્પર્ધાત્મક પરીક્ષાઓની A to Z માર્ગદર્શિકા", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pill_y = y + 25.5
    labels = ["વિશેષ ડિજિટલ એડિશન", "ગુજરાત & કેન્દ્ર સરકાર ભરતી વિશેષ"]
    pill_widths = [33, 47]
    total = sum(pill_widths) + 3
    px = x + (w - total) / 2

    for label, pw in zip(labels, pill_widths):
        pdf.rounded_box(px, pill_y, pw, 6.5, pdf.GOLD, pdf.GOLD, 3)
        pdf.set_xy(px, pill_y + 1.0)
        pdf.set_font("Gujarati", "B", 6.6)
        pdf.set_text_color(*pdf.NAVY)
        pdf.cell(pw, 4.5, label, align="C")
        px += pw + 3

    pdf.set_y(y + h + 6)

    pdf.info_card(
        "આ માર્ગદર્શિકા કોના માટે છે?",
        "ધોરણ ૧૦ કે ૧૨ પાસ કરેલ વિદ્યાર્થીઓ, વાલીઓ અને સરકારી નોકરીની તૈયારી કરતા ઉમેદવારો માટે એક સંપૂર્ણ સંકલન છે, જે ભવિષ્યના યોગ્ય નિર્ણયો લેવામાં મદદરૂપ બનશે."
    )

    pdf.section_title("૧", "ધોરણ ૧૦ પછી પ્રવાહની સાચી પસંદગી કેમ કરવી?")
    pdf.set_font("Gujarati", "", 9.5)
    pdf.set_text_color(*pdf.GRAY)
    pdf.multi_cell(0, 5, "ધોરણ ૧૦ પાસ કર્યા પછી વિદ્યાર્થીના જીવનનો સૌથી મહત્વનો વળાંક આવે છે. મોટાભાગના વિદ્યાર્થીઓ મિત્રો કે પરિવારના દબાણમાં આવીને પ્રવાહ પસંદ કરતા હોય છે. પ્રવાહ પસંદ કરતી વખતે નીચેના ૩ મુદ્દા ધ્યાનમાં રાખો:")
    pdf.ln(2)
    pdf.bullet("પોતાનો રસ અને ક્ષમતા: ", "ગણિત અને વિજ્ઞાનમાં સાચી રુચિ હોય તો સાયન્સ, ગણતરી અને વેપાર/નાણાંમાં રુચિ હોય તો કૉમર્સ, અને વાંચન, ભાષા, ઈતિહાસ કે વહીવટમાં રુચિ હોય તો આર્ટ્સ પસંદ કરવું જોઈએ.")
    pdf.bullet("ભવિષ્યનું લક્ષ્ય: ", "જો ડોક્ટર કે એન્જિનિયર બનવું હોય તો સાયન્સ જરૂરી છે. જો CA, બેંક ઓફિસર કે બિઝનેસ કરવો હોય તો કૉમર્સ શ્રેષ્ઠ છે. અને જો પોલીસ, તલાટી, ક્લાર્ક કે સિવિલ સર્વિસીસમાં જવું હોય તો આર્ટ્સ ઉપયોગી રહે છે.")
    pdf.bullet("સમય અને નાણાકીય રોકાણ: ", "સાયન્સમાં ટ્યુશન અને આગળના અભ્યાસનો ખર્ચ વધુ હોઈ શકે છે, જ્યારે આર્ટ્સ અને કૉમર્સમાં પ્રમાણમાં ઓછો ખર્ચ થાય છે.")

    pdf.section_title("૨", "પ્રવાહવાર સંપૂર્ણ વિશ્લેષણ (Science, Commerce, Arts)")

    pdf.stream_card(
        "સાયન્સ પ્રવાહ (Science Stream)",
        [
            ("", "સાયન્સ પ્રવાહમાં બે મુખ્ય ગ્રુપ હોય છે: ગ્રુપ-A (ગણિત) અને ગ્રુપ-B (બાયોલોજી)."),
            ("ગ્રુપ-A પછીના વિકલ્પો: ", "B.E. / B.Tech (કમ્પ્યુટર, મિકેનિકલ, સિવિલ, ઇલેક્ટ્રિકલ), આર્કિટેક્ચર, મર્ચન્ટ નેવી, NDA (એરફોર્સ/નેવી), B.Sc. IT/CS, ડેટા સાયન્સ."),
            ("ગ્રુપ-B પછીના વિકલ્પો: ", "MBBS, BDS, BAMS (આયુર્વેદ), BHMS (હોમિયોપેથી), નર્સિંગ (B.Sc Nursing), ફિઝિયોથેરાપી (BPT), ફાર્મસી (B.Pharm), એગ્રીકલ્ચર (B.Sc Agriculture)."),
            ("લાભ: ", "ટેક્નિકલ અને મેડિકલ ક્ષેત્રે ઊંચી આવકની તકો તેમજ સાયન્સ પછી અન્ય કોઈપણ ફિલ્ડમાં જવાની છૂટછાટ મળે છે."),
        ],
        height=52,
    )

    pdf.stream_card(
        "કૉમર્સ પ્રવાહ (Commerce Stream)",
        [
            ("", "નાણાકીય વ્યવહારો, બેંકિંગ, એકાઉન્ટિંગ અને વેપાર-વાણિજ્યમાં રુચિ ધરાવતા વિદ્યાર્થીઓ માટે કૉમર્સ શ્રેષ્ઠ વિકલ્પ છે."),
            ("મુખ્ય ડિગ્રી કોર્સ: ", "B.Com, BBA, BCA (કમ્પ્યુટર એપ્લિકેશન), BMS, B.Voc."),
            ("પ્રોફેશનલ કોર્સ: ", "CA (ચાર્ટર્ડ એકાઉન્ટન્ટ), CS (કંપની સેક્રેટરી), CMA (કોસ્ટ મેનેજમેન્ટ એકાઉન્ટન્ટ), CFA (ફાઇનાન્શિયલ એનાલિસ્ટ)."),
            ("", "કેરિયર ક્ષેત્રો: બેંકિંગ ક્ષેત્ર (PO, ક્લાર્ક), વીમા કંપનીઓ, ઇન્વેસ્ટમેન્ટ ફર્મ્સ, શેરબજાર, ટેક્સ કન્સલ્ટન્સી અને પોતાના સ્વતંત્ર બિઝનેસમાં ઉત્તમ તકો."),
        ],
        height=50,
    )

    pdf.stream_card(
        "આર્ટ્સ પ્રવાહ (Arts / Humanities)",
        [
            ("", "આર્ટ્સ એ સ્પર્ધાત્મક પરીક્ષાઓ અને સરકારી નોકરીઓ માટે સૌથી વધુ સ્કોરિંગ અને અનુકૂળ પ્રવાહ માનવામાં આવે છે."),
            ("મુખ્ય વિષયો: ", "ઇતિહાસ, ભૂગોળ, બંધારણ (રાજ્યશાસ્ત્ર), સમાજશાસ્ત્ર, મનોવિજ્ઞાન અને અર્થશાસ્ત્ર."),
            ("મુખ્ય ડિગ્રીઓ: ", "B.A., B.S.W. (સોશિયલ વર્ક), B.J.M.C. (પત્રકારત્વ), B.Ed. (શિક્ષક માટે), LL.B. (વકીલાત)."),
            ("વિશેષ ફાયદો: ", "તમામ સરકારી સ્પર્ધાત્મક પરીક્ષાઓ (GPSC, UPSC, પંચાયત) નો ૭૦% અભ્યાસક્રમ આર્ટ્સના વિષયો આધારિત હોય છે."),
        ],
        height=48,
    )

    pdf.add_page() 

    pdf.section_title("૩", "ડિપ્લોમા અને ITI (ધોરણ ૧૦ પછી સીધા ટેકનિકલ કોર્સ)")
    pdf.set_font("Gujarati", "B", 10)
    pdf.set_text_color(*pdf.NAVY)
    pdf.cell(0, 6, "ડિપ્લોમા એન્જિનિયરિંગ (૩ વર્ષ)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.bullet("", "મેકેનિકલ, સિવિલ, ઇલેક્ટ્રિકલ, કમ્પ્યુટર, ઓટોમોબાઇલ.")
    pdf.bullet("", "ડિપ્લોમા પછી સીધા ડિગ્રીના બીજા વર્ષમાં પ્રવેશ (D2D).")
    pdf.bullet("", "રેલવે જુનિયર એન્જિનિયર (RRB JE), GETCO, UGVCL/PGVCL માં સીધી જુનિયર એન્જિનિયર તરીકે ભરતી.")
    pdf.ln(3)

    pdf.set_font("Gujarati", "B", 10)
    pdf.set_text_color(*pdf.NAVY)
    pdf.cell(0, 6, "ITI વ્યવસાયિક કોર્સ (૧ થી ૨ વર્ષ)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.bullet("", "ઇલેક્ટ્રિશિયન, ફિટર, વાયરમેન, ડીઝલ મિકેનિક, COPA.")
    pdf.bullet("", "ટૂંકા ગાળામાં ટેકનિકલ નોકરી અથવા સ્વરોજગાર શરૂ કરવાની શ્રેષ્ઠ તક.")
    pdf.bullet("", "રેલવે આસિસ્ટન્ટ લોકો પાયલટ (ALP), ટેકનિશિયન અને ISRO/DRDO માં સીધી સરકારી ભરતી.")
    pdf.ln(5)

    pdf.section_title("૪", "ગુજરાત રાજ્ય સરકારની મુખ્ય ભરતીઓ")
    pdf.set_font("Gujarati", "B", 9)
    with pdf.table(col_widths=(45, 30, 30, 75), text_align="L", line_height=7) as table:
        row = table.row()
        for header in ["ભરતી/ હોદ્દો", "શૈક્ષણિક લાયકાત", "વયમર્યાદા", "પસંદગી પદ્ધતિ"]:
            row.cell(header)
        pdf.set_font("Gujarati", "", 8.5)
        data_guj = [
            ("પોલીસ કોન્સ્ટેબલ / LRD", "ધોરણ ૧૨ પાસ", "૧૮ થી ૩૩ વર્ષ", "શારીરિક કસોટી (દોડ) + લેખિત પરીક્ષા"),
            ("વનરક્ષક (Forest Guard)", "ધોરણ ૧૨ પાસ", "૧૮ થી ૩૩ વર્ષ", "CBRT કમ્પ્યુટર ટેસ્ટ + ફિઝિકલ ટેસ્ટ"),
            ("તલાટી કમ મંત્રી / જુનિયર ક્લાર્ક", "ગ્રેજ્યુએટ (સ્નાતક)", "૨૧ થી ૩૫ વર્ષ", "CBRT / ઓબ્જેક્ટિવ સ્પર્ધાત્મક કસોટી"),
            ("હાઈકોર્ટ પટાવાળા / બેલિફ", "ધોરણ ૧૦ / ૧૨ પાસ", "૧૮ થી ૩૫ વર્ષ", "ઓબ્જેક્ટિવ લેખિત પરીક્ષા"),
            ("મુખ્ય સેવિકા / ગ્રામ સેવક", "ડિપ્લોમા / ગ્રેજ્યુએટ", "૨૧ થી ૩૫ વર્ષ", "સ્પર્ધાત્મક લેખિત પરીક્ષા"),
            ("સબ-ઇન્સ્પેક્ટર (PSI)", "ગ્રેજ્યુએટ", "૨૧ થી ૩૫ વર્ષ", "ફિઝિકલ + પ્રિલિમિનરી + મુખ્ય પરીક્ષા")
        ]
        for item in data_guj:
            row = table.row()
            for cell_data in item:
                row.cell(cell_data)
    pdf.ln(4)

    pdf.section_title("૫", "કેન્દ્ર સરકારની મુખ્ય નોકરીઓની તકો")
    pdf.set_font("Gujarati", "B", 9)
    with pdf.table(col_widths=(40, 25, 45, 70), text_align="L", line_height=7) as table:
        row = table.row()
        for header in ["વિભાગ / પરીક્ષા", "લાયકાત", "મુખ્ય પદો", "વિશેષ લાભ"]:
            row.cell(header)
        pdf.set_font("Gujarati", "", 8.5)
        data_cen = [
            ("SSC GD કોન્સ્ટેબલ", "૧૦ પાસ", "BSF, CISF, CRPF", "પેરામિલેટરી ફોર્સિસમાં કાયમી નોકરી"),
            ("SSC CHSL", "૧૨ પાસ", "LDC, JSA, DEO", "કેન્દ્રીય મંત્રાલયોમાં ક્લાર્ક અને ઓફિસ વર્ક"),
            ("રેલવે ગ્રુપ-D / ટેકનિશિયન", "૧૦ પાસ / ITI", "ટ્રેક મેન્ટેનર, લોકો પાયલટ", "રેલવે પાસ, મેડિકલ સુવિધા"),
            ("ઇન્ડિયન આર્મી / નેવી", "૧૦ / ૧૨ પાસ", "અગ્નિવીર (જનરલ ડ્યુટી)", "૪ વર્ષ સેવા, આર્થિક પેકેજ"),
            ("ઇન્ડિયન કોસ્ટ ગાર્ડ", "૧૨ સાયન્સ", "નાવિક જનરલ ડ્યુટી", "સમુદ્ર સુરક્ષામાં કેન્દ્રીય સંરક્ષણ પદ")
        ]
        for item in data_cen:
            row = table.row()
            for cell_data in item:
                row.cell(cell_data)
    pdf.ln(4)

    pdf.section_title("૬", "સ્પર્ધાત્મક પરીક્ષાઓની તૈયારી માટે સ્માર્ટ રણનીતિ")
    pdf.bullet("1. સિલેબસ અને જૂના પેપર્સ: ", "સૌપ્રથમ જે પરીક્ષા આપવી હોય તેનો સત્તાવાર સિલેબસ મેળવી છેલ્લા ૫ વર્ષના પેપર સોલ્વ કરો.")
    pdf.bullet("2. GCERT / NCERT પુસ્તકો: ", "ધોરણ ૬ થી ૧૦ ના સામાજિક વિજ્ઞાન, વિજ્ઞાન અને ગણિતના પાઠ્યપુસ્તકો પાયો મજબૂત કરવા માટે શ્રેષ્ઠ છે.")
    pdf.bullet("3. ડેઇલી કરંટ અફેર્સ: ", "રોજના અખબારો અને વર્તમાન પ્રવાહોની નિયમિત નોંધ રાખવાની ટેવ પાડો.")
    pdf.bullet("4. ગણિત અને રિઝનિંગની પ્રેક્ટિસ: ", "રોજ ૧ કલાક શોર્ટ ટ્રીક્સ અને ઝડપી ગણતરીની પ્રેક્ટિસ કરો જેથી પેપરમાં સમય બચે.")
    pdf.bullet("5. નિયમિત મોક ટેસ્ટ: ", "અઠવાડિયે ઓછામાં ઓછી ૧ ઓનલાઇન કે ઓફલાઇન મોક ટેસ્ટ આપો અને પોતાની ભૂલો સુધારો.")

    try:
        pdf_bytes = pdf.output()
        if type(pdf_bytes) == str:
            pdf_bytes = pdf_bytes.encode('latin1')
    except TypeError:
        pdf_bytes = pdf.output(dest='S').encode('latin1')
        
    buffer = BytesIO(pdf_bytes)
    buffer.name = "Career_Guidance_Roadmap.pdf"
    buffer.seek(0)
    return buffer


# ============================================================
# DELIVERY (UPDATED TO USE PROFESSIONAL PDF)
# ============================================================

def send_purchase_success(
    user_id,
    order_id,
    coupon_code
):
    try:
        pdf_file = generate_career_pdf()
        pdf_file.seek(0)

        bot.send_document(
            user_id,
            pdf_file,
            visible_file_name="Career_Guidance_Roadmap.pdf",
            caption=(
                "✅ <b>Payment Successful</b>\n\n"
                f"🎟️ તમારો Unique Coupon: "
                f"<code>{coupon_code}</code>\n\n"
                "📄 તમારો Career Guidance Guide અહીં છે.\n\n"
                "આ coupon સાચવી રાખજો."
            )
        )

        mark_delivery_delivered(order_id)

        return True

    except Exception as exc:
        logger.exception(
            "PDF delivery failed for order %s",
            order_id
        )

        mark_delivery_failed(
            order_id,
            str(exc)
        )

        return False


def mark_delivery_delivered(order_id):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE delivery_queue
            SET status='delivered',
                updated_at=NOW()
            WHERE order_id=%s
        """, (order_id,))

        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception("mark_delivery_delivered failed")

    finally:
        conn.close()


def mark_delivery_failed(order_id, error):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE delivery_queue
            SET status='pending',
                attempts=attempts+1,
                last_error=%s,
                updated_at=NOW()
            WHERE order_id=%s
        """, (
            str(error)[:2000],
            order_id
        ))

        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception("mark_delivery_failed failed")

    finally:
        conn.close()


def delivery_worker():
    while True:
        try:
            conn = db_connect()

            try:
                cur = conn.cursor()

                cur.execute("""
                    SELECT *
                    FROM delivery_queue
                    WHERE status='pending'
                      AND attempts < 10
                    ORDER BY created_at ASC
                    LIMIT 5
                    FOR UPDATE SKIP LOCKED
                """)

                rows = cur.fetchall()

                for row in rows:
                    cur.execute("""
                        UPDATE delivery_queue
                        SET status='processing',
                            updated_at=NOW()
                        WHERE id=%s
                    """, (row["id"],))

                conn.commit()

            finally:
                conn.close()

            for row in rows:
                try:
                    send_purchase_success(
                        row["user_id"],
                        row["order_id"],
                        row["coupon_code"]
                    )
                except Exception:
                    logger.exception(
                        "Delivery worker error for %s",
                        row["order_id"]
                    )

        except Exception:
            logger.exception("Delivery worker loop failed")

        time.sleep(20)


# ============================================================
# PAYMENT CHECK
# ============================================================

def manually_check_payment(order_id, user_id):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM orders
            WHERE order_id=%s
              AND user_id=%s
        """, (
            order_id,
            int(user_id)
        ))

        order = cur.fetchone()

    finally:
        conn.close()

    if not order:
        return {
            "success": False,
            "message": "Order not found."
        }

    try:
        payment_link = fetch_payment_link(
            order["razorpay_link_id"]
        )

        valid, payment_id = validate_paid_payment_link(
            payment_link,
            order["razorpay_link_id"],
            order["reference_id"]
        )

        if not valid:
            return {
                "success": False,
                "message": (
                    "Payment હજુ successful દેખાતું નથી. "
                    "જો payment કર્યું હોય તો થોડું રાહ જોઈને "
                    "ફરી Check Payment કરો."
                )
            }

        result = process_paid_order(
            order_id,
            payment_link,
            payment_id
        )

        if result.get("requires_refund"):
            notify_admin_refund_required(result)
            return {
                "success": False,
                "message": (
                    "આ order payment થઈ ગયું છે પરંતુ "
                    "તમારી maximum purchase limit પૂર્ણ છે. "
                    "Admin દ્વારા refund process કરવામાં આવશે."
                )
            }

        if result.get("success"):
            coupon = result.get("coupon_code")

            send_purchase_success(
                user_id,
                order_id,
                coupon
            )

            return {
                "success": True,
                "coupon_code": coupon
            }

        return {
            "success": False,
            "message": "Payment processing failed."
        }

    except Exception:
        logger.exception(
            "Manual payment check failed"
        )

        return {
            "success": False,
            "message": "Payment verificationમાં error આવ્યો."
        }


# ============================================================
# ADMIN NOTIFICATION
# ============================================================

def notify_admin_refund_required(result):
    try:
        bot.send_message(
            ADMIN_CHAT_ID_INT,
            (
                "⚠️ <b>REFUND REQUIRED</b>\n\n"
                f"Order: <code>{result.get('order_id')}</code>\n"
                f"User ID: <code>{result.get('user_id')}</code>\n"
                "Reason: Maximum purchase limit reached after payment."
            )
        )
    except Exception:
        logger.exception(
            "Admin refund notification failed"
        )


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():
    markup = types.ReplyKeyboardMarkup(
        resize_keyboard=True
    )

    markup.row(
        "📘 Buy Guide",
        "🎟️ My Coupons"
    )

    markup.row(
        "🏆 Prize List",
        "👥 Refer & Earn"
    )

    markup.row(
        "💰 Wallet",
        "💸 Withdrawal"
    )

    markup.row(
        "📋 My Orders",
        "ℹ️ Help"
    )

    return markup


# ============================================================
# START
# ============================================================

@bot.message_handler(commands=["start"])
def start_handler(message):
    user_id = message.from_user.id
    name = message.from_user.first_name or ""

    referred_by = None

    parts = message.text.split(maxsplit=1)

    if len(parts) == 2:
        payload = parts[1].strip()

        if payload.startswith("ref_"):
            ref_id = payload.replace("ref_", "", 1)

            if ref_id.isdigit():
                ref_id_int = int(ref_id)

                if ref_id_int != user_id:
                    existing = get_user(user_id)

                    if not existing:
                        referred_by = ref_id_int

    create_or_update_user(
        user_id=user_id,
        name=name,
        referred_by=referred_by
    )

    user = get_user(user_id)

    if not user or not user.get("phone"):
        markup = types.ReplyKeyboardMarkup(
            resize_keyboard=True,
            one_time_keyboard=True
        )

        markup.add(
            types.KeyboardButton(
                "📱 Share Mobile Number",
                request_contact=True
            )
        )

        bot.send_message(
            user_id,
            (
                "નમસ્તે 👋\n\n"
                "EduGuide માં આપનું સ્વાગત છે.\n\n"
                "શરૂ કરવા માટે તમારો mobile number share કરો."
            ),
            reply_markup=markup
        )

        return

    bot.send_message(
        user_id,
        (
            "નમસ્તે 👋\n\n"
            "EduGuide માં આપનું સ્વાગત છે."
        ),
        reply_markup=main_menu()
    )


# ============================================================
# CONTACT
# ============================================================

@bot.message_handler(
    content_types=["contact"]
)
def contact_handler(message):
    user_id = message.from_user.id

    contact = message.contact

    if contact.user_id and contact.user_id != user_id:
        bot.send_message(
            user_id,
            "કૃપા કરીને તમારો પોતાનો contact share કરો."
        )
        return

    phone = contact.phone_number.strip()

    if not re.match(r"^\+?[0-9]{10,15}$", phone):
        bot.send_message(
            user_id,
            "Mobile number યોગ્ય નથી."
        )
        return

    update_user_phone(
        user_id,
        phone
    )

    bot.send_message(
        user_id,
        (
            "✅ Mobile number successfully saved.\n\n"
            "હવે તમે EduGuide ઉપયોગ કરી શકો છો."
        ),
        reply_markup=main_menu()
    )


# ============================================================
# BUY GUIDE
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "📘 Buy Guide"
)
def buy_guide_handler(message):
    user_id = message.from_user.id

    user = get_user(user_id)

    if not user:
        bot.send_message(
            user_id,
            "કૃપા કરીને /start કરો."
        )
        return

    if not user.get("phone"):
        bot.send_message(
            user_id,
            "પહેલા તમારો mobile number share કરો."
        )
        return

    count = get_purchase_count(user_id)

    if count >= MAX_PURCHASES:
        bot.send_message(
            user_id,
            (
                f"⚠️ તમે maximum {MAX_PURCHASES} "
                "successful purchases કરી ચૂક્યા છો."
            )
        )
        return

    try:
        result = create_payment_link_for_user(
            user_id
        )

        if not result["success"]:
            bot.send_message(
                user_id,
                (
                    f"⚠️ Maximum {MAX_PURCHASES} "
                    "purchasesની limit પૂર્ણ થઈ ગઈ છે."
                )
            )
            return

        markup = types.InlineKeyboardMarkup()

        markup.add(
            types.InlineKeyboardButton(
                "💳 Pay ₹50",
                url=result["short_url"]
            )
        )

        markup.add(
            types.InlineKeyboardButton(
                "🔄 Check Payment",
                callback_data=(
                    "checkpay:" +
                    result["order_id"]
                )
            )
        )

        bot.send_message(
            user_id,
            (
                "📘 <b>Career Guidance Guide</b>\n\n"
                "💰 કિંમત: <b>₹50</b>\n"
                f"🧾 Order: <code>{result['order_id']}</code>\n\n"
                "Payment કરવા માટે નીચેનું button દબાવો."
            ),
            reply_markup=markup
        )

    except Exception:
        logger.exception(
            "Buy guide failed"
        )

        bot.send_message(
            user_id,
            "Payment link બનાવવામાં અત્યારે સમસ્યા છે. ફરી પ્રયાસ કરો."
        )


# ============================================================
# CHECK PAYMENT CALLBACK
# ============================================================

@bot.callback_query_handler(
    func=lambda call: call.data.startswith("checkpay:")
)
def check_payment_callback(call):
    user_id = call.from_user.id

    order_id = call.data.split(
        "checkpay:",
        1
    )[1]

    bot.answer_callback_query(
        call.id,
        "Payment check કરી રહ્યા છીએ..."
    )

    result = manually_check_payment(
        order_id,
        user_id
    )

    if result["success"]:
        bot.send_message(
            user_id,
            (
                "✅ Payment successfully verified.\n\n"
                f"🎟️ Coupon: <code>"
                f"{result['coupon_code']}</code>\n\n"
                "📄 PDF મોકલવામાં આવી રહ્યો છે."
            )
        )

    else:
        bot.send_message(
            user_id,
            result.get(
                "message",
                "Payment હજુ verify થયું નથી."
            )
        )


# ============================================================
# COUPONS
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "🎟️ My Coupons"
)
def my_coupons_handler(message):
    user_id = message.from_user.id

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT coupon_code, order_id, created_at
            FROM coupons
            WHERE user_id=%s
            ORDER BY id DESC
        """, (user_id,))

        coupons = cur.fetchall()

    finally:
        conn.close()

    if not coupons:
        bot.send_message(
            user_id,
            "હજુ કોઈ coupon નથી."
        )
        return

    lines = [
        "<b>🎟️ My Coupons</b>",
        ""
    ]

    for index, coupon in enumerate(coupons, 1):
        lines.append(
            f"{index}. <code>{coupon['coupon_code']}</code>"
        )

    bot.send_message(
        user_id,
        "\n".join(lines)
    )


# ============================================================
# PRIZE LIST
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "🏆 Prize List"
)
def prize_list_handler(message):
    bot.send_message(
        message.from_user.id,
        prize_list_text()
    )


# ============================================================
# REFERRAL
# ============================================================

def get_bot_username():
    try:
        me = bot.get_me()
        return me.username
    except Exception:
        logger.exception(
            "get_bot_username failed"
        )
        return None


@bot.message_handler(
    func=lambda m: m.text == "👥 Refer & Earn"
)
def referral_handler(message):
    user_id = message.from_user.id

    username = get_bot_username()

    if not username:
        bot.send_message(
            user_id,
            "Referral link અત્યારે બનાવી શકાતી નથી."
        )
        return

    link = f"https://t.me/{username}?start=ref_{user_id}"

    share_text = (
        "📘 EduGuide Career Guidance Guide\n\n"
        "કરિયર, અભ્યાસ અને રોજગાર માટે ઉપયોગી "
        "Career Guidance Guide મેળવો.\n\n"
        "👇 અહીંથી EduGuide શરૂ કરો:"
    )

    share_url = (
        "https://t.me/share/url"
        "?url=" + quote(link, safe="")
        + "&text=" + quote(share_text, safe="")
    )

    markup = types.InlineKeyboardMarkup()

    markup.add(
        types.InlineKeyboardButton(
            "📤 Share",
            url=share_url
        )
    )

    bot.send_message(
        user_id,
        (
            "👥 <b>Refer & Earn</b>\n\n"
            "તમારો unique referral link:\n\n"
            f"<code>{link}</code>\n\n"
            "તમારા linkથી નવા customer આવે "
            "અને successful purchase કરે ત્યારે "
            f"તમને {format_rupees(REFERRAL_BONUS)} referral bonus મળશે."
        ),
        reply_markup=markup
    )


# ============================================================
# WALLET
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "💰 Wallet"
)
def wallet_handler(message):
    user_id = message.from_user.id

    user = get_user(user_id)

    if not user:
        bot.send_message(
            user_id,
            "કૃપા કરીને /start કરો."
        )
        return

    balance = money(
        user["wallet_balance"]
    )

    bot.send_message(
        user_id,
        (
            "💰 <b>My Wallet</b>\n\n"
            f"Available Balance: <b>"
            f"{format_rupees(balance)}</b>\n\n"
            f"Referral Bonus: <b>{format_rupees(REFERRAL_BONUS)}</b> "
            "per eligible successful referral."
        )
    )


# ============================================================
# WITHDRAWAL
# ============================================================

withdrawal_waiting = {}


@bot.message_handler(
    func=lambda m: m.text == "💸 Withdrawal"
)
def withdrawal_start(message):
    user_id = message.from_user.id

    user = get_user(user_id)

    if not user:
        bot.send_message(
            user_id,
            "કૃપા કરીને /start કરો."
        )
        return

    balance = money(
        user["wallet_balance"]
    )

    if balance <= 0:
        bot.send_message(
            user_id,
            "તમારા walletમાં હાલ withdrawal માટે balance નથી."
        )
        return

    withdrawal_waiting[user_id] = True

    bot.send_message(
        user_id,
        (
            f"💸 Available balance: <b>"
            f"{format_rupees(balance)}</b>\n\n"
            "Withdrawal માટે તમારું UPI ID મોકલો.\n\n"
            "ઉદાહરણ: name@upi"
        )
    )


@bot.message_handler(
    func=lambda m: withdrawal_waiting.get(m.from_user.id, False)
)
def withdrawal_receive(message):
    user_id = message.from_user.id

    upi_id = message.text.strip()

    if len(upi_id) < 5 or len(upi_id) > 100:
        bot.send_message(
            user_id,
            "UPI ID યોગ્ય નથી. ફરી મોકલો."
        )
        return

    if not re.match(
        r"^[A-Za-z0-9._-]{2,}@[A-Za-z0-9._-]{2,}$",
        upi_id
    ):
        bot.send_message(
            user_id,
            "UPI IDનું format યોગ્ય નથી."
        )
        return

    withdrawal_waiting.pop(
        user_id,
        None
    )

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM users
            WHERE user_id=%s
            FOR UPDATE
        """, (user_id,))

        user = cur.fetchone()

        if not user:
            conn.rollback()

            bot.send_message(
                user_id,
                "User account મળ્યું નથી."
            )
            return

        balance = money(
            user["wallet_balance"]
        )

        if balance <= 0:
            conn.rollback()

            bot.send_message(
                user_id,
                "તમારા walletમાં balance નથી."
            )
            return

        amount = balance

        cur.execute("""
            UPDATE users
            SET wallet_balance=0,
                updated_at=NOW()
            WHERE user_id=%s
        """, (user_id,))

        cur.execute("""
            INSERT INTO withdrawals
            (
                user_id,
                amount,
                upi_id,
                status
            )
            VALUES (%s, %s, %s, 'pending')
            RETURNING id
        """, (
            user_id,
            amount,
            upi_id
        ))

        withdrawal_id = cur.fetchone()["id"]

        conn.commit()

        bot.send_message(
            user_id,
            (
                "✅ Withdrawal request submitted.\n\n"
                f"Amount: <b>{format_rupees(amount)}</b>\n"
                f"UPI: <code>{upi_id}</code>\n"
                f"Request ID: <code>WD-{withdrawal_id}</code>\n\n"
                "Admin verification પછી payment process થશે."
            )
        )

        bot.send_message(
            ADMIN_CHAT_ID_INT,
            (
                "💸 <b>New Withdrawal Request</b>\n\n"
                f"Request ID: <code>WD-{withdrawal_id}</code>\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Amount: <b>{format_rupees(amount)}</b>\n"
                f"UPI: <code>{upi_id}</code>"
            )
        )

    except Exception:
        conn.rollback()

        logger.exception(
            "Withdrawal request failed"
        )

        bot.send_message(
            user_id,
            "Withdrawal requestમાં error આવ્યો."
        )

    finally:
        conn.close()


# ============================================================
# ADMIN WITHDRAWALS
# ============================================================

@bot.message_handler(
    commands=["withdrawals"]
)
def admin_withdrawals(message):
    if not is_admin(message.from_user.id):
        return

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM withdrawals
            WHERE status='pending'
            ORDER BY id ASC
            LIMIT 50
        """)

        rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        bot.send_message(
            ADMIN_CHAT_ID_INT,
            "No pending withdrawals."
        )
        return

    for row in rows:
        markup = types.InlineKeyboardMarkup()

        markup.row(
            types.InlineKeyboardButton(
                "✅ Approve",
                callback_data=f"wdapprove:{row['id']}"
            ),
            types.InlineKeyboardButton(
                "❌ Reject",
                callback_data=f"wdreject:{row['id']}"
            )
        )

        bot.send_message(
            ADMIN_CHAT_ID_INT,
            (
                f"<b>Withdrawal #{row['id']}</b>\n\n"
                f"User: <code>{row['user_id']}</code>\n"
                f"Amount: <b>{format_rupees(row['amount'])}</b>\n"
                f"UPI: <code>{row['upi_id']}</code>\n"
                f"Status: {row['status']}"
            ),
            reply_markup=markup
        )


@bot.callback_query_handler(
    func=lambda call:
        call.data.startswith("wdapprove:")
        or call.data.startswith("wdreject:")
)
def withdrawal_admin_action(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(
            call.id,
            "Unauthorized"
        )
        return

    action, raw_id = call.data.split(":", 1)

    withdrawal_id = safe_int(raw_id)

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM withdrawals
            WHERE id=%s
            FOR UPDATE
        """, (withdrawal_id,))

        row = cur.fetchone()

        if not row:
            conn.rollback()

            bot.answer_callback_query(
                call.id,
                "Withdrawal not found."
            )
            return

        if row["status"] != "pending":
            conn.rollback()

            bot.answer_callback_query(
                call.id,
                "Already processed."
            )
            return

        if action == "wdapprove":
            cur.execute("""
                UPDATE withdrawals
                SET status='approved',
                    updated_at=NOW()
                WHERE id=%s
            """, (withdrawal_id,))

            new_status = "approved"

        else:
            cur.execute("""
                UPDATE withdrawals
                SET status='rejected',
                    updated_at=NOW()
                WHERE id=%s
            """, (withdrawal_id,))

            cur.execute("""
                UPDATE users
                SET wallet_balance=wallet_balance + %s,
                    updated_at=NOW()
                WHERE user_id=%s
            """, (
                row["amount"],
                row["user_id"]
            ))

            new_status = "rejected"

        conn.commit()

        bot.answer_callback_query(
            call.id,
            "Updated"
        )

        bot.send_message(
            row["user_id"],
            (
                f"💸 Withdrawal request "
                f"<code>WD-{withdrawal_id}</code>\n\n"
                f"Status: <b>{new_status}</b>"
            )
        )

    except Exception:
        conn.rollback()
        logger.exception(
            "Withdrawal admin action failed"
        )

        bot.answer_callback_query(
            call.id,
            "Error"
        )

    finally:
        conn.close()


# ============================================================
# ORDER HISTORY
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "📋 My Orders"
)
def orders_handler(message):
    user_id = message.from_user.id

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT
                o.order_id,
                o.amount,
                o.status,
                o.created_at,
                c.coupon_code
            FROM orders o
            LEFT JOIN coupons c
                ON c.order_id=o.order_id
            WHERE o.user_id=%s
            ORDER BY o.id DESC
            LIMIT 50
        """, (user_id,))

        rows = cur.fetchall()

    finally:
        conn.close()

    if not rows:
        bot.send_message(
            user_id,
            "તમારો કોઈ order history નથી."
        )
        return

    lines = [
        "<b>📋 My Orders</b>",
        ""
    ]

    for row in rows:
        coupon = row["coupon_code"] or "-"

        lines.append(
            f"🧾 <code>{row['order_id']}</code>\n"
            f"Amount: {format_rupees(Decimal(row['amount']) / 100)}\n"
            f"Status: <b>{row['status']}</b>\n"
            f"Coupon: <code>{coupon}</code>\n"
        )

    bot.send_message(
        user_id,
        "\n".join(lines)
    )


# ============================================================
# HELP
# ============================================================

@bot.message_handler(
    func=lambda m: m.text == "ℹ️ Help"
)
def help_handler(message):
    bot.send_message(
        message.from_user.id,
        (
            "<b>ℹ️ EduGuide Help</b>\n\n"
            "📘 Buy Guide — ₹50\n"
            "🎟️ My Coupons — તમારા coupons\n"
            "🏆 Prize List — fixed prize list\n"
            "👥 Refer & Earn — referral link\n"
            "💰 Wallet — wallet balance\n"
            "💸 Withdrawal — withdrawal request\n"
            "📋 My Orders — order history\n\n"
            "Payment પછી PDF અને unique coupon આપવામાં આવશે."
        )
    )


# ============================================================
# ADMIN STATS
# ============================================================

@bot.message_handler(
    commands=["stats"]
)
def admin_stats(message):
    if not is_admin(message.from_user.id):
        return

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM users
        """)
        users = cur.fetchone()["count"]

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE status='paid'
              AND processed=TRUE
        """)
        successful_orders = cur.fetchone()["count"]

        cur.execute("""
            SELECT COALESCE(
                SUM(amount), 0
            ) AS total
            FROM orders
            WHERE status='paid'
              AND processed=TRUE
        """)
        revenue_paise = cur.fetchone()["total"]

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM coupons
        """)
        coupons = cur.fetchone()["count"]

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM withdrawals
            WHERE status='pending'
        """)
        pending_withdrawals = cur.fetchone()["count"]

        cur.execute("""
            SELECT COUNT(*) AS count
            FROM delivery_queue
            WHERE status='pending'
        """)
        pending_delivery = cur.fetchone()["count"]

    finally:
        conn.close()

    revenue = Decimal(revenue_paise or 0) / 100

    bot.send_message(
        ADMIN_CHAT_ID_INT,
        (
            "<b>📊 EduGuide Live Stats</b>\n\n"
            f"👤 Users: <b>{users}</b>\n"
            f"📘 Successful Purchases: <b>{successful_orders}</b>\n"
            f"💰 Gross Sales: <b>{format_rupees(revenue)}</b>\n"
            f"🎟️ Coupons: <b>{coupons}</b>\n"
            f"💸 Pending Withdrawals: <b>{pending_withdrawals}</b>\n"
            f"📄 Pending PDF Deliveries: <b>{pending_delivery}</b>"
        )
    )


# ============================================================
# ADMIN DELIVERY RETRY
# ============================================================

@bot.message_handler(
    commands=["retry_delivery"]
)
def admin_retry_delivery(message):
    if not is_admin(message.from_user.id):
        return

    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE delivery_queue
            SET status='pending',
                updated_at=NOW()
            WHERE status='failed'
               OR (
                    status='pending'
                    AND attempts >= 10
               )
        """)

        affected = cur.rowcount

        conn.commit()

    except Exception:
        conn.rollback()
        logger.exception(
            "retry_delivery failed"
        )
        affected = 0

    finally:
        conn.close()

    bot.send_message(
        ADMIN_CHAT_ID_INT,
        f"🔄 {affected} delivery records queued for retry."
    )


# ============================================================
# RAZORPAY WEBHOOK
# ============================================================

@app.route(
    "/razorpay/webhook",
    methods=["POST"]
)
def razorpay_webhook():
    raw_body = request.get_data()

    signature = request.headers.get(
        "X-Razorpay-Signature",
        ""
    )

    if not verify_webhook_signature(
        raw_body,
        signature
    ):
        logger.warning(
            "Invalid Razorpay webhook signature"
        )

        return jsonify({
            "ok": False,
            "error": "invalid signature"
        }), 400

    try:
        payload = request.get_json(
            force=True
        )
    except Exception:
        return jsonify({
            "ok": False,
            "error": "invalid json"
        }), 400

    event = payload.get("event", "")

    if event != "payment_link.paid":
        return jsonify({
            "ok": True,
            "ignored": event
        }), 200

    try:
        payment_link_entity = (
            payload
            .get("payload", {})
            .get("payment_link", {})
            .get("entity", {})
        )

        payment_entity = (
            payload
            .get("payload", {})
            .get("payment", {})
            .get("entity", {})
        )

        link_id = payment_link_entity.get("id")

        reference_id = payment_link_entity.get(
            "reference_id"
        )

        payment_id = payment_entity.get("id")

        if not link_id or not reference_id:
            logger.warning(
                "Webhook missing link/reference"
            )

            return jsonify({
                "ok": False
            }), 400

        conn = db_connect()

        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT *
                FROM orders
                WHERE razorpay_link_id=%s
                  AND reference_id=%s
                FOR UPDATE
            """, (
                link_id,
                reference_id
            ))

            order = cur.fetchone()

        finally:
            conn.close()

        if not order:
            logger.warning(
                "Webhook order not found: %s",
                reference_id
            )

            return jsonify({
                "ok": True
            }), 200

        payment_link = fetch_payment_link(
            link_id
        )

        valid, fetched_payment_id = (
            validate_paid_payment_link(
                payment_link,
                link_id,
                reference_id
            )
        )

        if not valid:
            logger.warning(
                "Webhook payment validation failed: %s",
                order["order_id"]
            )

            return jsonify({
                "ok": False
            }), 400

        if not payment_id:
            payment_id = fetched_payment_id

        result = process_paid_order(
            order["order_id"],
            payment_link,
            payment_id
        )

        if result.get("requires_refund"):
            notify_admin_refund_required(
                result
            )

            return jsonify({
                "ok": True,
                "status": "refund_required"
            }), 200

        if result.get("success"):
            logger.info(
                "Payment processed: %s",
                order["order_id"]
            )

        return jsonify({
            "ok": True
        }), 200

    except Exception:
        logger.exception(
            "Razorpay webhook processing failed"
        )

        return jsonify({
            "ok": False
        }), 500


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "app": APP_NAME,
        "status": "running"
    })


@app.route("/health", methods=["GET"])
def health():
    try:
        conn = db_connect()

        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()

        return jsonify({
            "status": "healthy",
            "database": "ok",
            "service": APP_NAME
        })

    except Exception as exc:
        logger.exception(
            "Health check failed"
        )

        return jsonify({
            "status": "unhealthy",
            "database": "error",
            "error": str(exc)
        }), 500


# ============================================================
# FALLBACK
# ============================================================

@bot.message_handler(
    func=lambda message: True,
    content_types=["text"]
)
def fallback_handler(message):
    if message.text.startswith("/"):
        return

    bot.send_message(
        message.from_user.id,
        (
            "મહેરબાની કરીને નીચેના menuમાંથી option પસંદ કરો."
        ),
        reply_markup=main_menu()
    )


# ============================================================
# START DELIVERY WORKER
# ============================================================

def start_delivery_worker():
    worker = threading.Thread(
        target=delivery_worker,
        daemon=True,
        name="delivery-worker"
    )

    worker.start()

    logger.info(
        "Delivery worker started"
    )


# ============================================================
# MAIN
# ============================================================

def start_flask():
    app.run(
        host="0.0.0.0",
        port=PORT,
        threaded=True
    )


def start_bot():
    logger.info(
        "Starting Telegram polling..."
    )

    while True:
        try:
            bot.infinity_polling(
                timeout=30,
                long_polling_timeout=30,
                skip_pending=False
            )

        except Exception:
            logger.exception(
                "Telegram polling crashed; restarting..."
            )

            time.sleep(5)


if __name__ == "__main__":
    init_db()

    start_delivery_worker()

    flask_thread = threading.Thread(
        target=start_flask,
        daemon=True,
        name="flask-server"
    )

    flask_thread.start()

    logger.info(
        "EduGuide production service started on port %s",
        PORT
    )

    start_bot()
