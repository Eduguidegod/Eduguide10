import os
import random
import string
import requests
import psycopg2
import threading
from flask import Flask
from io import BytesIO
from fpdf import FPDF
from fpdf.enums import XPos, YPos
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ----------------- FLASK WEB SERVER (RENDER PORT BINDING) -----------------
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "EduGuide Telegram Bot is live and running with Razorpay API!"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ----------------- CONFIGURATION & DB CONNECTION -----------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/dbname")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "123456789"))

# Razorpay API Credentials (Render ના Environment Variables માં સેટ કરવાના રહેશે)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "YOUR_RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "YOUR_RAZORPAY_KEY_SECRET")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            phone TEXT,
            language TEXT DEFAULT 'gu',
            referred_by BIGINT,
            wallet_balance NUMERIC DEFAULT 0,
            referral_count INT DEFAULT 0
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            razorpay_link_id TEXT,
            user_id BIGINT,
            phone TEXT,
            amount INT DEFAULT 50,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    cur.execute('''
        CREATE TABLE IF NOT EXISTS coupons (
            coupon_id TEXT PRIMARY KEY,
            user_id BIGINT,
            phone TEXT,
            order_id TEXT,
            is_winner BOOLEAN DEFAULT FALSE
        )
    ''')

    # Safety ALTER statements
    cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS razorpay_link_id TEXT;")
    cur.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS phone TEXT;")
    cur.execute("ALTER TABLE coupons ADD COLUMN IF NOT EXISTS coupon_id TEXT;")
    
    conn.commit()
    cur.close()
    conn.close()

init_db()

