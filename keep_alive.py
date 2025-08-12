# keep_alive.py
from flask import Flask
import threading

app = Flask('')

@app.route('/')
def home():
    return "🤖 بۆتەکە کاردەکات بە سەرکەوتووی!"

def run():
    # port 8080 is typical for Render / other hosts
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()
