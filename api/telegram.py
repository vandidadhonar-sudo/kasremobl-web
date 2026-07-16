"""
kasrmobilya — Telegram bot (Vercel Serverless Webhook)

Bu fonksiyon, ayrı bir 7/24 sunucuya gerek kalmadan botu Vercel üzerinde
çalıştırır. Telegram güncellemeleri webhook ile buraya POST edilir.
Konuşma durumu (state) sunucusuz ortamda bellekte tutulamayacağı için
Supabase'de saklanır:
  - public.bot_sessions : sohbet başına durum (JSON)
  - public.bot_uploads  : yüklenen ürün görselleri (satır bazlı, yarış koşulu yok)

Gerekli ortam değişkenleri (Vercel > Settings > Environment Variables):
  TELEGRAM_BOT_TOKEN, SUPABASE_SECRET_KEY, BOT_ADMIN_PASSWORD
  (SUPABASE_URL isteğe bağlı; varsayılanı vardır.)

Kurulumdan sonra webhook'u bir kez ayarlayın (tarayıcıda açın):
  https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<SITE>/api/telegram
"""

import os
import json
import html
import uuid
from http.server import BaseHTTPRequestHandler

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client

# ==========================================
# Config (public repo — sırlar YALNIZCA ortam değişkenlerinden okunur)
# ==========================================
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ooklzhsnzovfnmzdupoq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_SECRET_KEY")
ADMIN_PASSWORD = os.environ.get("BOT_ADMIN_PASSWORD")
# Webhook sahteciliğine karşı koruma: Telegram'ın setWebhook'ta verilen
# secret_token'ı her istekte başlık olarak göndermesi doğrulanır.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or ADMIN_PASSWORD

AVAILABLE_FEATURES = ["KARGO BEDAVA", "ÜCRETSİZ İADE", "120 GÜN DENEME", "4 TAKSİT İMKANI", "HIZLI KARGO"]

_READY = bool(BOT_TOKEN and SUPABASE_KEY and ADMIN_PASSWORD)

# Sunucusuz ortamda güncellemeleri aynı iş parçacığında işlemek için threaded=False.
bot = telebot.TeleBot(BOT_TOKEN or "0:disabled", threaded=False)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_KEY else None

# İstek başına hidrate edilen bellek içi durum (do_POST içinde DB ile senkronlanır).
user_states = {}

STATE_WAIT_PASSWORD = 1
STATE_MAIN_MENU = 2
STATE_WAIT_IMAGES = 3
STATE_WAIT_NAME = 4
STATE_WAIT_DESC = 5
STATE_WAIT_PRICE = 6
STATE_WAIT_DISCOUNT = 14
STATE_WAIT_STOCK = 15
STATE_WAIT_VALIDITY = 16
STATE_WAIT_CONTACT = 17
STATE_WAIT_FEATURES = 7

STATE_EDIT_PRICE_DISCOUNT = 10
STATE_EDIT_STOCK = 11
STATE_EDIT_VALIDITY = 12
STATE_EDIT_CONTACT = 13

# ==========================================
# Kalıcı oturum yardımcıları (Supabase)
# ==========================================
def load_session(chat_id):
    try:
        res = supabase.table("bot_sessions").select("data").eq("chat_id", chat_id).execute()
        return res.data[0]["data"] if res.data else None
    except Exception as e:
        print(f"load_session error: {e}")
        return None

def persist_session(chat_id):
    data = user_states.get(chat_id)
    try:
        if data:
            supabase.table("bot_sessions").upsert({"chat_id": chat_id, "data": data}).execute()
        else:
            supabase.table("bot_sessions").delete().eq("chat_id", chat_id).execute()
    except Exception as e:
        print(f"persist_session error: {e}")

# --- Görsel yüklemeleri (satır bazlı; albüm gönderiminde yarış koşulu olmaz) ---
def uploads_add(chat_id, url):
    supabase.table("bot_uploads").insert({"chat_id": chat_id, "url": url}).execute()

def uploads_count(chat_id):
    res = supabase.table("bot_uploads").select("id", count="exact").eq("chat_id", chat_id).execute()
    return res.count or 0

def uploads_list(chat_id):
    res = supabase.table("bot_uploads").select("url").eq("chat_id", chat_id).order("created_at").execute()
    return [r["url"] for r in (res.data or [])]

def uploads_clear(chat_id):
    supabase.table("bot_uploads").delete().eq("chat_id", chat_id).execute()