# ----------------- LOCALIZATION STRINGS -----------------
LANG = {
    'gu': {
        'ask_name': "કૃપા કરીને તમારું પૂરું નામ મોકલો:",
        'ask_phone': "કૃપા કરીને તમારો મોબાઈલ નંબર મોકલો:",
        'reg_success': "તમારું રજીસ્ટ્રેશન સફળ થઈ ગયું છે!",
        'menu': "મુખ્ય મેનુ પસંદ કરો:",
        'btn_buy': "📚 ગાઇડ ખરીદો (₹50) + ફ્રી કૂપન",
        'btn_wallet': "💳 મારું વોલેટ & વિડ્રોઅલ",
        'btn_coupons': "🎟 માય કૂપન્સ & ડ્રો ઈનામો",
        'btn_ref': "👥 રેફરલ લિંક",
        'limit_exceeded': "⚠️ તમે આ મોબાઈલ નંબરથી મહત્તમ ૨૦ પીડીએફ ખરીદવાની મર્યાદા પૂરી કરી દીધી છે.",
        'pay_text': "🔗 તમારી ₹૫૦ ની પેમેન્ટ લિંક તૈયાર છે:\nકૃપા કરીને નીચે આપેલ બટન પર ક્લિક કરીને પેમેન્ટ પૂરું કરો, ત્યારબાદ જ 'પેમેન્ટ સ્ટેટસ તપાસો' બટન દબાવો.",
        'btn_pay': "🔗 પેમેન્ટ કરો (Razorpay)",
        'btn_check': "🔄 પેમેન્ટ સ્ટેટસ તપાસો",
        'pay_pending': "⏳ તમારું પેમેન્ટ હજુ સુધી કન્ફર્મ થયું નથી. જો તમે પેમેન્ટ કરી દીધું હોય, તો થોડીવાર પછી ફરીથી 'પેમેન્ટ સ્ટેટસ તપાસો' બટન દબાવો.",
        'pay_success': "🎉 અભિનંદન! તમારું પેમેન્ટ સફળ થઈ ગયું છે. તમારી પ્રોફેશનલ પીડીએફ નીચે મુજબ છે:",
        'coupon_msg': "🎟 તમારો યુનિક કૂપન નંબર: `{}`\n\n⚠️ **ખાસ નોંધ:** કૃપા કરીને આ નંબર નોંધી રાખો અથવા સ્ક્રીનશોટ લો. લકી ડ્રો વખતે આ જ માન્ય રહેશે!",
        'wallet_info': "💳 તમારું વોલેટ બેલેન્સ: ₹{}\nસફળ રેફરલ્સ: {}\n\n(મિનિમમ ₹૫૦ થયા પછી UPI દ્વારા ઉપાડી શકાય છે.)",
        'btn_withdraw': "💸 UPI દ્વારા પૈસા ઉપાડો",
        'ask_upi': "કૃપા કરીને તમારું UPI ID મોકલો (દા.ત., yourname@upi):",
        'withdraw_success': "✅ તમારી વિડ્રોઅલ રિક્વેસ્ટ એડમિનને મોકલી દેવાઈ છે. ટૂંક સમયમાં બેંક ખાતામાં જમા થઈ જશે.",
        'prizes_info': "🎁 **₹૧૦,૦૬,૦૦૦ ના ડ્રો ઈનામો:**\n1. ₹5,00,000 (1 વ્યક્તિ)\n2. ₹2,00,000 (1 વ્યક્તિ)\n3. ₹1,00,000 (1 વ્યક્તિ)\n4. ₹50,000 (1 વ્યક્તિ)\n5. ₹25,000 (1 વ્યક્તિ)\n6-10. દરેકને ₹5,000\n11-50. દરેકને ₹1,000\n51-100. દરેકને ₹500\n101-500. દરેકને ₹100"
    },
    'en': {
        'ask_name': "Please send your full name:",
        'ask_phone': "Please send your mobile number:",
        'reg_success': "Registration successful!",
        'menu': "Select from the main menu:",
        'btn_buy': "📚 Buy Guide (₹50) + Free Coupon",
        'btn_wallet': "💳 My Wallet & Withdrawal",
        'btn_coupons': "🎟 My Coupons & Prizes",
        'btn_ref': "👥 Referral Link",
        'limit_exceeded': "⚠️ You have reached the maximum limit of 20 PDF purchases for this mobile number.",
        'pay_text': "🔗 Your ₹50 payment link is ready:\nPlease complete the payment using the button below, then click 'Check Payment Status'.",
        'btn_pay': "🔗 Pay Now (Razorpay)",
        'btn_check': "🔄 Check Payment Status",
        'pay_pending': "⏳ Your payment is not confirmed yet. If you have completed the payment, please try checking again after a moment.",
        'pay_success': "🎉 Congratulations! Your payment was successful. Here is your professional PDF:",
        'coupon_msg': "🎟 Your Unique Coupon Number: `{}`\n\n⚠️ **Important Note:** Please save this number or take a screenshot. This will be valid during the lucky draw!",
        'wallet_info': "💳 Your Wallet Balance: ₹{}\nSuccessful Referrals: {}\n\n(Withdrawals available at minimum ₹50 via UPI.)",
        'btn_withdraw': "💸 Withdraw via UPI",
        'ask_upi': "Please send your UPI ID (e.g., yourname@upi):",
        'withdraw_success': "✅ Your withdrawal request has been sent to the admin.",
        'prizes_info': "🎁 **₹10,06,000 Prize Pool:**\n1. ₹5,00,000 (1 winner)\n2. ₹2,00,000 (1 winner)\n3. ₹1,00,000 (1 winner)\n4. ₹50,000 (1 winner)\n5. ₹25,000 (1 winner)\n6-10. ₹5,000 each\n11-50. ₹1,000 each\n51-100. ₹500 each\n101-500. ₹100 each"
    }
}

def get_text(user_id, key):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT language FROM users WHERE user_id = %s", (user_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    lang = res[0] if res and res[0] in ['gu', 'en'] else 'gu'
    return LANG[lang].get(key, LANG['gu'][key])

# ----------------- RAZORPAY API HELPER FUNCTIONS -----------------
def create_razorpay_payment_link(order_id, amount_in_inr, customer_name, customer_phone):
    url = "https://api.razorpay.com/v1/payment_links"
    payload = {
        "amount": amount_in_inr * 100,  # Paise માં કન્વર્ટ કરવા માટે (₹50 = 5000 paise)
        "currency": "INR",
        "accept_partial": False,
        "description": "EduGuide Career Guidance PDF + Lucky Draw Coupon",
        "customer": {
            "name": customer_name,
            "contact": customer_phone
        },
        "notify": {
            "sms": False,
            "email": False
        },
        "reminder_enable": False,
        "notes": {
            "order_id": order_id
        }
    }
    try:
        response = requests.post(url, json=payload, auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        res_data = response.json()
        if response.status_code == 200:
            return res_data.get("id"), res_data.get("short_url")
        else:
            print("Razorpay API Error:", res_data)
            return None, None
    except Exception as e:
        print("Razorpay Connection Exception:", e)
        return None, None

def check_razorpay_payment_status(link_id):
    url = f"https://api.razorpay.com/v1/payment_links/{link_id}"
    try:
        response = requests.get(url, auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))
        res_data = response.json()
        if response.status_code == 200:
            status = res_data.get("status")
            # Razorpay લિંક સ્ટેટસ 'paid' હોય તો જ True પરત કરશે
            return status == "paid"
        return False
    except Exception as e:
        print("Razorpay Status Check Exception:", e)
        return False

