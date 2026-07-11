import os
import uuid
import logging
from logging.handlers import RotatingFileHandler
import re
import time
import pandas as pd
import telebot
import arabic_reshaper
import datetime as dt_module
from hijri_converter import Gregorian, Hijri
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from bidi.algorithm import get_display
from dotenv import load_dotenv
from arabic_reshaper import reshape
from functools import wraps
from flask import Flask
from threading import Thread

# جلب التاريخ الحالي وتاريخ الغد تلقائياً وتنسيقهما
today = dt_module.date.today()
tomorrow = today + dt_module.timedelta(days=1)

today_str = today.strftime('%Y/%m/%d')
tomorrow_str = tomorrow.strftime('%Y/%m/%d')

def parse_user_date(date_text):
    try:
        return dt_module.datetime.strptime(date_text.strip(), "%d/%m/%Y")
    except ValueError:
        try:
            return dt_module.datetime.strptime(date_text.strip(), "%Y-%m-%d")
        except ValueError:
            return None

# الفترات الزمنية المعتمدة للأعوام الهجرية الكاملة
start_1447 = dt_module.datetime(2025, 6, 26)
end_1447 = dt_module.datetime(2026, 6, 16)
start_1448 = dt_module.datetime(2026, 6, 17)
end_1448 = dt_module.datetime(today.year, today.month, today.day)

# تسجيل الخطوط والأوزان لدعم الـ PDF باللغة العربية
pdfmetrics.registerFont(TTFont('IBM-Plex-Regular', 'IBMPlexSansArabic-Regular.ttf'))
pdfmetrics.registerFont(TTFont('IBM-Plex-Bold', 'IBMPlexSansArabic-Bold.ttf'))
pdfmetrics.registerFontFamily('IBM-Plex', normal='IBM-Plex-Regular', bold='IBM-Plex-Bold')

FONT_NAME = 'IBM-Plex-Regular' 
FONT_NAME_BOLD = 'IBM-Plex-Bold'

FONT_FILE = "NotoNaskhArabic.ttf"
if os.path.exists(FONT_FILE):
    try:
        pdfmetrics.registerFont(TTFont("ArabicFont", FONT_FILE))
        FONT_NAME = "ArabicFont"
    except Exception:
        pass

# إعداد نظام تسجيل الأخطاء (Logging)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
log_handler = RotatingFileHandler("court_bot.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")
log_handler.setFormatter(log_formatter)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.addHandler(log_handler)
logger.addHandler(console_handler)

load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    logging.critical("❌ لم يتم العثور على BOT_TOKEN في ملف .env!")
    raise ValueError("Missing BOT_TOKEN")

bot = telebot.TeleBot(TOKEN)
DATA_FILE = "all_court_data.xlsx"
# =====================================================================
# --- خادم الويب المصغر (Flask) المتوافق مع شروط بيئة Render ---
# =====================================================================
app = Flask('')

@app.route('/')
def home():
    return "I am alive!"

def run():
    # Render تلقائياً تضع رقم المنفذ في المتغير PORT
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# تشغيل السيرفر الوهمي في مسار جانبي حتى لا يعطل برنامجك الأساسي
def keep_alive():
    t = Thread(target=run)
    t.start()

# استدعاء الدالة في بداية تشغيل الملف
keep_alive()

# =====================================================================
# 🔐 نظام إدارة الصلاحيات المطور عبر التحقق من رقم الهاتف (Live Security Gate)
# =====================================================================
VERIFIED_USERS = set()                  # لتخزين معرفات الـ Chat IDs المقبولة أثناء التشغيل
PHONE_BOOK_FILE = "allowed_phones.txt"  # قاعدة بيانات الأرقام المصرح لها

def normalize_yemeni_phone(phone):
    """توحيد صيغة رقم الهاتف اليمني لمنع تلاعب المسافات أو الرموز الدولية"""
    p = str(phone).strip().replace("+", "")
    if p.startswith("00967"):
        p = p[5:]
    elif p.startswith("967"):
        p = p[3:]
    if p.startswith("0") and len(p) == 10:
        p = p[1:]
    return p

def is_phone_authorized(phone):
    """الفحص الذكي: التأكد من وجود الرقم داخل ملف allowed_phones.txt"""
    if not os.path.exists(PHONE_BOOK_FILE):
        with open(PHONE_BOOK_FILE, "w", encoding="utf-8") as f:
            f.write("# أضف الأرقام المصرح لها هنا، رقم واحد في كل سطر\n")
            f.write("777914645\n") # رقم تجريبي افتراضي
        return False

    phone = normalize_yemeni_phone(phone)
    with open(PHONE_BOOK_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if normalize_yemeni_phone(line) == phone:
                return True
    return False

def phone_gatekeeper(chat_id):
    """عرض زر مشاركة جهة الاتصال لإجبار الحساب على إرسال الرقم الفعلي المربوط بالتليجرام"""
    markup = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    markup.add(KeyboardButton("📱 مشاركة رقم الهاتف لغرض التحقق", request_contact=True))
    
    msg_text = (
        "⚠️ **نظام الأمان والخصوصية القضائي**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "يتطلب الوصول إلى هذا النظام والبيانات الحساسة التحقق من هوية رقم الهاتف أولاً.\n\n"
        "👇 يرجى الضغط على الزر أدناه لمشاركة رقمك وتفعيل حسابك تلقائياً:"
    )
    bot.send_message(chat_id, msg_text, reply_markup=markup, parse_mode="Markdown")

def restricted_access():
    """مغلف أمني (Decorator) للتحقق من صلاحيات المشترك قبل معالجة أي رسائل أو أزرار"""
    def decorator(func):
        @wraps(func)
        def wrapped(message_or_call, *args, **kwargs):
            if hasattr(message_or_call, 'chat'):
                chat_id = message_or_call.chat.id
                is_callback = False
            else:
                chat_id = message_or_call.message.chat.id
                is_callback = True

            # إذا لم يتم التحقق من المستخدم بعد، يتم إيقافه وتوجيهه لبوابة التفعيل بالهاتف
            if chat_id not in VERIFIED_USERS:
                if is_callback:
                    try: bot.answer_callback_query(message_or_call.id, text="⚠️ يرجى تفعيل الحساب برقم الهاتف أولاً!", show_alert=True)
                    except Exception: pass
                phone_gatekeeper(chat_id)
                return
            return func(message_or_call, *args, **kwargs)
        return wrapped
    return decorator

# استقبال جهة الاتصال ومطابقتها بالقائمة البيضاء المصرح لها
@bot.message_handler(content_types=["contact"])
def verify_contact(message):
    chat_id = message.chat.id
    
    # حماية ضد تزوير جهات الاتصال (التأكد من أن الرقم يخص صاحب الحساب الفعلي)
    if message.contact.user_id != chat_id:
        bot.send_message(chat_id, "❌ **خطأ أمني:** يجب مشاركة رقمك الشخصي الفعلي عبر الضغط على الزر المخصص.")
        return

    phone = message.contact.phone_number
    if is_phone_authorized(phone):
        VERIFIED_USERS.add(chat_id) # تفعيل الصلاحية الفورية في الذاكرة المؤقتة
        bot.send_message(
            chat_id,
            "✅ **تم التحقق من صلاحية رقمك بنجاح ونظام الأمان مفعل!**",
            reply_markup=ReplyKeyboardRemove() # إخفاء زر الهاتف التفاعلي
        )
        # توجيهه مباشرة لعرض القائمة الرئيسية الفخمة للبوت بعد النجاح
        show_welcome_dashboard(chat_id)
    else:
        bot.send_message(
            chat_id,
            "❌ **وصول مرفوض!**\n"
            "رقم الهاتف الخاص بك غير مسجل في دليل الصلاحيات المعتمد للمحكمة.\n"
            "يرجى مراجعة مختص الدعم الفني بالمحكمة لتفعيل رقمك *777914645."
        )
# =====================================================================

# أسماء الصفحات المعتمدة في ملف الاكسيل
SESSIONS_SHEET = "Sheet1"
FINISHED_SHEET = "Sheet1 (2)"
PENDING_SHEET = "Sheet1 (3)"

REQUIRED_COLUMNS = [
    "الجلسة القادمة", "آخر جلسة", "قرار الجلسة", "برئاسة القاضي",
    "المدعى عليه", "المدعي", "تاريخ ورودها", "موضوعها", "نوعها",
    "رقم القضية", "مسلسل"
]

center_style = ParagraphStyle("center_std", fontName=FONT_NAME, fontSize=9, alignment=1, leading=13)
right_style = ParagraphStyle("right_std", fontName=FONT_NAME, fontSize=9, alignment=2, leading=14)
left_style = ParagraphStyle("left_std", fontName=FONT_NAME, fontSize=9, alignment=0, leading=12)
title_style_h1 = ParagraphStyle("h1_court", fontName=FONT_NAME, fontSize=15, alignment=1, leading=22)
title_style_h2 = ParagraphStyle("h2_court", fontName=FONT_NAME, fontSize=11, alignment=2, leading=18)

AR_NUMBERS = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
def to_arabic_number(value):
    return str(value).translate(AR_NUMBERS)

def ar(text):
    text = str(text) if text else ""
    text = re.sub(r'(\d{1,2}/\d{1,2}/\d{4})', lambda m: '\u200F' + m.group(1) + '\u200F', text)
    text = re.sub(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})', lambda m: '\u200F' + m.group(1) + '\u200F', text)
    
    lines = text.split('\n')
    processed_lines = [get_display(arabic_reshaper.reshape(line.strip())) for line in lines if line.strip()]
    processed_lines.reverse()
    return '<br/>'.join(processed_lines)

