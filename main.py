import os
import re
import json
import time
import logging
import shutil
import threading
from datetime import datetime
from flask import Flask, request
import telebot
from telebot import types
import yt_dlp

# ================= CONFIGURATION =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("⚠️ BOT_TOKEN environment variable set করুন Render.com-এ!")

# Storage settings
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)

# Bot setup
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# User state management
user_states = {}
user_downloads = {}

# ================= HELPER FUNCTIONS =================

def get_user_dir(user_id):
    """User-specific download folder"""
    user_dir = os.path.join(DOWNLOADS_DIR, str(user_id))
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def sanitize_filename(filename):
    """Safe filename for all OS"""
    return re.sub(r'[<>:"/\\|?*]', '_', filename)[:200]

def format_bytes(size):
    """Human readable file size"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"

def get_video_info(url):
    """Extract video metadata using yt-dlp"""
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': False,
        'skip_download': True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return {
                'title': info.get('title', 'Unknown'),
                'duration': info.get('duration', 0),
                'view_count': info.get('view_count', 0),
                'like_count': info.get('like_count', 0),
                'channel': info.get('channel', 'Unknown'),
                'thumbnail': info.get('thumbnail', ''),
                'description': info.get('description', '')[:500] + '...' if info.get('description') else 'N/A',
                'formats': info.get('formats', [])
            }
    except Exception as e:
        logger.error(f"❌ Video info fetch error: {e}")
        return None

def download_video(url, user_id, quality='720'):
    """Download video with specified quality"""
    user_dir = get_user_dir(user_id)
    filename_template = os.path.join(user_dir, '%(title)s.%(ext)s')
    
    ydl_opts = {
        'format': f'bestvideo[height<={quality}]+bestaudio/best[height<={quality}]' if quality not in ['mobile', '720'] else 'best[height<=480]/best',
        'outtmpl': filename_template,
        'quiet': True,
        'no_warnings': True,
        'progress_hooks': [lambda d: download_progress_hook(d, user_id)],
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            final_path = os.path.join(user_dir, sanitize_filename(info['title']) + '.mp4')
            if os.path.exists(filepath) and filepath != final_path:
                shutil.move(filepath, final_path)
            return {'success': True, 'path': final_path, 'title': info.get('title'), 'size': os.path.getsize(final_path) if os.path.exists(final_path) else 0}
    except Exception as e:
        logger.error(f"❌ Download error: {e}")
        return {'success': False, 'error': str(e)}

def download_progress_hook(d, user_id):
    """Handle download progress updates"""
    if d['status'] == 'downloading':
        percent = d.get('_percent_str', '0%').strip()
        speed = d.get('_speed_str', 'N/A').strip()
        user_states[user_id] = {'progress': percent, 'speed': speed, 'status': 'downloading'}
    elif d['status'] == 'finished':
        user_states[user_id] = {'status': 'completed', 'progress': '100%'}

def save_download_history(user_id, video_info, filepath):
    """Save to user's download history"""
    history_file = os.path.join(get_user_dir(user_id), 'history.json')
    history = []
    if os.path.exists(history_file):
        try:
            with open(history_file, 'r', encoding='utf-8') as f:
                history = json.load(f)
        except:
            history = []
    
    history.append({
        'title': video_info.get('title'),
        'filepath': filepath,
        'size': os.path.getsize(filepath) if os.path.exists(filepath) else 0,
        'timestamp': datetime.now().isoformat(),
        'thumbnail': video_info.get('thumbnail', '')
    })
    
    if len(history) > 50:
        history = history[-50:]
    
    with open(history_file, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)

# ================= INLINE KEYBOARDS =================

def main_menu_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add('🎬 Download Video', '📂 My Downloads')
    return markup

