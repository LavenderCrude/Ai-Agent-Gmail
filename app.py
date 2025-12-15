# app.py
from flask import Flask, jsonify, send_from_directory
from flask_socketio import SocketIO
from pymongo import MongoClient
from bson import ObjectId
from dotenv import load_dotenv
import os
import threading
import time

load_dotenv()

app = Flask(__name__, static_folder="static")
app.config["SECRET_KEY"] = "ai-agent-2025"
socketio = SocketIO(app, cors_allowed_origins="*")

MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI missing in .env")
client = MongoClient(MONGO_URI)
db = client["email_agent_db"]
logs = db["email_logs"]
print("Connected to MongoDB: email_agent_db.email_logs")

last_count = 0
def watch_mongo():
    global last_count
    print("Polling watcher started (every 2 sec)...")
    while True:
        try:
            cur = logs.count_documents({})
            if cur > last_count:
                print(f"New email detected! {last_count} → {cur}")
                socketio.emit('new_email')
                last_count = cur
            time.sleep(2)
        except Exception as e:
            print("Polling error:", e)
            time.sleep(5)

threading.Thread(target=watch_mongo, daemon=True).start()


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "dashboard.html")

@app.route("/api/emails")
def get_emails():
    emails = list(logs.find().sort("processed_at", -1))
    for e in emails:
        e["_id"] = str(e["_id"])
        e.setdefault("ai_category", "other")
    return jsonify(emails)

@app.route("/api/stats")
def api_stats():
    total = logs.count_documents({})
    cats = {
        "interview": logs.count_documents({"ai_category": "interview"}),
        "meeting":   logs.count_documents({"ai_category": "meeting"}),
        "important_email": logs.count_documents({"ai_category": "important_email"}),
        "not_important":   logs.count_documents({"ai_category": "not_important"}),
        "other":           logs.count_documents({"ai_category": "other"})
    }
    return jsonify({"total": total, "categories": cats})

@app.route("/api/delete/<email_id>", methods=["DELETE"])
def delete_email(email_id):
    try:
        res = logs.delete_one({"_id": ObjectId(email_id)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

    if res.deleted_count:
        print(f"Deleted email ID: {email_id}")
        socketio.emit('refresh')
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Not found"}), 404

@app.route("/api/refresh", methods=["POST"])
def manual_refresh():
    print("Manual refresh triggered")
    socketio.emit('refresh')
    return jsonify({"success": True})

if __name__ == "__main__":
    print("AI Email Dashboard → http://127.0.0.1:5000")
    socketio.run(app, host="127.0.0.1", port=5000, debug=False)