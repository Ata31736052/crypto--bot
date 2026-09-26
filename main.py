import requests

url = "https://api.binance.com/api/v3/ping"
try:
    response = requests.get(url, timeout=5)
    print("Status Code:", response.status_code)
    print("Response:", response.text)
except Exception as e:
    print("Connection Error:", e)
    