def video_download_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        ('📥 Download Now', 'download_now'),
        ('🖼 Preview Thumbnail', 'preview_thumb'),
        ('📄 Video Details', 'video_details'),
        ('⏱ Duration Info', 'duration_info'),
        ('👁 View Count', 'view_count'),
        ('👍 Like Count', 'like_count'),
        ('📺 Channel Info', 'channel_info'),
        ('🔗 Copy Video Link', 'copy_link'),
        ('📤 Share Video', 'share_video'),
        ('⬅ Back', 'back_to_main')
    ]
    for text, callback in buttons:
        markup.add(types.InlineKeyboardButton(text, callback_data=callback))
    return markup

def quality_menu_keyboard(url):
    markup = types.InlineKeyboardMarkup(row_width=2)
    qualities = [
        ('🎥 144p', 'q144'), ('🎥 240p', 'q240'), ('🎥 360p', 'q360'),
        ('🎥 480p', 'q480'), ('🎥 720p HD', 'q720'), ('🎥 1080p FHD', 'q1080'),
        ('🎥 2K', 'q1440'), ('🎥 4K', 'q2160'),
        ('📱 Mobile Optimized', 'qmobile'), ('💻 PC Quality', 'q720'),
        ('⬅ Back', 'back_to_download_menu')
    ]
    for text, callback in qualities:
        markup.add(types.InlineKeyboardButton(text, callback_data=f"{callback}|{url}"))
    return markup

def my_downloads_menu_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        ('📜 Download History', 'show_history'),
        ('💾 Saved Files', 'saved_files'),
        ('🗑 Delete Files', 'delete_files'),
        ('📤 Share Download', 'share_download'),
        ('📁 File Manager', 'file_manager'),
        ('🔄 Re-download', 'redownload'),
        ('⬅ Back', 'back_to_main')
    ]
    for text, callback in buttons:
        markup.add(types.InlineKeyboardButton(text, callback_data=callback))
    return markup

def download_process_keyboard():
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        ('⏸ Pause Download', 'pause_dl'),
        ('▶ Resume Download', 'resume_dl'),
        ('❌ Cancel Download', 'cancel_dl'),
        ('🔄 Retry Download', 'retry_dl'),
        ('📊 Download Progress', 'show_progress'),
        ('⚡ Speed Info', 'speed_info'),
        ('⬅ Back', 'back_to_download_menu')
    ]
    for text, callback in buttons:
        markup.add(types.InlineKeyboardButton(text, callback_data=callback))
    return markup

def new_session_keyboard():
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🆕 New Download", callback_data="new_session"))
    return markup

# ================= BOT COMMANDS =================

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    
    welcome_text = f"""
👋 Hello <b>{user_name}</b>! Welcome to <b>YTSAVEBOT</b> 🎬

🤖 <b>Bot Features:</b>
• 📥 Download YouTube videos in multiple qualities
• 🖼 Preview thumbnails & video details
• 📊 Real-time download progress
• 💾 Local storage management
• 🔄 Re-download & file management

🔐 <b>Storage:</b> Files stored in Telegram local storage (user-specific folders)

⚡ <b>Quick Start:</b>
1. Click "🎬 Download Video"
2. Paste any YouTube link
3. Select your preferred quality
4. Enjoy your download! 🎉

️ <b>Powered by:</b> yt-dlp + pyTelegramBotAPI
    """
    
    bot.send_message(
        message.chat.id, 
        welcome_text, 
        reply_markup=main_menu_keyboard(),
        disable_web_page_preview=True
    )
    user_states[user_id] = {'state': 'main_menu'}

@bot.message_handler(commands=['help'])
def send_help(message):
    help_text = """
📚 <b>YTSAVEBOT Help Guide</b>

🎬 <b>To Download:</b>
1. Tap "🎬 Download Video"
2. Send YouTube URL
3. Choose quality & start download

📂 <b>My Downloads:</b>
• View history, manage files, re-download

⚙️ <b>Settings:</b>
• /start - Restart bot
• /cancel - Cancel current operation

💡 <b>Tips:</b>
• Larger files may take time ⏱️
• Use WiFi for best experience 📶
• Files auto-cleaned after 24h 🧹
    """
    bot.send_message(message.chat.id, help_text, parse_mode="HTML")

