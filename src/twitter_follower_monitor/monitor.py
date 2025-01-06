import time
import json
import os
from pathlib import Path
from typing import  List, Dict, Optional

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
        print("Attempting to load saved session...")
        if self._load_cookies(driver):
            print("Successfully logged in using saved session")
            return

        print("Logging into Twitter...")
        driver.get("https://twitter.com/login")
        wait = WebDriverWait(driver, 10)

        email_field = wait.until(EC.presence_of_element_located((By.NAME, "text")))
        email_field.send_keys(self.twitter_username)
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
        
        print("Saving session cookies...")
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

    def start_monitoring(self, usernames: List[str]) -> None:
        self._is_running = True
        options = webdriver.ChromeOptions()
        options.add_argument("--start-maximized")
        options.add_argument("--headless")  
        options.add_argument('--no-sandbox')
        
        driver = webdriver.Chrome(options=options)
        
        try:
            self._login(driver)
            print("Login successful!")

            for username in usernames:
                try:
                    self._known_follows[username] = self._get_following(driver, username)
                    print(f"Initial following count for {username}: {self._known_follows[username]}")
                except Exception as e:
                    print(f"Failed to get initial count for {username}: {str(e)}")
                    continue
            
            while self._is_running:
                try:
                    current_usernames = self.db_manager.get_all_users()
                    
                    for username in current_usernames:
                        try:
                            if username not in self._known_follows:
                                try:
                                    self._known_follows[username] = self._get_following(driver, username)
                                    print(f"New user added - Initial following count for {username}: {self._known_follows[username]}")
                                    continue
                                except Exception as e:
                                    print(f"Failed to get initial count for new user {username}: {str(e)}")
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

                            
                        except Exception as e:
                            print(f"Error monitoring {username}: {str(e)}")
                            continue
                        
                    
                except Exception as e:
                    self.notifier.notify(f"Error in monitoring loop: {str(e)}")
                    time.sleep(self.check_interval)
                    
        finally:
            driver.quit() 