def get_section_boss(section_name):
    name_clean = str(section_name).strip()
    if "مدني" in name_clean: return "عصام محمد شائع"
    elif "جسيم" in name_clean and "غير" not in name_clean: return "علي عبدالله النور"
    elif "غير جسيم" in name_clean: return "مطيع محمد العمري"
    elif "شخصي" in name_clean: return "يحيى علي صالح"
    elif "تجاري" in name_clean or "إداري" in name_clean or "عمالي" in name_clean or "اداري" in name_clean: return "عبدالسلام علي الذاري"
    elif "مستعجل" in name_clean: return "عزيز علي الصباحي"
    else: return "جلال طه العيدروس\n"

def parse_date_input(text):
    clean_text = text.strip().lower()
    if clean_text in ("all", "الكل"):
        return "all", None, None
        
    clean_text = clean_text.replace('/', '-')

    range_match = re.match(r'(\d{4}-\d{1,2}-\d{1,2})\s*-\s*(\d{4}-\d{1,2}-\d{1,2})', clean_text)
    if range_match:
        try: return "range", pd.Timestamp(range_match.group(1)).normalize(), pd.Timestamp(range_match.group(2)).normalize()
        except Exception: pass

    range_match_ar = re.match(r'(\d{1,2}-\d{1,2}-\d{4})\s*-\s*(\d{1,2}-\d{1,2}-\d{4})', clean_text)
    if range_match_ar:
        try:
            start_dt = dt_module.datetime.strptime(range_match_ar.group(1), "%d-%m-%Y")
            end_dt = dt_module.datetime.strptime(range_match_ar.group(2), "%d-%m-%Y")
            return "range", pd.Timestamp(start_dt).normalize(), pd.Timestamp(end_dt).normalize()
        except Exception: pass

    single_match = re.match(r'^(\d{4}-\d{1,2}-\d{1,2})$', clean_text)
    if single_match:
        try: return "single", pd.Timestamp(single_match.group(1)).normalize(), None
        except Exception: pass

    single_match_ar = re.match(r'^(\d{1,2}-\d{1,2}-\d{4})$', clean_text)
    if single_match_ar:
        try:
            single_dt = dt_module.datetime.strptime(single_match_ar.group(1), "%d-%m-%Y")
            return "single", pd.Timestamp(single_dt).normalize(), None
        except Exception: pass
            
    return "invalid", None, None

def is_stagnant_case(case_no):
    if pd.isna(case_no): return False
    match = re.search(r'(?:14)\d{2}', str(case_no))
    if match:
        try:
            year = int(match.group(0))
            if year <= 1445: return True
        except ValueError: pass
    return False

def get_dynamic_hijri_months():
    try:
        today_g = Gregorian.today()
        today_h = today_g.to_hijri()
        current_year = today_h.year
        current_month = today_h.month
        
        if current_month == 1:
            prev_month = 12
            prev_year = current_year - 1
        else:
            prev_month = current_month - 1
            prev_year = current_year
            
        hijri_months_names = {
            1: "محرم", 2: "صفر", 3: "ربيع الأول", 4: "ربيع الآخر", 
            5: "جمادى الأولى", 6: "جمادى الآخرة", 7: "رجب", 8: "شعبان", 9: "رمضان", 10: "شوال", 11: "ذو القعدة", 12: "ذو الحجة"
        }
        
        h_prev_start = Hijri(prev_year, prev_month, 1)
        h_current_start = Hijri(current_year, current_month, 1)
        
        if current_month == 12:
            next_month = 1
            next_year = current_year + 1
        else:
            next_month = current_month + 1
            next_year = current_year
        h_next_start = Hijri(next_year, next_month, 1)
        
        g_prev_start = h_prev_start.to_gregorian()
        g_current_start = h_current_start.to_gregorian()
        g_next_start = h_next_start.to_gregorian()
        
        g_prev_end = g_current_start - dt_module.timedelta(days=1)
        g_current_end = g_next_start - dt_module.timedelta(days=1)
        
        return {
            "prev": {
                "name": f"{hijri_months_names[prev_month]}/{prev_year}هـ",
                "start": g_prev_start.strftime("%Y/%m/%d"),
                "end": g_prev_end.strftime("%Y/%m/%d"),
                "key": f"hijri_prev_{prev_year}_{prev_month}"
            },
            "current": {
                "name": f"{hijri_months_names[current_month]}/{current_year}هـ",
                "start": g_current_start.strftime("%Y/%m/%d"),
                "end": g_current_end.strftime("%Y/%m/%d"),
                "key": f"hijri_curr_{current_year}_{current_month}"
            }
        }
    except Exception as e:
        logger.error(f"خطأ في الحساب الديناميكي للهجري: {e}")
        return {
            "prev": {"name": "الشهر الماضي", "start": "2026/06/17", "end": "2026/07/16", "key": "hijri_prev_fallback"},
            "current": {"name": "الشهر الحالي", "start": "2026/07/17", "end": today.strftime("%Y/%m/%d"), "key": "hijri_curr_fallback"}
        }

def smart_parse_incoming_date(row):
    val = str(row.get("تاريخ ورودها", "")).strip()
    if not val or val == "nan" or val == "-":
        case_no = str(row.get("رقم القضية", ""))
        if "1448" in case_no: return pd.Timestamp("2026-06-20")
        elif "1447" in case_no: return pd.Timestamp("2025-07-01")
        return pd.NaT

    val_clean = val.replace('/', '-')
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S"):
        try: return pd.to_datetime(val_clean, format=fmt)
        except Exception: continue
    t_parsed = pd.to_datetime(val_clean, errors='coerce')
    if pd.isna(t_parsed):
        case_no = str(row.get("رقم القضية", ""))
        if "1448" in case_no: return pd.Timestamp("2026-06-20")
    return t_parsed