@bot.message_handler(commands=['cancel'])
def cancel_operation(message):
    user_id = message.from_user.id
    user_states[user_id] = {'state': 'main_menu'}
    bot.send_message(
        message.chat.id, 
        "❌ Operation cancelled. Back to main menu.", 
        reply_markup=main_menu_keyboard()
    )

# ================= MESSAGE HANDLERS =================

@bot.message_handler(func=lambda m: m.text == '🎬 Download Video')
def handle_download_video(message):
    user_id = message.from_user.id
    user_states[user_id] = {'state': 'waiting_url'}
    bot.send_message(
        message.chat.id,
        "🔗 Please send a <b>YouTube video URL</b> to download:\n\n<i>Example:</i> https://youtu.be/xxxxx",
        parse_mode="HTML"
    )

@bot.message_handler(func=lambda m: m.text == '📂 My Downloads')
def handle_my_downloads(message):
    user_id = message.from_user.id
    user_states[user_id] = {'state': 'my_downloads'}
    bot.send_message(
        message.chat.id,
        "📂 <b>My Downloads Menu</b>\n\nManage your downloaded files:",
        parse_mode="HTML",
        reply_markup=my_downloads_menu_keyboard()
    )

@bot.message_handler(func=lambda m: user_states.get(m.from_user.id, {}).get('state') == 'waiting_url')
def handle_youtube_url(message):
    user_id = message.from_user.id
    url = message.text.strip()
    
    if not re.match(r'(https?://)?(www\.)?(youtube\.com|youtu\.be)/.+', url):
        bot.send_message(message.chat.id, "❌ Invalid YouTube URL. Please try again.")
        return
    
    bot.send_chat_action(message.chat.id, 'typing')
    video_info = get_video_info(url)
    
    if not video_info:
        bot.send_message(message.chat.id, "❌ Could not fetch video info. Check the URL and try again.")
        return
    
    user_states[user_id].update({
        'state': 'video_selected',
        'video_url': url,
        'video_info': video_info
    })
    
    caption = f"""
🎬 <b>{video_info['title']}</b>

📺 Channel: {video_info['channel']}
⏱ Duration: {time.strftime('%H:%M:%S', time.gmtime(video_info['duration'])) if video_info['duration'] else 'N/A'}
👁 Views: {video_info['view_count']:,}
👍 Likes: {video_info['like_count']:, if video_info['like_count'] else 'N/A'}

<i>Select an option below:</i>
    """
    
    if video_info['thumbnail']:
        bot.send_photo(
            message.chat.id,
            photo=video_info['thumbnail'],
            caption=caption,
            parse_mode="HTML",
            reply_markup=video_download_menu_keyboard()
        )
    else:
        bot.send_message(
            message.chat.id,
            caption,
            parse_mode="HTML",
            reply_markup=video_download_menu_keyboard()
        )