# ----------------- PROFESSIONAL PDF GENERATOR -----------------
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
        self.set_fill_color(244, 246, 250)
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

def generate_career_pdf(coupon_code):
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
    labels = ["વિશેષ ડિજિટલ એડિશન", f"કૂપન: {coupon_code}"]
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
    pdf.info_card("આ માર્ગદર્શિકા કોના માટે છે અને તમારો લકી ડ્રો કૂપન:", f"ધોરણ ૧૦ કે ૧૨ પાસ કરેલ વિદ્યાર્થીઓ માટે ખાસ માર્ગદર્શિકા.\n🎟 તમારો યુનિક કૂપન નંબર: {coupon_code} (લકી ડ્રો માટે સાચવી રાખો)")
    pdf.section_title("૧", "ધોરણ ૧૦ પછી પ્રવાહની સાચી પસંદગી કેમ કરવી?")
    pdf.bullet("પોતાનો રસ અને ક્ષમતા: ", "ગણિત અને વિજ્ઞાનમાં સાચી રુચિ હોય તો સાયન્સ, ગણતરી અને વેપારમાં રુચિ હોય તો કૉમર્સ, અને વાંચન કે વહીવટમાં રુચિ હોય તો આર્ટ્સ પસંદ કરવું.")
    pdf.bullet("ભવિષ્યનું લક્ષ્ય: ", "ડોક્ટર કે એન્જિનિયર માટે સાયન્સ. CA કે બેંકિંગ માટે કૉમર્સ. પોલીસ, તલાટી કે સિવિલ સર્વિસીસ માટે આર્ટ્સ ઉપયોગી છે.")
    pdf.section_title("૨", "પ્રવાહવાર સંપૂર્ણ વિશ્લેષણ (Science, Commerce, Arts)")
    pdf.stream_card("સાયન્સ પ્રવાહ (Science Stream)", [("", "ગ્રુપ-A (ગણિત) અને ગ્રુપ-B (બાયોલોજી)."), ("વિકલ્પો: ", "B.E./B.Tech, MBBS, B.Sc, ફાર્મસી, વગેરે.")], height=30)
    pdf.stream_card("કૉમર્સ પ્રવાહ (Commerce Stream)", [("", "નાણાકીય વ્યવહારો, બેંકિંગ અને વેપાર-વાણિજ્ય."), ("ડિગ્રી કોર્સ: ", "B.Com, BBA, BCA, CA, CS.")], height=30)
    pdf.stream_card("આર્ટ્સ પ્રવાહ (Arts / Humanities)", [("", "સરકારી સ્પર્ધાત્મક પરીક્ષાઓ માટે સૌથી વધુ સ્કોરિંગ પ્રવાહ."), ("મુખ્ય વિષયો: ", "ઇતિહાસ, ભૂગોળ, બંધારણ, સમાજશાસ્ત્ર.")], height=30)

    try:
        pdf_bytes = pdf.output()
        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode('latin1')
    except TypeError:
        pdf_bytes = pdf.output(dest='S').encode('latin1')
        
    buffer = BytesIO(pdf_bytes)
    buffer.name = f"Career_Guidance_{coupon_code}.pdf"
    buffer.seek(0)
    return buffer

