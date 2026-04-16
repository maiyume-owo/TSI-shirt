import os
import json
import time
import asyncio
import smtplib
import argparse
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import pytz
import discord
from discord.ext import tasks

load_dotenv("config.env")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
# os.getenv returns "" for empty vars; filter strips those out after splitting on ","
RECIPIENT_EMAILS = [e.strip() for e in os.getenv("RECIPIENT_EMAILS", "").split(",") if e.strip()]
SUBJECT = "Thông báo ngày mặc áo TSI"
TZ = pytz.timezone('Asia/Ho_Chi_Minh')
STATE_FILE = "schedule.json"

DAYS_VN = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def get_now() -> datetime:
    return datetime.now(TZ)


def get_monday(date_obj) -> "datetime.date":
    """Return the Monday of the week containing *date_obj* (a datetime.date)."""
    return date_obj - timedelta(days=date_obj.weekday())


def calculate_shirt_day(monday_date):
    """Calculate the shirt day based on the Monday of that week.

    *monday_date* must be a datetime.date (or datetime) object.
    Returns (shirt_date: datetime.date, formula: str).
    """
    y = monday_date.year
    m = monday_date.month
    d = monday_date.day
    offset = (y + m + d) % 5

    shirt_date = monday_date + timedelta(days=offset)
    formula = f"({y} + {m} + {d}) mod 5 = {offset}"
    return shirt_date, formula


def format_date_vn(date_obj) -> str:
    day_name = DAYS_VN[date_obj.weekday()]
    return f"{day_name} ngày {date_obj.day:02d} tháng {date_obj.month:02d} năm {date_obj.year}"


def generate_email_content(current_week_monday, next_week_monday) -> str:
    curr_shirt_date, curr_formula = calculate_shirt_day(current_week_monday)
    next_shirt_date, _ = calculate_shirt_day(next_week_monday)

    content = f"""ngày mặc áo TSI cho tuần này là 

Công thức = {curr_formula}

{format_date_vn(curr_shirt_date)}

tuần tiếp theo: ({format_date_vn(next_shirt_date)} là ngày mặc áo của tuần tiếp)
"""
    return content

# --------------------------------------------------------------------------- #
# Email (blocking — called via run_in_executor so it never blocks the loop)   #
# --------------------------------------------------------------------------- #

MAX_EMAIL_RETRIES = 3
EMAIL_RETRY_DELAY = 30  # seconds between retries


def send_email(subject: str, body: str) -> bool:
    """Attempt to send an email, retrying up to MAX_EMAIL_RETRIES times."""
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAILS:
        print("Please configure SENDER_EMAIL, SENDER_PASSWORD, and RECIPIENT_EMAILS in the .env file.")
        return False

    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = ", ".join(RECIPIENT_EMAILS)
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    for attempt in range(1, MAX_EMAIL_RETRIES + 1):
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
            server.quit()
            print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Email sent successfully.")
            return True
        except Exception as e:
            print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] "
                  f"Failed to send email (attempt {attempt}/{MAX_EMAIL_RETRIES}): {e}")
            if attempt < MAX_EMAIL_RETRIES:
                time.sleep(EMAIL_RETRY_DELAY)

    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Giving up after {MAX_EMAIL_RETRIES} attempts.")
    return False

# --------------------------------------------------------------------------- #
# Schedule state (schedule.json)                                               #
# --------------------------------------------------------------------------- #

STATE_VERSION = 1


