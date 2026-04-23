import os
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def wake_streamlit():
    url = os.environ.get("STREAMLIT_URL")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        print(f"Navigating to {url}...")
        driver.get(url)
        
        # Wait up to 20 seconds for the 'Wake Up' button to appear
        # The button text is typically "Yes, get this app back up!"
        wait = WebDriverWait(driver, 20)
        wake_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[contains(text(), 'Yes')]")))
        
        wake_button.click()
        print("Wake-up button clicked successfully.")
        
        # Wait briefly for the reload to start
        wait.until(EC.presence_of_element_located((By.DATA_TESTID, "stAppViewContainer")))
        print("App is now active!")
        
    except Exception as e:
        print(f"App might already be awake or error occurred: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    wake_streamlit()
