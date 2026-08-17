"""Submit CAMS Consolidated Account Statement requests (Detailed, Specific Period from
01-Jan-2001 to today) for each investor listed in a CSV file.

CSV must have "email" and "pan" columns (case-insensitive).

Logs progress to console + cams_requests.log, and appends one row per
investor to results.csv (timestamp, email, status, detail). "success" only
means CAMS accepted the request (the form reset) - it does not confirm the
PAN/email matched a real account or that an email was actually delivered.
"""
import argparse
import csv
import datetime
import logging
import os
import re
import shutil
import time

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException, StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

URL = "https://www.camsonline.com/Investors/Statements/Consolidated-Account-Statement"
FROM_DATE = datetime.date(2001, 1, 1)
LOG_FILE = "cams_requests.log"
RESULTS_CSV = "results.csv"

logger = logging.getLogger("cams_request")


def setup_logging():
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    for handler in (logging.StreamHandler(), logging.FileHandler(LOG_FILE, encoding="utf-8")):
        handler.setFormatter(fmt)
        logger.addHandler(handler)


def log_result(email, status, detail):
    is_new = not os.path.exists(RESULTS_CSV)
    with open(RESULTS_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["timestamp", "email", "status", "detail"])
        writer.writerow([datetime.datetime.now().isoformat(timespec="seconds"), email, status, detail])


def read_rows(csv_path):
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            lower = {k.strip().lower(): (v or "").strip() for k, v in row.items()}
            yield lower.get("email", ""), lower.get("pan", "")


def build_driver(headless):
    """Use the system-installed Chromium/chromedriver if present, since Selenium
    Manager's auto-download has no binaries for some platforms (e.g. Raspberry Pi/aarch64)."""
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1280,1024")
    browser = shutil.which("chromium-browser") or shutil.which("chromium")
    if browser:
        opts.binary_location = browser
    driver_path = shutil.which("chromedriver")
    service = ChromeService(executable_path=driver_path) if driver_path else ChromeService()
    return webdriver.Chrome(options=opts, service=service)


def jsclick(driver, el):
    driver.execute_script("arguments[0].click();", el)


def click_radio(driver, scope_selector, value):
    inp = driver.find_element(By.CSS_SELECTOR, f"{scope_selector} input[value='{value}']")
    label = driver.find_element(By.CSS_SELECTOR, f"label[for='{inp.get_attribute('id')}']")
    jsclick(driver, label)


def accept_disclaimer(driver, wait):
    accept_label = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "label[for='mat-radio-9-input']"))
    )
    dialog = driver.find_element(By.CSS_SELECTOR, "mat-dialog-container")
    jsclick(driver, accept_label)
    jsclick(driver, driver.find_element(By.CSS_SELECTOR, "input.check-now-btn[value='PROCEED']"))
    wait.until(EC.staleness_of(dialog))


def dismiss_ad_popup(driver, wait):
    """An unrelated promo dialog sometimes appears right after the disclaimer and
    blocks the form underneath it - close it if present."""
    try:
        close_icon = WebDriverWait(driver, 3).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "mat-icon.close-popup"))
        )
        dialog = driver.find_element(By.CSS_SELECTOR, "mat-dialog-container")
        jsclick(driver, close_icon)
        wait.until(EC.staleness_of(dialog))
    except TimeoutException:
        pass


def retry_click(driver, find_target, condition, attempts=3, timeout=5):
    """Click an element (re-located fresh each attempt) until `condition(driver)` holds.
    Angular's overlay-open animations occasionally swallow the first click."""
    for attempt in range(attempts):
        jsclick(driver, find_target())
        try:
            WebDriverWait(driver, timeout, ignored_exceptions=(StaleElementReferenceException,)).until(condition)
            return
        except TimeoutException:
            if attempt == attempts - 1:
                raise


def find_cell(driver, label):
    return next(
        c for c in driver.find_elements(By.CSS_SELECTOR, ".mat-calendar-body-cell")
        if c.get_attribute("aria-label") == label
    )


def in_multi_year_view(driver):
    cells = driver.find_elements(By.CSS_SELECTOR, ".mat-calendar-body-cell")
    return bool(cells) and all((c.get_attribute("aria-label") or "").isdigit() for c in cells)


