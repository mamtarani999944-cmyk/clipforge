import os
import requests
from datetime import datetime

RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
GMAIL_SENDER_EMAIL = os.environ.get('GMAIL_SENDER_EMAIL')

def send_login_notification(to_email, user_name, request=None):
    print(f"[login_notification_email] CALLED for {to_email}", flush=True)
    if not RESEND_API_KEY:
        print("[login_notification_email] SKIPPED - missing RESEND_API_KEY env var", flush=True)
        return
    try:
        ip_address = 'Unknown'
        user_agent = 'Unknown'
        if request:
            ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
            user_agent = request.headers.get('User-Agent', 'Unknown')
        time_str = datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')
        subject = "New sign-in to your ClipForge account"
        html_body = f"""
        <div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px;color:#111">
          <h2 style="margin-bottom:4px">New sign-in detected</h2>
          <p style="color:#555">Hi {user_name or 'there'},</p>
          <p style="color:#555">Your ClipForge account was just signed into.</p>
          <table style="width:100%;margin:16px 0;font-size:14px;color:#333">
            <tr><td style="padding:4px 0"><b>Time:</b></td><td>{time_str}</td></tr>
            <tr><td style="padding:4px 0"><b>IP Address:</b></td><td>{ip_address}</td></tr>
            <tr><td style="padding:4px 0"><b>Device:</b></td><td>{user_agent[:80]}</td></tr>
          </table>
          <p style="color:#555">If this was you, no action is needed.</p>
          <p style="color:#c0392b"><b>If this wasn't you</b>, please secure your Google account immediately, since ClipForge login is tied to it.</p>
          <p style="color:#999;font-size:12px;margin-top:24px">- ClipForge Security</p>
        </div>
        """
        print("[login_notification_email] Sending via Resend API...", flush=True)
        resp = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {RESEND_API_KEY}',
                'Content-Type': 'application/json',
            },
            json={
                'from': 'ClipForge Security <onboarding@resend.dev>',
                'to': [to_email],
                'subject': subject,
                'html': html_body,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201, 202):
            print(f"[login_notification_email] SUCCESS - {resp.status_code} {resp.text}", flush=True)
        else:
            print(f"[login_notification_email] FAILED - {resp.status_code} {resp.text}", flush=True)
    except Exception as e:
        print(f"[login_notification_email] FAILED to send: {e}", flush=True)
