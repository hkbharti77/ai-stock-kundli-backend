import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import os
from datetime import datetime
from app.core.config import get_settings

logger = logging.getLogger("app.core.email")

def get_master_email_template(subject: str, html_content: str) -> str:
    """
    Wraps content in the global AI Stock Kundli HTML master template.
    Uses the 4 core frontend theme colors:
    - Deep Navy: #080E1A
    - Slate Text: #E2E8F0
    - Vibrant Blue: #3B82F6
    - Amber Gold: #F59E0B
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{subject}</title>
        <style>
            body {{
                margin: 0;
                padding: 0;
                background-color: #080E1A;
                font-family: 'Inter', Helvetica, Arial, sans-serif;
                color: #E2E8F0;
                -webkit-font-smoothing: antialiased;
            }}
            .container {{
                max-width: 600px;
                margin: 0 auto;
                padding: 20px;
            }}
            .header {{
                text-align: center;
                padding: 30px 0;
            }}
            .header h1 {{
                margin: 0;
                color: #E2E8F0;
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 1px;
            }}
            .header span.accent {{
                color: #3B82F6;
            }}
            .card {{
                background-color: #0f172a;
                border: 1px solid rgba(59, 130, 246, 0.2);
                border-radius: 12px;
                padding: 30px;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            }}
            .content {{
                font-size: 16px;
                line-height: 1.6;
                color: #E2E8F0;
            }}
            .footer {{
                text-align: center;
                padding: 30px 0;
                font-size: 13px;
                color: #94A3B8;
            }}
            .btn {{
                display: inline-block;
                background-color: #3B82F6;
                color: #ffffff !important;
                text-decoration: none;
                padding: 12px 24px;
                border-radius: 6px;
                font-weight: 600;
                margin: 20px 0;
            }}
            table.receipt {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
            }}
            table.receipt th, table.receipt td {{
                padding: 12px;
                text-align: left;
                border-bottom: 1px solid rgba(226, 232, 240, 0.1);
            }}
            table.receipt th {{
                color: #94A3B8;
                font-weight: 500;
                font-size: 14px;
                text-transform: uppercase;
            }}
            table.receipt tr.total td {{
                font-weight: 700;
                color: #F59E0B;
                border-bottom: none;
                border-top: 2px solid rgba(226, 232, 240, 0.2);
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>AI Stock <span class="accent">Kundli</span></h1>
            </div>
            
            <div class="card">
                <div class="content">
                    {html_content}
                </div>
            </div>
            
            <div class="footer">
                <p>&copy; {datetime.now().year} AI Stock Kundli. All rights reserved.</p>
                <p>This is an automated message. Please do not reply directly to this email.</p>
            </div>
        </div>
    </body>
    </html>
    """

async def send_subscription_receipt_email(user_email: str, plan_name: str, amount: int, order_id: str):
    """
    Sends a subscription receipt via email using SMTP.
    Falls back to mock printing if SMTP credentials are not configured.
    """
    settings = get_settings()
    
    SMTP_HOST = settings.SMTP_HOST
    SMTP_PORT = settings.SMTP_PORT
    SMTP_USER = settings.SMTP_USERNAME
    SMTP_PASS = settings.SMTP_PASSWORD
    if SMTP_PASS:
        SMTP_PASS = SMTP_PASS.strip('"').strip("'")
    FROM_EMAIL = settings.ADMIN_EMAIL or "support@aistockkundli.com"
    
    subject = f"Your AI Stock Kundli {plan_name.capitalize()} Subscription Receipt"
    receipt_link = f"https://kundli.app/receipts/{order_id}"
    
    total_inr = amount / 100.0
    # Assuming the total amount includes 18% GST
    base_price = total_inr / 1.18
    gst_amount = total_inr - base_price
    
    text_content = f"""
Hello,

Thank you for purchasing the {plan_name.capitalize()} plan!

Payment Details:
- Base Amount: ₹{base_price:.2f}
- GST (18%): ₹{gst_amount:.2f}
-----------------------------------
- Total Amount Paid: ₹{total_inr:.2f}

- Date: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
- Order ID: {order_id}

View your full receipt here: {receipt_link}

We hope you enjoy your new features.

Best,
The AI Stock Kundli Team
"""

    receipt_html = f"""
    <h2 style="margin-top: 0; color: #E2E8F0; font-size: 20px;">Receipt for {plan_name.capitalize()} Plan</h2>
    <p>Hello,</p>
    <p>Thank you for your recent purchase! Your subscription is now active.</p>
    
    <table class="receipt">
        <tr>
            <th>Description</th>
            <th style="text-align: right;">Amount</th>
        </tr>
        <tr>
            <td>{plan_name.capitalize()} Plan (Base)</td>
            <td style="text-align: right;">₹{base_price:.2f}</td>
        </tr>
        <tr>
            <td>GST (18%)</td>
            <td style="text-align: right;">₹{gst_amount:.2f}</td>
        </tr>
        <tr class="total">
            <td>Total Paid</td>
            <td style="text-align: right;">₹{total_inr:.2f}</td>
        </tr>
    </table>
    
    <p style="margin-top: 20px; font-size: 14px; color: #94A3B8;">
        <strong>Date:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}<br>
        <strong>Order ID:</strong> {order_id}
    </p>
    
    <div style="text-align: center; margin-top: 30px;">
        <a href="{receipt_link}" class="btn">View Online Receipt</a>
    </div>
    """
    
    full_html = get_master_email_template(subject, receipt_html)

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASS:
        # Fallback to Mock console print if no SMTP is configured
        print("========================================================")
        print(f"[MOCK EMAIL - SMTP NOT CONFIGURED] TO: {user_email}")
        print(f"SUBJECT: {subject}")
        print(text_content)
        print("========================================================")
        logger.info(f"Mock receipt email printed to console for {user_email}")
        return True

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = user_email
        msg["Subject"] = subject
        
        # Attach parts into message container.
        # According to RFC 2046, the last part of a multipart message, in this case
        # the HTML message, is best and preferred.
        part1 = MIMEText(text_content, "plain")
        part2 = MIMEText(full_html, "html")
        msg.attach(part1)
        msg.attach(part2)

        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)
        server.quit()
        logger.info(f"Real receipt email successfully sent to {user_email} via SMTP")
        return True
    except Exception as e:
        logger.error(f"Failed to send actual email via SMTP: {str(e)}")
        # Print fallback if sending fails
        print(text_content)
        return False
