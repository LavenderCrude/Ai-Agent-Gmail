import os
import base64
import json
import time
import re
from email.mime.text import MIMEText
from typing import Optional, Dict, Any, List
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from dotenv import load_dotenv
from pymongo import MongoClient
import google.generativeai as genai

load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model = genai.GenerativeModel('gemini-2.5-flash') 


SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/calendar.events'
]

# MongoDB setup
MONGO_URI = os.getenv("MONGO_URI")
if not MONGO_URI:
    raise ValueError("MONGO_URI not set in .env file or environment variables")

client = MongoClient(MONGO_URI)
db = client["email_agent_db"]
processed_collection = db["processed_messages"]
email_logs_collection = db["email_logs"] 
# ---------------------------
# Helpers: Simple DB for processed messages
# ---------------------------

def mark_processed(msg_id: str):
    doc = {"_id": msg_id, "processed_at": int(time.time())}
    processed_collection.insert_one(doc)

def is_processed(msg_id: str) -> bool:
    return processed_collection.find_one({"_id": msg_id}) is not None

# ---------------------------
# Helpers: Store email and reply data in MongoDB
# ---------------------------

def store_email_and_reply(data: Dict[str, Any], reply_template: Dict[str, Any], action_status: str):
    email_log = {
        "message_id": data["id"],
        "from": data["from"],
        "to": data["to"],
        "date": data["date"],
        "subject": data["subject"],
        "body": data["body"],
        "ai_reply": {
            "subject": reply_template.get("subject") or f"Re: {data['subject']}",
            "body": reply_template.get("body") or "Thank you for your email. I have received it and will get back to you shortly."
        } if reply_template.get("should_reply") else None,
        "action_status": action_status,
        "processed_at": int(time.time())
    }
    email_logs_collection.insert_one(email_log)

# ---------------------------
# Gmail auth / client
# ---------------------------

def gmail_authenticate():
    creds = None
    
    if os.path.exists('token.json'):
        try:
            creds = Credentials.from_authorized_user_file('token.json', SCOPES)
            temp_service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
            profile = temp_service.users().getProfile(userId='me').execute()
            user_email = profile.get('emailAddress')
            print(f"\nAuthenticated with: {user_email}")
            
            switch = input("\nA token file already exists. Switch account? (yes/no): ").lower()
            if switch == 'yes':
                os.remove('token.json')
                print("Existing token deleted. A new authentication window will open.")
                creds = None
        except Exception as e:
            print("Existing token is invalid. Proceeding with new authentication.")
            if os.path.exists('token.json'):
                os.remove('token.json')
            creds = None
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"Failed to refresh token: {e}")
                creds = None
        if not creds:
            if not os.path.exists('credentials.json'):
                raise FileNotFoundError("credentials.json not found. Create OAuth credentials in Google Cloud and download credentials.json")
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
    
    service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
    profile = service.users().getProfile(userId='me').execute()
    user_email = profile.get('emailAddress')
    print(f"\nAuthenticated with Gmail as: {user_email}")
    
    return service, creds, user_email  # Return creds as well for calendar

# ---------------------------
# Utilities: decode message payloads
# ---------------------------

def extract_email_address(from_header: str) -> str:
    m = re.search(r'<([^>]+)>', from_header)
    if m:
        return m.group(1)
    m2 = re.search(r'[\w\.-]+@[\w\.-]+', from_header)
    return m2.group(0) if m2 else from_header

def get_message_snippet_and_body(service, message) -> Dict[str, Any]:
    msg_id = message['id']
    try:
        full = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
        headers = full.get('payload', {}).get('headers', [])
        subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), '')
        from_hdr = next((h['value'] for h in headers if h['name'].lower() == 'from'), '')
        date_hdr = next((h['value'] for h in headers if h['name'].lower() == 'date'), '')
        to_hdr = next((h['value'] for h in headers if h['name'].lower() == 'to'), '')
        message_id_hdr = next((h['value'] for h in headers if h['name'].lower() == 'message-id'), '')
        
        snippet = full.get('snippet', '')
        
        parts = full.get('payload', {}).get('parts', [])
        body = ''
        if parts:
            for p in parts:
                if p.get('mimeType') == 'text/plain' and p.get('body', {}).get('data'):
                    data = p['body']['data']
                    body = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                    break
            if not body:
                for p in parts:
                    if p.get('mimeType') == 'text/html' and p.get('body', {}).get('data'):
                        data = p['body']['data']
                        body_html = base64.urlsafe_b64decode(data).decode('utf-8', errors='ignore')
                        body = re.sub('<[^<]+?>', '', body_html)
                        break
        else:
            body_data = full.get('payload', {}).get('body', {}).get('data')
            if body_data:
                body = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='ignore')

        return {
            "id": msg_id,
            "snippet": snippet,
            "subject": subject,
            "from": from_hdr,
            "from_email": extract_email_address(from_hdr),
            "to": to_hdr,
            "date": date_hdr,
            "message_id_header": message_id_hdr,
            "body": body
        }
    except Exception as e:
        print(f"Error decoding message {msg_id}: {e}")
        return {
            "id": msg_id,
            "snippet": "",
            "subject": "",
            "from": "",
            "from_email": "",
            "to": "",
            "date": "",
            "message_id_header": "",
            "body": ""
        }