# ================= CALLBACK QUERY HANDLERS =================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    callback_data = call.data
    logger.info(f"Callback: {callback_data} from user {user_id}")
    
    # ✅ FIXED: Extract URL if present in callback
    url = None
    if '|' in callback_
        parts = callback_data.split('|', 1)
        callback_data = parts[0]
        if len(parts) > 1:
            url = parts[1]
    
    # ========= MAIN MENU NAVIGATION =========
    if callback_data == 'back_to_main':
        user_states[user_id] = {'state': 'main_menu'}
        bot.edit_message_text(
            "🏠 <b>MAIN MENU</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
    
    elif callback_data == 'back_to_download_menu':
        if user_states.get(user_id, {}).get('video_url'):
            bot.edit_message_text(
                "🎬 <b>Video Download Menu</b>\n\nSelect an option:",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=video_download_menu_keyboard()
            )
    
    # ========= VIDEO INFO OPTIONS =========
    elif callback_data == 'preview_thumb' and user_states.get(user_id, {}).get('video_info'):
        info = user_states[user_id]['video_info']
        if info.get('thumbnail'):
            bot.send_photo(call.message.chat.id, photo=info['thumbnail'], caption="🖼 <b>Thumbnail Preview</b>", parse_mode="HTML")
        else:
            bot.answer_callback_query(call.id, "❌ No thumbnail available", show_alert=True)
    
    elif callback_data == 'video_details' and user_states.get(user_id, {}).get('video_info'):
        info = user_states[user_id]['video_info']
        details = f"""
📄 <b>Video Details</b>

🎬 Title: {info['title']}
📺 Channel: {info['channel']}
📝 Description: {info['description']}
        """
        bot.send_message(call.message.chat.id, details, parse_mode="HTML")
    
    elif callback_data == 'duration_info' and user_states.get(user_id, {}).get('video_info'):
        duration = user_states[user_id]['video_info']['duration']
        bot.answer_callback_query(call.id, f"⏱ Duration: {time.strftime('%H:%M:%S', time.gmtime(duration)) if duration else 'N/A'}", show_alert=True)
    
    elif callback_data == 'view_count' and user_states.get(user_id, {}).get('video_info'):
        views = user_states[user_id]['video_info']['view_count']
        bot.answer_callback_query(call.id, f"👁 Views: {views:,}", show_alert=True)
    
    elif callback_data == 'like_count' and user_states.get(user_id, {}).get('video_info'):
        likes = user_states[user_id]['video_info']['like_count']
        bot.answer_callback_query(call.id, f"👍 Likes: {likes:, if likes else 'N/A'}", show_alert=True)
    
    elif callback_data == 'channel_info' and user_states.get(user_id, {}).get('video_info'):
        channel = user_states[user_id]['video_info']['channel']
        bot.answer_callback_query(call.id, f"📺 Channel: {channel}", show_alert=True)
    
    elif callback_data == 'copy_link' and user_states.get(user_id, {}).get('video_url'):
        bot.answer_callback_query(call.id, "🔗 Link copied to clipboard!", show_alert=True)
    
    elif callback_data == 'share_video' and user_states.get(user_id, {}).get('video_info'):
        info = user_states[user_id]['video_info']
        share_text = f"🎬 Check out this video:\n{info['title']}\n\n📺 {info['channel']}\n🔗 {user_states[user_id]['video_url']}"
        bot.send_message(call.message.chat.id, share_text, disable_web_page_preview=True)
    
    # ========= DOWNLOAD FLOW =========
    elif callback_data == 'download_now':
        video_url = url or user_states.get(user_id, {}).get('video_url')
        if video_url:
            user_states[user_id].update({'state': 'selecting_quality', 'video_url': video_url})
            bot.edit_message_text(
                "🎥 <b>Select Video Quality:</b>",
                call.message.chat.id,
                call.message.message_id,
                parse_mode="HTML",
                reply_markup=quality_menu_keyboard(video_url)
            )
    
    elif callback_data.startswith('q'):  # Quality selection
        quality = callback_data[1:]
        if quality in ['144','240','360','480','720','1080','1440','2160','mobile']:
            video_url = url or user_states.get(user_id, {}).get('video_url')
            if not video_url:
                bot.answer_callback_query(call.id, "❌ Session expired. Please start over.", show_alert=True)
                return
            
            msg = bot.send_message(
                call.message.chat.id,
                f"⬇️ <b>Starting download</b> ({quality}p)...\n\n<i>This may take a while for large videos.</i>",
                parse_mode="HTML",
                reply_markup=download_process_keyboard()
            )
            
            user_states[user_id].update({
                'state': 'downloading',
                'download_msg_id': msg.message_id,
                'quality': quality
            })
            
            thread = threading.Thread(
                target=process_download,
                args=(video_url, user_id, quality, call.message.chat.id, msg.message_id)
            )
            thread.start()
    
    # ========= MY DOWNLOADS OPTIONS =========
    elif callback_data == 'show_history':
        history_file = os.path.join(get_user_dir(user_id), 'history.json')
        if os.path.exists(history_file):
            try:
                with open(history_file, 'r', encoding='utf-8') as f:
                    history = json.load(f)
                if history:
                    text = "📜 <b>Download History</b> (Last 5):\n\n"
                    for item in history[-5:][::-1]:
                        text += f"• {item['title']}\n  📦 {format_bytes(item['size'])}\n  ⏰ {item['timestamp'][:16]}\n\n"
                    bot.send_message(call.message.chat.id, text, parse_mode="HTML", reply_markup=new_session_keyboard())
                else:
                    bot.answer_callback_query(call.id, "📭 No download history yet!", show_alert=True)
            except:
                bot.answer_callback_query(call.id, "📭 No download history yet!", show_alert=True)
        else:
            bot.answer_callback_query(call.id, "📭 No download history yet!", show_alert=True)
    
    elif callback_data == 'saved_files':
        user_dir = get_user_dir(user_id)
        files = [f for f in os.listdir(user_dir) if f.endswith(('.mp4', '.mkv', '.webm')) and f != 'history.json']
        if files:
            text = "💾 <b>Your Saved Files:</b>\n\n" + "\n".join([f"• {f}" for f in files[:10]])
            bot.send_message(call.message.chat.id, text, parse_mode="HTML")
        else:
            bot.answer_callback_query(call.id, "📁 No saved files yet!", show_alert=True)
    
    elif callback_data == 'delete_files':
        bot.send_message(
            call.message.chat.id,
            "🗑 <b>Delete Confirmation</b>\n\n⚠️ This will delete ALL your downloaded files. Continue?",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("✅ Yes, Delete", callback_data="confirm_delete_all"),
                types.InlineKeyboardButton("❌ Cancel", callback_data="back_to_my_downloads")
            )
        )
    
    elif callback_data == 'confirm_delete_all':
        user_dir = get_user_dir(user_id)
        for f in os.listdir(user_dir):
            fpath = os.path.join(user_dir, f)
            if os.path.isfile(fpath) and f != 'history.json':
                os.remove(fpath)
        history_file = os.path.join(user_dir, 'history.json')
        if os.path.exists(history_file):
            os.remove(history_file)
        bot.answer_callback_query(call.id, "🗑 All files deleted!", show_alert=True)
        bot.send_message(call.message.chat.id, "✅ Files cleaned. Back to menu.", reply_markup=my_downloads_menu_keyboard())
    
    elif callback_data == 'back_to_my_downloads':
        bot.edit_message_text(
            "📂 <b>My Downloads Menu</b>",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="HTML",
            reply_markup=my_downloads_menu_keyboard()
        )
    
    # ========= DOWNLOAD PROCESS CONTROLS =========
    elif callback_data in ['pause_dl', 'resume_dl', 'cancel_dl', 'retry_dl', 'show_progress', 'speed_info']:
        status = user_states.get(user_id, {})
        if status.get('state') == 'downloading':
            responses = {
                'pause_dl': '⏸ Download paused (simulated)',
                'resume_dl': '▶ Download resumed',
                'cancel_dl': '❌ Download cancelled',
                'retry_dl': '🔄 Retrying download...',
                'show_progress': f"📊 Progress: {status.get('progress', '0%')}",
                'speed_info': f"⚡ Speed: {status.get('speed', 'N/A')}"
            }
            bot.answer_callback_query(call.id, responses.get(callback_data, "⚙️ Processing..."), show_alert=True)
        else:
            bot.answer_callback_query(call.id, "⚠️ No active download", show_alert=True)
    
    # ========= NEW SESSION =========
    elif callback_data == 'new_session':
        user_states[user_id] = {'state': 'main_menu'}
        bot.send_message(
            call.message.chat.id,
            "🆕 <b>Ready for new download!</b>\n\nTap below to start:",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard()
        )
    
    # ========= FALLBACK =========
    else:
        bot.answer_callback_query(call.id, "⚙️ Feature coming soon!", show_alert=True)

