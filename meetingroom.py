import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue
)
import sqlite3
from datetime import datetime, timedelta, date
import calendar
import pytz
import asyncio

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = '7241979147:AAF6tcOzQqpwsXdyJz5HjskshXp4zfXFYIA'
ADMIN_ID = 336723881
TIMEZONE = pytz.timezone('Europe/Moscow')
REMINDER_MINUTES = 15
PORT = int(os.environ.get('PORT', 8443))

# Добавьте недостающую функцию show_calendar
async def show_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: int, year: int, month: int):
    query = update.callback_query
    await query.edit_message_text(
        f"Календарь бронирований: {get_room_name(room_id)}\n"
        "🔴 — есть бронирования, 🟢 — свободный день\n"
        "Выберите день:",
        reply_markup=generate_calendar(year, month, room_id),
        parse_mode='HTML'
    )

async def start_calendar_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        query = update.callback_query
    else:
        query = None
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT room_id, name FROM rooms')
    rooms = cursor.fetchall()
    conn.close()
    if not rooms:
        message_text = "Нет доступных переговорных комнат."
    else:
        message_text = "Выберите переговорную комнату для просмотра календаря:"
    keyboard = []
    for room_id, name in rooms:
        keyboard.append([InlineKeyboardButton(name, callback_data=f'select_calendar_room_{room_id}')])
    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_main')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        is_admin INTEGER DEFAULT 0,
        is_active INTEGER DEFAULT 0
    )''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS rooms (
        room_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT
    )''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS bookings (
        booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        room_id INTEGER,
        user_id INTEGER,
        start_time DATETIME,
        end_time DATETIME,
        is_recurring INTEGER DEFAULT 0,
        recurrence_pattern TEXT,
        parent_booking_id INTEGER DEFAULT NULL,
        FOREIGN KEY (room_id) REFERENCES rooms (room_id),
        FOREIGN KEY (user_id) REFERENCES users (user_id)
    )''')

    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin_notifications (
        notification_id INTEGER PRIMARY KEY AUTOINCREMENT,
        booking_id INTEGER,
        admin_id INTEGER,
        notified INTEGER DEFAULT 0,
        FOREIGN KEY (booking_id) REFERENCES bookings (booking_id),
        FOREIGN KEY (admin_id) REFERENCES users (user_id)
    )''')

    cursor.execute('SELECT 1 FROM rooms LIMIT 1')
    if not cursor.fetchone():
        cursor.execute('INSERT INTO rooms (name, description) VALUES (?, ?)',
                       ("ДБ (бывш. аренда)", "Основная переговорная комната"))

    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (ADMIN_ID,))
    if not cursor.fetchone():
        cursor.execute('''
        INSERT INTO users (user_id, username, full_name, is_admin, is_active)
        VALUES (?, ?, ?, 1, 1)
        ''', (ADMIN_ID, "admin", "Администратор"))

    conn.commit()
    conn.close()

# Вспомогательные функции
def is_admin(user_id: int) -> bool:
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_admin FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def month_keyboard():
    months = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    keyboard = []
    for i in range(0, 12, 3):
        row = [
            InlineKeyboardButton(months[i], callback_data=f'month_{i+1}'),
            InlineKeyboardButton(months[i+1], callback_data=f'month_{i+2}'),
            InlineKeyboardButton(months[i+2], callback_data=f'month_{i+3}')
        ]
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def is_user_active(user_id: int) -> bool:
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_active FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result and result[0] == 1

def get_room_name(room_id: int) -> str:
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM rooms WHERE room_id = ?', (room_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else "Неизвестная комната"

