import requests
import time
import logging
import os
import smtplib
from datetime import datetime

from playwright.sync_api import sync_playwright
import pandas as pd

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders


# -------------------- LOGGING --------------------
logging.basicConfig(
    filename="daily_script.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# -------------------- EMAIL --------------------
def send_email(subject, body, to_email, filename=None):
    sender_email = "soothesphereshop@gmail.com"
    sender_password = os.getenv("EMAIL_PASSWORD")

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    if filename and os.path.exists(filename):
        with open(filename, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(filename)}"
        )
        msg.attach(part)

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        logging.info("Email sent successfully")
    except Exception as e:
        logging.error(f"Email error: {e}")


# -------------------- SCRAPING --------------------
def scrape_matches():
    url = "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/dzisiaj"
    matches = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )

        page = context.new_page()
        logging.info("Opening Superbet page")
        page.goto(url, timeout=60000)

        page.wait_for_selector("div.event-card", timeout=30000)

        last_height = 0
        for _ in range(10):
            page.mouse.wheel(0, 3000)
            time.sleep(2)
            height = page.evaluate("document.body.scrollHeight")
            if height == last_height:
                break
            last_height = height

        cards = page.query_selector_all("div.event-card")
        logging.info(f"Found {len(cards)} matches")

        for c in cards:
            try:
                home = c.query_selector("div.e2e-event-team1-name").inner_text().strip()
                away = c.query_selector("div.e2e-event-team2-name").inner_text().strip()
            except Exception:
                continue

            try:
                match_time = c.query_selector("span.event-card-label").inner_text().strip()
            except Exception:
                match_time = "N/A"

            odds = {"homeWin": "N/A", "draw": "N/A", "awayWin": "N/A"}

            odd_elements = c.query_selector_all("span.odd-button__odd-value span")

            if len(odd_elements) >= 3:
                odds["homeWin"] = odd_elements[0].inner_text().strip()
                odds["draw"] = odd_elements[1].inner_text().strip()
                odds["awayWin"] = odd_elements[2].inner_text().strip()

            matches.append({
                "homeTeam": home,
                "awayTeam": away,
                "time": match_time,
                "odds": odds
            })

        browser.close()

    return matches


# -------------------- EXCEL --------------------
def save_to_excel(matches, filename="matches_daily.xlsx"):
    all_rows = []
    low_rows = []

    for m in matches:
        row = {
            "Home Team": m["homeTeam"],
            "Away Team": m["awayTeam"],
            "Time": m["time"],
            "Home Win Odds": m["odds"]["homeWin"],
            "Draw Odds": m["odds"]["draw"],
            "Away Win Odds": m["odds"]["awayWin"],
        }

        all_rows.append(row)

        try:
            home_odds = float(m["odds"]["homeWin"].replace(",", "."))
            away_odds = float(m["odds"]["awayWin"].replace(",", "."))

            if home_odds < 1.50:
                row["Low Odds Type"] = "Home Win"
                low_rows.append(row)

            elif away_odds < 1.50:
                row["Low Odds Type"] = "Away Win"
                low_rows.append(row)

        except Exception:
            pass

    with pd.ExcelWriter(filename, engine="openpyxl") as writer:
        pd.DataFrame(all_rows).to_excel(writer, sheet_name="All Matches", index=False)
        pd.DataFrame(low_rows).to_excel(writer, sheet_name="Low Odds", index=False)

    logging.info(f"Excel saved: {filename}")
    return low_rows


# -------------------- TELEGRAM --------------------
def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Telegram not configured")
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, json=payload, timeout=15)
        r.raise_for_status()
        logging.info("Telegram alert sent")
    except Exception as e:
        logging.error(f"Telegram error: {e}")


def format_telegram_message(rows):
    lines = ["<b>⚽ Low Odds Matches</b>\n"]

    for r in rows:
        lines.append(
            f"🏟 <b>{r['Home Team']} vs {r['Away Team']}</b>\n"
            f"⏰ {r['Time']}\n"
            f"🔥 {r['Low Odds Type']} below 1.50\n"
            f"🏠 Home: {r['Home Win Odds']} | ✈ Away: {r['Away Win Odds']}\n"
        )

    return "\n".join(lines)


# -------------------- MAIN --------------------
def main():
    matches = scrape_matches()
    low = save_to_excel(matches)

    if low:
        email_body = "Low Odds Matches:\n\n" + "\n".join(
            f"{r['Home Team']} vs {r['Away Team']} | "
            f"{r['Time']} | {r['Low Odds Type']}"
            for r in low
        )

        send_email(
            "Daily Low Odds Matches",
            email_body,
            "nganatech@gmail.com",
            "matches_daily.xlsx"
        )

        telegram_message = format_telegram_message(low)
        send_telegram(telegram_message)


# -------------------- RUN --------------------
if __name__ == "__main__":
    main()
