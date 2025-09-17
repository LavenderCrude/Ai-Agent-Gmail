require('dotenv').config();
const { google } = require('googleapis');
const { MongoClient } = require('mongodb');
const axios = require('axios');
const fs = require('fs');
const { OAuth2Client } = require('google-auth-library');
const mime = require('mime-types');

const SCOPES = [
  'https://www.googleapis.com/auth/gmail.modify',
  'https://www.googleapis.com/auth/gmail.send',
  'https://www.googleapis.com/auth/calendar.events'
];

const MONGO_URI = process.env.MONGO_URI;
const AI_API_KEY = process.env.AI_API_KEY;

if (!MONGO_URI) throw new Error("MONGO_URI not set in .env file");
if (!AI_API_KEY) throw new Error("AI_API_KEY not set in .env file");

const mongoClient = new MongoClient(MONGO_URI);
const db = mongoClient.db("email_agent_db");
const processedCollection = db.collection("processed_messages");
const emailLogsCollection = db.collection("email_logs");

// ------------------------
// Helpers: DB Operations
// ------------------------

async function markProcessed(msgId) {
  await processedCollection.insertOne({ _id: msgId, processed_at: Math.floor(Date.now() / 1000) });
}

async function isProcessed(msgId) {
  return await processedCollection.findOne({ _id: msgId }) !== null;
}

async function storeEmailAndReply(data, replyTemplate, actionStatus) {
  const emailLog = {
    message_id: data.id,
    from: data.from,
    to: data.to,
    date: data.date,
    subject: data.subject,
    body: data.body,
    ai_reply: replyTemplate.should_reply ? {
      subject: replyTemplate.subject || `Re: ${data.subject}`,
      body: replyTemplate.body || "Thank you for your email. I have received it and will get back to you shortly."
    } : null,
    action_status: actionStatus,
    processed_at: Math.floor(Date.now() / 1000)
  };
  await emailLogsCollection.insertOne(emailLog);
}

// ------------------------
// Gmail Authentication
// ------------------------

async function gmailAuthenticate() {
  const oauth2Client = new OAuth2Client({
    clientId: process.env.GOOGLE_CLIENT_ID,
    clientSecret: process.env.GOOGLE_CLIENT_SECRET,
    redirectUri: process.env.GOOGLE_REDIRECT_URI
  });

  const tokenPath = 'token.json';
  let credentials;

  if (fs.existsSync('credentials.json')) {
    credentials = JSON.parse(fs.readFileSync('credentials.json'));
  } else {
    throw new Error("credentials.json not found");
  }

  if (fs.existsSync(tokenPath)) {
    const token = JSON.parse(fs.readFileSync(tokenPath));
    oauth2Client.setCredentials(token);
    const gmail = google.gmail({ version: 'v1', auth: oauth2Client });
    const profile = await gmail.users.getProfile({ userId: 'me' });
    console.log(`\nAuthenticated with: ${profile.data.emailAddress}`);

    const switchAccount = await new Promise(resolve => {
      process.stdout.write("\nA token file already exists. Switch account? (yes/no): ");
      process.stdin.once('data', data => resolve(data.toString().trim().toLowerCase()));
    });
    if (switchAccount === 'yes') {
      fs.unlinkSync(tokenPath);
      console.log("Existing token deleted. A new authentication window will open.");
    } else {
      return { service: google.gmail({ version: 'v1', auth: oauth2Client }), userEmail: profile.data.emailAddress };
    }
  }

  const authUrl = oauth2Client.generateAuthUrl({
    access_type: 'offline',
    scope: SCOPES,
  });
  console.log('Authorize this app by visiting this url:', authUrl);
  const code = await new Promise(resolve => {
    process.stdout.write('Enter the code from that page here: ');
    process.stdin.once('data', data => resolve(data.toString().trim()));
  });

  const { tokens } = await oauth2Client.getToken(code);
  oauth2Client.setCredentials(tokens);
  fs.writeFileSync(tokenPath, JSON.stringify(tokens));
  const gmail = google.gmail({ version: 'v1', auth: oauth2Client });
  const profile = await gmail.users.getProfile({ userId: 'me' });
  console.log(`\nAuthenticated with Gmail as: ${profile.data.emailAddress}`);
  return { service: gmail, userEmail: profile.data.emailAddress };
}

// ------------------------
// Utilities
// ------------------------

