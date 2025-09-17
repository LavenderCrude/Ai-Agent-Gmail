Gmail AI Agent
Last Updated: September 17, 2025, 11:32 PM IST
Author: LevenderCrude

Overview
The Gmail AI Agent is a versatile automation tool designed to process incoming Gmail emails, classify them using an AI model (e.g., Gemini for Python, OpenAI as a placeholder for Node.js), send automated replies, create Google Calendar events for meetings or interviews, and log details to a MongoDB database. This repository includes implementations in both Python (gmail_ai_agent.py) and Node.js (gmail_ai_agent.js), allowing flexibility based on your preferred environment.
The agent authenticates with Gmail and Google Calendar APIs using OAuth 2.0, polls for unread emails at regular intervals, and performs actions based on AI-driven classifications. It’s ideal for automating email management and scheduling tasks.
Features

Email Classification: Categorizes emails as interview, meeting, important_email, not_important, or other using an AI model.
Automated Replies: Sends polite, AI-generated responses for applicable emails.
Calendar Integration: Extracts event details (date, time, location) from email bodies and creates events in your primary Google Calendar.
Logging: Stores email metadata, AI replies, and processing status in a MongoDB collection (email_logs).
Email Management: Marks emails as read or archives them based on classification.
Cross-Platform: Available in Python and Node.js versions.

Prerequisites

A Google Cloud project with Gmail and Calendar APIs enabled.
MongoDB server running locally or remotely.
An AI API key (e.g., Gemini API key for Python, OpenAI or Gemini-compatible key for Node.js).
Node.js (for gmail_ai_agent.js) or Python 3.8+ (for gmail_ai_agent.py).

Installation
Common Setup

Google Cloud Configuration:

Create a project in the Google Cloud Console.
Enable the Gmail API and Google Calendar API under "APIs & Services" > "Library".
Go to "APIs & Services" > "Credentials", create an OAuth 2.0 Client ID (select "Desktop app"), and download the credentials.json file.
Place credentials.json in the project root directory.


Environment Variables:

Create a .env file in the project root with the following (adjust values as needed):
text# For both Python and Node.js
MONGO_URI=mongodb://localhost:27017
# For Python
GEMINI_API_KEY=your-gemini-api-key
# For Node.js
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
AI_API_KEY=your-ai-api-key



MongoDB:

Ensure a MongoDB server is running. Install MongoDB locally or use a remote instance, and update MONGO_URI accordingly.



Python Version (gmail_ai_agent.py)

Install Dependencies:
bashpip install google-auth google-auth-oauthlib google-auth-httplib2 google-api-python-client python-dotenv pymongo google-generativeai

Run the Script:
bashpython gmail_ai_agent.py

On the first run, a browser window will open for OAuth authentication. Log in and grant permissions, then save the generated token.json.



Node.js Version (gmail_ai_agent.js)

Initialize Project:
bashnpm init -y

Install Dependencies:
bashnpm install googleapis dotenv mongodb axios

Run the Script:
bashnode gmail_ai_agent.js

On the first run, follow the browser prompt to authenticate and generate token.json.



Usage

Testing: Send a test email to the authenticated Gmail account with event details (e.g., "Meeting on September 18, 2025, at 10:00 AM IST via Google Meet").
Monitoring: The script polls for unread emails every 20 seconds and prints processing details to the console.
Verification:

Check Google Calendar for created events.
Verify logs in MongoDB (email_agent_db.email_logs).