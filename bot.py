import os
import json
import time
import smtplib
import argparse
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
import pytz

load_dotenv("config.env")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
RECIPIENT_EMAILS = os.getenv("RECIPIENT_EMAILS", "").split(",")
# Clean up whitespace in recipient emails
RECIPIENT_EMAILS = [email.strip() for email in RECIPIENT_EMAILS if email.strip()]
SUBJECT = "Thông báo ngày mặc áo TSI"
TZ = pytz.timezone('Asia/Ho_Chi_Minh')
STATE_FILE = "schedule.json"

DAYS_VN = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ Nhật"]

def get_now():
    return datetime.now(TZ)

def get_monday(date_obj):
    return date_obj - timedelta(days=date_obj.weekday())

def calculate_shirt_day(monday_date):
    """Calculate the shirt day based on the Monday of that week."""
    y = monday_date.year
    m = monday_date.month
    d = monday_date.day
    offset = (y + m + d) % 5
    
    shirt_date = monday_date + timedelta(days=offset)
    formula = f"({y} + {m} + {d}) mod 5 = {offset}"
    return shirt_date, formula

def format_date_vn(date_obj):
    day_name = DAYS_VN[date_obj.weekday()]
    return f"{day_name} ngày {date_obj.day:02d} tháng {date_obj.month:02d} năm {date_obj.year}"

def generate_email_content(current_week_monday, next_week_monday):
    curr_shirt_date, curr_formula = calculate_shirt_day(current_week_monday)
    next_shirt_date, _ = calculate_shirt_day(next_week_monday)
    
    content = f"""ngày mặc áo TSI cho tuần này là 

the formula = {curr_formula}

{format_date_vn(curr_shirt_date)}

tuần tiếp theo: ({format_date_vn(next_shirt_date)} là ngày mặc áo của tuần tiếp)
"""
    return content

def send_email(subject, body):
    if not SENDER_EMAIL or not SENDER_PASSWORD or not RECIPIENT_EMAILS:
        print("Please configure SENDER_EMAIL, SENDER_PASSWORD, and RECIPIENT_EMAILS in the .env file.")
        return False
        
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = ", ".join(RECIPIENT_EMAILS)
    msg['Subject'] = subject
    
    msg.attach(MIMEText(body, 'plain', 'utf-8'))
    
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Email sent successfully.")
        return True
    except Exception as e:
        print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Failed to send email: {e}")
        return False

def update_schedule_state():
    """Calculates and saves the schedule for the current and 2 next weeks to schedule.json."""
    now = get_now()
    curr_monday = get_monday(now.date())
    
    schedule_data = {}
    
    # Pre-calculate 3 weeks
    for i in range(3):
        week_monday = curr_monday + timedelta(days=i*7)
        shirt_date, formula = calculate_shirt_day(week_monday)
        
        # Save keyed by the week's Monday string
        key = week_monday.strftime('%Y-%m-%d')
        schedule_data[key] = {
            "monday": key,
            "shirt_date": shirt_date.strftime('%Y-%m-%d'),
            "formula": formula
        }
        
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(schedule_data, f, indent=4, ensure_ascii=False)
    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Schedule updated and saved.")
    
def get_saved_shirt_date(monday_date):
    key = monday_date.strftime('%Y-%m-%d')
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if key in data:
                return datetime.strptime(data[key]["shirt_date"], '%Y-%m-%d').date()
    except Exception:
        pass
    # Fallback if file doesn't exist or is corrupted
    shirt_date, _ = calculate_shirt_day(monday_date)
    return shirt_date

def daemon_loop():
    print(f"[{get_now().strftime('%Y-%m-%d %H:%M:%S')}] Starting TSI Shirt Bot daemon...")
    update_schedule_state()
    
    # Keep track of when we last sent emails to prevent spamming within the same minute
    last_sunday_email_date = None
    last_morning_email_date = None
    
    while True:
        now = get_now()
        
        # Every Monday at 00:00, refresh state
        if now.weekday() == 0 and now.hour == 0 and now.minute == 0:
            update_schedule_state()

        curr_monday = get_monday(now.date())
        next_monday = curr_monday + timedelta(days=7)

        # TRIGGER 1: Sunday 9:00 PM for the upcoming week
        if now.weekday() == 6 and now.hour == 21 and now.minute == 0:
            if last_sunday_email_date != now.date():
                body = generate_email_content(next_monday, next_monday + timedelta(days=7))
                send_email(SUBJECT, body)
                last_sunday_email_date = now.date()

        # TRIGGER 2: Calculated shirt day at 7:45 AM
        shirt_date = get_saved_shirt_date(curr_monday)
        if now.date() == shirt_date and now.hour == 7 and now.minute == 45:
            if last_morning_email_date != now.date():
                body = generate_email_content(curr_monday, next_monday)
                send_email(SUBJECT, body)
                last_morning_email_date = now.date()

        time.sleep(60)

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
        daemon_loop()