function extractEmailAddress(fromHeader) {
  const m = fromHeader.match(/<([^>]+)>/);
  if (m) return m[1];
  const m2 = fromHeader.match(/[\w\.-]+@[\w\.-]+/);
  return m2 ? m2[0] : fromHeader;
}

async function getMessageSnippetAndBody(service, message) {
  const msgId = message.id;
  try {
    const full = await service.users.messages.get({ userId: 'me', id: msgId, format: 'full' });
    const headers = full.data.payload.headers || [];
    const subject = headers.find(h => h.name.toLowerCase() === 'subject')?.value || '';
    const fromHdr = headers.find(h => h.name.toLowerCase() === 'from')?.value || '';
    const dateHdr = headers.find(h => h.name.toLowerCase() === 'date')?.value || '';
    const toHdr = headers.find(h => h.name.toLowerCase() === 'to')?.value || '';
    const messageIdHdr = headers.find(h => h.name.toLowerCase() === 'message-id')?.value || '';

    let body = '';
    if (full.data.payload.parts) {
      for (const part of full.data.payload.parts) {
        if (part.mimeType === 'text/plain' && part.body?.data) {
          body = Buffer.from(part.body.data, 'base64').toString('utf-8');
          break;
        }
      }
      if (!body) {
        for (const part of full.data.payload.parts) {
          if (part.mimeType === 'text/html' && part.body?.data) {
            const bodyHtml = Buffer.from(part.body.data, 'base64').toString('utf-8');
            body = bodyHtml.replace(/<[^<]+?>/g, '');
            break;
          }
        }
      }
    } else if (full.data.payload.body?.data) {
      body = Buffer.from(full.data.payload.body.data, 'base64').toString('utf-8');
    }

    return {
      id: msgId,
      snippet: full.data.snippet || '',
      subject,
      from: fromHdr,
      from_email: extractEmailAddress(fromHdr),
      to: toHdr,
      date: dateHdr,
      message_id_header: messageIdHdr,
      body
    };
  } catch (e) {
    console.error(`Error decoding message ${msgId}: ${e}`);
    return {
      id: msgId,
      snippet: '',
      subject: '',
      from: '',
      from_email: '',
      to: '',
      date: '',
      message_id_header: '',
      body: ''
    };
  }
}

// ------------------------
// AI Analysis
// ------------------------

async function callAIForStructured(emailSubject, emailFrom, emailBody) {
  const EXTRA_SYSTEM = `
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
`;

  const PROMPT_TEMPLATE = `
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

Now analyze the email.
`;

  const prompt = PROMPT_TEMPLATE.replace('{subject}', emailSubject)
    .replace('{from_hdr}', emailFrom)
    .replace('{body}', emailBody);

  try {
    const response = await axios.post('https://api.openai.com/v1/chat/completions', {
      model: 'gpt-3.5-turbo',
      messages: [{ role: 'system', content: EXTRA_SYSTEM }, { role: 'user', content: prompt }],
      max_tokens: 500
    }, {
      headers: { 'Authorization': `Bearer ${AI_API_KEY}` }
    });

    let text = response.data.choices[0].message.content.trim();
    if (text.startsWith('```json')) text = text.slice(7).trim();
    if (text.endsWith('```')) text = text.slice(0, -3).trim();
    return JSON.parse(text);
  } catch (e) {
    console.error("Error parsing AI model output:", e);
    return {
      category: "other",
      confidence: 0.0,
      summary: "Could not parse model output",
      action: "no_action",
      reply_template: { should_reply: false, subject: null, body: null },
      metadata: { calendar_event: null }
    };
  }
}

// ------------------------
// Gmail and Calendar Helpers
// ------------------------

async function sendReply(service, replyTo, subject, body, threadId, inReplyTo, senderEmail) {
  const msg = [
    `From: ${senderEmail || ''}`,
    `To: ${replyTo}`,
    `Subject: ${subject}`,
    `In-Reply-To: ${inReplyTo || ''}`,
    `References: ${inReplyTo || ''}`,
    '',
    body
  ].join('\n');

  try {
    const encodedMsg = Buffer.from(msg).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
    const res = await service.users.messages.send({
      userId: 'me',
      requestBody: {
        raw: encodedMsg,
        threadId
      }
    });
    return res.data;
  } catch (e) {
    console.error(`Error sending reply: ${e}`);
    return null;
  }
}