# ==========================================
# Veritabanı fonksiyonları
# ==========================================
def upload_image_to_supabase(file_bytes, file_name):
    try:
        supabase.storage.from_("furniture_images").upload(
            path=file_name, file=file_bytes, file_options={"content-type": "image/jpeg"}
        )
        return supabase.storage.from_("furniture_images").get_public_url(file_name)
    except Exception as e:
        print(f"Upload error: {e}")
        return None

def save_product(category, name, desc, price, image_urls, features, discount, stock, validity, contact):
    parts = contact.split('-')
    s_name = parts[0].strip() if len(parts) > 0 else contact
    s_phone = parts[1].strip() if len(parts) > 1 else ""

    data = {
        "category": category,
        "name_or_desc": f"{name}\n\n{desc}",
        "price": price,
        "image_urls": image_urls,
        "status": "Mevcut",
        "features": features,
        "discount": discount,
        "stock_count": int(stock) if str(stock).isdigit() else 1,
        "validity_period": validity,
        "salesperson_name": s_name,
        "contact_phone": s_phone
    }
    res = supabase.table("products").insert(data).execute()
    return res.data[0]['id'] if res.data else None

# ==========================================
# Inline Dashboard
# ==========================================
def show_dashboard(chat_id, product_id):
    res = supabase.table("products").select("*").eq("id", product_id).execute()
    if not res.data:
        bot.send_message(chat_id, "❌ Ürün veritabanında bulunamadı.")
        return
    p = res.data[0]

    markup = InlineKeyboardMarkup()
    markup.row(InlineKeyboardButton("💰 İndirim / Fiyat Belirle", callback_data=f"ed_pr_{product_id}"))
    markup.row(
        InlineKeyboardButton(f"📦 Stok: {p.get('stock_count', 1)}", callback_data=f"ed_st_{product_id}"),
        InlineKeyboardButton(f"⏳ Geçerlilik: {p.get('validity_period') or 'Sınırsız'}", callback_data=f"ed_va_{product_id}")
    )
    markup.row(InlineKeyboardButton(f"📞 İletişim: {p.get('salesperson_name') or 'Belirlenmedi'}", callback_data=f"ed_co_{product_id}"))
    markup.row(InlineKeyboardButton("❌ Ürünü Siteden Sil", callback_data=f"del_{product_id}"))
    markup.row(InlineKeyboardButton("➕ Yeni Ürün Ekle", callback_data="main_menu"))

    name = p['name_or_desc'].split('\n\n')[0]
    text = (f"🎛 <b>Ürün Yönetim Paneli:</b> {html.escape(name)}\n\n"
            f"<b>Sitedeki Mevcut Durumu:</b>\n"
            f"Fiyat: {html.escape(str(p.get('price')))}\n"
            f"Kayıtlı İndirim: {html.escape(str(p.get('discount') or 'Yok'))}\n"
            f"Stok: {html.escape(str(p.get('stock_count', 1)))}\n"
            f"Satıcı: {html.escape(str(p.get('salesperson_name') or 'Belirlenmedi'))} | {html.escape(str(p.get('contact_phone') or ''))}\n\n"
            f"👇 Herhangi bir bölümü düzenlemek için aşağıdaki butonlara tıklayın:")

    bot.send_message(chat_id, text, reply_markup=markup, parse_mode="HTML")

def show_categories(chat_id):
    markup = InlineKeyboardMarkup()
    for cat in ["YEMEK TAKIMLARI", "TEKİL ÜRÜNLER", "EV TEKSTİLİ", "KOLTUK TAKIMLARI", "YATAK ODASI", "AKSESUAR"]:
        markup.row(InlineKeyboardButton(cat, callback_data=f"cat_{cat}"))
    bot.send_message(chat_id, "Giriş başarılı ✅\nLütfen bir kategori seçin:", reply_markup=markup)
    user_states[chat_id] = {"state": STATE_MAIN_MENU}

def show_features_keyboard(chat_id):
    data = user_states.get(chat_id, {})
    markup = InlineKeyboardMarkup()
    for feat in AVAILABLE_FEATURES:
        status = "✅ " if feat in data.get("selected_features", []) else "⬜️ "
        markup.row(InlineKeyboardButton(f"{status}{feat}", callback_data=f"feat_{feat}"))
    markup.row(InlineKeyboardButton("🚀 Sitede Yayınla", callback_data="commit_product"))
    bot.send_message(chat_id, "✨ Son Adım! Özellikleri seçin ve Sitede Yayınlayın:", reply_markup=markup)

