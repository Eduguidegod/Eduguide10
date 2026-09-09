import os
import sqlite3 # Keep as fallback if needed, but using psycopg2 for Postgres
import psycopg2
from psycopg2.extras import RealDictCursor
import threading
import random
import hashlib
import hmac
import json
from io import BytesIO

import telebot
from telebot import types
import razorpay

from flask import Flask, request
from fpdf import FPDF
from fpdf.enums import XPos, YPos


# ============================================================
# CONFIGURATION (SECURE - ENVIRONMENT VARIABLES)
# ============================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN")
RAZORPAY_KEY_ID = os.environ.get("RAZORPAY_KEY_ID")
RAZORPAY_KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET")
RAZORPAY_WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "")
RENDER_URL = os.environ.get("RENDER_URL")
DATABASE_URL = os.environ.get("DATABASE_URL") # PostgreSQL URL from Render

bot = telebot.TeleBot(BOT_TOKEN)
razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

app = Flask(__name__)


# ============================================================
# DATABASE SETUP (POSTGRESQL)
# ============================================================

def get_db_connection():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        raise Exception("DATABASE_URL environment variable is not set!")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create orders table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY,
            user_id BIGINT,
            amount INTEGER,
            status TEXT,
            payment_link_id TEXT
        )
    """)
    
    # Create coupons table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            id SERIAL PRIMARY KEY,
            coupon_code TEXT UNIQUE,
            user_id BIGINT,
            order_id TEXT,
            payment_link_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    conn.commit()
    cursor.close()
    conn.close()

# Initialize Database on startup
try:
    init_db()
    print("PostgreSQL Database initialized successfully.")
except Exception as e:
    print(f"Database initialization error: {e}")


# ============================================================
# PROFESSIONAL PDF (FIXED TEXT SHAPING & JOINING)
# ============================================================

class ProfessionalPDF(FPDF):
    def __init__(self):
        super().__init__(orientation="P", unit="mm", format="A4")
        self.set_margins(15, 15, 15)
        self.set_auto_page_break(auto=True, margin=15)

        try:
            self.set_text_shaping(True)
        except Exception:
            pass

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
        self.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=radius)

    def footer(self):
        self.set_y(-15)
        self.set_font("Gujarati", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"પાનું {self.page_no()}", align="C")

    def info_card(self, title, text):
        self.ln(3)
        start_x = self.get_x()
        start_y = self.get_y()

        self.set_fill_color(*self.LIGHT_BG)
        self.set_draw_color(200, 210, 230)
        self.set_line_width(0.2)
        self.rect(start_x, start_y, self.CONTENT_W, 27, style="DF", round_corners=True, corner_radius=2)

        self.set_xy(start_x + 4, start_y + 3)
        self.set_font("Gujarati", "B", 10.5)
        self.set_text_color(*self.NAVY)
        self.cell(self.CONTENT_W - 8, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        self.set_xy(start_x + 4, start_y + 10)
        self.set_font("Gujarati", "", 9.2)
        self.set_text_color(*self.GRAY)
        self.multi_cell(self.CONTENT_W - 8, 5, text)
        self.set_y(start_y + 30)

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
        self.rect(self.MARGIN, start_y, self.CONTENT_W, height, style="D", round_corners=True, corner_radius=2)

        self.set_xy(self.MARGIN + 4, start_y + 3)
        self.set_font("Gujarati", "B", 11)
        self.set_text_color(*self.NAVY)
        self.cell(self.CONTENT_W - 8, 6, title, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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


# ============================================================
# GENERATE PDF (FULL & FIXED)
# ============================================================

def generate_career_pdf():
    pdf = ProfessionalPDF()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(base_dir, "Gujarati.ttf")

    if not os.path.exists(font_path):
        print("ERROR: Gujarati.ttf not found")
        return None

    try:
        pdf.add_font("Gujarati", "", font_path)
        pdf.add_font("Gujarati", "B", font_path)
    except Exception as e:
        print("Font Error:", repr(e))
        return None

    pdf.add_page()

    x = pdf.MARGIN
    y = pdf.get_y()
    w = pdf.CONTENT_W
    h = 35

    pdf.rounded_box(x, y, w, h, pdf.NAVY, pdf.NAVY, 3)

    pdf.set_xy(x + 5, y + 6)
    pdf.set_font("Gujarati", "B", 15)
    pdf.set_text_color(*pdf.WHITE)
    pdf.multi_cell(
        w - 10,
        7,
        "સંપૂર્ણ કારકિર્દી માર્ગદર્શિકા અને સરકારી નોકરી રોડમેપ",
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT
    )

    pdf.set_font("Gujarati", "", 9)
    pdf.set_text_color(225, 232, 249)
    pdf.multi_cell(
        w - 10,
        5.3,
        "ધોરણ ૧૦ અને ૧૨ પછી શ્રેષ્ઠ પ્રવાહ પસંદગી, ઉચ્ચ અભ્યાસ અને સ્પર્ધાત્મક પરીક્ષાઓની A to Z માર્ગદર્શિકા",
        align="C",
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT
    )

    pill_y = y + 25.5
    labels = ["વિશેષ ડિજિટલ એડિશન", "ગુજરાત & કેન્દ્ર સરકાર ભરતી વિશેષ"]
    pill_widths = [33, 47]
    total = sum(pill_widths) + 3
    px = x + (w - total) / 2

    for label, pw in zip(labels, pill_widths):
        pdf.rounded_box(px, pill_y, pw, 6.5, pdf.GOLD, pdf.GOLD, 3)
        pdf.set_xy(px, pill_y + 1)
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
    pdf.multi_cell(
        0,
        5,
        "ધોરણ ૧૦ પાસ કર્યા પછી વિદ્યાર્થીના જીવનનો સૌથી મહત્વનો વળાંક આવે છે. મોટાભાગના વિદ્યાર્થીઓ મિત્રો કે પરિવારના દબાણમાં આવીને પ્રવાહ પસંદ કરતા હોય છે. પ્રવાહ પસંદ કરતી વખતે નીચેના ૩ મુદ્દા ધ્યાનમાં રાખો:"
    )
    pdf.ln(2)

    pdf.bullet(
        "પોતાનો રસ અને ક્ષમતા: ",
        "ગણિત અને વિજ્ઞાનમાં સાચી રુચિ હોય તો સાયન્સ, ગણતરી અને વેપાર/નાણાંમાં રુચિ હોય તો કૉમર્સ, અને વાંચન, ભાષા, ઇતિહાસ કે વહીવટમાં રુચિ હોય તો આર્ટ્સ પસંદ કરવું જોઈએ."
    )
    pdf.bullet(
        "ભવિષ્યનું લક્ષ્ય: ",
        "જો ડોક્ટર કે એન્જિનિયર બનવું હોય તો સાયન્સ જરૂરી છે. જો CA, બેંક ઓફિસર કે બિઝનેસ કરવો હોય તો કૉમર્સ શ્રેષ્ઠ છે. અને જો પોલીસ, તલાટી, ક્લાર્ક કે સિવિલ સર્વિસીસમાં જવું હોય તો આર્ટ્સ ઉપયોગી રહે છે."
    )
    pdf.bullet(
        "સમય અને નાણાકીય રોકાણ: ",
        "સાયન્સમાં ટ્યુશન અને આગળના અભ્યાસનો ખર્ચ વધુ હોઈ શકે છે, જ્યારે આર્ટ્સ અને કૉમર્સમાં પ્રમાણમાં ઓછો ખર્ચ થાય છે."
    )

    pdf.section_title("૨", "પ્રવાહવાર સંપૂર્ણ વિશ્લેષણ (Science, Commerce, Arts)")

    pdf.stream_card(
        "સાયન્સ પ્રવાહ (Science Stream)",
        [
            ("", "સાયન્સ પ્રવાહમાં બે મુખ્ય ગ્રુપ હોય છે: ગ્રુપ-A (ગણિત) અને ગ્રુપ-B (બાયોલોજી)."),
            ("ગ્રુપ-A પછીના વિકલ્પો: ", "B.E. / B.Tech (કમ્પ્યુટર, મિકેનિકલ, સિવિલ, ઇલેક્ટ્રિકલ), આર્કિટેક્ચર, મર્ચન્ટ નેવી, NDA (એરફોર્સ/નેવી), B.Sc. IT/CS, ડેટા સાયન્સ."),
            ("ગ્રુપ-B પછીના વિકલ્પો: ", "MBBS, BDS, BAMS (આયુર્વેદ), BHMS (હોમિયોપેથી), નર્સિંગ (B.Sc Nursing), ફિઝિયોથેરાપી (BPT), ફાર્મસી (B.Pharm), એગ્રીકલ્ચર (B.Sc Agriculture)."),
            ("લાભ: ", "ટેક્નિકલ અને મેડિકલ ક્ષેત્રે ઊંચી આવકની તકો તેમજ સાયન્સ પછી અન્ય કોઈપણ ફિલ્ડમાં જવાની છૂટછાટ મળે છે.")
        ],
        height=52
    )

    pdf.stream_card(
        "કૉમર્સ પ્રવાહ (Commerce Stream)",
        [
            ("", "નાણાકીય વ્યવહારો, બેંકિંગ, એકાઉન્ટિંગ અને વેપાર-વાણિજ્યમાં રુચિ ધરાવતા વિદ્યાર્થીઓ માટે કૉમર્સ શ્રેષ્ઠ વિકલ્પ છે."),
            ("મુખ્ય ડિગ્રી કોર્સ: ", "B.Com, BBA, BCA (કમ્પ્યુટર એપ્લિકેશન), BMS, B.Voc."),
            ("પ્રોફેશનલ કોર્સ: ", "CA (ચાર્ટર્ડ એકાઉન્ટન્ટ), CS (કંપની સેક્રેટરી), CMA (કોસ્ટ મેનેજમેન્ટ એકાઉન્ટન્ટ), CFA (ફાઇનાન્શિયલ એનાલિસ્ટ)."),
            ("", "કેરિયર ક્ષેત્રો: બેંકિંગ ક્ષેત્ર (PO, ક્લાર્ક), વીમા કંપનીઓ, ઇન્વેસ્ટમેન્ટ ફર્મ્સ, શેરબજાર, ટેક્સ કન્સલ્ટન્સી અને પોતાના સ્વતંત્ર બિઝનેસમાં ઉત્તમ તકો.")
        ],
        height=50
    )

    pdf.stream_card(
        "આર્ટ્સ પ્રવાહ (Arts / Humanities)",
        [
            ("", "આર્ટ્સ એ સ્પર્ધાત્મક પરીક્ષાઓ અને સરકારી નોકરીઓ માટે ઉપયોગી પ્રવાહ છે."),
            ("મુખ્ય વિષયો: ", "ઇતિહાસ, ભૂગોળ, બંધારણ (રાજ્યશાસ્ત્ર), સમાજશાસ્ત્ર, મનોવિજ્ઞાન અને અર્થશાસ્ત્ર."),
            ("મુખ્ય ડિગ્રીઓ: ", "B.A., B.S.W. (સોશિયલ વર્ક), B.J.M.C. (પત્રકારત્વ), B.Ed. (શિક્ષક માટે), LL.B. (વકીલાત)."),
            ("વિશેષ ફાયદો: ", "ઘણી સરકારી સ્પર્ધાત્મક પરીક્ષાઓમાં સામાન્ય જ્ઞાન, ઇતિહાસ, ભૂગોળ, બંધારણ અને અર્થતંત્ર જેવા વિષયો મહત્વપૂર્ણ હોય છે.")
        ],
        height=48
    )

    pdf.add_page()

    pdf.section_title("૩", "ડિપ્લોમા અને ITI (ધોરણ ૧૦ પછી સીધા ટેકનિકલ કોર્સ)")
    pdf.set_font("Gujarati", "B", 10)
    pdf.set_text_color(*pdf.NAVY)
    pdf.cell(0, 6, "ડિપ્લોમા એન્જિનિયરિંગ (૩ વર્ષ)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.bullet("", "મેકેનિકલ, સિવિલ, ઇલેક્ટ્રિકલ, કમ્પ્યુટર, ઓટોમોબાઇલ.")
    pdf.bullet("", "ડિપ્લોમા પછી યોગ્ય નિયમો અને પ્રવેશ પ્રક્રિયા મુજબ ડિગ્રીના બીજા વર્ષમાં પ્રવેશ (D2D) મેળવી શકાય છે.")
    pdf.bullet("", "રેલવે, વીજળી ક્ષેત્ર અને અન્ય ટેકનિકલ સંસ્થાઓમાં લાયકાત અનુસાર વિવિધ ભરતીની તકો મળે છે.")
    pdf.ln(3)

    pdf.set_font("Gujarati", "B", 10)
    pdf.set_text_color(*pdf.NAVY)
    pdf.cell(0, 6, "ITI વ્યવસાયિક કોર્સ (૧ થી ૨ વર્ષ)", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.bullet("", "ઇલેક્ટ્રિશિયન, ફિટર, વાયરમેન, ડીઝલ મિકેનિક, COPA.")
    pdf.bullet("", "ટૂંકા ગાળામાં ટેકનિકલ નોકરી અથવા સ્વરોજગાર શરૂ કરવાની તક.")
    pdf.bullet("", "રેલવે, ટેકનિકલ વિભાગો અને અન્ય સરકારી ભરતીમાં લાયકાત અનુસાર તકો ઉપલબ્ધ થઈ શકે છે.")
    pdf.ln(5)

    pdf.section_title("૪", "ગુજરાત રાજ્ય સરકારની મુખ્ય ભરતીઓ")
    pdf.set_font("Gujarati", "", 9)

    recruitment_data = [
        ("પોલીસ કોન્સ્ટેબલ / LRD", "ધોરણ ૧૨ પાસ", "જાહેરાત મુજબ", "શારીરિક કસોટી + લેખિત પરીક્ષા"),
        ("વનરક્ષક", "ધોરણ ૧૨ પાસ", "જાહેરાત મુજબ", "CBRT / લેખિત + ફિઝિકલ"),
        ("તલાટી કમ મંત્રી", "જાહેરાત મુજબ", "જાહેરાત મુજબ", "સ્પર્ધાત્મક પરીક્ષા"),
        ("જુનિયર ક્લાર્ક", "જાહેરાત મુજબ", "જાહેરાત મુજબ", "CBRT / સ્પર્ધાત્મક પરીક્ષા"),
        ("હાઈકોર્ટ પટાવાળા / બેલિફ", "જાહેરાત મુજબ", "જાહેરાત મુજબ", "લેખિત / અન્ય પ્રક્રિયા"),
        ("PSI", "ગ્રેજ્યુએટ", "જાહેરાત મુજબ", "ફિઝિકલ + પરીક્ષા")
    ]

    for item in recruitment_data:
        title, qualification, age, selection = item
        pdf.set_font("Gujarati", "B", 9.2)
        pdf.set_text_color(*pdf.NAVY)
        pdf.cell(48, 6, title)
        pdf.set_font("Gujarati", "", 8.5)
        pdf.set_text_color(*pdf.GRAY)
        pdf.cell(38, 6, qualification)
        pdf.cell(30, 6, age)
        pdf.multi_cell(64, 6, selection)
        pdf.ln(1)

    pdf.section_title("૫", "કેન્દ્ર સરકારની મુખ્ય નોકરીઓની તકો")
    central_data = [
        ("SSC GD કોન્સ્ટેબલ", "૧૦ પાસ", "BSF, CISF, CRPF વગેરે", "કેન્દ્રીય સુરક્ષા દળોમાં તક"),
        ("SSC CHSL", "૧૨ પાસ", "LDC, JSA, DEO", "કેન્દ્રીય કચેરીઓમાં તક"),
        ("રેલવે", "૧૦ / ITI / અન્ય", "ટેક્નિશિયન વગેરે", "રેલવે ક્ષેત્રમાં તક"),
        ("ઇન્ડિયન આર્મી / નેવી", "જાહેરાત મુજબ", "વિવિધ પદો", "સંરક્ષણ ક્ષેત્રમાં કારકિર્દી"),
        ("કોસ્ટ ગાર્ડ", "જાહેરાત મુજબ", "નાવિક વગેરે", "સમુદ્ર સુરક્ષા ક્ષેત્ર")
    ]

    for item in central_data:
        title, qualification, posts, benefit = item
        pdf.set_font("Gujarati", "B", 9)
        pdf.set_text_color(*pdf.NAVY)
        pdf.cell(43, 6, title)
        pdf.set_font("Gujarati", "", 8.5)
        pdf.set_text_color(*pdf.GRAY)
        pdf.cell(30, 6, qualification)
        pdf.cell(43, 6, posts)
        pdf.multi_cell(64, 6, benefit)
        pdf.ln(1)

    pdf.section_title("૬", "સ્પર્ધાત્મક પરીક્ષાઓની તૈયારી માટે સ્માર્ટ રણનીતિ")
    pdf.bullet("1. સિલેબસ અને જૂના પેપર્સ: ", "સૌપ્રથમ જે પરીક્ષા આપવી હોય તેનો સત્તાવાર સિલેબસ મેળવો અને ઉપલબ્ધ જૂના પેપર સોલ્વ કરો.")
    pdf.bullet("2. GCERT / NCERT પુસ્તકો: ", "ધોરણ ૬ થી ૧૦ ના સામાજિક વિજ્ઞાન, વિજ્ઞાન અને ગણિતના પાઠ્યપુસ્તકો પાયો મજબૂત કરવા માટે ઉપયોગી છે.")
    pdf.bullet("3. ડેઇલી કરંટ અફેર્સ: ", "રોજના અખબારો અને વિશ્વસનીય વર્તમાન પ્રવાહોની નિયમિત નોંધ રાખવાની ટેવ પાડો.")
    pdf.bullet("4. ગણિત અને રિઝનિંગની પ્રેક્ટિસ: ", "રોજ નિયમિત પ્રશ્નોની પ્રેક્ટિસ કરો જેથી ઝડપ અને ચોકસાઈ વધે.")
    pdf.bullet("5. નિયમિત મોક ટેસ્ટ: ", "અઠવાડિયે ઓછામાં ઓછી એક મોક ટેસ્ટ આપો અને પોતાની ભૂલોનું વિશ્લેષણ કરો.")

    pdf.add_page()
    pdf.rounded_box(20, 70, 170, 85, pdf.NAVY, pdf.NAVY, 5)
    pdf.set_xy(30, 85)
    pdf.set_font("Gujarati", "B", 18)
    pdf.set_text_color(*pdf.WHITE)
    pdf.multi_cell(150, 10, "તમારી કારકિર્દી,\nતમારો નિર્ણય,\nતમારું ભવિષ્ય!", align="C")

    pdf.set_xy(30, 125)
    pdf.set_font("Gujarati", "", 10)
    pdf.set_text_color(225, 232, 249)
    pdf.multi_cell(150, 6, "યોગ્ય માહિતી મેળવો, સત્તાવાર ભરતી જાહેરાતો તપાસો અને સતત તૈયારી કરતા રહો.", align="C")

    try:
        pdf_bytes = pdf.output()
        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode("latin1")
    except TypeError:
        pdf_bytes = pdf.output(dest="S").encode("latin1")

    buffer = BytesIO(pdf_bytes)
    buffer.name = "Career_Guidance_Roadmap.pdf"
    buffer.seek(0)
    return buffer


# ============================================================
# TELEGRAM BOT HANDLERS & FLASK WEBHOOK
# ============================================================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    markup = types.InlineKeyboardMarkup()
    btn_buy = types.InlineKeyboardButton("💳 પેમેન્ટ કરો (Pay ₹50)", callback_data="buy_pdf")
    btn_my_coupon = types.InlineKeyboardButton("🎟 માય કુપન (My Coupons)", callback_data="my_coupons")
    markup.add(btn_buy)
    markup.add(btn_my_coupon)
    
    welcome_text = (
        "નમસ્કાર! કારકિર્દી માર્ગદર્શિકા અને સરકારી નોકરી રોડમેપ બોટમાં આપનું સ્વાગત છે.\n\n"
        "આ ડિજિટલ માર્ગદર્શિકા મેળવવા માટે નીચેના બટન પર ક્લિક કરો."
    )
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data == "buy_pdf")
def handle_buy_pdf(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    try:
        # Create Razorpay Payment Link
        payment_link = razorpay_client.payment_link.create({
            "amount": 5000,  # ₹50.00 in paise
            "currency": "INR",
            "accept_partial": False,
            "description": "Career Guidance Roadmap PDF & Coupon",
            "customer": {
                "name": str(call.from_user.first_name or "User"),
                "email": "user@example.com",
                "contact": "9999999999"
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "callback_url": f"{RENDER_URL}/success",
            "callback_method": "get"
        })
        
        order_id = payment_link.get("id")
        short_url = payment_link.get("short_url")
        
        # Save order to PostgreSQL using %s placeholder
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO orders (order_id, user_id, amount, status, payment_link_id) VALUES (%s, %s, %s, %s, %s)",
            (order_id, user_id, 50, "created", order_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔗 પેમેન્ટ લિંક ખોલો", url=short_url))
        markup.add(types.InlineKeyboardButton("🔄 પેમેન્ટ સ્ટેટસ તપાસો", callback_data=f"check_pay_{order_id}"))
        
        bot.send_message(chat_id, "તમારી પેમેન્ટ લિંક તૈયાર છે. નીચેના બટનથી ચૂકવણી કરો:", reply_markup=markup)
        
    except Exception as e:
        print(f"Payment Link Error: {e}")
        bot.send_message(chat_id, "પેમેન્ટ લિંક બનાવવામાં ભૂલ થઈ છે. કૃપા કરીને થોડીવાર પછી પ્રયત્ન કરો.")


@bot.callback_query_handler(func=lambda call: call.data.startswith("check_pay_"))
def handle_check_payment(call):
    order_id = call.split("_")[2] if len(call.data.split("_")) > 2 else call.data.replace("check_pay_", "")
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
        order = cursor.fetchone()
        
        if not order:
            bot.answer_callback_query(call.id, "ઓર્ડર મળ્યો નથી.")
            cursor.close()
            conn.close()
            return
            
        if order['status'] == 'paid':
            bot.answer_callback_query(call.id, "પેમેન્ટ પહેલેથી જ થઈ ગયું છે!")
            send_pdf_and_coupon(chat_id, user_id, order_id)
            cursor.close()
            conn.close()
            return
            
        # Fetch from Razorpay
        link_info = razorpay_client.payment_link.fetch(order_id)
        status = link_info.get("status")
        
        if status == "paid":
            cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", ("paid", order_id))
            conn.commit()
            cursor.close()
            conn.close()
            
            bot.answer_callback_query(call.id, "પેમેન્ટ સફળ થઈ ગયું છે!")
            send_pdf_and_coupon(chat_id, user_id, order_id)
        else:
            bot.answer_callback_query(call.id, "પેમેન્ટ હજુ સુધી પ્રાપ્ત થયું નથી.", show_alert=True)
            cursor.close()
            conn.close()
            
    except Exception as e:
        print(f"Check Payment Error: {e}")
        bot.answer_callback_query(call.id, "સ્ટેટસ તપાસવામાં ભૂલ થઈ.", show_alert=True)


@bot.callback_query_handler(func=lambda call: call.data == "my_coupons")
def handle_my_coupons(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT coupon_code, created_at FROM coupons WHERE user_id = %s", (user_id,))
        coupons = cursor.fetchall()
        cursor.close()
        conn.close()
        
        if not coupons:
            bot.send_message(chat_id, "તમારી પાસે હજુ સુધી કોઈ કુપન ઉપલબ્ધ નથી.")
            return
            
        text = "🎟 **તમારા ખરીદેલા કુપન નંબરો:**\n\n"
        for c in coupons:
            text += f"• `{c['coupon_code']}` (તારીખ: {c['created_at']})\n"
            
        bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception as e:
        print(f"My Coupons Error: {e}")
        bot.send_message(chat_id, "કુપન લોડ કરવામાં સમસ્યા આવી.")


def send_pdf_and_coupon(chat_id, user_id, order_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Check if coupon already generated for this order
        cursor.execute("SELECT coupon_code FROM coupons WHERE order_id = %s", (order_id,))
        existing = cursor.fetchone()
        
        if existing:
            coupon_code = existing['coupon_code']
        else:
            coupon_code = f"EDU-{random.randint(100000, 999999)}"
            cursor.execute(
                "INSERT INTO coupons (coupon_code, user_id, order_id) VALUES (%s, %s, %s)",
                (coupon_code, user_id, order_id)
            )
            conn.commit()
            
        cursor.close()
        conn.close()
        
        # Generate PDF
        pdf_buffer = generate_career_pdf()
        if pdf_buffer:
            bot.send_document(
                chat_id,
                pdf_buffer,
                caption=f"অভિનંદન! તમારી કારકિર્દી માર્ગદર્શિકા PDF અહીં છે.\n\n🎟 તમારો યુનિક કુપન નંબર: `{coupon_code}`",
                parse_mode="Markdown"
            )
        else:
            bot.send_message(chat_id, f"પેમેન્ટ સફળ થયું! તમારો કુપન નંબર: `{coupon_code}`", parse_mode="Markdown")
            
    except Exception as e:
        print(f"Send PDF Error: {e}")


@app.route('/razorpay/webhook', methods=['POST'])
def razorpay_webhook():
    webhook_signature = request.headers.get('X-Razorpay-Signature', '')
    webhook_body = request.data
    
    if RAZORPAY_WEBHOOK_SECRET:
        generated_signature = hmac.new(
            RAZORPAY_WEBHOOK_SECRET.encode('utf-8'),
            webhook_body,
            hashlib.sha256
        ).hexdigest()
        
        if not hmac.compare_digest(generated_signature, webhook_signature):
            return "Invalid Signature", 400
            
    data = request.json
    event = data.get("event")
    
    if event == "payment_link.paid":
        payload = data.get("payload", {})
        payment_link_entity = payload.get("payment_link", {}).get("entity", {})
        order_id = payment_link_entity.get("id")
        
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM orders WHERE order_id = %s", (order_id,))
            order = cursor.fetchone()
            
            if order and order['status'] != 'paid':
                cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", ("paid", order_id))
                conn.commit()
                
                user_id = order['user_id']
                send_pdf_and_coupon(user_id, user_id, order_id)
                
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Webhook DB Error: {e}")
            
    return "OK", 200


@app.route('/success', methods=['GET'])
def payment_success():
    return "<h3>પેમેન્ટ સફળ થઈ ગયું છે! તમે હવે ટેલિગ્રામ બોટ પર પાછા જઈ શકો છો.</h3>"


# Run Flask in a separate thread
def run_flask():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

if __name__ == "__main__":
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    print("Telegram Bot is running with PostgreSQL support...")
    bot.infinity_polling()