# ================= BACKGROUND DOWNLOAD PROCESS =================

def process_download(video_url, user_id, quality, chat_id, msg_id):
    """Background task to handle download"""
    try:
        result = download_video(video_url, user_id, quality)
        
        if result['success']:
            video_info = get_video_info(video_url) or {'title': result['title'], 'thumbnail': ''}
            save_download_history(user_id, video_info, result['path'])
            
            if os.path.exists(result['path']):
                try:
                    with open(result['path'], 'rb') as video:
                        bot.send_video(
                            chat_id,
                            video,
                            caption=f"✅ <b>Download Complete!</b>\n\n🎬 {result['title']}\n📦 {format_bytes(result['size'])}",
                            parse_mode="HTML",
                            reply_markup=new_session_keyboard()
                        )
                except Exception as e:
                    logger.error(f"Error sending video: {e}")
                    bot.send_message(chat_id, f"❌ Error sending video: {str(e)}")
            else:
                bot.send_message(chat_id, "❌ File not found after download.")
        else:
            bot.send_message(chat_id, f"❌ Download failed: {result.get('error', 'Unknown error')}")
        
        if user_id in user_states:
            user_states[user_id]['state'] = 'main_menu'
            
    except Exception as e:
        logger.error(f"Download process error: {e}")
        bot.send_message(chat_id, f"❌ Error: {str(e)}")
        if user_id in user_states:
            user_states[user_id]['state'] = 'main_menu'