# ==========================================
# Handlers
# ==========================================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    uploads_clear(message.chat.id)
    bot.reply_to(message, "Lütfen şifreyi giriniz:")
    user_states[message.chat.id] = {"state": STATE_WAIT_PASSWORD}

@bot.message_handler(commands=['iptal'])
def cancel_operation(message):
    uploads_clear(message.chat.id)
    user_states[message.chat.id] = {"state": STATE_MAIN_MENU}
    bot.reply_to(message, "❌ İşlem iptal edildi. Ana menüye dönüldü.")
    show_categories(message.chat.id)

@bot.message_handler(commands=['manage'])
def manage_products(message):
    res = supabase.table("products").select("id, name_or_desc").order("created_at", desc=True).limit(10).execute()
    if not res.data:
        bot.reply_to(message, "Hiç ürün bulunamadı.")
        return
    markup = InlineKeyboardMarkup()
    for p in res.data:
        name = p['name_or_desc'].split('\n\n')[0][:30]
        markup.row(InlineKeyboardButton(name, callback_data=f"dash_{p['id']}"))
    bot.reply_to(message, "Son eklenen ürünler (Yönetmek için tıklayın):\n\n(İşlemi iptal etmek için /iptal yazabilirsiniz)", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith('dash_'))
def open_dash(call):
    show_dashboard(call.message.chat.id, call.data.split('_')[1])

@bot.message_handler(func=lambda message: user_states.get(message.chat.id, {}).get("state") == STATE_WAIT_PASSWORD)
def check_password(message):
    if message.text == ADMIN_PASSWORD:
        show_categories(message.chat.id)
    else:
        bot.reply_to(message, "Şifre yanlış! Lütfen tekrar deneyin:")

@bot.callback_query_handler(func=lambda call: call.data == "main_menu")
def back_to_main(call):
    show_categories(call.message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('cat_'))
def handle_category_selection(call):
    category = call.data.split('_')[1]
    chat_id = call.message.chat.id
    uploads_clear(chat_id)
    user_states[chat_id] = {"state": STATE_WAIT_IMAGES, "category": category}
    markup = InlineKeyboardMarkup().row(InlineKeyboardButton("✅ Görselleri Tamamla", callback_data="done_images"))
    bot.edit_message_text(
        f"Kategori: {category}\n\n📸 Ürün fotoğraflarını gönderin (Maksimum 10 adet). Bitince 'Tamamla' butonuna basın:\n\n(Vazgeçmek için /iptal yazın)",
        chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup
    )

@bot.message_handler(content_types=['photo', 'document'], func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_IMAGES)
def handle_images(message):
    chat_id = message.chat.id
    if uploads_count(chat_id) >= 10:
        bot.reply_to(message, "⚠️ En fazla 10 görsel ekleyebilirsiniz. 'Tamamla' butonuna basın.")
        return
    try:
        file_id = message.photo[-1].file_id if message.content_type == 'photo' else message.document.file_id
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        # Rastgele dosya adı: sahibin chat_id'sinin herkese açık URL'lerde sızmasını önler.
        public_url = upload_image_to_supabase(downloaded_file, f"{uuid.uuid4().hex}.jpg")
        if public_url:
            uploads_add(chat_id, public_url)
            markup = InlineKeyboardMarkup().row(InlineKeyboardButton("✅ Görselleri Tamamla", callback_data="done_images"))
            bot.reply_to(message, f"📸 {uploads_count(chat_id)} görsel yüklendi.", reply_markup=markup)
    except Exception as e:
        bot.reply_to(message, f"Hata: {str(e)}")

