import os
import logging
from datetime import datetime
from pathlib import Path
from email.message import EmailMessage

import aiosmtplib
from dotenv import load_dotenv

# Load .env from the same folder as this file
load_dotenv(dotenv_path=Path(__file__).parent / ".env", override=True)

log = logging.getLogger("email-service")


class EmailService:
    def __init__(self):
        self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "your-email@ttu.ac.ke")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "your-password")
        self.from_address = f'"TTU Microsoft 365" <{self.smtp_user}>'
        self.app_url = os.getenv("APP_URL", "http://localhost:3000")
        self.template_path = Path(__file__).parent / "email-template.html"

    # ------------------------------------------------------------------
    # Internal: actually send the email
    # ------------------------------------------------------------------
    async def _send(self, to: str, subject: str, html: str, text: str | None = None):
        msg = EmailMessage()
        msg["From"] = self.from_address
        msg["To"] = to
        msg["Subject"] = subject
        if text:
            msg.set_content(text)
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")

        try:
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_user,
                password=self.smtp_password,
                start_tls=True,   # matches nodemailer's `secure: false` on port 587
                timeout=20,
            )
            # aiosmtplib returns the SMTP response; generate a pseudo message-id
            message_id = f"<{datetime.utcnow().timestamp()}@ttu.ac.ke>"
            log.info(f"Email sent successfully: {message_id}")
            return {"success": True, "messageId": message_id}
        except Exception as e:
            log.error(f"Error sending email: {e}")
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Load and populate the HTML template
    # ------------------------------------------------------------------
    async def load_email_template(self, user_data: dict) -> str:
        try:
            html_content = self.template_path.read_text(encoding="utf-8")
            replacements = {
                "{{firstName}}": user_data.get("firstName", ""),
                "{{lastName}}": user_data.get("lastName", ""),
                "{{email}}": user_data.get("email", ""),
                "{{password}}": user_data.get("password", ""),
                "{{displayName}}": user_data.get("displayName", ""),
                "{{licenseType}}": user_data.get("licenseType", ""),
                "{{licenseClass}}": (
                    "license-student"
                    if user_data.get("licenseType") == "Student"
                    else "license-faculty"
                ),
                "{{year}}": str(datetime.utcnow().year),
            }
            for key, value in replacements.items():
                html_content = html_content.replace(key, value)
            return html_content
        except Exception as e:
            log.error(f"Error loading email template: {e}")
            return self.get_plain_text_template(user_data)

    # ------------------------------------------------------------------
    # Fallback plain text template
    # ------------------------------------------------------------------
    def get_plain_text_template(self, user_data: dict) -> str:
        return f"""
            Welcome to TTU Microsoft 365!

            Hello {user_data.get('firstName')} {user_data.get('lastName')},

            Congratulations! Your Taita Taveta University Microsoft 365 account has been successfully created.

            ACCOUNT DETAILS:
            -----------------
            Email: {user_data.get('email')}
            Account Type: {user_data.get('licenseType')}
            Temporary Password: {user_data.get('password')}
            Display Name: {user_data.get('displayName')}

            IMPORTANT INFORMATION:
            ----------------------
            • First Login: You will be prompted to change your password on first login
            • Set up Multi-Factor Authentication for enhanced security
            • You have 1TB of OneDrive storage
            • Your account remains active while enrolled/employed at TTU
            • Never share your password with anyone

            HOW TO GET STARTED:
            -------------------
            1. Visit portal.office.com
            2. Enter your email: {user_data.get('email')}
            3. Use the temporary password provided above
            4. Follow the prompts to set a new, secure password
            5. Set up multi-factor authentication when prompted
            6. Download Microsoft 365 apps on your devices

            Need Help?
            ----------
            Contact IT Support: support@ttu.ac.ke | +254 123 456 789

            This is an automated message. Please do not reply to this email.

            © {datetime.utcnow().year} Taita Taveta University. All rights reserved.
        """

    # ------------------------------------------------------------------
    # Public: Send account creation email
    # ------------------------------------------------------------------
    async def send_account_creation_email(self, user_data: dict) -> dict:
        html_content = await self.load_email_template(user_data)
        plain_text_content = self.get_plain_text_template(user_data)
        return await self._send(
            to=user_data["email"],
            subject="Welcome to TTU Microsoft 365 - Your Account Details",
            html=html_content,
            text=plain_text_content,
        )

    # ------------------------------------------------------------------
    # Public: Send password reset email
    # ------------------------------------------------------------------
    async def send_password_reset_email(self, email: str, reset_token: str) -> dict:
        reset_link = f"{self.app_url}/reset-password?token={reset_token}"
        html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Password Reset Request</title>
            </head>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #1e6b52 0%, #2d8c6f 100%); padding: 30px; text-align: center;">
                    <h1 style="color: white;">Password Reset Request</h1>
                </div>
                <div style="padding: 30px;">
                    <p>Hello,</p>
                    <p>We received a request to reset your TTU Microsoft 365 account password.</p>
                    <p>Click the button below to reset your password:</p>
                    <div style="text-align: center; margin: 30px 0;">
                        <a href="{reset_link}" style="background: #1e6b52; color: white; padding: 12px 24px; text-decoration: none; border-radius: 5px; display: inline-block;">Reset Password</a>
                    </div>
                    <p>If you didn't request this, please ignore this email. Your password will remain unchanged.</p>
                    <p>This link will expire in 1 hour.</p>
                    <hr style="margin: 20px 0;">
                    <p style="color: #666; font-size: 12px;">Taita Taveta University IT Support</p>
                </div>
            </body>
            </html>
        """
        return await self._send(
            to=email,
            subject="Password Reset Request - TTU Microsoft 365",
            html=html_content,
        )

    # ------------------------------------------------------------------
    # Public: Send welcome resources email
    # ------------------------------------------------------------------
    async def send_welcome_resources_email(self, email: str, first_name: str) -> dict:
        html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <title>Welcome to TTU Microsoft 365 - Resources</title>
            </head>
            <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                <div style="background: linear-gradient(135deg, #1e6b52 0%, #2d8c6f 100%); padding: 30px; text-align: center;">
                    <h1 style="color: white;">Welcome to TTU Microsoft 365!</h1>
                </div>
                <div style="padding: 30px;">
                    <p>Hello {first_name},</p>
                    <p>Your account is now active! Here are some resources to help you get started:</p>

                    <h3>📚 Training Resources</h3>
                    <ul>
                        <li><a href="https://support.microsoft.com/en-us/office">Microsoft 365 Training Center</a></li>
                        <li><a href="https://www.microsoft.com/en-us/education/products/office">Student Resources</a></li>
                        <li><a href="https://support.microsoft.com/en-us/teams">Microsoft Teams Tutorials</a></li>
                    </ul>

                    <h3>📱 Download Apps</h3>
                    <ul>
                        <li><a href="https://www.microsoft.com/en-us/microsoft-365/microsoft-365-apps">Download Office Apps</a></li>
                        <li><a href="https://www.microsoft.com/en-us/microsoft-teams/download-app">Download Microsoft Teams</a></li>
                        <li><a href="https://www.microsoft.com/en-us/microsoft-365/onedrive/download">Download OneDrive</a></li>
                    </ul>

                    <h3>💡 Quick Tips</h3>
                    <ul>
                        <li>Enable Multi-Factor Authentication for better security</li>
                        <li>Use OneDrive to backup your important files</li>
                        <li>Join Teams channels for your courses</li>
                        <li>Check email daily for important university communications</li>
                    </ul>

                    <hr style="margin: 20px 0;">
                    <p style="color: #666; font-size: 12px;">
                        Need help? Contact IT Support: <a href="mailto:support@ttu.ac.ke">support@ttu.ac.ke</a>
                    </p>
                </div>
            </body>
            </html>
        """
        return await self._send(
            to=email,
            subject="Welcome to TTU Microsoft 365 - Get Started Resources",
            html=html_content,
        )


# Module-level singleton — matches `module.exports = new EmailService();`
email_service = EmailService()