import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

def setup_logging() -> None:
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    log_file = log_dir / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from .notifications import NotificationService
from .database import DatabaseManager
from bs4 import BeautifulSoup

class FollowerMonitor:

    def __init__(
        self, 
        notifier: NotificationService, 
        check_interval: int,
        twitter_email: str,
        twitter_username: str,
        twitter_password: str,
        db_manager: DatabaseManager
    ) -> None:

        self.notifier = notifier
        self.check_interval = check_interval
        self.twitter_email = twitter_email
        self.twitter_username = twitter_username
        self.twitter_password = twitter_password
        self.db_manager = db_manager
        self._known_follows: Dict[str, int] = {}
        self._is_running: bool = False
        self.cookies_file = Path("twitter_cookies.json")
        self._consecutive_errors = 0
        self._max_consecutive_errors = 8
        self._driver_restarts = 0
        self._normal_login_attempts = 0
        self._cookie_login_attempts = 0
        self._normal_login_failures = 0
        setup_logging()

    def _save_cookies(self, driver: webdriver.Chrome) -> None:
        cookies = driver.get_cookies()
        with open(self.cookies_file, "w") as f:
            json.dump(cookies, f)

    def _load_cookies(self, driver: webdriver.Chrome) -> bool:
        if not self.cookies_file.exists():
            return False
        
        driver.get("https://twitter.com")
        try:
            with open(self.cookies_file) as f:
                cookies = json.load(f)
                for cookie in cookies:
                    driver.add_cookie(cookie)
            driver.refresh()
            return "login" not in driver.current_url
        except Exception as e:
            print(f"Error loading cookies: {e}")
            return False

    def _login(self, driver: webdriver.Chrome) -> None:
        self._cookie_login_attempts += 1
        if self._load_cookies(driver):
            logging.info("Successfully logged in using cookies")
            return

        self._normal_login_attempts += 1
        logging.info("Attempting normal login...")
        
        #print("Attempting to load saved session...")
        #if self._load_cookies(driver):
        #    print("Successfully logged in using saved session")
        #    return

        print("Logging into Twitter...")
        driver.get("https://twitter.com/login")
        time.sleep(5)
        with open("login_page_initial.html", "w", encoding='utf-8') as f:
            f.write(driver.page_source)
        
        wait = WebDriverWait(driver, 10)

        email_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
        email_field.send_keys(self.twitter_username)
        email_field.send_keys(Keys.RETURN)
        
        time.sleep(2)
        with open("login_page_after_username.html", "w", encoding='utf-8') as f:
            f.write(driver.page_source)
        
        try:
            username_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
            username_field.send_keys(self.twitter_email.split('@')[0])
            username_field.send_keys(Keys.RETURN)
            time.sleep(2)
            with open("login_page_after_email.html", "w", encoding='utf-8') as f:
                f.write(driver.page_source)
        except:
            pass

        password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))
        password_field.send_keys(self.twitter_password)
        password_field.send_keys(Keys.RETURN)

        time.sleep(5)
        with open("login_page_after_password.html", "w", encoding='utf-8') as f:
            f.write(driver.page_source)
        
        if "login" in driver.current_url:
            with open("login_failed_page.html", "w", encoding='utf-8') as f:
                f.write(driver.page_source)
            self._normal_login_failures += 1
            logging.warning(f"Normal login failed. Total failures: {self._normal_login_failures}")
            
            self._cookie_login_attempts += 1
            if self._load_cookies(driver):
                logging.info("Successfully logged in using cookies after normal login failed")
                return
            else:
                logging.error("Both normal login and cookie login failed")
                raise Exception("All login attempts failed")
        
        logging.info("Normal login successful")
        self._save_cookies(driver)

    def _get_following(self, driver: webdriver.Chrome, username: str) -> int:
        print(f"Navigating to https://twitter.com/{username}'s profile page")
        driver.get(f"https://twitter.com/{username}")
        
        try:
            wait = WebDriverWait(driver, 10)
            following_xpath = "(//div[contains(@class, 'r-1rtiivn')])[1]"
            following_element = wait.until(EC.presence_of_element_located((By.XPATH, following_xpath)))
            
            html_content = following_element.get_attribute('innerHTML')
            soup = BeautifulSoup(html_content, 'html.parser')
            
            following_count = soup.find('span', text=lambda text: text and any(char.isdigit() for char in text)).text.strip()
            following_count_clean = ''.join(filter(str.isdigit, following_count))

            return int(following_count_clean)
        except Exception as e:
            raise Exception(f"Failed to get following count for @{username}. Account may not exist or be private: {str(e)}")

    def _get_latest_follow(self, driver: webdriver.Chrome, username: str) -> Optional[str]:
        for attempt in range(2):
            try:
                logging.info(f"Checking latest follow for @{username} - XPath attempt {attempt + 1}")
                driver.get(f"https://twitter.com/{username}/following")
                time.sleep(5)  

                xpath = '//*[@id="react-root"]/div/div/div[2]/main/div/div/div/div[1]/div/section/div/div/div[1]/div/div/button/div/div[2]/div[1]/div[1]/div/div[2]/div/a/div/div/span'

                element = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.XPATH, xpath)))
                    
                if element.text.strip():
                    username_text = element.text.strip()
                    if username_text.startswith('@'):
                        return username_text[1:]
                    return username_text
                    
            except Exception as e:
                logging.error(f"XPath attempt {attempt + 1} failed: {str(e)}")
                if attempt < 1:
                    time.sleep(3)
                    continue
        
        for attempt in range(2):
            result = self._get_latest_follow_from_html(driver, username)
            if result:
                return result
            if attempt < 1:
                driver.refresh()
                time.sleep(3)
        
        return None

    def _get_latest_follow_from_html(self, driver: webdriver.Chrome, username: str) -> Optional[str]:
        try:
            logging.info(f"Attempting to find latest follow for @{username} using HTML parsing")
            html_content = driver.page_source
            soup = BeautifulSoup(html_content, 'html.parser')
            
            spans = soup.find_all('span', class_='css-1jxf684')
            
            counter = 0
            for span in spans:
                if span.text and span.text.strip().startswith('@'):
                    counter += 1
                    if counter == 3: 
                        username_text = span.text.strip()
                        logging.info(f"Found latest follow for @{username}: from entire html scan")
                        return username_text[1:] if username_text.startswith('@') else username_text
            logging.info(f"No latest follow found for @{username} from entire html scan")
            return None
            
        except Exception as e:
            logging.error(f"Error parsing HTML for latest follow of {username}: {str(e)}")
            return None

    def _initialize_driver(self) -> webdriver.Chrome:
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--headless")
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument("--remote-debugging-port=9222")
        options.add_argument('--window-size=1920,1080')
        options.page_load_timeout = 30
        options.set_capability("pageLoadStrategy", "eager")
        
        service = webdriver.ChromeService()
        driver = webdriver.Chrome(service=service, options=options)
        
        try:
            self._login(driver)
            return driver
        except Exception as e:
            try:
                driver.quit()
            except:
                pass
            raise e

    def _restart_driver(self, driver: webdriver.Chrome) -> webdriver.Chrome:
        self._driver_restarts += 1
        logging.warning(f"Restarting driver. Total restarts: {self._driver_restarts}")
        
        for attempt in range(3):
            try:
                try:
                    driver.close()
                    driver.quit()
                    time.sleep(3)
                except:
                    pass

                try:
                    import psutil
                    for proc in psutil.process_iter(['pid', 'name']):
                        if 'chrome' in proc.info['name'].lower():
                            try:
                                psutil.Process(proc.info['pid']).terminate()
                            except:
                                pass
                    time.sleep(3)
                except:
                    pass

                new_driver = self._initialize_driver()
                logging.info(f"Driver successfully restarted on attempt {attempt + 1}")
                return new_driver

            except Exception as e:
                logging.error(f"Failed to restart driver on attempt {attempt + 1}: {str(e)}")
                time.sleep(5)
        
        logging.critical("Failed to restart Chrome driver after 3 attempts")
        raise Exception("Failed to restart Chrome driver after 3 attempts")

    def stop_monitoring(self) -> None:
        self._is_running = False
        logging.info(f"""Monitoring stopped. Statistics:
        Driver restarts: {self._driver_restarts}
        Normal login attempts: {self._normal_login_attempts}
        Cookie login attempts: {self._cookie_login_attempts}
        Normal login failures: {self._normal_login_failures}""")

    def start_monitoring(self, usernames: List[str]) -> None:
        self._is_running = True
        driver = self._initialize_driver()
        
        try:
            print("Login successful!")

            for username in usernames:
                try:
                    time.sleep(self.check_interval)
                    self._known_follows[username] = self._get_following(driver, username)
                    print(f"Initial following count for {username}: {self._known_follows[username]}")
                    self._consecutive_errors = 0
                except Exception as e:
                    print(f"Failed to get initial count for {username}: {str(e)}")
                    self._consecutive_errors += 1
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        driver = self._restart_driver(driver)
                        self._consecutive_errors = 0
                    continue
            
            while self._is_running:
                try:
                    current_usernames = self.db_manager.get_all_users()
                    
                    for username in current_usernames:
                        time.sleep(self.check_interval)
                        try:
                            if username not in self._known_follows:
                                try:
                                    self._known_follows[username] = self._get_following(driver, username)
                                    print(f"New user added - Initial following count for {username}: {self._known_follows[username]}")
                                    self._consecutive_errors = 0
                                    continue
                                except Exception as e:
                                    print(f"Failed to get initial count for new user {username}: {str(e)}")
                                    self._consecutive_errors += 1
                                    if self._consecutive_errors >= self._max_consecutive_errors:
                                        driver = self._restart_driver(driver)
                                        self._consecutive_errors = 0
                                    continue

                            current_follows = self._get_following(driver, username)

                            if current_follows > self._known_follows[username]:
                                latest_follow = self._get_latest_follow(driver, username)
                                if latest_follow:
                                    self.notifier.notify(
                                        f"@{username} started following @{latest_follow}"
                                    )
                                else:
                                    self.notifier.notify(
                                        f"@{username} started following {current_follows - self._known_follows[username]} new account(s). "
                                        f"Total following: {current_follows}"
                                    )
                            elif current_follows < self._known_follows[username]:
                                self.notifier.notify(
                                    f"@{username} unfollowed {self._known_follows[username] - current_follows} account(s). "
                                    f"Total following: {current_follows}"
                                )
                            
                            self._known_follows[username] = current_follows
                            self.db_manager.update_follower_count(username, current_follows)
                            self._consecutive_errors = 0

                        except Exception as e:
                            print(f"Error monitoring {username}: {str(e)}")
                            self._consecutive_errors += 1
                            if self._consecutive_errors >= self._max_consecutive_errors:
                                driver = self._restart_driver(driver)
                                self._consecutive_errors = 0
                            continue
                    
                except Exception as e:
                    #self.notifier.notify(f"Error in monitoring loop: {str(e)}")
                    print(f"Error in monitoring loop: {str(e)}")
                    self._consecutive_errors += 1
                    if self._consecutive_errors >= self._max_consecutive_errors:
                        driver = self._restart_driver(driver)
                        self._consecutive_errors = 0
                    time.sleep(self.check_interval)
                    
        finally:
            self._is_running = False
            try:
                driver.quit()
            except:
                pass 