def get_user_bookings(user_id: int):
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
    SELECT b.booking_id, r.name, b.start_time, b.end_time 
    FROM bookings b
    JOIN rooms r ON b.room_id = r.room_id
    WHERE b.user_id = ?
    ORDER BY b.start_time
    ''', (user_id,))
    bookings = cursor.fetchall()
    conn.close()
    return bookings

def parse_db_time(time_str: str) -> datetime:
    """Парсит время из базы данных в datetime с учетом временной зоны"""
    try:
        if isinstance(time_str, datetime):
            return time_str.replace(tzinfo=TIMEZONE)
            
        time_clean = time_str.split('+')[0].strip()
        # Пробуем разные форматы
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M:%S.%f']:
            try:
                dt = datetime.strptime(time_clean, fmt)
                return TIMEZONE.localize(dt)
            except ValueError:
                continue
        # Если ничего не сработало, возвращаем текущее время
        logger.error(f"Не удалось распарсить время: {time_str}")
        return datetime.now(TIMEZONE)
    except Exception as e:
        logger.error(f"Ошибка парсинга времени '{time_str}': {e}")
        return datetime.now(TIMEZONE)

# Клавиатуры
def admin_keyboard():
    keyboard = [
        [InlineKeyboardButton("Добавить комнату", callback_data='add_room')],
        [InlineKeyboardButton("Управление пользователями", callback_data='manage_users')],
        [InlineKeyboardButton("Просмотр бронирований", callback_data='view_bookings')],
        [InlineKeyboardButton("Календарь бронирований", callback_data='view_calendar')],
        [InlineKeyboardButton("Забронировать комнату", callback_data='book_room')],
        [InlineKeyboardButton("Мой профиль", callback_data='my_profile')]
    ]
    return InlineKeyboardMarkup(keyboard)

def user_keyboard():
    keyboard = [
        [InlineKeyboardButton("Забронировать комнату", callback_data='book_room')],
        [InlineKeyboardButton("Календарь бронирований", callback_data='view_calendar')],
        [InlineKeyboardButton("Мой профиль", callback_data='my_profile')],
        [InlineKeyboardButton("Мои бронирования", callback_data='my_bookings')]
    ]
    return InlineKeyboardMarkup(keyboard)

def profile_keyboard(is_admin: bool = False):
    keyboard = [
        [InlineKeyboardButton("Изменить имя", callback_data='change_name')],
        [InlineKeyboardButton("Назад", callback_data='back_to_main')]
    ]
    return InlineKeyboardMarkup(keyboard)

def day_keyboard(year, month):
    num_days = calendar.monthrange(year, month)[1]
    keyboard = []
    row = []
    for day in range(1, num_days + 1):
        row.append(InlineKeyboardButton(str(day), callback_data=f'day_{day}'))
        if len(row) == 7:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def generate_calendar(year: int, month: int, room_id: int) -> InlineKeyboardMarkup:
    cal = calendar.monthcalendar(year, month)
    month_name = calendar.month_name[month][:3].title()
    keyboard = [
        [InlineKeyboardButton(f"{month_name} {year}", callback_data='ignore')]
    ]
    week_days = ["Пн", "Вт", "Ср", "Чт", "Пт"]
    header_buttons = [InlineKeyboardButton(day, callback_data='ignore') for day in week_days]
    keyboard.append(header_buttons)
    
    for week in cal:
        week_buttons = []
        for i in range(5):  # Только рабочие дни
            if i < len(week) and week[i] != 0:
                day = week[i]
                date_str = f"{year}-{month:02d}-{day:02d}"
                conn = sqlite3.connect('meeting_rooms.db')
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT 1 FROM bookings 
                    WHERE room_id = ? 
                    AND date(start_time) = date(?)
                    LIMIT 1
                ''', (room_id, date_str))
                has_bookings = cursor.fetchone() is not None
                conn.close()

                emoji = "🔴" if has_bookings else "🟢"
                text = f"{emoji} {day:2d}"
                callback_data = f'day_{year}_{month}_{day}_{room_id}'
            else:
                text = "  "
                callback_data = 'ignore'

            week_buttons.append(InlineKeyboardButton(text, callback_data=callback_data))
        keyboard.append(week_buttons)

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    nav_row = [
        InlineKeyboardButton("◀️", callback_data=f'nav_{prev_year}_{prev_month}_{room_id}'),
        InlineKeyboardButton("Сегодня", callback_data=f'today_{room_id}'),
        InlineKeyboardButton("▶️", callback_data=f'nav_{next_year}_{next_month}_{room_id}')
    ]
    keyboard.append(nav_row)
    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_main')])

    return InlineKeyboardMarkup(keyboard)

def is_user_booking(booking_id: int, user_id: int) -> bool:
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT 1 FROM bookings WHERE booking_id = ? AND user_id = ?', (booking_id, user_id))
    result = cursor.fetchone()
    conn.close()
    return result is not None