# ----------------- BOT HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    args = context.args
    referred_by = int(args[0]) if args and args[0].isdigit() else None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT user_id, language, phone FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()

    if not user:
        cur.execute("INSERT INTO users (user_id, referred_by) VALUES (%s, %s) ON CONFLICT (user_id) DO NOTHING", (user_id, referred_by))
        conn.commit()
        cur.close()
        conn.close()
        keyboard = [[InlineKeyboardButton("🇬🇯 ગુજરાતી", callback_data="lang_gu"), InlineKeyboardButton("🇬🇧 English", callback_data="lang_en")]]
        await update.message.reply_text("નમસ્કાર! Welcome!\nકૃપા કરીને તમારી ભાષા પસંદ કરો / Please select your language:", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        cur.close()
        conn.close()
        await show_main_menu(update, context)

async def language_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    lang = 'gu' if query.data == 'lang_gu' else 'en'

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET language = %s WHERE user_id = %s", (lang, user_id))
    conn.commit()
    cur.close()
    conn.close()

    await query.message.reply_text(LANG[lang]['ask_name'])
    context.user_data['state'] = 'WAITING_FOR_NAME'

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    state = context.user_data.get('state')
    text = update.message.text

    if state == 'WAITING_FOR_NAME':
        context.user_data['name'] = text
        context.user_data['state'] = 'WAITING_FOR_PHONE'
        await update.message.reply_text(get_text(user_id, 'ask_phone'))

    elif state == 'WAITING_FOR_PHONE':
        phone = text
        name = context.user_data.get('name', 'User')
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET name = %s, phone = %s WHERE user_id = %s", (name, phone, user_id))
        conn.commit()
        cur.close()
        conn.close()

        context.user_data['state'] = None
        await update.message.reply_text(get_text(user_id, 'reg_success'))
        await show_main_menu_message(update, context)

    elif state == 'WAITING_FOR_UPI':
        upi_id = text
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT wallet_balance, phone FROM users WHERE user_id = %s", (user_id,))
        res = cur.fetchone()
        balance, phone = res[0], res[1]
        
        if balance >= 50:
            cur.execute("UPDATE users SET wallet_balance = wallet_balance - %s WHERE user_id = %s", (balance, user_id))
            conn.commit()
            await context.bot.send_message(ADMIN_CHAT_ID, f"🚨 **New Withdrawal Request**\nUser ID: {user_id}\nPhone: {phone}\nUPI ID: {upi_id}\nAmount: ₹{balance}")
            await update.message.reply_text(get_text(user_id, 'withdraw_success'))
        cur.close()
        conn.close()
        context.user_data['state'] = None
        await show_main_menu_message(update, context)

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton(get_text(user_id, 'btn_buy'), callback_data="buy_pdf")],
        [InlineKeyboardButton(get_text(user_id, 'btn_wallet'), callback_data="my_wallet")],
        [InlineKeyboardButton(get_text(user_id, 'btn_coupons'), callback_data="my_coupons")],
        [InlineKeyboardButton(get_text(user_id, 'btn_ref'), callback_data="refer_earn")]
    ]
    await update.message.reply_text(get_text(user_id, 'menu'), reply_markup=InlineKeyboardMarkup(keyboard))

async def show_main_menu_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    keyboard = [
        [InlineKeyboardButton(get_text(user_id, 'btn_buy'), callback_data="buy_pdf")],
        [InlineKeyboardButton(get_text(user_id, 'btn_wallet'), callback_data="my_wallet")],
        [InlineKeyboardButton(get_text(user_id, 'btn_coupons'), callback_data="my_coupons")],
        [InlineKeyboardButton(get_text(user_id, 'btn_ref'), callback_data="refer_earn")]
    ]
    await update.message.reply_text(get_text(user_id, 'menu'), reply_markup=InlineKeyboardMarkup(keyboard))