@bot.callback_query_handler(func=lambda call: call.data == "done_images")
def handle_done_images(call):
    chat_id = call.message.chat.id
    if uploads_count(chat_id) == 0:
        bot.answer_callback_query(call.id, "Lütfen en az bir fotoğraf gönderin!")
    else:
        data = user_states.get(chat_id, {})
        data["state"] = STATE_WAIT_NAME
        user_states[chat_id] = data
        bot.send_message(chat_id, "✍️ Ürün adını giriniz:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_NAME)
def handle_name(message):
    data = user_states.get(message.chat.id, {})
    data["name"] = message.text
    data["state"] = STATE_WAIT_DESC
    user_states[message.chat.id] = data
    bot.reply_to(message, "📝 Ürün açıklamasını giriniz:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_DESC)
def handle_desc(message):
    data = user_states.get(message.chat.id, {})
    data["desc"] = message.text
    data["state"] = STATE_WAIT_PRICE
    user_states[message.chat.id] = data
    bot.reply_to(message, "💰 Ürün fiyatını giriniz:")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_PRICE)
def handle_price(message):
    data = user_states.get(message.chat.id, {})
    data["price"] = message.text
    data["state"] = STATE_WAIT_DISCOUNT
    user_states[message.chat.id] = data
    bot.reply_to(message, "🏷 İndirim var mı? (Örn: %30 İndirim veya yoksa 'Yok' yazın):")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_DISCOUNT)
def handle_discount(message):
    data = user_states.get(message.chat.id, {})
    data["discount"] = message.text
    data["state"] = STATE_WAIT_STOCK
    user_states[message.chat.id] = data
    bot.reply_to(message, "📦 Stok miktarı nedir? (Sadece sayı, Örn: 5):")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_STOCK)
def handle_stock(message):
    data = user_states.get(message.chat.id, {})
    data["stock"] = message.text
    data["state"] = STATE_WAIT_VALIDITY
    user_states[message.chat.id] = data
    bot.reply_to(message, "⏳ Fiyat/İndirim geçerlilik süresi? (Örn: Sadece 3 gün veya 'Sınırsız'):")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_VALIDITY)
def handle_validity(message):
    data = user_states.get(message.chat.id, {})
    data["validity"] = message.text
    data["state"] = STATE_WAIT_CONTACT
    user_states[message.chat.id] = data
    bot.reply_to(message, "📞 Satıcı adı ve iletişim numarası? (Örn: Hadi - +905522640025):")

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") == STATE_WAIT_CONTACT)
def handle_contact(message):
    data = user_states.get(message.chat.id, {})
    data["contact"] = message.text
    data["state"] = STATE_WAIT_FEATURES
    data["selected_features"] = []
    user_states[message.chat.id] = data
    show_features_keyboard(message.chat.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('feat_'))
def handle_feature_toggle(call):
    chat_id = call.message.chat.id
    feature = call.data.split('_')[1]
    data = user_states.get(chat_id)
    if not data:
        return
    selected = data.get("selected_features", [])
    if feature in selected:
        selected.remove(feature)
    else:
        selected.append(feature)
    data["selected_features"] = selected
    user_states[chat_id] = data
    bot.delete_message(chat_id, call.message.message_id)
    show_features_keyboard(chat_id)

@bot.callback_query_handler(func=lambda call: call.data == "commit_product")
def handle_commit_product(call):
    chat_id = call.message.chat.id
    data = user_states.get(chat_id)
    if not data:
        return
    try:
        image_urls = uploads_list(chat_id)
        pid = save_product(
            data["category"], data["name"], data["desc"], data["price"],
            image_urls, data.get("selected_features", []),
            data["discount"], data["stock"], data["validity"], data["contact"]
        )
        uploads_clear(chat_id)
        user_states[chat_id] = {"state": STATE_MAIN_MENU}
        bot.answer_callback_query(call.id, "Başarıyla yayınlandı!")
        bot.send_message(chat_id, "✅ Ürün tüm detaylarıyla SİTEDE YAYINLANDI!\n\nAşağıdaki panelden istediğiniz zaman düzenleyebilirsiniz:")
        show_dashboard(chat_id, pid)
    except Exception as e:
        bot.send_message(chat_id, f"Hata: {str(e)}")

# ==========================================
# Dashboard Edit Handlers
# ==========================================
@bot.callback_query_handler(func=lambda call: call.data.startswith('del_'))
def handle_delete(call):
    pid = call.data.split('_')[1]
    supabase.table("products").delete().eq("id", pid).execute()
    bot.answer_callback_query(call.id, "✅ Ürün silindi.")
    bot.edit_message_text("🗑 Ürün veritabanından ve siteden tamamen silindi.", chat_id=call.message.chat.id, message_id=call.message.message_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith('ed_'))
