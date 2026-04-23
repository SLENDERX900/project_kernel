import os
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException

def wake_streamlit_app():
    """Wake up Streamlit app by clicking the wake-up button if present."""
    
    # Get URL from environment variable
    streamlit_url = os.getenv('STREAMLIT_URL')
    if not streamlit_url:
        print("ERROR: STREAMLIT_URL environment variable not set")
        return False
    
    print(f"Attempting to wake up app at: {streamlit_url}")
    
    # Configure Chrome options for headless mode
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    try:
        # Initialize WebDriver
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        
        # Navigate to the app
        print("Loading app page...")
        driver.get(streamlit_url)
        
        # Wait for page to load
        time.sleep(3)
        
        # Check if app is already awake (no wake-up button needed)
        try:
            # Look for typical Streamlit app elements that indicate it's awake
            app_elements = driver.find_elements(By.CSS_SELECTOR, "[data-testid='stApp'], .stApp, main")
            if app_elements:
                print("✓ App is already awake!")
                driver.quit()
                return True
        except:
            pass
        
        # Look for the wake-up button with multiple possible selectors
        wake_button_selectors = [
            "//button[contains(text(), 'Yes, get this app back up!')]",
            "//button[contains(text(), 'Yes, get this app back up')]",
            "//button[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'yes, get this app back up')]",
            "//button[contains(@class, 'wake-up')]",
            "//button[contains(@aria-label, 'wake')]",
            "//button[contains(text(), 'Wake up')]",
            "//button[contains(text(), 'Get this app back up')]"
        ]
        
        button_found = False
        for selector in wake_button_selectors:
            try:
                # Try XPath first
                if selector.startswith('//'):
                    wake_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, selector))
                    )
                else:
                    # Try CSS selector
                    wake_button = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.CSS_SELECTOR, selector))
                    )
                
                print(f"✓ Found wake-up button with selector: {selector}")
                wake_button.click()
                print("✓ Successfully clicked wake-up button!")
                button_found = True
                break
                
            except TimeoutException:
                continue
            except Exception as e:
                print(f"Error with selector {selector}: {str(e)}")
                continue
        
        if not button_found:
            print("No wake-up button found. App might already be awake or button text has changed.")
            driver.quit()
            return True
        
        # Wait a moment for the app to start waking up
        time.sleep(5)
        
        # Check if app is now awake
        try:
            app_elements = driver.find_elements(By.CSS_SELECTOR, "[data-testid='stApp'], .stApp, main")
            if app_elements:
                print("✓ App successfully woke up!")
                driver.quit()
                return True
            else:
                print("⚠ Wake-up button clicked, but app status unclear")
                driver.quit()
                return True  # Still consider success since button was clicked
        except:
            print("⚠ Could not verify app status after wake-up attempt")
            driver.quit()
            return True  # Still consider success since button was clicked
            
    except Exception as e:
        print(f"ERROR: Failed to wake up app: {str(e)}")
        try:
            driver.quit()
        except:
            pass
        return False

if __name__ == "__main__":
    success = wake_streamlit_app()
    if success:
        print("Wake-up process completed successfully")
        exit(0)
    else:
        print("Wake-up process failed")
        exit(1)
