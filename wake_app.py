import os
import sys
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

def wake_streamlit():
    url = os.environ.get("STREAMLIT_URL", "").strip()
    
    if not url:
        print("❌ CRITICAL: STREAMLIT_URL is NOT detected in environment variables!")
        return # This will help identify if the secret isn't passing through
    
    # Print the first 10 characters to verify it's working without exposing the whole URL
    print(f"🔗 URL detected (masked): {url[:10]}...")
    
    # 1. Check if empty
    if not url:
        print("ERROR: STREAMLIT_URL is empty! Check your GitHub Secrets.")
        sys.exit(1)
        
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
    
    print(f"🚀 Navigating to: {url}")
    driver.get(url)
    
    # 1. Wait for initial JS load and check for the 'Wake Up' button
    time.sleep(15) 
    buttons = driver.find_elements(By.TAG_NAME, "button")
    wake_button = next((b for b in buttons if "Yes" in b.text or "Wake" in b.text), None)

    if wake_button:
        print("👆 Hibernation detected. Clicking 'Wake Up'...")
        wake_button.click()
        time.sleep(5)
    else:
        print("✅ No wake button found. App might be starting up already.")

    # 2. Polling Loop: Wait up to 120 seconds for the app UI to appear
    print("⏳ Waiting for Streamlit UI to render (2 min timeout)...")
    start_time = time.time()
    is_active = False

    while time.time() - start_time < 120:
        # stAppViewContainer is the root div of a loaded Streamlit app
        if driver.find_elements(By.CSS_SELECTOR, "[data-testid='stAppViewContainer']"):
            print(f"✨ SUCCESS: App is LIVE after {int(time.time() - start_time)}s")
            is_active = True
            break
        
        print("...still booting...")
        time.sleep(10)

    driver.quit()

    if not is_active:
        print("❌ TIMEOUT: App failed to boot within 2 minutes.")
        sys.exit(1) # Makes the Action RED

if __name__ == "__main__":
    wake_streamlit()
