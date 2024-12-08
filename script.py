import os
import time
import logging
import sys
from datetime import datetime
import smtplib
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import pandas as pd
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from webdriver_manager.chrome import ChromeDriverManager  # Import WebDriver Manager

# Setup logging
output_folder = "output"
os.makedirs(output_folder, exist_ok=True)

# Create a logger
logger = logging.getLogger()

# Set logging level to INFO (or DEBUG for more verbosity)
logger.setLevel(logging.INFO)

# Create file handler to log to a file
file_handler = logging.FileHandler(os.path.join(output_folder, "daily_script.log"))
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler.setFormatter(file_formatter)

# Create console handler to log to the console (GitHub Actions)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter("%(asctime)s [%(levelname)s]: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
console_handler.setFormatter(console_formatter)

# Add both handlers to the logger
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Setup Selenium WebDriver with ChromeDriver managed by WebDriver Manager
chrome_options = Options()
chrome_options.add_argument("--headless")  # Run headless Chrome
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

# Use WebDriver Manager to automatically download and set up ChromeDriver
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)

def send_email(subject, body, to_email, filename=None):
    sender_email = "soothesphereshop@gmail.com"   
    sender_password = os.getenv("EMAIL_PASSWORD")  #Load from environment variable

    if not sender_password:
        logger.error("EMAIL_PASSWORD environment variable is not set.".sender_password)
        raise ValueError("EMAIL_PASSWORD environment variable is required.")

    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = to_email
    message["Subject"] = subject

    # Attach the body text
    message.attach(MIMEText(body, "plain"))

    # Attach the file if provided and exists
    if filename and os.path.exists(filename):
        try:
            with open(filename, "rb") as attachment:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(attachment.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(filename)}")
                message.attach(part)
            logger.info(f"Attached file: {filename}")
        except Exception as e:
            logger.error(f"Error while attaching file {filename}: {e}")
            return

    try:
        # Connect to the Gmail SMTP server
        logger.info("Connecting to Gmail SMTP server...")
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            logger.info("Logging in to the email server...")
            server.login(sender_email, sender_password)
            logger.info("Login successful. Sending email...")
            server.sendmail(sender_email, to_email, message.as_string())
        logger.info("Email sent successfully.")
    except smtplib.SMTPException as smtp_err:
        logger.error(f"SMTP error: {smtp_err}")
    except Exception as e:
        logger.error(f"Error while sending email: {e}")

def scrape_homepage(url, driver):
    logger.info(f"Scraping the homepage: {url}")
    driver.get(url)
    time.sleep(5)
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0

    while scroll_attempts < 10:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(7)
        new_height = driver.execute_script("return document.body.scrollHeight")

        if new_height == last_height:
            scroll_attempts += 1
        else:
            scroll_attempts = 0
        last_height = new_height

    return driver.page_source

def extract_matches(soup):
    logger.info("Extracting match data...")
    elements_matches = soup.find_all("div", class_="event-card")
    matches_list = []

    for match_element in elements_matches:
        match = {}
        home_team = match_element.find("div", class_="e2e-event-team1-name")
        match['homeTeam'] = home_team.get_text(strip=True) if home_team else "N/A"
        away_team = match_element.find("div", class_="e2e-event-team2-name")
        match['awayTeam'] = away_team.get_text(strip=True) if away_team else "N/A"
        match_time = match_element.find("span", class_="event-card-label")
        match['time'] = match_time.get_text(strip=True) if match_time else "N/A"
        match['odds'] = {'homeWin': "N/A", 'draw': "N/A", 'awayWin': "N/A"}

        odds_container = match_element.select("div.odd-offer div:nth-child(1) button div span.odd-button__odd-value-new")
        if len(odds_container) >= 3:
            match['odds']['homeWin'] = odds_container[0].get_text(strip=True).replace(',', '')
            match['odds']['draw'] = odds_container[1].get_text(strip=True).replace(',', '')
            match['odds']['awayWin'] = odds_container[2].get_text(strip=True).replace(',', '')

        matches_list.append(match)

    return matches_list

def save_to_excel(matches_list, filename):
    logger.info(f"Saving match data to Excel: {filename}")
    all_matches_data = []
    low_odds_data = []

    for match in matches_list:
        match_data = {
            'Home Team': match['homeTeam'],
            'Away Team': match['awayTeam'],
            'Time': match['time'],
            'Home Win Odds': match['odds']['homeWin'],
            'Draw Odds': match['odds']['draw'],
            'Away Win Odds': match['odds']['awayWin']
        }
        all_matches_data.append(match_data)

        try:
            if float(match['odds']['homeWin']) < 1.50:
                low_odds_data.append(match_data)
        except ValueError:
            pass

    df_all_matches = pd.DataFrame(all_matches_data)
    df_low_odds_matches = pd.DataFrame(low_odds_data)

    with pd.ExcelWriter(filename, engine='openpyxl', mode='w') as writer:
        df_all_matches.to_excel(writer, sheet_name="All Matches", index=False)
        df_low_odds_matches.to_excel(writer, sheet_name="Low Odds Matches", index=False)

    return low_odds_data

def main():
    logger.info("Starting the script.")
    url = "https://superbet.pl/zaklady-bukmacherskie/pilka-nozna/dzisiaj"
    page_source = scrape_homepage(url, driver)
    soup = BeautifulSoup(page_source, 'html.parser')
    matches_list = extract_matches(soup)

    timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
    excel_filename = os.path.join(output_folder, f"matches_{timestamp}.xlsx")

    low_odds_data = save_to_excel(matches_list, excel_filename)

    if low_odds_data:
        email_body = "Low Odds Matches:\n\n" + "\n".join(
            f"{match['Home Team']} vs {match['Away Team']} | Time: {match['Time']} | Home Win Odds: {match['Home Win Odds']}"
            for match in low_odds_data
        )
    else:
        email_body = "No low odds matches found today."

    send_email(
        subject="Daily Low Odds Matches",
        body=email_body,
        to_email="nganatech@gmail.com",
        filename=excel_filename
    )

    logger.info("Script finished execution.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        driver.quit()