def draw_rounded_table_background(canvas_obj, table_obj, x, y, width, height, radius=8):
    canvas_obj.saveState()
    canvas_obj.setStrokeColor(colors.HexColor("#002060"))
    canvas_obj.setLineWidth(1.2)
    canvas_obj.setFillColor(colors.HexColor("#FFFFFF"))
    canvas_obj.roundRect(x, y, width, height, radius, stroke=1, fill=0)
    canvas_obj.restoreState()

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_decorations(self, page_count):
        self.saveState()
        width, height = self._pagesize  
        margin = 15        
        
        self.setStrokeColor(colors.HexColor("#002060"))
        self.setLineWidth(1.5)
        self.roundRect(margin, margin, width - (2 * margin), height - (2 * margin), 20, stroke=1, fill=0)
        if os.path.exists("republic_logo.png"):
            self.drawImage("republic_logo.png", width - 130, height - 110, width=100, height=100, preserveAspectRatio=True, mask='auto')
        if os.path.exists("ministry_logo.png"):
            self.drawImage("ministry_logo.png", (width / 2) + 5, height - 100, width=100, height=100, preserveAspectRatio=True, mask='auto')

        self.setStrokeColor(colors.HexColor("#002060"))
        self.setLineWidth(1)
        self.line(20, 38, width - 20, 38)
        
        self.setFont('IBM-Plex-Regular', 9)
        self.setFillColor(colors.HexColor("#002060"))
        
        report_mode = getattr(self, "report_mode", "sessions")
        if report_mode == "incoming":
            supervisor_text = "رئيس قلم الكتاب أ/ مراد محمد المرادي"
        else:
            supervisor_text = "مشرف النظام الآلي أ/ محمد عبده الأحمدي"
            
        developer_text = "إعداد وتطوير المهندس/ نبيل محمد الزنن"
        
        self.drawString(40, 22, ar(developer_text))
        self.drawRightString(width - 40, 22, ar(supervisor_text))
        
        page_num_ar = to_arabic_number(self._pageNumber)
        total_pages_ar = to_arabic_number(page_count)
        page_string = f"الصفحة {page_num_ar} من {total_pages_ar}"
        self.drawCentredString(width / 2, 22, ar(page_string))
        self.restoreState()

class CourtRepository:
    def __init__(self, filepath):
        self.filepath = filepath
        self.df_sessions = pd.DataFrame()
        self.df_finished = pd.DataFrame()
        self.df_pending = pd.DataFrame()
        self._last_signature = None
        self.reload_data()

    def clean_dataframe(self, df, required_columns, date_column, parsed_name, is_finished_sheet=False):
        if df.empty: return df
        df.columns = df.columns.astype(str).str.replace("\n", "", regex=False).str.replace("\r", "", regex=False).str.strip()
        
        if is_finished_sheet and "آخر جلسة" in df.columns and "قرار الجلسة" in df.columns:
            sample_last = str(df["آخر جلسة"].iloc[0]) if len(df) > 0 else ""
            sample_decision = str(df["قرار الجلسة"].iloc[0]) if len(df) > 0 else ""
            if re.search(r'\d{4}', sample_decision) and not re.search(r'\d{4}', sample_last):
                df["آخر جلسة"], df["قرار الجلسة"] = df["قرار الجلسة"].copy(), df["آخر جلسة"].copy()

        df = df.dropna(how="all")
        df = df.reindex(columns=required_columns)
        if "رقم القضية" in df.columns:
            df = df[df["رقم القضية"].notna() & (df["رقم القضية"].astype(str).str.strip() != "")]
        if df.empty: return df
        
        date_series = df[date_column].copy()
        for col in df.columns:
            if col not in ["الجلسة القادمة", "آخر جلسة", "تاريخ ورودها"]:
                df[col] = df[col].fillna("").astype(str).str.strip()
        
        date_series_clean = date_series.astype(str).str.strip().str.replace('/', '-', regex=False)
        
        def smart_date_convert(val):
            val = str(val).strip()
            for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
                try: return pd.to_datetime(val, format=fmt)
                except Exception: continue
            return pd.to_datetime(val, errors='coerce')

        df[parsed_name] = date_series_clean.apply(smart_date_convert).dt.normalize()
        return df

    def reload_data(self):
        if not os.path.exists(self.filepath): return
        try:
            current_signature = (os.path.getmtime(self.filepath), os.path.getsize(self.filepath))
            if self._last_signature == current_signature: return

            with pd.ExcelFile(self.filepath) as xls:
                if SESSIONS_SHEET in xls.sheet_names:
                    self.df_sessions = self.clean_dataframe(pd.read_excel(xls, sheet_name=SESSIONS_SHEET), REQUIRED_COLUMNS, "الجلسة القادمة", "الجلسة القادمة_parsed", False)
                    if "تاريخ ورودها" in self.df_sessions.columns:
                        self.df_sessions["تاريخ ورودها_parsed"] = self.df_sessions.apply(smart_parse_incoming_date, axis=1)
                        self.df_sessions["تاريخ ورودها_parsed"] = pd.to_datetime(self.df_sessions["تاريخ ورودها_parsed"]).dt.normalize()
                
                if FINISHED_SHEET in xls.sheet_names:
                    self.df_finished = self.clean_dataframe(pd.read_excel(xls, sheet_name=FINISHED_SHEET), REQUIRED_COLUMNS, "آخر جلسة", "آخر جلسة_parsed", True)
                
                if PENDING_SHEET in xls.sheet_names:
                    self.df_pending = self.clean_dataframe(pd.read_excel(xls, sheet_name=PENDING_SHEET), REQUIRED_COLUMNS, "الجلسة القادمة", "الجلسة القادمة_parsed", False)
                    if "تاريخ ورودها" in self.df_pending.columns:
                        self.df_pending["تاريخ ورودها_parsed"] = self.df_pending.apply(smart_parse_incoming_date, axis=1)
                        self.df_pending["تاريخ ورودها_parsed"] = pd.to_datetime(self.df_pending["تاريخ ورودها_parsed"]).dt.normalize()

            self._last_signature = current_signature
        except Exception as e:
            logging.error(f"❌ خطأ في طبقة البيانات: {e}")

    def get_sessions(self): self.reload_data(); return self.df_sessions
    def get_finished(self): self.reload_data(); return self.df_finished
    def get_pending(self): self.reload_data(); return self.df_pending
    def get_all_incoming_combined(self):
        self.reload_data()
        combined = pd.concat([self.df_sessions.copy(), self.df_pending.copy()], ignore_index=True)
        if not combined.empty and "رقم القضية" in combined.columns:
            combined = combined.drop_duplicates(subset=["رقم القضية"])
        return combined

repo = CourtRepository(DATA_FILE)
user_session = {}

def check_session_init(chat_id):
    if chat_id not in user_session:
        user_session[chat_id] = {}
    return user_session[chat_id]

