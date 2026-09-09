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
    return "EduGuide Telegram Bot is live and running!"

def run_flask():
    port = int(os.getenv("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# ----------------- CONFIGURATION & DB CONNECTION -----------------
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL", "postgres://user:password@localhost:5432/dbname")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "123456789"))

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
        'pay_text': "📚 **સંપૂર્ણ કારકિર્દી માર્ગદર્શિકા અને સરકારી નોકરી રોડમેપ**\n\n✨ **આ પીડીએફની ખાસિયતો:**\n🎯 **સાચો પ્રવાહ પસંદ કરો:** ધોરણ ૧૦ અને ૧૨ પછી Science, Commerce કે Arts માંથી કયા ક્ષેત્રમાં ભવિષ્ય ઉજ્જવળ છે તેની સાચી દિશા.\n🛠️ **ડિપ્લોમા અને ITI ના શોર્ટકટ્સ:** ઓછા સમયમાં ડાયરેક્ટ સરકારી નોકરી મેળવવાના ટેકનિકલ કોર્સની સંપૂર્ણ માહિતી.\n🇮🇳 **ગુજરાત અને કેન્દ્ર સરકારની ભરતીઓ:** LRD પોલીસ, વનરક્ષક, તલાટી, રેલવે, SSC અને બેંકિંગ જેવી પરીક્ષાઓ માટે લાયકાત અને તૈયારીની સ્માર્ટ રણનીતિ.\n\n🔗 તમારી ₹૫૦ ની પેમેન્ટ લિંક તૈયાર છે:",
        'btn_pay': "🔗 પેમેન્ટ કરો (Razorpay)",
        'btn_check': "🔄 પેમેન્ટ સ્ટેટસ તપાસો",
        'pay_pending': "⏳ તમારું પેમેન્ટ હજુ સુધી કન્ફર્મ થયું નથી. જો તમે પેમેન્ટ કરી દીધું હોય, તો થોડીવાર પછી ફરીથી 'પેમેન્ટ સ્ટેટસ તપાસો' બટન દબાવો.",
        'pay_success': "🎉 અભિનંદન! તમારું પેમેન્ટ સફળ થઈ ગયું છે. તમારી પ્રોફેશનલ પીડીએફ નીચે મુજબ છે:",
        'coupon_msg': "🎟 તમારો યુનિક કૂપન નંબર: `{}`\n\n⚠️ **ખાસ નોંધ:** કૃપા કરીને આ નંબર નોંધી રાખો અથવા સ્ક્રીનશોટ લો. લકી ડ્રો વખતે આ જ માન્ય રહેશે!",
        'wallet_info': "💳 તમારું વોલેટ બેલેન્સ: ₹{}\nસફળ રેફરલ્સ: {}\n\n(મિનિમમ ₹૫૦ થયા પછી UPI દ્વારા ઉપાડી શકાય છે.)",
        'btn_withdraw': "💸 UPI દ્વારા પૈસા ઉપાડો",
        'ask_upi': "કૃપા કરીને તમારું UPI ID મોકલો (દા.ત., yourname@upi):",
        'withdraw_success': "✅ તમારી વિડ્રોઅલ રિક્વેસ્ટ એડમિનને મોકલી દેવાઈ છે. ટૂંક સમયમાં બેંક ખાતામાં જમા થઈ જશે.",
        'prizes_info': "🎁 **₹૧૦,૦૬,૦૦૦ ના ડ્રો ઈનામો:**\n1. ₹5,00,000 (1 વ્યક્તિ)\n2. ₹2,00,000 (1 વ્યક્તિ)\n3. ₹1,00,000 (1 વ્યક્તિ)\n4. ₹50,000 (1 વ્યક્તિ)\n5. ₹25,000 (1 વ્યક્તિ)\n6-10. દરેકને ₹5,000\n11-50. દરેકને ₹1,000\n51-100. દરેકને ₹500\n101-500. દરેકને ₹100",
        'ref_text': "👥 **રેફર એન્ડ અર્ન (Refer & Earn):**\nઆ લિંક તમારા મિત્રો સાથે શેર કરો. જ્યારે તેઓ પીડીએફ ખરીદશે, ત્યારે તમારા વોલેટમાં ₹૧૦ જમા થશે!\n\n`{}`",
        'btn_share': "📤 મિત્રો સાથે શેર કરો (WhatsApp/અન્ય)",
        'share_msg': "ધોરણ ૧૦ અને ૧૨ પછી કારકિર્દી ઘડવા માટેની શ્રેષ્ઠ માર્ગદર્શિકા મેળવો:"
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
        'pay_text': "📚 **Complete Career Guidance & Government Job Roadmap**\n\n✨ **Key Features:**\n🎯 **Right Stream Selection:** Guidance after 10th & 12th.\n🛠️ **Diploma & ITI Shortcuts:** Technical courses for direct jobs.\n🇮🇳 **Govt Recruitments:** LRD, Talati, Railways, SSC, Banking prep.\n\n🔗 Your ₹50 payment link is ready:",
        'btn_pay': "🔗 Pay Now (Razorpay)",
        'btn_check': "🔄 Check Payment Status",
        'pay_pending': "⏳ Your payment is not confirmed yet. If you have completed the payment, please try checking again after a moment.",
        'pay_success': "🎉 Congratulations! Your payment was successful. Here is your professional PDF:",
        'coupon_msg': "🎟 Your Unique Coupon Number: `{}`\n\n⚠️ **Important Note:** Please save this number or take a screenshot. This will be valid during the lucky draw!",
        'wallet_info': "💳 Your Wallet Balance: ₹{}\nSuccessful Referrals: {}\n\n(Withdrawals available at minimum ₹50 via UPI.)",
        'btn_withdraw': "💸 Withdraw via UPI",
        'ask_upi': "Please send your UPI ID (e.g., yourname@upi):",
        'withdraw_success': "✅ Your withdrawal request has been sent to the admin.",
        'prizes_info': "🎁 **₹10,06,000 Prize Pool:**\n1. ₹5,00,000 (1 winner)\n2. ₹2,00,000 (1 winner)\n3. ₹1,00,000 (1 winner)\n4. ₹50,000 (1 winner)\n5. ₹25,000 (1 winner)\n6-10. ₹5,000 each\n11-50. ₹1,000 each\n51-100. ₹500 each\n101-500. ₹100 each",
        'ref_text': "👥 **Refer & Earn:**\nShare this link with your friends. When they buy a PDF, you get ₹10 in your wallet!\n\n`{}`",
        'btn_share': "📤 Share with Friends (WhatsApp/Others)",
        'share_msg': "Get the ultimate career guidance roadmap after 10th & 12th:"
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

def get_user_lang(user_id):
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT language FROM users WHERE user_id = %s", (user_id,))
    res = cur.fetchone()
    cur.close()
    conn.close()
    return res[0] if res and res[0] in ['gu', 'en'] else 'gu'

# ----------------- RAZORPAY API HELPER FUNCTIONS -----------------
def create_razorpay_payment_link(order_id, amount_in_inr, customer_name, customer_phone):
    url = "https://api.razorpay.com/v1/payment_links"
    payload = {
        "amount": amount_in_inr * 100,
        "currency": "INR",
        "accept_partial": False,
        "description": "EduGuide Career Guidance PDF + Lucky Draw Coupon",
        "customer": {
            "name": customer_name,
            "contact": customer_phone
        },
        "notify": {"sms": False, "email": False},
        "reminder_enable": False,
        "notes": {"order_id": order_id}
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

    pdf.info_card(
        "આ માર્ગદર્શિકા કોના માટે છે અને તમારો લકી ડ્રો કૂપન:",
        f"ધોરણ ૧૦ કે ૧૨ પાસ કરેલ વિદ્યાર્થીઓ માટે ખાસ માર્ગદર્શિકા.\n🎟 તમારો યુનિક કૂપન નંબર: {coupon_code} (લકી ડ્રો માટે સાચવી રાખો)"
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
        await query.message.reply_text(get_text(user_id, 'pay_text'), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

    elif data.startswith("check_"):
        parts = data.split("_", 1)
        if len(parts) < 2:
            await query.message.reply_text("⚠️ ઓર્ડરની માહિતી મળી નથી.")
            return
            
        order_id = parts[1]
        
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT razorpay_link_id, status FROM orders WHERE order_id = %s AND user_id = %s", (order_id, user_id))
        order_res = cur.fetchone()
        
        if not order_res:
            cur.execute("SELECT order_id, razorpay_link_id, status FROM orders WHERE user_id = %s AND status != 'paid' ORDER BY order_id DESC LIMIT 1", (user_id,))
            latest_order = cur.fetchone()
            if latest_order:
                order_id = latest_order[0]
                link_id = latest_order[1]
                current_status = latest_order[2]
            else:
                cur.close()
                conn.close()
                await query.message.reply_text("⚠️ ઓર્ડરની માહિતી મળી નથી. કૃપા કરીને 'ગાઇડ ખરીદો' દબાવી નવી લિંક બનાવો.")
                return
        else:
            link_id, current_status = order_res[0], order_res[1]

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
        
        user_lang = get_user_lang(user_id)
        share_text = LANG[user_lang]['share_msg']
        encoded_share_url = f"https://t.me/share/url?url={ref_link}&text={requests.utils.quote(share_text)}"
        
        keyboard = [[InlineKeyboardButton(get_text(user_id, 'btn_share'), url=encoded_share_url)]]
        
        await query.message.reply_text(get_text(user_id, 'ref_text').format(ref_link), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def main():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(language_selection, pattern="^lang_"))
    app.add_handler(CallbackQueryHandler(button_router, pattern="^(buy_pdf|check_|my_wallet|withdraw_req|my_coupons|refer_earn)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running successfully with all finalized features...")
    app.run_polling()

if __name__ == '__main__':
    main()
