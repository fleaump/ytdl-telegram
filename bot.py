import os
import logging
from pathlib import Path
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

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
        "Максимальный размер файла: 50МБ",
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

    status_msg = await update.message.reply_text("🔍 Обрабатываю ваш запрос...")

    async def progress_callback(message: str):
        """Update progress message"""
        try:
            await context.bot.edit_message_text(
                chat_id=update.message.chat_id,
                message_id=status_msg.message_id,
                text=message
            )
        except Exception as e:
            logger.warning(f"Failed to update progress: {e}")

    try:
        await progress_callback("🔍 Получаю информацию о видео...")

        video_info = await download_manager.download(text, progress_callback)

        # Send the video file
        caption = f"📺 {video_info.title}"
        with open(video_info.filepath, 'rb') as video_file:
            await update.message.reply_video(
                video=video_file,
                caption=caption,
                supports_streaming=True
            )

        await progress_callback("✅ Видео успешно отправлено!")

        # Clean up
        download_manager.cleanup(video_info)

    except ValueError as e:
        # User-friendly errors
        await progress_callback(f"❌ Ошибка: {str(e)}")
    except Exception as e:
        logger.error(f"Error processing video: {e}", exc_info=True)
        try:
            await progress_callback(f"❌ Ошибка: {str(e)}")
        except:
            await update.message.reply_text(f"❌ Ошибка: {str(e)}")


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

    application = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("add", whitelist_add))
    application.add_handler(CommandHandler("remove", whitelist_remove))
    application.add_handler(CommandHandler("list", whitelist_list))
    application.add_handler(CommandHandler("me", whitelist_me))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot is running!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == '__main__':
    main()