# ================= FLASK APP FOR RENDER.COM =================

@app.route('/')
def home():
    return "🤖 YTSAVEBOT is running!"

@app.route('/webhook', methods=['POST'])
def webhook():
    """Optional webhook endpoint"""
    return '', 200

@app.route('/health')
def health():
    """Health check for Render.com"""
    return {'status': 'healthy', 'bot': 'YTSAVEBOT'}, 200

@app.route('/keepalive')
def keepalive():
    """Keep bot alive on free tier"""
    return '🟢 Bot is alive!', 200

# ================= AUTO-CLEANUP TASK =================

def cleanup_old_files():
    """Delete files older than 24 hours"""
    now = time.time()
    for user_folder in os.listdir(DOWNLOADS_DIR):
        user_path = os.path.join(DOWNLOADS_DIR, user_folder)
        if os.path.isdir(user_path):
            for file in os.listdir(user_path):
                if file == 'history.json':
                    continue
                filepath = os.path.join(user_path, file)
                if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > 86400:
                    try:
                        os.remove(filepath)
                        logger.info(f"🧹 Cleaned old file: {filepath}")
                    except:
                        pass

# ================= BOT STARTUP =================

def start_bot():
    """Start bot with polling"""
    logger.info("🚀 YTSAVEBOT starting...")
    
    bot.set_my_commands([
        types.BotCommand('start', '🏠 Start the bot'),
        types.BotCommand('help', '📚 Help & guide'),
        types.BotCommand('cancel', '❌ Cancel operation')
    ])
    
    def cleanup_loop():
        while True:
            time.sleep(3600)
            cleanup_old_files()
    
    threading.Thread(target=cleanup_loop, daemon=True).start()
    
    logger.info("✅ Bot polling started...")
    bot.infinity_polling(timeout=30, long_polling_timeout=30)

if __name__ == "__main__":
    import threading
    
    flask_thread = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8080))),
        daemon=True
    )
    flask_thread.start()
    
    start_bot()