def update_schedule_state():
    """Calculates and saves the schedule for the current and 2 next weeks to schedule.json.
    Also preserves the persisted email-sent dates so they survive restarts.
    Must be called while holding _email_lock to prevent torn writes.
    """
    now = get_now()
    curr_monday = get_monday(now.date())

    # Read existing data (to preserve email-sent tracking fields)
    existing = _load_state_file()

    schedule_data = {
        "version": STATE_VERSION,
        # Preserve restart-proof email-sent guards
        "last_sunday_email_date": existing.get("last_sunday_email_date"),
        "last_morning_email_date": existing.get("last_morning_email_date"),
    }

    # Pre-calculate 3 weeks
    for i in range(3):
        week_monday = curr_monday + timedelta(days=i * 7)
        shirt_date, formula = calculate_shirt_day(week_monday)

        key = week_monday.strftime('%Y-%m-%d')
        schedule_data[key] = {
            "monday": key,
            "shirt_date": shirt_date.strftime('%Y-%m-%d'),
            "formula": formula,
        }

    _write_state_file(schedule_data)
    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Schedule updated and saved.")


def _load_state_file() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_state_file(data: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_saved_shirt_date(monday_date):
    """Return the shirt date for the week starting on *monday_date* (datetime.date)."""
    key = monday_date.strftime('%Y-%m-%d')
    data = _load_state_file()
    if key in data:
        try:
            return datetime.strptime(data[key]["shirt_date"], '%Y-%m-%d').date()
        except Exception:
            pass
    # Fallback: recalculate if file is missing or corrupted
    shirt_date, _ = calculate_shirt_day(monday_date)
    return shirt_date


def _load_email_sent_dates():
    """Load persisted email-sent guard dates from schedule.json."""
    data = _load_state_file()
    sunday_str = data.get("last_sunday_email_date")
    morning_str = data.get("last_morning_email_date")
    try:
        sunday = datetime.strptime(sunday_str, '%Y-%m-%d').date() if sunday_str else None
    except Exception:
        sunday = None
    try:
        morning = datetime.strptime(morning_str, '%Y-%m-%d').date() if morning_str else None
    except Exception:
        morning = None
    return sunday, morning


def _persist_email_sent_dates(sunday_date, morning_date):
    """Write email-sent guard dates back into schedule.json."""
    data = _load_state_file()
    data["last_sunday_email_date"] = sunday_date.strftime('%Y-%m-%d') if sunday_date else None
    data["last_morning_email_date"] = morning_date.strftime('%Y-%m-%d') if morning_date else None
    _write_state_file(data)

# --------------------------------------------------------------------------- #
# Discord client                                                                #
# --------------------------------------------------------------------------- #

# TZ-aware so uptime display is consistent with all other timestamps in the bot
START_TIME = datetime.now(TZ)

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# asyncio.Lock prevents two near-simultaneous ticks from both passing the date
# guard before either has written it back to disk.
_email_lock = asyncio.Lock()


@tasks.loop(minutes=1)
async def update_uptime_status():
    """Updates the Discord bot's activity with the current container uptime."""
    delta = datetime.now(TZ) - START_TIME  # both TZ-aware; naive - aware raises TypeError
    days = delta.days
    minutes = delta.seconds // 60  # seconds within the current day only

    day_str = f"{days} day{'s' if days != 1 else ''}"
    min_str = f"{minutes} minute{'s' if minutes != 1 else ''}"
    status_text = f"containers uptime {day_str} {min_str}"

    activity = discord.Game(name=status_text)
    await client.change_presence(status=discord.Status.online, activity=activity)


@update_uptime_status.before_loop
async def before_update_uptime_status():
    await client.wait_until_ready()


@tasks.loop(minutes=1)
async def daemon_loop_task():
    """Check email triggers every minute. Uses an asyncio.Lock + JSON persistence
    to be race-condition-safe and restart-proof.

    All file reads/writes (including the Monday schedule refresh) run inside
    _email_lock so there is no torn write between update_schedule_state() and
    _persist_email_sent_dates().
    """
    now = get_now()
    curr_monday = get_monday(now.date())
    next_monday = curr_monday + timedelta(days=7)

    # Determine what (if anything) needs to be sent — hold the lock only for
    # the fast guard-read/write, NOT for the slow SMTP call.
    send_sunday = False
    send_morning = False
    sunday_body = None
    morning_body = None

    async with _email_lock:
        # Refresh schedule every Monday at midnight — inside the lock so the
        # read-modify-write in update_schedule_state() can't race with
        # _persist_email_sent_dates() from another tick.
        if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
            update_schedule_state()

        last_sunday_email_date, last_morning_email_date = _load_email_sent_dates()

        # TRIGGER 1: Sunday 8:55 PM — preview of the UPCOMING week.
        # On Sunday: curr_monday = 6 days ago (last Mon), next_monday = tomorrow (Mon).
        # We pass next_monday as the "current" week for the preview so the email
        # shows what shirt day falls on in the week that is about to start.
        if now.weekday() == 6 and now.hour == 20 and now.minute == 55:
            if last_sunday_email_date != now.date():
                preview_monday = next_monday
                sunday_body = generate_email_content(preview_monday, preview_monday + timedelta(days=7))
                # Flip guard now — inside the lock — before we release it.
                # If send fails we log but do NOT retry until the next trigger window.
                last_sunday_email_date = now.date()
                _persist_email_sent_dates(last_sunday_email_date, last_morning_email_date)
                send_sunday = True

        # TRIGGER 2: Shirt day at 7:00 AM — morning reminder
        shirt_date = get_saved_shirt_date(curr_monday)
        if now.date() == shirt_date and now.hour == 7 and now.minute == 0:
            if last_morning_email_date != now.date():
                morning_body = generate_email_content(curr_monday, next_monday)
                last_morning_email_date = now.date()
                _persist_email_sent_dates(last_sunday_email_date, last_morning_email_date)
                send_morning = True

    # Send outside the lock so SMTP retries (up to 90 s) don't block other ticks.
    loop = asyncio.get_running_loop()
    if send_sunday:
        await loop.run_in_executor(None, send_email, SUBJECT, sunday_body)
    if send_morning:
        await loop.run_in_executor(None, send_email, SUBJECT, morning_body)


@daemon_loop_task.before_loop
async def before_daemon_loop_task():
    await client.wait_until_ready()


@client.event
async def on_ready():
    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Discord Bot logged in as {client.user}")
    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Starting TSI Shirt Bot daemon...")
    # Acquire the lock so this initial write can't race with the first loop tick.
    async with _email_lock:
        update_schedule_state()
    daemon_loop_task.start()
    update_uptime_status.start()

# --------------------------------------------------------------------------- #
# Entry point                                                                  #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TSI Shirt Reminder Bot")
    parser.add_argument("--test", action="store_true", help="Send a test email immediately")
    args = parser.parse_args()

    if args.test:
        print("Sending test email...")
        now = get_now()
        curr_monday = get_monday(now.date())
        next_monday = curr_monday + timedelta(days=7)
        body = generate_email_content(curr_monday, next_monday)
        print("====== EMAIL CONTENT ======")
        print(body)
        print("===========================")
        success = send_email(f"[TEST] {SUBJECT}", body)
        if success:
            print("Test email sent successfully!")
        else:
            print("Failed to send test email. Check your .env configuration.")
    else:
        # Validate required config before attempting to connect
        missing = []
        if not DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
        elif not DISCORD_TOKEN.count(".") >= 2:
            # Discord tokens contain at least two '.' separators; catch obvious placeholders
            print("ERROR: DISCORD_TOKEN looks invalid (expected format: xxx.yyy.zzz).")
            missing.append("DISCORD_TOKEN (invalid format)")
        if not SENDER_EMAIL:
            missing.append("SENDER_EMAIL")
        if not SENDER_PASSWORD:
            missing.append("SENDER_PASSWORD")
        if not RECIPIENT_EMAILS:
            missing.append("RECIPIENT_EMAILS")
        if missing:
            print(f"ERROR: Missing or invalid config values: {', '.join(missing)}")
            print("Please fill them in config.env before starting the bot.")
        else:
            client.run(DISCORD_TOKEN)
