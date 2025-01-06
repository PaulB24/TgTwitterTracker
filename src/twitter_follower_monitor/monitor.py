from concurrent.futures import ThreadPoolExecutor
import time
from typing import List, Dict, Optional
from threading import Lock

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
        twitter_password: str,
        db_manager: DatabaseManager,
        max_workers: int = 5  # Number of concurrent browsers
    ) -> None:

        self.notifier = notifier
        self.check_interval = check_interval
        self.twitter_email = twitter_email
        self.twitter_password = twitter_password
        self.db_manager = db_manager
        self.max_workers = max_workers
        self._known_follows: Dict[str, int] = {}
        self._is_running: bool = False
        self._browser_pool: List[webdriver.Chrome] = []
        self._browser_lock = Lock()

    def _login(self, driver: webdriver.Chrome) -> None:
        print("Logging into Twitter...")
        driver.get("https://twitter.com/login")
        wait = WebDriverWait(driver, 10)

        email_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
        email_field.send_keys(self.twitter_email)
        email_field.send_keys(Keys.RETURN)
        
        try:
            username_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
            username_field.send_keys(self.twitter_email.split('@')[0])
            username_field.send_keys(Keys.RETURN)
        except:
            pass

        password_field = wait.until(EC.presence_of_element_located((By.NAME, "password")))
        password_field.send_keys(self.twitter_password)
        password_field.send_keys(Keys.RETURN)

        time.sleep(5)  
        
        if "login" in driver.current_url:
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
        options.add_argument("--start-maximized")
        options.add_argument("--headless")

        for i in range(0, len(usernames), 5):
            user_group = usernames[i:i+5]
            driver = webdriver.Chrome(options=options)
            self._login(driver)
            browser_assignments[driver] = user_group
            self._browser_pool.append(driver)

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

    def start_monitoring(self, usernames: List[str]) -> None:
        self._is_running = True
        
        try:
            print("Initializing browser pool...")
            browser_assignments = self._initialize_browser_pool(usernames)

            with ThreadPoolExecutor(max_workers=len(browser_assignments)) as executor:
                while self._is_running:
                    current_usernames = self.db_manager.get_all_users()
                    
                    if set(current_usernames) != set(sum(browser_assignments.values(), [])):
                        for driver in self._browser_pool:
                            driver.quit()
                        self._browser_pool.clear()
                        browser_assignments = self._initialize_browser_pool(current_usernames)
                    
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