# ---------------------------
# Gemini analysis: classification
# ---------------------------
EXTRA_SYSTEM = """
You are an assistant that reads a plain-text email and returns EXACTLY one JSON object (no extra text).
The JSON must match the schema below (keys must exist; set values to null if not applicable).

Schema:
{
  "category": "interview" | "meeting" | "important_email" | "not_important" | "other",
  "confidence": 0.0-1.0,
  "summary": "<one-line summary>",
  "action": "reply" | "archive" | "label_only" | "no_action",
  "reply_template": {
     "should_reply": true|false,
     "subject": "<subject for reply>",
     "body": "<body text for reply (plain text)>"
  },
  "metadata": {
     "calendar_event": {
         "summary": "<event summary|null>",
         "start": "<ISO8601 datetime|null>",
         "end": "<ISO8601 datetime|null>",
         "location": "<text|null>",
         "description": "<event description|null>"
     }
  }
}
Return only the JSON (no markdown, no explanation).
"""
PROMPT_TEMPLATE = """
Email Subject:
{subject}

From:
{from_hdr}

Body:
{body}

Instructions:
1) Classify the email and extract structured fields per the JSON schema.
2) If the email is a confirmed interview or meeting, extract the event details (summary, start, end, location) from the body. Parse dates and times to ISO8601 format (e.g., 2025-09-18T10:00:00+05:30 for IST). Assume timezone is IST (Asia/Kolkata) if not specified. Set category to "interview" or "meeting" and reply_template.should_reply to true. Provide a concise, polite confirmation reply.
3) If the email is important but not a meeting (e.g., a formal notice), set category to "important_email" and action to "no_action".
4) If the email is not important (e.g., a newsletter, promotion), set category to "not_important", action to "archive", and reply_template.should_reply to true. Provide a concise, polite automatic reply.
5) If you are not confident, set confidence appropriately and prefer safe actions.
6) Do not include any extra keys beyond the schema. Output JSON only.
7) In End After sincerly, Name is Akhil Kushwaha. 
Now analyze the email.
"""

def call_gemini_for_structured(email_subject: str, email_from: str, email_body: str) -> Dict[str, Any]:
    prompt = PROMPT_TEMPLATE.format(subject=email_subject, from_hdr=email_from, body=email_body)
    
    try:
        response = gemini_model.generate_content(
            contents=[
                {"role": "user", "parts": [EXTRA_SYSTEM + "\n\n" + prompt]}
            ]
        )
        
        text = response.text.strip()
        if text.startswith('```json'):
            text = text[len('```json'):].strip()
        if text.endswith('```'):
            text = text[:-len('```')].strip()
        
        parsed = json.loads(text)
    except Exception as e:
        print("Error parsing Gemini model output:", e)
        parsed = {
            "category": "other",
            "confidence": 0.0,
            "summary": "Could not parse model output",
            "action": "no_action",
            "reply_template": {"should_reply": False, "subject": None, "body": None},
            "metadata": {"calendar_event": None}
        }
    return parsed

# ---------------------------
# Gmail send/reply/label helpers
# ---------------------------

def send_reply(service, reply_to: str, subject: str, body: str, thread_id: str=None, in_reply_to: str=None, sender_email: str=None):
    msg = MIMEText(body)
    if sender_email:
        msg['From'] = sender_email
    msg['To'] = reply_to
    msg['Subject'] = subject
    if in_reply_to:
        msg['In-Reply-To'] = in_reply_to
        msg['References'] = in_reply_to
    
    try:
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode('utf-8')  # Fixed: encode instead of decode
        body_req = {'raw': raw}
        if thread_id:
            body_req['threadId'] = thread_id
        
        sent = service.users().messages().send(userId='me', body=body_req).execute()
        return sent
    except Exception as e:
        print(f"Error sending reply: {e}")
        return None

def modify_labels(service, message_id: str, add_labels: List[str]=None, remove_labels: List[str]=None):
    body = {}
    if add_labels: body['addLabelIds'] = add_labels
    if remove_labels: body['removeLabelIds'] = remove_labels
    try:
        return service.users().messages().modify(userId='me', id=message_id, body=body).execute()
    except Exception as e:
        print(f"Error modifying labels for message {message_id}: {e}")
        return None