def handle_edit(call):
    action, pid = call.data.split('_')[1], call.data.split('_')[2]
    chat_id = call.message.chat.id
    bot.answer_callback_query(call.id)

    if action == 'pr':
        bot.send_message(chat_id, "Lütfen yeni indirim miktarını veya fiyatı yazın (Örnek: %15 İndirim):")
        user_states[chat_id] = {"state": STATE_EDIT_PRICE_DISCOUNT, "pid": pid}
    elif action == 'st':
        bot.send_message(chat_id, "Lütfen mevcut stok miktarını SAYI olarak girin (Örnek: 3):")
        user_states[chat_id] = {"state": STATE_EDIT_STOCK, "pid": pid}
    elif action == 'va':
        bot.send_message(chat_id, "Bu fiyat veya indirim ne zamana kadar geçerli? (Örnek: Sadece önümüzdeki 3 gün):")
        user_states[chat_id] = {"state": STATE_EDIT_VALIDITY, "pid": pid}
    elif action == 'co':
        bot.send_message(chat_id, "Satıcı adını ve WhatsApp numarasını tire (-) ile girin.\nÖrnek: Hadi - +905522640025")
        user_states[chat_id] = {"state": STATE_EDIT_CONTACT, "pid": pid}

@bot.message_handler(func=lambda m: user_states.get(m.chat.id, {}).get("state") in [STATE_EDIT_PRICE_DISCOUNT, STATE_EDIT_STOCK, STATE_EDIT_VALIDITY, STATE_EDIT_CONTACT])
def process_edit(message):
    chat_id = message.chat.id
    state_data = user_states.get(chat_id)
    if not state_data:
        return
    state, pid, val = state_data["state"], state_data["pid"], message.text

    try:
        if state == STATE_EDIT_PRICE_DISCOUNT:
            supabase.table("products").update({"discount": val}).eq("id", pid).execute()
        elif state == STATE_EDIT_STOCK:
            supabase.table("products").update({"stock_count": int(val) if val.isdigit() else 1}).eq("id", pid).execute()
        elif state == STATE_EDIT_VALIDITY:
            supabase.table("products").update({"validity_period": val}).eq("id", pid).execute()
        elif state == STATE_EDIT_CONTACT:
            parts = val.split('-')
            name = parts[0].strip() if len(parts) > 0 else val
            phone = parts[1].strip() if len(parts) > 1 else ""
            supabase.table("products").update({"salesperson_name": name, "contact_phone": phone}).eq("id", pid).execute()

        user_states[chat_id] = {"state": STATE_MAIN_MENU}
        bot.send_message(chat_id, "✅ Sitenin veritabanında başarıyla güncellendi.")
        show_dashboard(chat_id, pid)
    except Exception as e:
        bot.send_message(chat_id, f"❌ Güncelleme hatası: {e}")

# ==========================================
# Webhook giriş noktası (Vercel Serverless)
# ==========================================
def _extract_chat_id(update):
    if getattr(update, "message", None):
        return update.message.chat.id
    if getattr(update, "callback_query", None):
        return update.callback_query.message.chat.id
    if getattr(update, "edited_message", None):
        return update.edited_message.chat.id
    return None

class handler(BaseHTTPRequestHandler):
    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_GET(self):
        # Sağlık kontrolü — tarayıcıda açınca durum görürsünüz.
        status = "hazır" if _READY else "ortam değişkenleri eksik"
        self._send(200, f"kasrmobilya telegram webhook — {status}")

    def do_POST(self):
        # Telegram'ın tekrar tekrar denemesini önlemek için her durumda 200 döneriz.
        if not (_READY and supabase is not None):
            print("Webhook called but environment not configured.")
            self._send(200, "not-configured")
            return
        # GÜVENLİK: yalnızca Telegram'dan gelen (doğru secret_token başlıklı)
        # istekleri işle; sahte/dış istekleri sessizce yok say.
        if WEBHOOK_SECRET and self.headers.get("X-Telegram-Bot-Api-Secret-Token", "") != WEBHOOK_SECRET:
            print("Webhook: invalid or missing secret token — update ignored.")
            self._send(200, "ignored")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            update_dict = json.loads(raw.decode("utf-8"))
            update = telebot.types.Update.de_json(update_dict)
            chat_id = _extract_chat_id(update)

            if chat_id is not None:
                sess = load_session(chat_id)
                if sess:
                    user_states[chat_id] = sess
                bot.process_new_updates([update])
                persist_session(chat_id)
                user_states.pop(chat_id, None)
        except Exception as e:
            print(f"Webhook error: {e}")
        self._send(200, "ok")