# Обработчики команд
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()

    cursor.execute('''
    INSERT OR IGNORE INTO users (user_id, username, full_name, is_admin, is_active)
    VALUES (?, ?, ?, ?, 0)
    ''', (user.id, user.username, user.full_name, 1 if user.id == ADMIN_ID else 0))

    if user.id == ADMIN_ID:
        cursor.execute('UPDATE users SET is_active = 1 WHERE user_id = ?', (user.id,))

    conn.commit()
    conn.close()

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "Добро пожаловать, администратор!",
            reply_markup=admin_keyboard()
        )
    elif is_user_active(user.id):
        await update.message.reply_text(
            "Добро пожаловать в систему бронирования переговорок!",
            reply_markup=user_keyboard()
        )
    else:
        await update.message.reply_text(
            "Ваш аккаунт ожидает одобрения администратором. Доступ временно ограничен."
        )

async def start_booking_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        query = update.callback_query
    else:
        query = None

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT room_id, name FROM rooms')
    rooms = cursor.fetchall()
    conn.close()

    message_text = "Выберите переговорную комнату:" if rooms else "Нет доступных переговорных комнат."

    keyboard = []
    for room_id, name in rooms:
        keyboard.append([InlineKeyboardButton(name, callback_data=f'select_room_{room_id}')])
    keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_main')])

    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

async def handle_occupied_time(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: int, booking_date: date):
    """
    Если выбранное время занято — показывает все бронирования на этот день.
    """
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()

    cursor.execute('''
        SELECT b.start_time, b.end_time, u.full_name
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        WHERE b.room_id = ? AND date(b.start_time) = date(?)
        ORDER BY b.start_time
    ''', (room_id, booking_date))
    bookings = cursor.fetchall()
    conn.close()

    room_name = get_room_name(room_id)
    message = f"⏰ Это время занято.\n\n📅 Бронирования {room_name} на {booking_date.strftime('%d.%m.%Y')}:"

    if not bookings:
        message += "\n✅ Этот день свободен."
    else:
        for start, end, user_name in bookings:
            start_dt = parse_db_time(start)
            end_dt = parse_db_time(end)
            message += f"\n⏱ {start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')} — {user_name}"

    keyboard = [
        [InlineKeyboardButton("Выбрать другое время", callback_data=f'select_room_{room_id}')],
        [InlineKeyboardButton("Отмена", callback_data='back_to_main')]
    ]

    await update.message.reply_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )

async def view_day_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE, room_id: int, year: int, month: int, day: int):
    date_str = f"{year}-{month:02d}-{day:02d}"
    booking_date = date(year, month, day)
    now = datetime.now(TIMEZONE)
    today = now.date()

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT b.start_time, b.end_time, u.full_name 
        FROM bookings b
        JOIN users u ON b.user_id = u.user_id
        WHERE b.room_id = ? 
        AND date(b.start_time) = date(?)
        ORDER BY b.start_time
    ''', (room_id, date_str))

    bookings = cursor.fetchall()
    conn.close()

    room_name = get_room_name(room_id)
    message = f"📅 Бронирования {room_name} на {day:02d}.{month:02d}.{year}:\n\n"

    if not bookings:
        message += "✅ Этот день свободен."
    else:
        for start, end, name in bookings:
            start_dt = parse_db_time(start)
            end_dt = parse_db_time(end)
            message += f"⏱ {start_dt.strftime('%H:%M')}-{end_dt.strftime('%H:%M')} — {name}\n"

    keyboard = []

    if booking_date >= today:
        keyboard.append([
            InlineKeyboardButton("✅ Забронировать этот день", callback_data=f'book_selected_day_{year}_{month}_{day}_{room_id}')
        ])

    keyboard.append([InlineKeyboardButton("Назад", callback_data=f'select_calendar_room_{room_id}')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(message, reply_markup=reply_markup)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if update.message.text == '/start':
        await start(update, context)
        return

    if not is_user_active(user_id) and user_id != ADMIN_ID:
        await update.message.reply_text("Ваш доступ не активирован.")
        return

    if 'waiting_for' not in context.user_data:
        await update.message.reply_text(
            "Чтобы начать, используйте команду /start или кнопки меню.",
            reply_markup=admin_keyboard() if is_admin(user_id) else user_keyboard()
        )
        return

    waiting_for = context.user_data['waiting_for']

    try:
        if waiting_for == 'start_time':
            hours, minutes = map(int, update.message.text.split(':'))
            now = datetime.now(TIMEZONE)
            booking_date = context.user_data['booking_date']
            start_datetime = TIMEZONE.localize(
                datetime(booking_date.year, booking_date.month, booking_date.day, hours, minutes)
            )

            if start_datetime < now:
                await update.message.reply_text("Нельзя забронировать время в прошлом. Введите другое время:")
                return

            room_id = context.user_data['selected_room']

            conn = sqlite3.connect('meeting_rooms.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 1 FROM bookings
                WHERE room_id = ? AND end_time > ? AND start_time < ?
            ''', (room_id, start_datetime, start_datetime + timedelta(minutes=1)))

            if cursor.fetchone():
                conn.close()
                await handle_occupied_time(update, context, room_id, booking_date)
                return

            conn.close()

            context.user_data['start_time'] = f"{hours:02d}:{minutes:02d}"
            await update.message.reply_text("Введите продолжительность бронирования в минутах (например, 30):")
            context.user_data['waiting_for'] = 'duration'

        elif waiting_for == 'duration':
            duration = int(update.message.text)
            if duration <= 0:
                await update.message.reply_text("Продолжительность должна быть положительным числом. Введите количество минут:")
                return

            booking_date = context.user_data['booking_date']
            hours, minutes = map(int, context.user_data['start_time'].split(':'))
            room_id = context.user_data['selected_room']

            start_datetime = TIMEZONE.localize(datetime(booking_date.year, booking_date.month, booking_date.day, hours, minutes))
            end_datetime = start_datetime + timedelta(minutes=duration)

            conn = sqlite3.connect('meeting_rooms.db')
            cursor = conn.cursor()
            cursor.execute('''
                SELECT 1 FROM bookings
                WHERE room_id = ? AND end_time > ? AND start_time < ?
            ''', (room_id, start_datetime, end_datetime))

            if cursor.fetchone():
                conn.close()
                await handle_occupied_time(update, context, room_id, booking_date)
                return

            cursor.execute('''
                INSERT INTO bookings (room_id, user_id, start_time, end_time)
                VALUES (?, ?, ?, ?)
            ''', (room_id, user_id, start_datetime, end_datetime))
            conn.commit()
            booking_id = cursor.lastrowid
            conn.close()

            context.user_data.clear()

            room_name = get_room_name(room_id)
            await update.message.reply_text(
                f"✅ Вы успешно забронировали комнату <b>{room_name}</b>!\n"
                f"📅 {start_datetime.strftime('%d.%m.%Y')}\n"
                f"⏰ {start_datetime.strftime('%H:%M')} — {end_datetime.strftime('%H:%M')}",
                parse_mode='HTML'
            )

            # Установка напоминания
            reminder_time = start_datetime - timedelta(minutes=REMINDER_MINUTES)
            if reminder_time > datetime.now(TIMEZONE):
                context.application.job_queue.run_once(
                    send_reminder,
                    when=reminder_time,
                    data={'chat_id': user_id, 'booking_id': booking_id},
                    name=f"reminder_{booking_id}"
                )

        elif waiting_for == 'room_name':
            room_name = update.message.text.strip()
            if not room_name:
                await update.message.reply_text("Название комнаты не может быть пустым. Введите корректное название:")
                return
            conn = sqlite3.connect('meeting_rooms.db')
            cursor = conn.cursor()
            cursor.execute('INSERT INTO rooms (name) VALUES (?)', (room_name,))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ Переговорная комната '{room_name}' добавлена.")
            if is_admin(user_id):
                await update.message.reply_text("Администраторское меню:", reply_markup=admin_keyboard())
            else:
                await update.message.reply_text("Главное меню:", reply_markup=user_keyboard())

        elif waiting_for == 'new_name':
            new_name = update.message.text.strip()
            if not new_name:
                await update.message.reply_text("Имя не может быть пустым. Введите корректное имя:")
                return
            conn = sqlite3.connect('meeting_rooms.db')
            cursor = conn.cursor()
            cursor.execute('UPDATE users SET full_name = ? WHERE user_id = ?', (new_name, user_id))
            conn.commit()
            conn.close()
            context.user_data.clear()
            await update.message.reply_text(f"✅ Ваше имя изменено на {new_name}.")
            await show_profile(update, context)

        else:
            await update.message.reply_text("Неизвестное состояние. Пожалуйста, начните с команды /start.")

    except ValueError:
        if waiting_for == 'start_time':
            await update.message.reply_text("Неверный формат времени. Введите в формате ЧЧ:ММ (например, 14:30).")
        elif waiting_for == 'duration':
            await update.message.reply_text("Пожалуйста, введите число минут (например, 30).")
        elif waiting_for == 'room_name':
            await update.message.reply_text("Введите корректное название комнаты.")
        elif waiting_for == 'new_name':
            await update.message.reply_text("Введите корректное имя.")
        else:
            await update.message.reply_text("Ошибка ввода. Попробуйте снова.")

    except Exception as e:
        logger.error(f"Ошибка в handle_text: {e}")
        await update.message.reply_text("Произошла ошибка. Пожалуйста, попробуйте позже.")
        context.user_data.clear()

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    chat_id = job.data['chat_id']
    booking_id = job.data['booking_id']
    try:
        conn = sqlite3.connect('meeting_rooms.db')
        cursor = conn.cursor()
        cursor.execute('''
            SELECT r.name, b.start_time 
            FROM bookings b
            JOIN rooms r ON b.room_id = r.room_id
            WHERE b.booking_id = ?
        ''', (booking_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return
        room_name, start_time = row
        start_dt = parse_db_time(start_time)
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"⏰ Напоминание: у вас бронирование '{room_name}' {start_dt.strftime('%d.%m.%Y в %H:%M')}"
        )
    except Exception as e:
        logger.error(f"Ошибка напоминания: {e}")

async def manage_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("У вас нет прав администратора!", show_alert=True)
        else:
            await update.message.reply_text("У вас нет прав администратора!")
        return

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, full_name, is_admin, is_active FROM users')
    users = cursor.fetchall()
    conn.close()

    if not users:
        message_text = "Нет зарегистрированных пользователей."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_admin')]])
    else:
        message_text = "Список пользователей (✅ — активен, ⏳ — ожидает):\n"
        keyboard = []
        for user in users:
            user_id, username, full_name, is_admin_flag, is_active_flag = user
            status = "✅" if is_active_flag else "⏳"
            text = f"{full_name} (@{username}) {'👑' if is_admin_flag else ''} {status}"
            keyboard.append([InlineKeyboardButton(text, callback_data=f'user_action_{user_id}')])
        keyboard.append([InlineKeyboardButton("Назад", callback_data='back_to_admin')])
        reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

async def view_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        if update.callback_query:
            await update.callback_query.answer("У вас нет прав администратора!", show_alert=True)
        else:
            await update.message.reply_text("У вас нет прав администратора!")
        return

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT b.booking_id, r.name, u.full_name, b.start_time, b.end_time
        FROM bookings b
        JOIN rooms r ON b.room_id = r.room_id
        JOIN users u ON b.user_id = u.user_id
        ORDER BY b.start_time
    ''')
    bookings = cursor.fetchall()
    conn.close()

    if not bookings:
        message_text = "📅 Нет активных бронирований."
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_admin')]])
    else:
        message_text = "📅 Все активные бронирования:\n\n"
        for booking in bookings:
            booking_id, room_name, user_name, start_time, end_time = booking
            start_dt = parse_db_time(start_time)
            end_dt = parse_db_time(end_time)
            message_text += (
                f"🔹 <b>{room_name}</b>\n"
                f"👤 {user_name}\n"
                f"📅 {start_dt.strftime('%d.%m.%Y')}\n"
                f"🕒 {start_dt.strftime('%H:%M')} – {end_dt.strftime('%H:%M')}\n\n"
            )

        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_admin')]])

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode='HTML')

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('SELECT username, full_name, is_admin FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        message_text = "Профиль не найден."
        reply_markup = None
    else:
        username, full_name, is_admin_flag = user
        message_text = (f"👤 Ваш профиль:\n"
                       f"🆔 ID: {user_id}\n"
                       f"📛 Имя: {full_name}\n"
                       f"🔖 Логин: @{username if username else 'не указан'}\n"
                       f"👑 Статус: {'Администратор' if is_admin_flag else 'Пользователь'}")
        reply_markup = profile_keyboard(is_admin_flag)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

async def show_user_bookings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bookings = get_user_bookings(user_id)

    if not bookings:
        message_text = 'У вас нет активных бронирований.'
        reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data='back_to_main')]])
    else:
        message_text = "📅 Ваши бронирования:\n"
        keyboard = []
        for booking in bookings:
            booking_id, room_name, start_time, end_time = booking
            start_dt = parse_db_time(start_time)
            end_dt = parse_db_time(end_time)
            message_text += f"🔹 {room_name}\n🕒 {start_dt.strftime('%d.%m.%Y %H:%M')} - {end_dt.strftime('%H:%M')}\n"
            keyboard.append([
                InlineKeyboardButton(
                    f"❌ Отменить {room_name} {start_dt.strftime('%d.%m %H:%M')}",
                    callback_data=f'cancel_booking_{booking_id}'
                )
            ])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup)

async def confirm_cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE, booking_id: int):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    if not is_user_booking(booking_id, user_id):
        await query.edit_message_text("Вы не можете отменить это бронирование.")
        return

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.name, b.start_time, b.end_time
        FROM bookings b
        JOIN rooms r ON b.room_id = r.room_id
        WHERE b.booking_id = ?
    ''', (booking_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        await query.edit_message_text("Бронирование не найдено.")
        return

    room_name, start_time, end_time = result
    start_dt = parse_db_time(start_time)
    end_dt = parse_db_time(end_time)

    keyboard = [
        [InlineKeyboardButton("✅ Да, отменить", callback_data=f'confirm_cancel_{booking_id}')],
        [InlineKeyboardButton("❌ Нет, оставить", callback_data='my_bookings')]
    ]

    await query.edit_message_text(
        f"Вы уверены, что хотите отменить бронирование?\n\n"
        f"🔹 {room_name}\n"
        f"📅 {start_dt.strftime('%d.%m.%Y')}\n"
        f"🕒 {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel_booking(update: Update, context: ContextTypes.DEFAULT_TYPE, booking_id: int):
    user_id = update.callback_query.from_user.id
    if not is_user_booking(booking_id, user_id):
        await update.callback_query.answer("Вы не можете отменить это бронирование!", show_alert=True)
        return

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT r.name, b.start_time, b.end_time
        FROM bookings b
        JOIN rooms r ON b.room_id = r.room_id
        WHERE b.booking_id = ?
    ''', (booking_id,))
    booking_info = cursor.fetchone()
    conn.close()

    if not booking_info:
        await update.callback_query.answer("Бронирование не найдено!", show_alert=True)
        return

    conn = sqlite3.connect('meeting_rooms.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM bookings WHERE booking_id = ?', (booking_id,))
    conn.commit()
    conn.close()

    room_name, start_time, end_time = booking_info
    start_dt = parse_db_time(start_time)
    end_dt = parse_db_time(end_time)

    await update.callback_query.edit_message_text(
        f"✅ Бронирование отменено:\n\n"
        f"🔹 {room_name}\n"
        f"📅 {start_dt.strftime('%d.%m.%Y')}\n"
        f"🕒 {start_dt.strftime('%H:%M')} - {end_dt.strftime('%H:%M')}"
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if not is_user_active(user_id) and user_id != ADMIN_ID:
        await query.edit_message_text("Ваш аккаунт ожидает одобрения администратором.")
        return

    if data == 'back_to_main':
        if is_admin(user_id):
            await query.edit_message_text("Администраторское меню:", reply_markup=admin_keyboard())
        else:
            await query.edit_message_text("Главное меню:", reply_markup=user_keyboard())

    elif data == 'back_to_admin':
        await query.edit_message_text("Администраторское меню:", reply_markup=admin_keyboard())

    elif data == 'book_room':
        await start_booking_process(update, context)

    elif data == 'view_calendar':
        await start_calendar_process(update, context)

    elif data == 'my_profile':
        await show_profile(update, context)

    elif data == 'my_bookings':
        await show_user_bookings(update, context)

    elif data == 'add_room':
        if not is_admin(user_id):
            await query.answer("У вас нет прав администратора!", show_alert=True)
            return
        context.user_data['waiting_for'] = 'room_name'
        await query.edit_message_text("Введите название новой переговорной комнаты:")

    elif data == 'manage_users':
        await manage_users(update, context)

    elif data == 'view_bookings':
        await view_bookings(update, context)

    elif data == 'change_name':
        context.user_data['waiting_for'] = 'new_name'
        await query.edit_message_text("Введите ваше новое имя:")

    elif data.startswith('select_room_'):
        room_id = int(data.split('_')[2])
        context.user_data['selected_room'] = room_id
        context.user_data['waiting_for'] = 'start_time'
        await query.edit_message_text(
            f"Вы выбрали: {get_room_name(room_id)}\n"
            f"Введите время начала бронирования в формате ЧЧ:ММ (например, 14:30):"
        )

    elif data.startswith('select_calendar_room_'):
        room_id = int(data.split('_')[3])
        now = datetime.now(TIMEZONE)
        await show_calendar(update, context, room_id, now.year, now.month)

    elif data.startswith('nav_'):
        parts = data.split('_')
        year = int(parts[1])
        month = int(parts[2])
        room_id = int(parts[3])
        await show_calendar(update, context, room_id, year, month)

    elif data.startswith('today_'):
        room_id = int(data.split('_')[1])
        now = datetime.now(TIMEZONE)
        await show_calendar(update, context, room_id, now.year, now.month)

    elif data.startswith('day_'):
        parts = data.split('_')
        if len(parts) == 5:
            year = int(parts[1])
            month = int(parts[2])
            day = int(parts[3])
            room_id = int(parts[4])
            await view_day_bookings(update, context, room_id, year, month, day)

    elif data.startswith('book_selected_day_'):
        parts = data.split('_')
        year = int(parts[4])
        month = int(parts[5])
        day = int(parts[6])
        room_id = int(parts[7])
        context.user_data['selected_room'] = room_id
        context.user_data['booking_date'] = date(year, month, day)
        context.user_data['waiting_for'] = 'start_time'
        await query.edit_message_text(
            f"Вы выбрали: {get_room_name(room_id)} на {day:02d}.{month:02d}.{year}\n"
            f"Введите время начала бронирования в формате ЧЧ:ММ (например, 14:30):"
        )

    elif data.startswith('user_action_'):
        target_user_id = int(data.split('_')[2])
        conn = sqlite3.connect('meeting_rooms.db')
        cursor = conn.cursor()
        cursor.execute('SELECT full_name, is_active FROM users WHERE user_id = ?', (target_user_id,))
        user_info = cursor.fetchone()
        conn.close()

        if not user_info:
            await query.answer("Пользователь не найден!", show_alert=True)
            return

        full_name, is_active = user_info
        keyboard = []
        if not is_active:
            keyboard.append([InlineKeyboardButton("✅ Активировать", callback_data=f'activate_{target_user_id}')])
        else:
            keyboard.append([InlineKeyboardButton("⏳ Деактивировать", callback_data=f'deactivate_{target_user_id}')])
        keyboard.append([InlineKeyboardButton("Назад", callback_data='manage_users')])

        await query.edit_message_text(
            f"Пользователь: {full_name}\nСтатус: {'✅ Активен' if is_active else '⏳ Ожидает активации'}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith('activate_'):
        target_user_id = int(data.split('_')[1])
        conn = sqlite3.connect('meeting_rooms.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET is_active = 1 WHERE user_id = ?', (target_user_id,))
        conn.commit()
        conn.close()
        await query.answer("Пользователь активирован!", show_alert=True)
        await manage_users(update, context)

    elif data.startswith('deactivate_'):
        target_user_id = int(data.split('_')[1])
        conn = sqlite3.connect('meeting_rooms.db')
        cursor = conn.cursor()
        cursor.execute('UPDATE users SET is_active = 0 WHERE user_id = ?', (target_user_id,))
        conn.commit()
        conn.close()
        await query.answer("Пользователь деактивирован!", show_alert=True)
        await manage_users(update, context)

    elif data.startswith('cancel_booking_'):
        booking_id = int(data.split('_')[2])
        await confirm_cancel_booking(update, context, booking_id)

    elif data.startswith('confirm_cancel_'):
        booking_id = int(data.split('_')[2])
        await cancel_booking(update, context, booking_id)

    elif data == 'ignore':
        pass

    else:
        await query.edit_message_text("Неизвестная команда. Пожалуйста, используйте /start для начала.")

def main():
    init_db()

    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Запуск приложения для Render
    if os.environ.get('RENDER'):
        # Для Render используем webhook
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=TOKEN,
            webhook_url=f"https://your-app-name.onrender.com/{TOKEN}"
        )
    else:
        # Для локальной разработки используем polling
        application.run_polling()

if __name__ == '__main__':
    main()