# ---------------------------
# Google Calendar Integration
# ---------------------------
def create_calendar_event(creds, event_data: dict, attendee_email: str):  # Changed: Accept creds directly
    calendar_service = build('calendar', 'v3', credentials=creds)  # Build with creds
    
    event = {
        'summary': event_data.get('summary', 'New Event'),
        'location': event_data.get('location', 'Online'),
        'description': event_data.get('description', ''),
        'start': {
            'dateTime': event_data.get('start'),
            'timeZone': 'Asia/Kolkata',
        },
        'end': {
            'dateTime': event_data.get('end'),
            'timeZone': 'Asia/Kolkata',
        },
        'attendees': [
            {'email': attendee_email}
        ],
    }

    try:
        created_event = calendar_service.events().insert(calendarId='primary', body=event).execute()
        print(f"Event created: {created_event.get('htmlLink')}")
        return True
    except Exception as e:
        print(f"Failed to create calendar event: {e}")
        return False

# ---------------------------
# Utility to get authenticated email
# ---------------------------
def get_authenticated_email(service) -> Optional[str]:
    try:
        profile = service.users().getProfile(userId='me').execute()
        return profile.get('emailAddress')
    except Exception as e:
        print(f"Error fetching authenticated email: {e}")
        return None

# ---------------------------
# Main program flow
# ---------------------------

def main_loop(poll_interval=20):
    try:
        service, creds, user_email = gmail_authenticate()
        print("Authenticated with Gmail.")

        while True:
            try:
                query = "is:unread is:important"
                # query = "is:unread"
                messages_resp = service.users().messages().list(userId='me', q=query, maxResults=20).execute()
                msgs = messages_resp.get('messages', []) or []

                if not msgs:
                    print(f"No new important unread emails. Sleeping for {poll_interval}s...")  # Updated for clarity
                    time.sleep(poll_interval)
                    continue

                for msg in msgs:
                    msg_id = msg['id']
                    if is_processed(msg_id):
                        continue

                    data = get_message_snippet_and_body(service, msg)
                    print("-" * 50)
                    print(f"Processing NEW EMAIL:")
                    print(f"From: {data['from']}")
                    print(f"To: {data['to']}")
                    print(f"Date: {data['date']}")
                    print(f"Subject: {data['subject']}")
                    print(f"Body: {data['body']}")
                    print("-" * 50)

                    structured = call_gemini_for_structured(data['subject'], data['from'], data['body'])
                    print("Model classified as:", structured.get('category'), "action:", structured.get('action'))
                    
                    reply_template = structured.get('reply_template', {}) or {}
                    if reply_template.get('should_reply'):
                        print("\nAI Proposed Reply:")
                        print(f"Subject: {reply_template.get('subject')}")
                        print(f"Body:\n{reply_template.get('body')}")
                        print("-" * 50)
                    else:
                        print("\nReply: None")
                        print("-" * 50)

                    event_data = structured.get('metadata', {}).get('calendar_event')
                    calendar_created = False
                    if event_data and event_data.get('start') and event_data.get('end'):
                        calendar_created = create_calendar_event(creds, event_data, data['from_email'])  # Pass creds

                    action_status = ""
                    if reply_template.get('should_reply'):
                        subject_reply = reply_template.get('subject') or f"Re: {data['subject']}"
                        body_reply = reply_template.get('body') or "Thank you for your email. I have received it and will get back to you shortly."
                        full_meta = service.users().messages().get(userId='me', id=msg_id, format='metadata').execute()
                        thread_id = full_meta.get('threadId')
                        try:
                            send_reply(
                                service,
                                data['from_email'],
                                subject_reply,
                                body_reply,
                                thread_id=thread_id,
                                in_reply_to=data.get('message_id_header'),
                                sender_email=user_email
                            )
                            print("Sent automated reply.")
                            action_status = "Sent automated reply."
                        except Exception as e:
                            print(f"Failed to send reply: {e}")
                            action_status = f"Failed to send reply: {e}"

                    if structured.get('action') == 'archive' or structured.get('category') == 'not_important':
                        modify_labels(service, msg_id, remove_labels=['UNREAD', 'INBOX'])
                        action_status += " Email processed and archived."
                        print("Email processed and archived.")
                    else:
                        modify_labels(service, msg_id, remove_labels=['UNREAD'])
                        action_status += " Email processed, marked as read."
                        print("Email processed, marked as read.")

                    if calendar_created:
                        action_status += " Calendar event created."

                    # Store email and reply data in MongoDB
                    store_email_and_reply(data, reply_template, action_status)

                    mark_processed(msg_id)
                    time.sleep(2)
                
            except KeyboardInterrupt:
                print("Interrupted by user. Exiting.")
                break
            except Exception as e:
                print("Error in main loop:", e)
                time.sleep(10)
    finally:
        client.close()
        print("MongoDB connection closed.")

if __name__ == "__main__":
    main_loop(poll_interval=20)