async function modifyLabels(service, messageId, addLabels = [], removeLabels = []) {
  try {
    return await service.users.messages.modify({
      userId: 'me',
      id: messageId,
      requestBody: { addLabelIds: addLabels, removeLabelIds: removeLabels }
    });
  } catch (e) {
    console.error(`Error modifying labels for message ${messageId}: ${e}`);
    return null;
  }
}

async function createCalendarEvent(service, eventData, attendeeEmail) {
  const calendar = google.calendar({ version: 'v3', auth: service.auth });
  const event = {
    summary: eventData.summary || 'New Event',
    location: eventData.location || 'Online',
    description: eventData.description || '',
    start: { dateTime: eventData.start, timeZone: 'Asia/Kolkata' },
    end: { dateTime: eventData.end, timeZone: 'Asia/Kolkata' },
    attendees: [{ email: attendeeEmail }]
  };

  try {
    const createdEvent = await calendar.events.insert({
      calendarId: 'primary',
      requestBody: event
    });
    console.log(`Event created: ${createdEvent.data.htmlLink}`);
    return true;
  } catch (e) {
    console.error(`Failed to create calendar event: ${e}`);
    return false;
  }
}

// ------------------------
// Main Loop
// ------------------------

async function mainLoop(pollInterval = 20000) {
  try {
    const { service, userEmail } = await gmailAuthenticate();
    console.log("Authenticated with Gmail.");

    while (true) {
      try {
        const response = await service.users.messages.list({ userId: 'me', q: 'is:unread', maxResults: 20 });
        const msgs = response.data.messages || [];

        if (!msgs.length) {
          console.log(`No new unread emails. Sleeping for ${pollInterval / 1000}s...`);
          await new Promise(resolve => setTimeout(resolve, pollInterval));
          continue;
        }

        for (const msg of msgs) {
          const msgId = msg.id;
          if (await isProcessed(msgId)) continue;

          const data = await getMessageSnippetAndBody(service, msg);
          console.log("-".repeat(50));
          console.log("Processing NEW EMAIL:");
          console.log(`From: ${data.from}`);
          console.log(`To: ${data.to}`);
          console.log(`Date: ${data.date}`);
          console.log(`Subject: ${data.subject}`);
          console.log(`Body: ${data.body}`);
          console.log("-".repeat(50));

          const structured = await callAIForStructured(data.subject, data.from, data.body);
          console.log("Model classified as:", structured.category, "action:", structured.action);

          const replyTemplate = structured.reply_template || {};
          if (replyTemplate.should_reply) {
            console.log("\nAI Proposed Reply:");
            console.log(`Subject: ${replyTemplate.subject}`);
            console.log(`Body:\n${replyTemplate.body}`);
            console.log("-".repeat(50));
          } else {
            console.log("\nReply: None");
            console.log("-".repeat(50));
          }

          const eventData = structured.metadata?.calendar_event;
          if (eventData && eventData.start && eventData.end) {
            await createCalendarEvent(service, eventData, data.from_email);
          }

          let actionStatus = "";
          if (replyTemplate.should_reply) {
            const subjectReply = replyTemplate.subject || `Re: ${data.subject}`;
            const bodyReply = replyTemplate.body || "Thank you for your email. I have received it and will get back to you shortly.";
            const fullMeta = await service.users.messages.get({ userId: 'me', id: msgId, format: 'metadata' });
            const threadId = fullMeta.data.threadId;
            try {
              await sendReply(service, data.from_email, subjectReply, bodyReply, threadId, data.message_id_header, userEmail);
              console.log("Sent automated reply.");
              actionStatus = "Sent automated reply.";
            } catch (e) {
              console.error(`Failed to send reply: ${e}`);
              actionStatus = `Failed to send reply: ${e}`;
            }
          }

          if (structured.action === 'archive' || structured.category === 'not_important') {
            await modifyLabels(service, msgId, [], ['UNREAD', 'INBOX']);
            actionStatus += " Email processed and archived.";
            console.log("Email processed and archived.");
          } else {
            await modifyLabels(service, msgId, [], ['UNREAD']);
            actionStatus += " Email processed, marked as read.";
            console.log("Email processed, marked as read.");
          }

          await storeEmailAndReply(data, replyTemplate, actionStatus);
          await markProcessed(msgId);
          await new Promise(resolve => setTimeout(resolve, 2000));
        }
      } catch (e) {
        console.error("Error in main loop:", e);
        await new Promise(resolve => setTimeout(resolve, 10000));
      }
    }
  } finally {
    await mongoClient.close();
    console.log("MongoDB connection closed.");
  }
}

mainLoop().catch(console.error);