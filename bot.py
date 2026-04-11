import os
import sys
import time
import logging
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.request import HTTPXRequest
from telegram import error as tg_error
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters, CallbackQueryHandler

from db import WhitelistDB
from youtube import download_manager, VideoInfo

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable
BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")

# Admin chat_id — has full access to whitelist management
ADMIN_CHAT_ID = os.environ.get('ADMIN_CHAT_ID')
if not ADMIN_CHAT_ID:
    raise ValueError("ADMIN_CHAT_ID environment variable is required")

ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)

# Password that users must provide to add themselves via /me
ACCESS_PASSWORD = os.environ.get('ACCESS_PASSWORD', '')

# Database path
DB_PATH = Path("/tmp/downloads/whitelist.db")

# Initialize database
whitelist_db = WhitelistDB(DB_PATH)
whitelist_db.init_db()


def is_admin(chat_id: int) -> bool:
    """Check if chat_id is the admin"""
    return chat_id == ADMIN_CHAT_ID


def check_access(chat_id: int) -> bool:
    """Check if chat_id has access to the bot"""
    return whitelist_db.is_allowed(chat_id)


async def send_access_denied(update: Update):
    """Send access denied message with chat_id info"""
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"❌ У вас нет доступа к этому боту.\n"
        f"Ваш chat_id: {chat_id}\n"
        f"Отправьте его администратору для получения доступа."
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if not check_access(update.message.chat_id):
        await send_access_denied(update)
        return

    await update.message.reply_text(
        "👋 Привет! Отправьте мне ссылку на YouTube видео, и я скачаю его для вас.\n\n"
        "Просто вставьте URL, а я сделаю всё остальное!"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    if not check_access(update.message.chat_id):
        await send_access_denied(update)
        return

    await update.message.reply_text(
        "📺 *Загрузчик видео с YouTube*\n\n"
        "Как использовать:\n"
        "1. Скопируйте URL видео с YouTube\n"
        "2. Вставьте его сюда\n"
        "3. Я скачаю и отправлю его вам\n\n"
        "Поддерживаемые форматы: MP4 (видео)\n"
        "Максимальный размер файла: 2000МБ (2ГБ)",
        parse_mode='Markdown'
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming messages with video URLs"""
    if not check_access(update.message.chat_id):
        await send_access_denied(update)
        return

    text = update.message.text
    if not text:
        return

    status_msg = await update.message.reply_text("🔍 Получаю информацию о видео...")

    try:
        # Get video info without downloading
        video_info = await download_manager.get_video_info(text)

        # Delete status message
        await status_msg.delete()

        # Build keyboard with quality options
        keyboard = []
        for fmt in video_info.formats:
            # Build button text
            size_text = ""
            if fmt.filesize:
                size_mb = fmt.filesize / (1024 * 1024)
                size_text = f" ({size_mb:.0f}МБ)"
            
            button_text = f"{fmt.format_note}{size_text}"
            
            # Callback data: format_index
            # We'll store video info in context.user_data
            callback_data = f"quality_{len(keyboard)}"
            
            keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        # Store video info for later use
        context.user_data['pending_video'] = {
            'url': video_info.url,
            'title': video_info.title,
            'formats': [(fmt.format_str, fmt.format_note) for fmt in video_info.formats]
        }

        # Send message with quality options
        await update.message.reply_text(
            f"📺 *{video_info.title}*\n\n"
            f"Выберите качество видео:",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except ValueError as e:
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")
    except Exception as e:
        logger.error(f"Error getting video info: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Ошибка: {str(e)}")


async def handle_quality_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quality button clicks"""
    query = update.callback_query
    await query.answer()

    # Check access via query.message.chat_id
    if not check_access(query.message.chat_id):
        await query.answer("У вас нет доступа к этому боту.", show_alert=True)
        return

    # Get stored video info
    video_data = context.user_data.get('pending_video')
    if not video_data:
        await query.edit_message_text("❌ Информация о видео устарела. Отправьте ссылку снова.")
        return

    # Parse quality index from callback data
    quality_index = int(query.data.split('_')[1])
    
    # Get format info
    if quality_index >= len(video_data['formats']):
        await query.edit_message_text("❌ Неверный выбор качества.")
        return

    format_str, format_note = video_data['formats'][quality_index]
    video_url = video_data['url']
    video_title = video_data['title']

    # Update message to show downloading progress
    await query.edit_message_text(f"📥 Скачиваю видео в качестве {format_note}...\n\n📺 {video_title}")

    async def progress_callback(message: str):
        """Update progress message"""
        try:
            await context.bot.edit_message_text(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=message
            )
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")

    try:
        # Download video with selected quality
        await progress_callback(f"📥 Скачиваю видео в качестве {format_note}...")

        video_info = await download_manager.download_with_format(video_url, progress_callback, format_str)

        # Send the video file
        caption = f"📺 {video_title}"
        with open(video_info.filepath, 'rb') as video_file:
            await query.message.reply_video(
                video=video_file,
                caption=caption,
                supports_streaming=True
            )

        await query.message.reply_text("✅ Видео успешно отправлено!")

        # Clean up
        download_manager.cleanup(video_info)

        # Clear stored video info
        context.user_data.pop('pending_video', None)

    except ValueError as e:
        await query.message.reply_text(f"❌ Ошибка: {str(e)}")
    except tg_error.TimedOut as e:
        logger.error(f"Telegram API timeout: {e}", exc_info=True)
        try:
            await query.message.reply_text("❌ Превышено время ожидания. Попробуйте видео меньшего размера или повторите позже.")
        except:
            pass
    except tg_error.NetworkError as e:
        logger.error(f"Network error: {e}", exc_info=True)
        try:
            await query.message.reply_text(f"❌ Ошибка сети: {str(e)}")
        except:
            pass
    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        try:
            await query.message.reply_text(f"❌ Ошибка: {str(e)}")
        except:
            pass


async def whitelist_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add command — add chat_id to whitelist (admin only)"""
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Использование: /add <chat_id>\n"
            "Пример: /add 123456789"
        )
        return

    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный формат chat_id. Должно быть число.")
        return

    if whitelist_db.add(target_chat_id):
        await update.message.reply_text(f"✅ chat_id {target_chat_id} добавлен в белый список")
    else:
        await update.message.reply_text(f"ℹ️ chat_id {target_chat_id} уже в белом списке")


async def whitelist_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /remove command — remove chat_id from whitelist (admin only)"""
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Использование: /remove <chat_id>\n"
            "Пример: /remove 123456789"
        )
        return

    try:
        target_chat_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Неверный формат chat_id. Должно быть число.")
        return

    if whitelist_db.remove(target_chat_id):
        await update.message.reply_text(f"✅ chat_id {target_chat_id} удалён из белого списка")
    else:
        await update.message.reply_text(f"❌ chat_id {target_chat_id} не найден в белом списке")


async def whitelist_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /list command — show all whitelisted chat_ids (admin only)"""
    if not is_admin(update.message.chat_id):
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return

    chat_ids = whitelist_db.list_all()

    if not chat_ids:
        await update.message.reply_text("📋 Белый список пуст")
        return

    text = "📋 Белый список:\n" + "\n".join(f"• `{cid}`" for cid in chat_ids)
    await update.message.reply_text(text, parse_mode='Markdown')


async def whitelist_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /me command — add current chat to whitelist (admin or password)"""
    chat_id = update.message.chat_id

    if not is_admin(chat_id):
        if not ACCESS_PASSWORD:
            await update.message.reply_text(
                "❌ Саморегистрация отключена. Обратитесь к администратору."
            )
            return

        if not context.args or context.args[0] != ACCESS_PASSWORD:
            await update.message.reply_text(
                "❌ Неверный пароль.\n"
                f"Использование: /me <пароль>"
            )
            return

    if whitelist_db.add(chat_id):
        await update.message.reply_text(f"✅ Ваш chat_id ({chat_id}) добавлен в белый список")
    else:
        await update.message.reply_text(f"ℹ️ Ваш chat_id ({chat_id}) уже в белом списке")


def main():
    """Start the bot"""
    logger.info("Starting YouTube Downloader Bot...")

    # Use local Bot API server if configured (allows files up to 2000MB)
    local_api_url = os.environ.get('LOCAL_BOT_API_URL')

    if local_api_url:
        logger.info(f"Using local Bot API server: {local_api_url}")
        # Wait for local API server to be ready
        health_url = f"{local_api_url}/bot{BOT_TOKEN}/getMe"
        max_retries = 30
        for attempt in range(1, max_retries + 1):
            try:
                import urllib.request
                resp = urllib.request.urlopen(health_url, timeout=5)
                if resp.status == 200:
                    logger.info("Local Bot API server is ready!")
                    break
            except Exception:
                if attempt >= max_retries:
                    logger.error(f"Local Bot API server not ready after {max_retries} attempts, exiting")
                    sys.exit(1)
                logger.info(f"Waiting for local Bot API server... (attempt {attempt}/{max_retries})")
                time.sleep(2)

        request = HTTPXRequest(
            connect_timeout=300,
            read_timeout=300,
            write_timeout=300,
            pool_timeout=300,
        )
        application = Application.builder().token(BOT_TOKEN).request(request).base_url(f"{local_api_url}/bot").build()
    else:
        logger.info("Using default Telegram Bot API (50MB file limit)")
        # Default Telegram API - shorter timeouts are fine for smaller files
        request = HTTPXRequest(
            connect_timeout=30,
            read_timeout=30,
            write_timeout=300,
            pool_timeout=30
        )
        application = Application.builder().token(BOT_TOKEN).request(request).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", whitelist_add))
    application.add_handler(CommandHandler("remove", whitelist_remove))
    application.add_handler(CommandHandler("list", whitelist_list))
    application.add_handler(CommandHandler("me", whitelist_me))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(handle_quality_selection))

    logger.info("Bot is running!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