def create_keyboard(items, prefix, include_all=True, row_width=2):
    kb = InlineKeyboardMarkup(row_width=row_width)
    buttons = []
    for i, item in enumerate(items):
        buttons.append(InlineKeyboardButton(str(item), callback_data=f"{prefix}:{i}"))
    kb.add(*buttons)
    footer_row = []
    if include_all:
        footer_row.append(InlineKeyboardButton("الكل ✨", callback_data=f"{prefix}:all"))
    footer_row.append(InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu"))
    kb.row(*footer_row)
    return kb

def clean_case_no(case):
    if pd.isna(case): return ""
    return str(case).replace(".0", "").strip()

def generate_report(chat_id):
    s = user_session.get(chat_id)
    if not s: return

    mode = s.get("mode", "sessions")
    type_label = s.get("type", "الكل")
    target_date = s.get("date", "all")

    mode_titles = {
        "sessions": "قوائم الجلسات الحالية", "finished": "القضايا المنتهية",
        "active_view": "القضايا المنظورة", "stagnant": "القضايا المتعثرة",
        "stagnant_done": "المنتهي من المتعثر", "incoming": "القضايا الواردة",
        "frozen_review": "المحجوزة للاطلاع", "frozen_judgment": "المحجوزة للحكم",
        "excluded": "القضايا المستبعدة"
    }
    
    # ---------- استبدال وتوسيع تسمية الملف لضم نطاق التاريخ واسم القاضي بصيغة عربية ----------
    report_title_text = mode_titles.get(mode, "تقرير قضائي")
    type_label = s.get("type", "الكل")
    judge_label = s.get("judge", "الكل")

    # بناء تسمية التاريخ المناسبة (تعامل مع single, range, all, والحالات الهجرية الديناميكية)
    date_label = ""
    try:
        dt_type, sd, ed = parse_date_input(str(target_date))
    except Exception:
        dt_type, sd, ed = "invalid", None, None

    if dt_type == "single" and sd is not None:
        date_label = sd.strftime("%d/%m/%Y")
    elif dt_type == "range" and sd is not None and ed is not None:
        date_label = f"{sd.strftime('%d/%m/%Y')} - {ed.strftime('%d/%m/%Y')}"
    elif isinstance(target_date, str) and ("hijri_prev_" in target_date or "hijri_curr_" in target_date):
        h_info = s.get("dynamic_hijri", get_dynamic_hijri_months())
        if "prev" in target_date:
            date_label = f"{h_info['prev']['name']} ({h_info['prev']['start']} - {h_info['prev']['end']})"
        else:
            date_label = f"{h_info['current']['name']} ({h_info['current']['start']} - {h_info['current']['end']})"
    elif str(target_date).lower() in ("all", "الكل"):
        date_label = "شامل"
    else:
        date_label = str(target_date) if target_date else ""

    # ترجمة الأرقام إلى العربية داخل التسمية
    date_label_ar = to_arabic_number(date_label)

    # دمج أجزاء الاسم: نوع التقرير، نوع القضايا، اسم القاضي، ونطاق التاريخ
    judge_part = f" - {judge_label}" if judge_label and judge_label != "all" else ""
    type_part = f" - {type_label}" if type_label else ""
    date_part = f" - {date_label_ar}" if date_label_ar else ""

    filename = f"تقرير {report_title_text}{type_part}{judge_part}{date_part}.pdf"
    filename = re.sub(r'[\\/*?:"<>|]', "", filename).strip()
# ----------------------------------------------------------------------------------------
    
    try:
        doc = SimpleDocTemplate(filename, pagesize=landscape(A4), rightMargin=20, leftMargin=20, topMargin=10, bottomMargin=35)
        story = []

        header_cell_style = ParagraphStyle("h_cell_fin", fontName=FONT_NAME_BOLD, fontSize=11, alignment=1, leading=14, textColor=colors.HexColor("#FFFFFF"))
        boss_label_style = ParagraphStyle("boss_lbl_style", fontName=FONT_NAME, fontSize=11, alignment=0, leading=15)
        style_title_main = ParagraphStyle("t_main", fontName=FONT_NAME_BOLD, fontSize=14, alignment=1, leading=18)
        style_line5 = ParagraphStyle("l5_fin", fontName=FONT_NAME, fontSize=12, alignment=1, leading=15)

        if mode == "finished" or mode == "stagnant_done":
            if mode == "finished": df_all = repo.get_finished().copy()
            else: df_all = repo.get_finished()[repo.get_finished()["رقم القضية"].apply(is_stagnant_case)].copy()

            if df_all.empty: return bot.send_message(chat_id, "⚠️ لا توجد نتائج مسجلة في شيت المنتهي حالياً.")

            year_option = s.get("year_option", "all")
            if year_option == "1447":
                df_all = df_all[(df_all["آخر جلسة_parsed"] >= start_1447) & (df_all["آخر جلسة_parsed"] <= end_1447)]
            elif year_option == "1448":
                df_all = df_all[(df_all["آخر جلسة_parsed"] >= start_1448) & (df_all["آخر جلسة_parsed"] <= end_1448)]
            elif year_option == "custom":
                try:
                    c_start = dt_module.datetime.strptime(s.get("custom_start", ""), "%d/%m/%Y")
                    c_end = dt_module.datetime.strptime(s.get("custom_end", ""), "%d/%m/%Y")
                    df_all = df_all[(df_all["آخر جلسة_parsed"] >= c_start) & (df_all["آخر جلسة_parsed"] <= c_end)]
                except Exception: return bot.send_message(chat_id, "⚠️ حدث خلل أثناء تحليل التواريخ المخصصة.")

            if df_all.empty: return bot.send_message(chat_id, "⚠️ لم تظهر أي نتائج مرتبطة بالقضايا المنتهية ضمن الإطار الزمني.")
            df_all["آخر جلسة_parsed"] = pd.to_datetime(df_all["آخر جلسة_parsed"])

            def extract_sort_tuple(case_val):
                nums = re.findall(r'\d+', str(case_val))
                if len(nums) >= 2: return (int(nums[0]), int(nums[1]))
                elif len(nums) == 1: return (999999, int(nums[0]))
                return (999999, 999999)
            df_all["_sort_case_tuple"] = df_all["رقم القضية"].apply(extract_sort_tuple)

            df_all["رقم_الحكم_المولد"] = 0
            for c_type in df_all["نوعها"].unique():
                type_mask = df_all["نوعها"] == c_type
                df_type_block = df_all[type_mask].sort_values(by=["آخر جلسة_parsed", "_sort_case_tuple"], ascending=[True, True])
                df_all.loc[df_type_block.index, "رقم_الحكم_المولد"] = range(1, len(df_type_block) + 1)

            df_report = df_all.copy()
            if "نوعها" in df_report.columns:
                df_report["نوعها"] = df_report["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})

            if s.get("type") != "all": df_report = df_report[df_report["نوعها"] == s.get("type")]
            if s.get("judge") != "all": df_report = df_report[df_report["برئاسة القاضي"] == s.get("judge")]
            if df_report.empty: return bot.send_message(chat_id, "⚠️ لا توجد قضايا تطابق هذه الفلاتر المحددة.")

            df_report = df_report.sort_values(by=["آخر جلسة_parsed", "_sort_case_tuple"], ascending=[True, True])
            grouped = df_report.groupby(["نوعها"], sort=False)
            valid_blocks = []

            for p_type, group_df in grouped:
                p_type_str = str(p_type).strip()
                boss_name = get_section_boss(p_type_str)
                boss_paragraph = Paragraph(f"<b><font color='#002060'>{ar('رئيس القسم:')}</font></b><br/>{ar(boss_name)}", boss_label_style)

                if year_option == "1447": raw_text = "كشف بالقضايا المنتهية لعام ١٤٤٧هـ (من: \u200F26/06/2025م\u200F إلى: \u200F16/06/2026م\u200F)"
                elif year_option == "1448": raw_text = f"كشف بالقضايا المنتهية لعام ١٤٤٨هـ (من: \u200F17/06/2026م\u200F إلى: \u200F{today.strftime('%d/%m/%Y')}م\u200F)"
                else: raw_text = f"كشف بالتقرير القضائي: {report_title_text}"

                date_line_text = Paragraph(ar(raw_text), style_line5)
                judge_title_part = f" القاضي: {s.get('judge')}" if s.get('judge') != "all" else " (لكل القضاة)"
                text_table = Table([[Paragraph(f"{ar(p_type_str)}{ar(judge_title_part)} :<font color='#002060'><b>{ar('قسم')}</b></font>", style_title_main)]], colWidths=[450])

                center_story_elements = [Spacer(1, 72), text_table, Spacer(1, 6), date_line_text]
                main_header_table = Table([[boss_paragraph, center_story_elements, Paragraph("", center_style)]], colWidths=[140, 530, 145])
                main_header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 14)]))

                block = []
                headers = ["حالة القضية", "رقم الحكم", "تاريخ النطق", "برئاسة القاضي", "المدعى عليه", "المدعي", "موضوع القضية", "رقم القضية", "م"]
                col_widths = [95, 60, 75, 110, 120, 120, 105, 70, 30]
                
                table_data = [[main_header_table, "", "", "", "", "", "", "", ""], [Paragraph(ar(h), header_cell_style) for h in headers]]
                
                for idx, (_, row) in enumerate(group_df.iterrows(), 1):
                    h_date_raw = row["آخر جلسة_parsed"]
                    h_date_str = to_arabic_number(h_date_raw.strftime('%Y/%m/%d')) if pd.notna(h_date_raw) else ""
                    
                    table_data.append([
                        Paragraph(ar(row["قرار الجلسة"]), center_style), 
                        Paragraph(to_arabic_number(row["رقم_الحكم_المولد"]), center_style), 
                        Paragraph(h_date_str, center_style), 
                        Paragraph(ar(row["برئاسة القاضي"]), center_style), 
                        Paragraph(ar(row["المدعى عليه"]), right_style),
                        Paragraph(ar(row["المدعي"]), right_style), 
                        Paragraph(ar(row["موضوعها"]), center_style),
                        Paragraph(ar(clean_case_no(row["رقم القضية"])), center_style), 
                        Paragraph(to_arabic_number(idx), center_style)
                    ])
                
                t = Table(table_data, colWidths=col_widths, style=TableStyle([
                    ("SPAN", (0, 0), (-1, 0)), ("LINEBELOW", (0, 0), (-1, 0), 1.5, colors.HexColor("#002060")),
                    ("GRID", (0, 1), (-1, -1), 0.5, colors.black), ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#002060")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 1), (-1, -1), 5), ("BOTTOMPADDING", (0, 1), (-1, -1), 5)
                ]), repeatRows=2)
                block.append(t)
                valid_blocks.append(block)

            for idx, block in enumerate(valid_blocks):
                if idx > 0: story.append(PageBreak())
                story.extend(block)

        elif mode == "sessions":
            full_db = repo.get_sessions().copy()
            if full_db.empty: return bot.send_message(chat_id, "⚠️ الملف خالي من أي جلسات قادمة.")
            
            # تحويل عمود الجلسة إلى تاريخ فقط للمقارنة والفرز
            full_db['temp_date_only'] = pd.to_datetime(full_db["الجلسة القادمة_parsed"], errors='coerce').dt.date
            raw_df = full_db.copy()
            
            # تطبيق فلترة النطاق/اليوم
            date_type, start_dt, end_dt = parse_date_input(target_date)
            if date_type == "single":
                raw_df = raw_df[raw_df["temp_date_only"] == start_dt.date()]
            elif date_type == "range":
                raw_df = raw_df[(raw_df["temp_date_only"] >= start_dt.date()) & (raw_df["temp_date_only"] <= end_dt.date())]
            
            # تطبيع أسماء الأنواع إن وجدت
            if "نوعها" in raw_df.columns:
                raw_df["نوعها"] = raw_df["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})

            # تطبيق فلاتر القاضي والنوع إذا اختيرت
            if s.get("judge") != "all": raw_df = raw_df[raw_df["برئاسة القاضي"] == s.get("judge")]
            if s.get("type") != "all": raw_df = raw_df[raw_df["نوعها"] == s.get("type")]
            if raw_df.empty: return bot.send_message(chat_id, "⚠️ لم يتم العثور على أي جلسات مطابقة للنطاق المحدد.")

            # الآن نُرتّب حسب التاريخ ثم نقسم العرض يوم بيوم
            raw_df = raw_df.sort_values(by=["temp_date_only", "نوعها", "برئاسة القاضي"])
            unique_dates = sorted(raw_df['temp_date_only'].dropna().unique())

            valid_blocks = []
            style_line3 = ParagraphStyle("l3", fontName=FONT_NAME_BOLD, fontSize=17, alignment=1, leading=22)
            style_line4 = ParagraphStyle("l4", fontName=FONT_NAME_BOLD, fontSize=15, alignment=1, leading=19)
            past_cell_style = ParagraphStyle("past_cell_st", fontName=FONT_NAME, fontSize=9, alignment=1, leading=12, spaceBefore=2, spaceAfter=2)

            # نمر على كل يوم في النطاق ثم نجمع داخله حسب النوع والقاضي
            for current_date in unique_dates:
                date_df = raw_df[raw_df["temp_date_only"] == current_date]
                if date_df.empty:
                    continue

                # إعداد سلاسل التاريخ لعنوان الصفحة لهذا اليوم
                y, m, d = current_date.year, current_date.month, current_date.day
                arabic_days = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]
                day_name = arabic_days[current_date.weekday()] if hasattr(current_date, "weekday") else ""
                months_ar = {1:"يناير", 2:"فبراير", 3:"مارس", 4:"أبريل", 5:"مايو", 6:"يونيو", 7:"يوليو", 8:"أغسطس", 9:"سبتمبر", 10:"أكتوبر", 11:"نوفمبر", 12:"ديسمبر"}
                month_name = months_ar.get(m, str(m))
                try:
                    h = Gregorian(y, m, d).to_hijri()
                    hijri_months_ar = {1: "محرم", 2: "صفر", 3: "ربيع الأول", 4: "ربيع الآخر", 5: "جمادى الأولى", 6: "جمادى الآخرة", 7: "رجب", 8: "شعبان", 9: "رمضان", 10: "شوال", 11: "ذو القعدة", 12: "ذو الحجة"}
                    hijri_str = f"{to_arabic_number(h.day)}/{hijri_months_ar.get(h.month, '')}/{to_arabic_number(h.year)}هـ"
                except Exception:
                    hijri_str = ""
                date_line_text = ar(f"يوم {day_name} {hijri_str} الموافق {to_arabic_number(d)} {month_name} {to_arabic_number(y)}م")

                # داخل كل يوم نجمع حسب النوع والقاضي
                grouped = date_df.groupby(["نوعها", "برئاسة القاضي"], sort=True)
                for (p_type, judge), group_df in grouped:
                    p_type_str = str(p_type)
                    judge_str = str(judge)

                    # إعداد رأس الجدول الخاص بهذا النوع للقاضي ليوم current_date
                    weeks_table_data = [[Paragraph(ar("عدد الجلسات"), center_style), Paragraph(ar("التاريخ"), center_style), Paragraph(ar("اليوم"), center_style)]]
                    # إحسب موجز الأسابيع بناءً على current_date
                    for w_idx in range(1, 6):
                        next_w_date = current_date + dt_module.timedelta(weeks=w_idx)
                        future_count = len(full_db[(full_db["temp_date_only"] == next_w_date) & (full_db["برئاسة القاضي"] == str(judge).strip()) & (full_db["نوعها"] == str(p_type).strip())])
                        count_display = to_arabic_number(future_count) if future_count > 0 else ""
                        weeks_table_data.append([Paragraph(f"<font color='red'><b>{count_display}</b></font>" if future_count > 0 else "", center_style), Paragraph(to_arabic_number(str(next_w_date)), center_style), Paragraph(ar(day_name), center_style)])

                    weeks_small_table_container = Table([[Table(weeks_table_data, colWidths=[80, 100, 55], style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4F81BD")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#EAF2FB")), ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#B2EBF2")), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))]], colWidths=[235])
                    weeks_small_table_container.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 2.5, colors.HexColor("#002060")), ("PADDING", (0, 0), (-1, -1), 5)]))

                    center_elements = [Spacer(1, 70), Paragraph(f"{ar(judge_str)} <font color='blue'><b>{ar('القاضي:')}</b></font>", style_line3), Spacer(1, 4), Paragraph(f"{ar(p_type_str)} <font color='blue'><b>{ar('نوع القضايا:')}</b></font>", style_line4), Spacer(1, 4), Paragraph(date_line_text, style_line5)]
                    main_header_table = Table([[weeks_small_table_container, center_elements, Paragraph("", center_style)]], colWidths=[235, 350, 145])
                   
                    decision_header_subtable = Table([
                        [Paragraph(ar("قرار الجلسة"), header_cell_style), ""],
                        [Paragraph(ar("الحالية"), header_cell_style), Paragraph(ar("الماضية"), header_cell_style)]
                    ], colWidths=[154, 76])
                    decision_header_subtable.setStyle(TableStyle([
                        ("SPAN", (0, 0), (1, 0)), ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#002060")), ("LINEBELOW", (0, 0), (1, 0), 0.5, colors.white),
                        ("LINEBEFORE", (1, 1), (1, 1), 0.5, colors.white), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                    ]))

                    # بناء جدول القضايا لليوم/النوع/القاضي الحالي
                    block = []
                    headers = ["الجلسة القادمة", decision_header_subtable, "المدعى عليه", "المدعي", "موضوع القضية", "نوع القضية", "رقم القضية", "م"]
                    table_data = [[main_header_table, "", "", "", "", "", "", ""], [h if isinstance(h, Table) else Paragraph(ar(h), header_cell_style) for h in headers]]

                    for i, (_, row) in enumerate(group_df.iterrows(), 1):
                        decision_cell_subtable = Table([
                            [Paragraph("", past_cell_style), Paragraph(ar(row["قرار الجلسة"]), past_cell_style)]
                        ], colWidths=[154, 76])
                        decision_cell_subtable.setStyle(TableStyle([
                            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBEFORE", (1, 0), (1, 0), 0.5, colors.black),
                            ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                            ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ]))

                        table_data.append([
                            Paragraph("", center_style), decision_cell_subtable, Paragraph(ar(row["المدعى عليه"]), right_style),
                            Paragraph(ar(row["المدعي"]), right_style), Paragraph(ar(row["موضوعها"]), center_style),
                            Paragraph(ar(row["نوعها"]), center_style), Paragraph(ar(clean_case_no(row["رقم القضية"])), center_style),
                            Paragraph(to_arabic_number(i), center_style)
                        ])

                    t = Table(table_data, colWidths=[80, 230, 115, 115, 100, 55, 65, 25], style=TableStyle([
                        ("SPAN", (0, 0), (-1, 0)), ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
                        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#002060")), ("LINEBELOW", (0, 1), (-1, 1), 2.2, colors.HexColor("#001030")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 0),
                        ("TOPPADDING", (0, 2), (-1, -1), 3), ("BOTTOMPADDING", (0, 2), (-1, -1), 3),
                    ]), repeatRows=2)

                    t.setStyle(TableStyle([("POSTSCRIPT", (0, 1), (-1, -1), draw_rounded_table_background)]))
                    block.append(t)
                    valid_blocks.append(block)

            # أضف الفواصل بين الكتل ثم ابنِ المستند (كالسابق)
            for idx, block in enumerate(valid_blocks):
                if idx > 0: story.append(PageBreak())
                story.extend(block)
        else:
            if mode == "active_view":
                df_base = pd.concat([repo.get_sessions(), repo.get_pending()], ignore_index=True).drop_duplicates(subset=["رقم القضية"])
                date_col = "الجلسة القادمة_parsed"
            elif mode == "stagnant":
                df_all = pd.concat([repo.get_sessions(), repo.get_pending()], ignore_index=True)
                df_base = df_all[df_all["رقم القضية"].apply(is_stagnant_case)]
                date_col = "الجلسة القادمة_parsed"
            elif mode == "incoming":
                df_base = repo.get_all_incoming_combined()
                date_col = "تاريخ ورودها_parsed"
            elif mode == "frozen_review":
                df_all = pd.concat([repo.get_sessions(), repo.get_pending()], ignore_index=True)
                df_base = df_all[df_all["قرار الجلسة"].str.contains("اطلاع|الاطلاع", na=False, case=False)]
                date_col = "الجلسة القادمة_parsed"
            elif mode == "frozen_judgment":
                df_all = pd.concat([repo.get_sessions(), repo.get_pending()], ignore_index=True)
                df_base = df_all[df_all["قرار الجلسة"].str.contains("حجز القضية للحكم|للحكم", na=False, case=False)]
                date_col = "الجلسة القادمة_parsed"
            elif mode == "excluded":
                df_all = pd.concat([repo.get_sessions(), repo.get_pending()], ignore_index=True)
                df_base = df_all[df_all["قرار الجلسة"].str.contains("استبعاد|مستبعدة", na=False, case=False)]
                date_col = "الجلسة القادمة_parsed"
            else:
                df_base = pd.DataFrame()

            if df_base.empty: return bot.send_message(chat_id, "⚠️ لا توجد قضايا حالية مدرجة تحت هذا النمط الفرعي.")

            if "نوعها" in df_base.columns:
                df_base["نوعها"] = df_base["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})

            if mode == "incoming" and ("_prev_" in target_date or "_curr_" in target_date):
                h_info = s.get("dynamic_hijri", get_dynamic_hijri_months())
                if "_prev_" in target_date:
                    start_p = pd.Timestamp(h_info['prev']['start'].replace('/', '-')).normalize()
                    end_p = pd.Timestamp(h_info['prev']['end'].replace('/', '-')).normalize()
                    date_lbl = f"الوارد خلال شهر {h_info['prev']['name']}"
                else:
                    start_p = pd.Timestamp(h_info['current']['start'].replace('/', '-')).normalize()
                    end_p = pd.Timestamp(today).normalize()
                    date_lbl = f"الوارد خلال شهر {h_info['current']['name']}"
                
                if date_col in df_base.columns:
                    mask_date = (df_base[date_col] >= start_p) & (df_base[date_col] <= end_p)
                    if "1448" in h_info['current']['name'] or "1448" in h_info['prev']['name']:
                        mask_date = mask_date | (df_base["رقم القضية"].astype(str).str.contains("1448/"))
                    df_base = df_base[mask_date]
            elif target_date != "all":
                date_type, start_dt, end_dt = parse_date_input(text=target_date)
                if date_type != "all" and date_type != "invalid":
                    df_base['temp_date_only'] = pd.to_datetime(df_base[date_col], errors='coerce').dt.date
                    if date_type == "single":
                        df_base = df_base[df_base["temp_date_only"] == start_dt.date()]
                    elif date_type == "range":
                        df_base = df_base[(df_base["temp_date_only"] >= start_dt.date()) & (df_base["temp_date_only"] <= end_dt.date())]
                date_lbl = f"الفترة: {target_date}"
            else:
                date_lbl = "كشف شامل عام"

            if s.get("type") != "all": df_base = df_base[df_base["نوعها"] == s.get("type")]
            if s.get("judge") != "all": df_base = df_base[df_base["برئاسة القاضي"] == s.get("judge")]

            if df_base.empty: return bot.send_message(chat_id, "⚠️ لم تظهر نتائج بعد التصفية والتطابق المحدب.")

            grouped = df_base.groupby(["نوعها"], sort=False)
            valid_blocks = []

            for p_type, group_df in grouped:
                p_type_str = str(p_type).strip()
                boss_name = get_section_boss(p_type_str)
                boss_paragraph = Paragraph(f"<b><font color='#002060'>{ar('رئيس القسم:')}</font></b><br/>{ar(boss_name)}", boss_label_style)

                judge_title_part = f" - القاضي: {s.get('judge')}" if s.get('judge') != "all" else " (لكل القضاة)"
                text_table = Table([[Paragraph(f"{ar(report_title_text)} - {ar(p_type_str)}{ar(judge_title_part)}", style_title_main)]], colWidths=[500])
                
                center_elements = [Spacer(1, 60), text_table, Spacer(1, 4), Paragraph(ar(date_lbl), center_style)]
                main_header_table = Table([[boss_paragraph, center_elements, Paragraph("", center_style)]], colWidths=[150, 500, 135])

                block = []
                headers = ["تاريخ الورود / الإجراء", "قرار الجلسة / الحالة", "برئاسة القاضي", "المدعى عليه", "المدعي", "موضوع القضية", "رقم القضية", "م"]
                table_data = [[main_header_table, "", "", "", "", "", "", ""], [Paragraph(ar(h), header_cell_style) for h in headers]]
                
                for i, (_, row) in enumerate(group_df.iterrows(), 1):
                    if mode == "incoming" and pd.notna(row.get("تاريخ ورودها_parsed")):
                        t_date = row["تاريخ ورودها_parsed"].strftime('%Y/%m/%d')
                    elif date_col == "الجلسة القادمة_parsed" and pd.notna(row.get(date_col)):
                        t_date = row[date_col].strftime('%Y/%m/%d')
                    else:
                        t_date = row.get("تاريخ ورودها", row.get("الجلسة القادمة", ""))
                        
                    table_data.append([
                        Paragraph(to_arabic_number(t_date), center_style), Paragraph(ar(row.get("قرار الجلسة", "")), center_style),
                        Paragraph(ar(row.get("برئاسة القاضي", "")), center_style), Paragraph(ar(row.get("المدعى عليه", "")), right_style),
                        Paragraph(ar(row.get("المدعي", "")), right_style), Paragraph(ar(row.get("موضوعها", "")), center_style),
                        Paragraph(ar(clean_case_no(row.get("رقم القضية", ""))), center_style), Paragraph(to_arabic_number(i), center_style)
                    ])

                t = Table(table_data, colWidths=[90, 130, 115, 120, 120, 110, 75, 25], style=TableStyle([
                    ("SPAN", (0, 0), (-1, 0)), ("GRID", (0, 1), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#002060")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")
                ]), repeatRows=2)
                block.append(t)
                valid_blocks.append(block)

            for idx, block in enumerate(valid_blocks):
                if idx > 0: story.append(PageBreak())
                story.extend(block)

        if story:
            def make_canvas(*args, **kwargs):
                canvas_obj = NumberedCanvas(*args, **kwargs)
                canvas_obj.report_mode = mode
                return canvas_obj

            doc.build(story, canvasmaker=make_canvas)
            if os.path.exists(filename):
                with open(filename, "rb") as f: 
                    bot.send_document(chat_id, f, caption="🎯 تم إنشاء وتحديث كشف الـ PDF بنجاح وفقاً لصلاحيات رقم الهاتف المدقق وجاهز للاستخدام.")
            time.sleep(1)
            try: os.remove(filename)
            except Exception: pass
        else:
            bot.send_message(chat_id, "⚠️ لا توجد قضايا مستوفية للشروط لإنشاء الملف.")
            
    except Exception as e:
        logging.error(f"Error drawing report: {e}", exc_info=True)
        bot.send_message(chat_id, "❌ خطأ غير متوقع أثناء معالجة الجداول والرسم الفني بالـ ReportLab.")
    finally:
        if os.path.exists(filename):
            try: os.remove(filename)
            except Exception: pass
        user_session.pop(chat_id, None)

# --- لوحة القيادة واستقبال الأوامر المحمية بالجدار الأمني لجهات الاتصال ---

@bot.message_handler(commands=['start'])
def start(message):
    chat_id = message.chat.id
    user_session[chat_id] = {}
    
    # فحص فوري: إذا كان مسجلاً بالذاكرة ينتقل للوحة الرئيسية، وغير ذلك يجبره على إرسال جهة الاتصال
    if chat_id in VERIFIED_USERS:
        show_welcome_dashboard(chat_id)
    else:
        phone_gatekeeper(chat_id)

def show_welcome_dashboard(chat_id):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📅 قوائم الجلسات", callback_data="set_mode:sessions"),
        InlineKeyboardButton("✅ القضايا المنتهية", callback_data="finished_reports_menu"),
        InlineKeyboardButton("👁️ القضايا المنظورة", callback_data="set_mode:active_view"),
        InlineKeyboardButton("⏳ القضايا المتعثرة", callback_data="stagnant_menu"),
        InlineKeyboardButton("📥 القضايا الواردة", callback_data="incoming_menu"),
        InlineKeyboardButton("📖 المحجوزة للاطلاع", callback_data="set_mode:frozen_review"),
        InlineKeyboardButton("👨‍⚖️ المحجوزة للحكم", callback_data="set_mode:frozen_judgment"),
        InlineKeyboardButton("❌ القضايا المستبعدة", callback_data="set_mode:excluded")
    )
    
    welcome_text = (
        "⚖️ **أهلاً بكم في النظام الآلي لمحكمة الظهار الابتدائية. ⚖️**\n\n"
        "نحن هنا لخدمتكم وتسهيل إجراءاتكم القضائية. يمكنك من خلال هذه القائمة الوصول إلى الخدمات المتاحة:"
    )
    bot.send_message(chat_id, welcome_text, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "main_menu")
@restricted_access()
def callback_main_menu(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    user_session[chat_id] = {}
    show_welcome_dashboard(chat_id)

@bot.callback_query_handler(func=lambda call: call.data == "incoming_menu")
@restricted_access()
def show_incoming_menu(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    s = check_session_init(chat_id)
    s["mode"] = "incoming"
    h_info = get_dynamic_hijri_months()
    s["dynamic_hijri"] = h_info 
    
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton(f"🌙 وارد الشهر الماضي {h_info['prev']['name']} ({to_arabic_number(h_info['prev']['start'])} - {to_arabic_number(h_info['prev']['end'])})", callback_data=f"set_incoming_date:{h_info['prev']['key']}"),
        InlineKeyboardButton(f"🌙 وارد الشهر الحالي {h_info['current']['name']} ({to_arabic_number(h_info['current']['start'])} - اليوم)", callback_data=f"set_incoming_date:{h_info['current']['key']}"),
        InlineKeyboardButton("🔍 وارد تاريخ يوم معين", callback_data="set_incoming_date:single_day"),
        InlineKeyboardButton("📊 تحديد فترة محددة (نطاق زمني)", callback_data="set_incoming_date:custom_range"),
        InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu")
    )
    guide_incoming = (
        "📥 **تصفية القضايا الواردة (جدول التوزيع السنوي)**\n\n"
        "يرجى تحديد الخيار الزمني المناسب لاستخراج البيانات المؤتمتة للوارد السنوي:"
    )
    bot.send_message(chat_id, guide_incoming, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_incoming_date:"))
@restricted_access()
def handle_incoming_date_selection(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    selection = call.data.split(":")[1]
    s = check_session_init(chat_id)
    
    if "hijri_prev_" in selection or "hijri_curr_" in selection:
        s["date"] = selection
        move_to_type_selection(chat_id)
    elif selection == "single_day":
        msg = bot.send_message(chat_id, "✏️ **تحديد يوم محدد:**\nيرجى كتابة اليوم المطلوب بالصيغة التالية:\n`2026/07/05` أو `05/07/2026`")
        bot.register_next_step_handler(msg, process_final_date)
    elif selection == "custom_range":
        instruction = "✏️ **تحديد النطاق الزمني المخصص:**\nيرجى كتابة المدة كالمثال التالي:\n`2026/06/17 - 2026/07/06`"
        msg = bot.send_message(chat_id, instruction, parse_mode="Markdown")
        bot.register_next_step_handler(msg, process_final_date)

def move_to_type_selection(chat_id):
    s = check_session_init(chat_id)
    df_combined = repo.get_all_incoming_combined()
    
    if "نوعها" in df_combined.columns:
        df_combined["نوعها"] = df_combined["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})

    all_available_types = sorted(df_combined["نوعها"].dropna().unique())
    
    if s.get("mode") == "incoming" and ("_prev_" in str(s.get("date")) or "_curr_" in str(s.get("date"))):
        h_info = s.get("dynamic_hijri", get_dynamic_hijri_months())
        if "_prev_" in s.get("date"):
            start_p = pd.Timestamp(h_info['prev']['start'].replace('/', '-')).normalize()
            end_p = pd.Timestamp(h_info['prev']['end'].replace('/', '-')).normalize()
        else:
            start_p = pd.Timestamp(h_info['current']['start'].replace('/', '-')).normalize()
            end_p = pd.Timestamp(today).normalize()
            
        df_filtered = df_combined[(df_combined["تاريخ ورودها_parsed"] >= start_p) & (df_combined["تاريخ ورودها_parsed"] <= end_p)]
        types = sorted(df_filtered["نوعها"].dropna().unique())
        if not types or "غير جسيم" not in types: types = all_available_types
    else:
        types = all_available_types

    s["types"] = types
    bot.send_message(chat_id, "📌 **اختيار القسم القضائي المستهدف:**", reply_markup=create_keyboard(types, "core_type", include_all=True, row_width=2), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data == "finished_reports_menu")
@restricted_access()
def show_finished_years_options(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    s = check_session_init(chat_id)
    s["mode"] = "finished"
    
    kb = InlineKeyboardMarkup()
    kb.row(InlineKeyboardButton("📅 عام 1447 (من 26/06/2025 إلى 16/06/2026)", callback_data="set_year:1447"))
    kb.row(InlineKeyboardButton("📅 عام 1448 (من 17/06/2026 إلى اليوم)", callback_data="set_year:1448"))
    kb.row(InlineKeyboardButton("✏️ إدخال فترة محددة مخصصة", callback_data="set_year:custom"))
    kb.row(InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu"))
    
    guide_finished = "✅ **أرشيف الأحكام والقضايا المنتهية:**\nيرجى تحديد الدورة القضائية المستهدفة لحساب تراتيب الأحكام آلياً:"
    bot.send_message(chat_id, guide_finished, reply_markup=kb, parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_year:"))
@restricted_access()
def handle_year_selection(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    selection = call.data.split(":")[1]
    s = check_session_init(chat_id)
    s["year_option"] = selection
    
    if selection in ["1447", "1448"]:
        s["date"] = "range"
        df = repo.get_finished()
        if "نوعها" in df.columns:
            df["نوعها"] = df["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})
        types = sorted(df["نوعها"].dropna().unique())
        s["types"] = types
        bot.send_message(chat_id, "⚖️ حدد الآن **نوع وقسم القضايا الصادر بها أحكام:**", reply_markup=create_keyboard(types, "core_type", row_width=2))
    elif selection == "custom":
        msg = bot.send_message(chat_id, "✏️ **النطاق المخصص للمنتهي:**\nأدخل تاريخ **بداية** المدة المطلوبة، مثال: `26/06/2025`")
        bot.register_next_step_handler(msg, process_custom_start)

def process_custom_start(message):
    chat_id = message.chat.id
    s = check_session_init(chat_id)
    s["custom_start"] = message.text.strip()
    msg = bot.send_message(chat_id, "✏️ أدخل الآن تاريخ **نهاية** المدة المخصصة، مثال: `16/06/2026`")
    bot.register_next_step_handler(msg, process_custom_end)

def process_custom_end(message):
    chat_id = message.chat.id
    s = check_session_init(chat_id)
    s["custom_end"] = message.text.strip()
    s["date"] = "range"
    
    df = repo.get_finished()
    if "نوعها" in df.columns:
        df["نوعها"] = df["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})
    types = sorted(df["نوعها"].dropna().unique())
    s["types"] = types
    bot.send_message(chat_id, "⚖️ حدد **تصنيف القسم المستهدف:**", reply_markup=create_keyboard(types, "core_type", row_width=2))

@bot.callback_query_handler(func=lambda call: call.data == "stagnant_menu")
@restricted_access()
def stagnant_menu(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("⚠️ كشف كافة المتعثرة (قبل 1445هـ)", callback_data="set_mode:stagnant"),
        InlineKeyboardButton("🏆 كشف المنجز من المتعثر", callback_data="set_mode:stagnant_done"),
        InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu")
    )
    bot.send_message(chat_id, "⚙️ **دائرة القضايا المتعثرة المتأخرة:**", reply_markup=kb)

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_mode:"))
@restricted_access()
def set_mode(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    mode = call.data.split(":")[1]
    s = check_session_init(chat_id)
    s["mode"] = mode
    
    if mode == "sessions":
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton(f"📆 جلسات اليوم ({today_str})", callback_data=f"set_sessions_date:today:{today_str}"),
            InlineKeyboardButton(f"🌅 جلسات غداً ({tomorrow_str})", callback_data=f"set_sessions_date:tomorrow:{tomorrow_str}"),
            InlineKeyboardButton("🔍 جلسات يوم محدد", callback_data="set_sessions_date:single_day"),
            InlineKeyboardButton("📊 تحديد فترة محددة (نطاق زمني)", callback_data="set_sessions_date:custom_range"),
            InlineKeyboardButton("🔙 العودة للرئيسية", callback_data="main_menu")
        )
        bot.send_message(chat_id, "📅 **تحديد موعد قوائم الجلسات القادمة:**", reply_markup=kb)
    else:
        df = repo.get_sessions()
        if "نوعها" in df.columns:
            df["نوعها"] = df["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})
        types = sorted(df["نوعها"].dropna().unique())
        s["types"] = types
        bot.send_message(chat_id, "⚖️ حدد **تصنيف ونوع القضية** المراد سحب بياناتها:", reply_markup=create_keyboard(types, "core_type", row_width=2))

@bot.callback_query_handler(func=lambda call: call.data.startswith("set_sessions_date:"))
@restricted_access()
def handle_sessions_date_selection(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    parts = call.data.split(":")
    selection = parts[1]
    s = check_session_init(chat_id)
    
    if selection in ["today", "tomorrow"]:
        s["date"] = parts[2]
        move_to_type_selection(chat_id)
    elif selection == "single_day":
        msg = bot.send_message(chat_id, "✏️ **جلسات يوم معين:** اكتب التاريخ بدقة، مثال: `2026/07/05`")
        bot.register_next_step_handler(msg, process_final_date)
    elif selection == "custom_range":
        msg = bot.send_message(chat_id, "✏️ **جلسات فترة ممتدة:** اكتب النطاق كالمثال: `2026/06/01 - 2026/06/30`")
        bot.register_next_step_handler(msg, process_final_date)

@bot.callback_query_handler(func=lambda call: call.data.startswith("core_type:"))
@restricted_access()
def handle_core_type(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    val = call.data.split(":")[1]
    s = check_session_init(chat_id)
    if "types" not in s:
        df = repo.get_all_incoming_combined() if s.get("mode") == "incoming" else repo.get_sessions()
        if "نوعها" in df.columns:
            df["نوعها"] = df["نوعها"].astype(str).str.strip().replace({"غير جسيمة": "غير جسيم", "غير جسيم": "غير جسيم"})
        s["types"] = sorted(df["نوعها"].dropna().unique())

    s["type"] = "all" if val == "all" else s["types"][int(val)]
    
    df_sessions = repo.get_sessions()
    raw_judges = df_sessions["برئاسة القاضي"].dropna().astype(str).str.strip().unique()
    
    transferred_judges = {
        "جمال محمد الكدهي", "عادل حمود عبده العواضي", "فارس محمد عبده الحمودي", 
        "قابوس سعيد سلطان الشرعبي", "محمد أحمد محمد السناوي", "صدام علي علي الحنظلي", 
        "مهيب علي عبدالغني السفياني", "علي عبدالله قاسم حسن العفيف",
        "جمال الكدهي", "عادل العواضي", "فارس الحمودي", "قابوس الشرعبي", "محمدالسناوي", "محمد السناوي", "علي العفيف", "صدام الحنظلي", "مهيب السفياني"
    }
    
    judges = [j for j in raw_judges if j not in transferred_judges and j != "" and "nan" not in j.lower()]
    judges.sort()
    s["judges"] = judges
    
    guide_judges = "👨‍⚖️ **اختيار اسمم القاضي للمطابقة:**"
    bot.send_message(chat_id, guide_judges, reply_markup=create_keyboard(judges, "core_judge", include_all=True, row_width=2))

@bot.callback_query_handler(func=lambda call: call.data.startswith("core_judge:"))
@restricted_access()
def handle_core_judge(call):
    chat_id = call.message.chat.id
    try: bot.answer_callback_query(call.id)
    except Exception: pass
    
    val = call.data.split(":")[1]
    s = check_session_init(chat_id)
    
    if "judges" not in s:
        df = repo.get_sessions()
        raw_judges = df["برئاسة القاضي"].dropna().astype(str).str.strip().unique()
        transferred_judges = {
            "جمال محمد الكدهي", "عادل حمود عبده العواضي", "فارس محمد عبده الحمودي", 
            "قابوس سعيد سلطان الشرعبي", "محمد أحمد محمد السناوي", "صدام علي علي الحنظلي", 
            "مهيب علي عبدالغني السفياني", "علي عبدالله قاسم حسن العفيف",
            "جمال الكدهي", "عادل العواضي", "فارس الحمودي", "قابوس الشرعبي", "محمدالسناوي", "محمد السناوي", "علي العفيف", "صدام الحنظلي", "مهيب السفياني"
        }
        judges = [j for j in raw_judges if j not in transferred_judges and j != "" and "nan" not in j.lower()]
        judges.sort()
        s["judges"] = judges

    s["judge"] = "all" if val == "all" else s["judges"][int(val)]
    
    bot.send_message(chat_id, "🔄 جاري فحص البيانات، تطبيق المعايير ورسم جداول الـ PDF...")
    generate_report(chat_id)

def process_final_date(message):
    chat_id = message.chat.id
    text = message.text.strip()
    s = check_session_init(chat_id)

    date_type, _, _ = parse_date_input(text)
    if date_type == "invalid":
        msg = bot.send_message(chat_id, "⚠️ **صيغة غير مدعومة:** يرجى إعادة كتابة التاريخ بدقة وبشكل صحيح:")
        bot.register_next_step_handler(msg, process_final_date)
        return

    s["date"] = text
    move_to_type_selection(chat_id)

@bot.message_handler(func=lambda message: True)
def handle_unknown_text(message):
    chat_id = message.chat.id
    # إذا لم يكن مفعلاً، يتم إرسال بوابة التحقق بالهاتف تلقائياً عند كتابة أي نص عشوائي
    if chat_id not in VERIFIED_USERS:
        phone_gatekeeper(chat_id)
    else:
        show_welcome_dashboard(chat_id)

if __name__ == "__main__":
    logging.info("🚀 تم إطلاق البوت القضائي الذكي بنجاح مع تفعيل حماية جهات الاتصال الصارمة...")
    bot.infinity_polling(skip_pending=True)