async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT name, phone FROM users WHERE user_id = %s", (user_id,))
    user_res = cur.fetchone()
    name = user_res[0] if user_res else "User"
    phone = user_res[1] if user_res else "9999999999"
    cur.close()
    conn.close()

    if data == "buy_pdf":
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM orders WHERE phone = %s AND status = 'paid'", (phone,))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()

        if count >= 20:
            await query.message.reply_text(get_text(user_id, 'limit_exceeded'))
            return

        order_id = "ORD_" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        
        # Razorpay API દ્વારા ઓટોમેટિક નવી પેમેન્ટ લિંક જનરેટ કરો
        link_id, payment_url = create_razorpay_payment_link(order_id, 50, name, phone)
        
        if not payment_url:
            await query.message.reply_text("⚠️ પેમેન્ટ લિંક જનરેટ કરવામાં તકલીફ પડી છે. કૃપા કરીને થોડીવાર પછી પ્રયાસ કરો.")
            return

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("INSERT INTO orders (order_id, razorpay_link_id, user_id, phone, status) VALUES (%s, %s, %s, %s, 'pending')", (order_id, link_id, user_id, phone))
        conn.commit()
        cur.close()
        conn.close()

        keyboard = [
            [InlineKeyboardButton(get_text(user_id, 'btn_pay'), url=payment_url)],
            [InlineKeyboardButton(get_text(user_id, 'btn_check'), callback_data=f"check_{order_id}")]
        ]
        await query.message.reply_text(get_text(user_id, 'pay_text'), reply_markup=InlineKeyboardMarkup(keyboard))

    elif data.startswith("check_"):
        order_id = data.split("_")[1]
        
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT razorpay_link_id, status FROM orders WHERE order_id = %s", (order_id,))
        order_res = cur.fetchone()
        
        if not order_res:
            cur.close()
            conn.close()
            await query.message.reply_text("⚠️ ઓર્ડરની માહિતી મળી નથી.")
            return

        link_id, current_status = order_res[0], order_res[1]

        # જો ઓર્ડર પહેલેથી paid ન હોય, તો Razorpay API થી લિંકનું સ્ટેટસ લાઈવ ચેક કરો
        if current_status != 'paid':
            is_paid = check_razorpay_payment_status(link_id)
            if is_paid:
                cur.execute("UPDATE orders SET status = 'paid' WHERE order_id = %s", (order_id,))
                conn.commit()
            else:
                cur.close()
                conn.close()
                await query.message.reply_text(get_text(user_id, 'pay_pending'))
                return

        # પેમેન્ટ સફળ થયા પછી જ આગળની પ્રોસેસ થશે
        # રેફરલ બોનસ અને કૂપન જનરેશન
        cur.execute("SELECT referred_by FROM users WHERE user_id = %s", (user_id,))
        ref_res = cur.fetchone()
        if ref_res and ref_res[0]:
            referrer_id = ref_res[0]
            cur.execute("UPDATE users SET wallet_balance = wallet_balance + 10, referral_count = referral_count + 1 WHERE user_id = %s", (referrer_id,))

        coupon_code = "EDU-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        cur.execute("INSERT INTO coupons (coupon_id, user_id, phone, order_id) VALUES (%s, %s, %s, %s)", (coupon_code, user_id, phone, order_id))
        conn.commit()
        cur.close()
        conn.close()

        pdf_file = generate_career_pdf(coupon_code)
        
        await query.message.reply_text(get_text(user_id, 'pay_success'))
        await context.bot.send_document(chat_id=user_id, document=pdf_file, caption=get_text(user_id, 'coupon_msg').format(coupon_code), parse_mode="Markdown")

    elif data == "my_wallet":
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT wallet_balance, referral_count FROM users WHERE user_id = %s", (user_id,))
        balance, ref_count = cur.fetchone()
        cur.close()
        conn.close()

        keyboard = [[InlineKeyboardButton(get_text(user_id, 'btn_withdraw'), callback_data="withdraw_req")]] if balance >= 50 else []
        await query.message.reply_text(get_text(user_id, 'wallet_info').format(balance, ref_count), reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None)

    elif data == "withdraw_req":
        await query.message.reply_text(get_text(user_id, 'ask_upi'))
        context.user_data['state'] = 'WAITING_FOR_UPI'

    elif data == "my_coupons":
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT coupon_id FROM coupons WHERE user_id = %s", (user_id,))
        coupons = cur.fetchall()
        cur.close()
        conn.close()

        coupon_list = "\n".join([c[0] for c in coupons]) if coupons else "No coupons yet."
        msg = f"🎟 **Your Coupons:**\n{coupon_list}\n\n{get_text(user_id, 'prizes_info')}"
        await query.message.reply_text(msg, parse_mode="Markdown")

    elif data == "refer_earn":
        bot_username = (await context.bot.get_me()).username
        ref_link = f"https://t.me/{bot_username}?start={user_id}"
        await query.message.reply_text(f"👥 **Refer & Earn:**\nShare this link with your friends. When they buy a PDF, you get ₹10 in your wallet!\n\n`{ref_link}`", parse_mode="Markdown")

def main():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(language_selection, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^(buy_pdf|check_|my_wallet|withdraw_req|my_coupons|refer_earn)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running with Razorpay API Integration & Professional PDF...")
    app.run_polling()

if __name__ == '__main__':
    main()
