import time, logging, os, smtplib
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

# ───────── LOGGING ─────────
logging.basicConfig(
    filename="daily_script.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

chrome_options = Options()
chrome_options.add_argument("--headless")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--window-size=1920,1080")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=chrome_options
)

def send_email(subject, body, to_email, filename=None):
    sender_email = "soothesphereshop@gmail.com"
    sender_password = os.getenv("EMAIL_PASSWORD")  # <- App Password

    msg = MIMEMultipart()
    msg["From"], msg["To"], msg["Subject"] = sender_email, to_email, subject
    msg.attach(MIMEText(body, "plain"))

    if filename and os.path.exists(filename):
        try:
            with open(filename, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f"attachment; filename={os.path.basename(filename)}"
            )
            msg.attach(part)
        except Exception as e:
            logging.error(f"Attachment error {filename}: {e}")

    try:
        logging.info("Connecting to Gmail SMTP...")
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, to_email, msg.as_string())
        logging.info(f"Email sent to {to_email}")
        print("Email sent successfully.")
    except smtplib.SMTPAuthenticationError as e:
        logging.error(f"SMTP authentication error: {e}")
    except Exception as e:
        logging.error(f"Unexpected email error: {e}")

def scrape_homepage(url):
    logging.info(f"Opening URL: {url}")
    driver.get(url)
    time.sleep(5)  # allow initial load

    # scroll to bottom gradually to load dynamic content
    last_height = driver.execute_script("return document.body.scrollHeight")
    attempts = 0
    while attempts < 10:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(7)  # wait for content to load
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            attempts += 1
        else:
            attempts = 0
        last_height = new_height

    logging.info("Finished scrolling.")
    return driver.page_source

def extract_matches(soup):
    cards = soup.find_all("div", class_="event-card")
    logging.info(f"Found {len(cards)} event cards.")
    matches = []
    for c in cards:
        # Extract team names
        home = c.find("div", "e2e-event-team1-name")
        away = c.find("div", "e2e-event-team2-name")
        time_span = c.find("span", "event-card-label")

        home_name = home.get_text(strip=True) if home else "N/A"
        away_name = away.get_text(strip=True) if away else "N/A"
        match_time = time_span.get_text(strip=True) if time_span else "N/A"

        m = {
            "homeTeam": home_name,
            "awayTeam": away_name,
            "time": match_time,
            "odds": {"homeWin": "N/A", "draw": "N/A", "awayWin": "N/A"}
        }

        # Try extracting odds
        # New selector: all odds spans under buttons
        odds_spans = c.select("div.odd-offer button span.odd-button__odd-value-new")

        # If that fails or returns fewer than 3, try fallback selectors
        if len(odds_spans) < 3:
            logging.info(f"Primary odds selector yielded {len(odds_spans)} items for {home_name} vs {away_name}, trying fallback.")
            # Fallback option: maybe class changed or wrappers present
            # Try selecting spans with similar name or pattern
            fallback = c.select("span.odd-value, span.odd-button__odd-value-new, div.odd-offer span")
            # Filter out empty or non-numerical
            fallback_clean = []
            for fs in fallback:
                text = fs.get_text(strip=True)
                # Simple check: contains digits and maybe comma or dot
                if any(ch.isdigit() for ch in text):
                    fallback_clean.append(fs)
            if len(fallback_clean) >= 3:
                odds_spans = fallback_clean

        # If now enough, assign
        if len(odds_spans) >= 3:
            try:
                m["odds"]["homeWin"] = odds_spans[0].get_text(strip=True).replace(",", "")
                m["odds"]["draw"]    = odds_spans[1].get_text(strip=True).replace(",", "")
                m["odds"]["awayWin"] = odds_spans[2].get_text(strip=True).replace(",", "")
            except Exception as e:
                logging.error(f"Error parsing odds for {home_name} vs {away_name}: {e}")
        else:
            logging.warning(f"Not enough odds found for {home_name} vs {away_name}. Odds found: {len(odds_spans)}")

        matches.append(m)

    return matches

def save_to_excel(matches, filename="matches.xlsx"):
    all_rows = []
    low_rows = []
    for m in matches:
        row = {
            "Home Team": m["homeTeam"],
            "Away Team": m["awayTeam"],
            "Time": m["time"],
            "Home Win Odds": m["odds"]["homeWin"],
            "Draw Odds": m["odds"]["draw"],
            "Away Win Odds": m["odds"]["awayWin"]
        }
        all_rows.append(row)
        # If numeric odds and homeWin is less than threshold
        try:
            # Convert with float; if comma used as decimal, adjust accordingly
            hw = float(m["odds"]["homeWin"].replace(",", "."))
            if hw < 1.50:  # you can adjust threshold
                low_rows.append(row)
        except Exception:
            # skip non-numeric or missing
            pass

    with pd.ExcelWriter(filename, engine="openpyxl", mode="w") as writer:
        pd.DataFrame(all_rows).to_excel(writer, sheet_name="All Matches", index=False)
        pd.DataFrame(low_rows).to_excel(writer, sheet_name="Low Odds", index=False)

    logging.info(f"Excel saved: {filename}")
    return low_rows

def main():
    logging.info("===== Script started =====")
    url = "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/dzisiaj"
    html = scrape_homepage(url)
    soup = BeautifulSoup(html, "html.parser")
    matches = extract_matches(soup)
    low = save_to_excel(matches, "matches_daily.xlsx")

    if low:
        body = "Low Odds Matches:\n\n" + "\n".join(
            f"{row['Home Team']} vs {row['Away Team']} | {row['Time']} | HomeWin: {row['Home Win Odds']}"
            for row in low
        )
        send_email("Daily Low Odds Matches", body, "nganatech@gmail.com", "matches_daily.xlsx")
    else:
        logging.info("No low odds matches found.")

    logging.info("===== Script finished =====")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.error(f"Fatal error: {e}")
        print("Fatal error:", e)
    finally:
        driver.quit()
