from concurrent.futures import ThreadPoolExecutor
import time
from typing import List, Dict, Optional
from threading import Lock
import pickle
import os
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.chrome.service import Service
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
        db_manager: DatabaseManager,
        max_workers: int = 5  # Number of concurrent browsers
    ) -> None:

        self.notifier = notifier
        self.check_interval = check_interval
        self.twitter_email = twitter_email
        self.twitter_username = twitter_username
        self.twitter_password = twitter_password
        self.db_manager = db_manager
        self.max_workers = max_workers
        self._known_follows: Dict[str, int] = {}
        self._is_running: bool = False
        self._browser_pool: List[webdriver.Chrome] = []
        self._browser_lock = Lock()
        self.cookies_dir = Path("twitter_cookies")
        self.cookies_dir.mkdir(exist_ok=True)
        self.cookie_file = self.cookies_dir / f"cookies_{twitter_username}.pkl"

    def _save_cookies(self, driver: webdriver.Chrome) -> None:
        pickle.dump(driver.get_cookies(), self.cookie_file.open("wb"))
        print("Cookies saved successfully")

    def _load_cookies(self, driver: webdriver.Chrome) -> bool:
        if not self.cookie_file.exists():
            return False

        try:
            cookies = pickle.load(self.cookie_file.open("rb"))
            
            driver.get("https://twitter.com")
            
            for cookie in cookies:
                try:
                    driver.add_cookie(cookie)
                except Exception as e:
                    print(f"Error adding cookie: {str(e)}")

            driver.get("https://twitter.com/home")
            time.sleep(3)

            if "login" in driver.current_url:
                print("Cookies expired or invalid")
                self.cookie_file.unlink()  
                return False

            print("Successfully loaded cookies")
            return True

        except Exception as e:
            print(f"Error loading cookies: {str(e)}")
            return False

    def _login(self, driver: webdriver.Chrome) -> None:
        print("Attempting to login...")

        if self._load_cookies(driver):
            print("Logged in successfully using cookies")
            return

        print("No valid cookies found, performing full login...")
        driver.get("https://twitter.com/login")
        wait = WebDriverWait(driver, 10)


        email_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
        email_field.send_keys(self.twitter_username)
        email_field.send_keys(Keys.RETURN)
        print("Email field sent")
        time.sleep(2)


        try:
            password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))
            print("Password field found")
            password_field.send_keys(self.twitter_password)
            password_field.send_keys(Keys.RETURN)
            print("Password field sent")
        except Exception as e:
            print(f"Error finding password field: {str(e)}")
            print(f"Current page content preview: {driver.page_source[:500]}")



        time.sleep(5)
        print(f"Final URL: {driver.current_url}")
        
        if "login" in driver.current_url:
            print("Final page content:", driver.page_source[:500])
            raise Exception("Login failed - please check credentials")
        time.sleep(5)  
        if "login" not in driver.current_url:
            self._save_cookies(driver)
        else:
            raise Exception("Login failed - please check credentials")

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

        try:
            print(f"Checking latest follow for @{username}")
            driver.get(f"https://twitter.com/{username}/following")
            
            wait = WebDriverWait(driver, 10)
            latest_follow_xpath = '//*[@id="react-root"]/div/div/div[2]/main/div/div/div/div[1]/div/section/div/div/div[1]/div/div/button/div/div[2]/div[1]/div[1]/div/div[2]/div/a/div/div/span'
            
            latest_follow_element = wait.until(EC.presence_of_element_located((By.XPATH, latest_follow_xpath)))
            latest_follow = latest_follow_element.text.strip()
            
            if latest_follow.startswith('@'):
                return latest_follow[1:]  
            return latest_follow
            
        except Exception as e:
            print(f"Error getting latest follow for {username}: {str(e)}")
            return None

    def stop_monitoring(self) -> None:
        self._is_running = False

    def _initialize_browser_pool(self, usernames: List[str]) -> Dict[webdriver.Chrome, List[str]]:
        browser_assignments: Dict[webdriver.Chrome, List[str]] = {}
        options = webdriver.ChromeOptions()
        #CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
        #service = Service(CHROMEDRIVER_PATH)

        options.add_argument("--start-maximized")
        options.add_argument("--headless")
        options.add_argument('--no-sandbox')

        # Create browser instances
        for i in range(0, len(usernames), 5):
            user_group = usernames[i:i+5]
            driver = webdriver.Chrome( options=options)
            
            try:
                self._login(driver)  # No instance_id needed anymore
                browser_assignments[driver] = user_group
                self._browser_pool.append(driver)
            except Exception as e:
                print(f"Failed to initialize browser instance for group {i//5}: {str(e)}")
                driver.quit()
                continue

        return browser_assignments

    def _get_browser(self) -> webdriver.Chrome:
        with self._browser_lock:
            return self._browser_pool.pop()

    def _return_browser(self, driver: webdriver.Chrome) -> None:
        with self._browser_lock:
            self._browser_pool.append(driver)

    def _monitor_user_group(self, driver: webdriver.Chrome, usernames: List[str]) -> None:
        try:
            for username in usernames:
                try:
                    current_follows = self._get_following(driver, username)

                    if username not in self._known_follows:
                        self._known_follows[username] = current_follows
                        continue

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
                    
                except Exception as e:
                    print(f"Error monitoring {username}: {str(e)}")
                
        except Exception as e:
            print(f"Error in browser instance monitoring group {usernames}: {str(e)}")

    def _reorganize_browser_assignments(
        self, 
        current_assignments: Dict[webdriver.Chrome, List[str]], 
        all_usernames: List[str]
    ) -> Dict[webdriver.Chrome, List[str]]:
        """Reorganize browser assignments when users are added/removed."""
        
        # Get all current browsers and usernames
        current_browsers = list(current_assignments.keys())
        monitored_users = set(sum(current_assignments.values(), []))
        new_users = [u for u in all_usernames if u not in monitored_users]
        
        if not new_users:
            return current_assignments
        
        new_assignments: Dict[webdriver.Chrome, List[str]] = {}
        
        # First, fill existing browsers that aren't at capacity
        for browser in current_browsers:
            current_users = current_assignments[browser]
            available_slots = 5 - len(current_users)
            
            if available_slots > 0:
                users_to_add = new_users[:available_slots]
                new_users = new_users[available_slots:]  # Remove assigned users
                new_assignments[browser] = current_users + users_to_add
            else:
                new_assignments[browser] = current_users
        
        if new_users:
            options = webdriver.ChromeOptions()
            #CHROMEDRIVER_PATH = '/usr/bin/chromedriver'
            #service = Service(CHROMEDRIVER_PATH)

            options.add_argument("--start-maximized")
            options.add_argument("--headless")
            options.add_argument('--no-sandbox')
            
            for i in range(0, len(new_users), 5):
                user_group = new_users[i:i+5]
                driver = webdriver.Chrome( options=options)
                
                try:
                    self._login(driver)
                    new_assignments[driver] = user_group
                    self._browser_pool.append(driver)
                except Exception as e:
                    print(f"Failed to initialize browser instance for new group: {str(e)}")
                    driver.quit()
                    continue
        
        return new_assignments

    def start_monitoring(self, usernames: List[str]) -> None:
        self._is_running = True
        
        try:
            print("Initializing browser pool...")
            browser_assignments = self._initialize_browser_pool(usernames)

            with ThreadPoolExecutor() as executor:
                while self._is_running:
                    current_usernames = self.db_manager.get_all_users()
                    
                    # Check if user list has changed
                    if set(current_usernames) != set(sum(browser_assignments.values(), [])):
                        print("User list changed, reorganizing browser assignments...")
                        browser_assignments = self._reorganize_browser_assignments(
                            browser_assignments, 
                            current_usernames
                        )
                    
                    futures = [
                        executor.submit(self._monitor_user_group, driver, users)
                        for driver, users in browser_assignments.items()
                    ]
                    
                    for future in futures:
                        future.result()
                    
                    time.sleep(self.check_interval)
                    
        finally:
            self._is_running = False
            for driver in self._browser_pool:
                try:
                    driver.quit()
                except:
                    pass 