def set_date(driver, wait, formcontrolname, target: datetime.date):
    target_str = target.strftime("%d-%b-%Y")
    inp = wait.until(
        EC.presence_of_element_located((By.CSS_SELECTOR, f"input[formcontrolname='{formcontrolname}']"))
    )
    cal_id = inp.get_attribute("data-mat-calendar")
    jsclick(driver, driver.find_element(By.CSS_SELECTOR, f"mat-datepicker-toggle[data-mat-calendar='{cal_id}'] button"))
    wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, ".mat-calendar-period-button")))

    retry_click(
        driver,
        lambda: driver.find_element(By.CSS_SELECTOR, ".mat-calendar-period-button"),
        in_multi_year_view,
    )

    for _ in range(50):
        period_text = driver.find_element(By.CSS_SELECTOR, ".mat-calendar-period-button").text
        years = [int(y) for y in re.findall(r"\d{4}", period_text)]
        lo, hi = min(years), max(years)
        if lo <= target.year <= hi:
            break
        nav = ".mat-calendar-previous-button" if target.year < lo else ".mat-calendar-next-button"
        jsclick(driver, driver.find_element(By.CSS_SELECTOR, nav))
        time.sleep(0.2)

    retry_click(
        driver,
        lambda: find_cell(driver, str(target.year)),
        lambda d: d.find_element(By.CSS_SELECTOR, ".mat-calendar-period-button").text.strip() == str(target.year),
    )

    month_label = f"01-{target.strftime('%b')}-{target.year}"
    retry_click(
        driver,
        lambda: find_cell(driver, month_label),
        lambda d: d.find_element(By.CSS_SELECTOR, ".mat-calendar-period-button").text.strip().upper() == target_str.upper(),
    )

    retry_click(
        driver,
        lambda: find_cell(driver, target_str),
        lambda d: d.find_element(By.CSS_SELECTOR, f"input[formcontrolname='{formcontrolname}']").get_attribute("value") == target_str,
    )


def build_password(pan):
    """PAN-derived password meeting CAMS complexity rules (upper, lower, digit, special)."""
    return f"{pan[:1].upper()}{pan[1:].lower()}@1"


def fill_form(driver, wait, email, pan):
    click_radio(driver, "mat-radio-group[formcontrolname='statemttype']", "detailed")
    wait.until(EC.presence_of_element_located(
        (By.CSS_SELECTOR, "mat-radio-group[formcontrolname='request_flag']")
    ))
    click_radio(driver, "mat-radio-group[formcontrolname='request_flag']", "SP")

    set_date(driver, wait, "from_date", FROM_DATE)
    set_date(driver, wait, "to_date", datetime.date.today())

    password = build_password(pan)
    driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='email_id']").send_keys(email)
    driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='pan']").send_keys(pan.upper())
    driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='password']").send_keys(password)
    driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='confirmPassword']").send_keys(password)


def wait_for_submit_result(driver, timeout=15):
    """CAMS gives no success/error toast - a successful submit either clears the
    email field or re-renders the form entirely (field disappears). A mat-error
    appearing instead means the request was rejected."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            email_val = driver.find_element(By.CSS_SELECTOR, "input[formcontrolname='email_id']").get_attribute("value")
        except (NoSuchElementException, StaleElementReferenceException):
            return "success", "Form accepted; page re-rendered by CAMS"
        if not email_val:
            return "success", "Form accepted and reset by CAMS"
        errors = [e.text for e in driver.find_elements(By.CSS_SELECTOR, "mat-error") if e.text.strip()]
        if errors:
            return "error", "; ".join(errors)
        time.sleep(0.5)
    return "unknown", "No confirmation observed within timeout; verify manually"


def submit(driver):
    btn = driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
    if btn.get_attribute("disabled") or btn.get_attribute("aria-disabled") == "true":
        invalid = [
            f.get_attribute("formcontrolname")
            for f in driver.find_elements(By.CSS_SELECTOR, ".ng-invalid[formcontrolname]")
        ]
        return "validation_failed", f"Submit button disabled, invalid fields: {invalid}"
    jsclick(driver, btn)
    return wait_for_submit_result(driver)


def process_row(email, pan, headless):
    if not email or not pan:
        logger.warning("Skipping row (missing email or pan): email=%r pan=%r", email, pan)
        log_result(email, "skipped", "missing email or pan")
        return
    driver = build_driver(headless)
    wait = WebDriverWait(driver, 20, ignored_exceptions=(StaleElementReferenceException,))
    try:
        driver.get(URL)
        accept_disclaimer(driver, wait)
        dismiss_ad_popup(driver, wait)
        fill_form(driver, wait, email, pan)
        status, detail = submit(driver)
        if status == "success":
            logger.info("SUCCESS %s - %s", email, detail)
        else:
            logger.error("%s %s - %s", status.upper(), email, detail)
        log_result(email, status, detail)
    except Exception as exc:
        logger.exception("FAILED %s - unexpected error", email)
        log_result(email, "exception", str(exc))
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_path", help="CSV file with 'email' and 'pan' columns")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless")
    args = parser.parse_args()

    setup_logging()
    for email, pan in read_rows(args.csv_path):
        process_row(email, pan, args.headless)


if __name__ == "__main__":
    main()
