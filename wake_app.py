import os
import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def wake_streamlit():
    url = os.environ.get("STREAMLIT_URL", "").strip()
    
    # 1. Check if empty
    if not url:
        print("❌ CRITICAL: STREAMLIT_URL is NOT detected in environment variables!")
        print("Please set STREAMLIT_URL in GitHub Secrets with your app URL (e.g., https://your-app.streamlit.app)")
        sys.exit(1)
    
    # Print the first 10 characters to verify it's working without exposing the whole URL
    print(f"🔗 URL detected (masked): {url[:10]}...")
        
    # 2. Fix missing protocol
    if not url.startswith("http"):
        print(f"Warning: URL missing protocol. Adding https to: {url}")
        url = f"https://{url}"

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    try:
        print(f"🚀 Navigating to: {url}")
        driver.get(url)
        wait = WebDriverWait(driver, 30)
        
        # Click "Yes, get this app back up" button
        wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Yes, get this app back up')]"))).click()
        print("👆 Wake-up button clicked successfully.")
        
        # CRITICAL: Wait for main container and stabilize
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='stAppViewContainer']")))
        print("✨ App container detected, waiting for WebSocket connection...")
        time.sleep(10) # Ensure WebSocket is fully established
        print("✅ App is now fully awake and active!")
        
    except Exception as e:
        print(f"⚠ Error occurred: {e}")
        print("App might already be awake or button not found.")
    finally:
        driver.quit()

if __name__ == "__main__":
    wake_streamlit()
