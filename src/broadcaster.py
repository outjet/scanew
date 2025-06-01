# src/broadcaster.py
import time
import logging
from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
    StaleElementReferenceException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from config import BROADCASTIFY_URL
logger = logging.getLogger(__name__)

def initialize_driver(headless: bool = False) -> webdriver.Chrome:
    """
    Create a new Chrome WebDriver instance using webdriver-manager.
    Automatically downloads the correct version of ChromeDriver.
    """
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if headless:
        options.add_argument("--headless=new")

    # This line uses the correct driver version automatically
    chrome_service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=chrome_service, options=options)
    driver.set_page_load_timeout(30)
    return driver

def safe_find_element(driver: webdriver.Chrome, by: By, value: str, timeout: int = 10):
    """
    Wait up to `timeout` seconds for an element to appear; if not found, raise.
    """
    try:
        wait = WebDriverWait(driver, timeout)
        element = wait.until(EC.presence_of_element_located((by, value)))
        return element
    except TimeoutException as e:
        logger.error(f"Timeout waiting for element {value!r}: {e}")
        raise

def click_play_button(driver: webdriver.Chrome, retries: int = 3) -> bool:
    """
    Attempts to find and click the "Play" button on the Broadcastify page.
    Returns True if after clicking, we detect that playback started (e.g. text changed).
    Returns False otherwise.
    """
    for attempt in range(1, retries + 1):
        try:
            # You must inspect the actual Broadcastify page to get a stable CSS selector
            # Commonly the "Play" button is a <button> with some class like "playpause" or similar.
            # Here is an example selector—adjust to match your specific station page.
            from config import PLAY_BUTTON_SELECTOR
            play_button = safe_find_element(driver, By.CSS_SELECTOR, PLAY_BUTTON_SELECTOR, timeout=10)
            btn_text = play_button.text.strip().lower()
            if "play" in btn_text:
                try:
                    play_button.click()
                except WebDriverException:
                    # fallback to JS click if normal click is intercepted
                    driver.execute_script("arguments[0].click();", play_button)
                time.sleep(2)  # give it a moment to switch to "Pause"
                updated_text = play_button.text.strip().lower()
                if "pause" in updated_text:
                    logger.info("Broadcastify is now playing.")
                    return True
                else:
                    logger.warning(f"Click did not start playback (button text is still {updated_text!r}); attempt {attempt}/{retries}")
            else:
                # Maybe it’s already playing?
                logger.debug(f"Play button text is {btn_text!r}, assuming playback already active.")
                return True
        except Exception as e:
            logger.warning(f"Attempt {attempt}/{retries} to click play button raised {e.__class__.__name__}: {e}")
            time.sleep(2)
    return False

def get_backoff_time(downtime_seconds: int) -> int:
    """
    Returns a backoff interval (in seconds) based on how long the feed has been down.
    """
    if downtime_seconds < 300:
        return 10  # retry after 10s if < 5 minutes down
    elif downtime_seconds < 3600:
        return 30  # retry after 30s if < 1 hour down
    elif downtime_seconds < 86400:
        return 180  # retry after 3 minutes if < 24 hours down
    else:
        return 900  # retry after 15 minutes if > 24 hours down

def start_and_monitor_broadcastify(headless: bool = False):
    """
    Main loop: open Chrome, navigate to BROADCASTIFY_URL, click Play, then
    poll the button to ensure it’s still "playing." If it ever reverts to "Play"
    or we get an exception, we force a restart with a backoff.
    This function never returns—it logs and sleeps, then retries forever.
    """
    while True:
        driver = None
        try:
            driver = initialize_driver(headless=headless)
            logger.debug(f"Selenium: Navigating to {BROADCASTIFY_URL}")
            driver.get(BROADCASTIFY_URL)

            # Attempt to click “Play” up to a few times or refresh
            if not click_play_button(driver, retries=3):
                logger.error("Initial click_play_button attempts failed—trying a refresh.")
                driver.refresh()
                time.sleep(5)
                if not click_play_button(driver, retries=3):
                    raise RuntimeError("Could not start playback after refresh.")

            last_success_time = time.time()
            logger.info("Broadcastify playback confirmed. Entering monitoring loop.")

            # Poll loop
            while True:
                time.sleep(10)  # poll every 10s
                try:
                    from config import PLAY_BUTTON_SELECTOR
                    play_button = safe_find_element(driver, By.CSS_SELECTOR, PLAY_BUTTON_SELECTOR, timeout=5)
                    btn_text = play_button.text.strip().lower()
                    if "play" in btn_text:
                        # The button changed to “Play” → feed stopped
                        raise RuntimeError("Playback stopped (button reverted to Play).")
                    else:
                        # Still “Pause” → playing
                        last_success_time = time.time()
                        continue
                except (TimeoutException, NoSuchElementException, StaleElementReferenceException) as e:
                    downtime = int(time.time() - last_success_time)
                    backoff = get_backoff_time(downtime)
                    if downtime < 3600:
                        logger.info(f"Playback lost ~{downtime}s ago; retrying in {backoff}s.")
                    elif downtime < 86400:
                        logger.warning(f"Playback lost ~{downtime/3600:.1f}h ago; retrying in {backoff}s.")
                    else:
                        logger.error(f"Playback lost ~{downtime/3600/24:.1f}d ago; retrying in {backoff}s.")
                    driver.quit()
                    time.sleep(backoff)
                    break  # outer while restarts

        except Exception as ex:
            logger.critical(f"Unexpected error in Broadcastify monitor: {ex}", exc_info=True)
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            logger.critical("Re‐attempting Broadcastify monitor in 60 seconds...")
            time.sleep(60)
            continue  # retry from top

