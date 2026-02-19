import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Union
from app.config import (
    SMTP_SERVER,
    SMTP_PORT,
    SMTP_USER,
    SMTP_PASSWORD,
    EMAIL_FROM,
)


def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """
    Send an email using SMTP.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        html_content: HTML content of the email
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = EMAIL_FROM
        msg["To"] = to_email

        # Attach HTML part
        html_part = MIMEText(html_content, "html")
        msg.attach(html_part)

        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, to_email, msg.as_string())

        return True
    except Exception as e:
        print(f"Error sending email to {to_email}: {str(e)}")
        return False


def get_common_styles(is_rtl: bool = False) -> str:
    direction = "rtl" if is_rtl else "ltr"
    align = "right" if is_rtl else "left"
    font = "Tahoma, Arial, sans-serif" if is_rtl else "Arial, sans-serif"
    return f'font-family: {font}; direction: {direction}; text-align: {align};'


def send_pending_review_notification(
    admins_data: List[Dict[str, str]], horse_title: str, seller_email: str
) -> bool:
    """
    Send notification to all admins when a new horse listing is pending review.
    admins_data: List of dicts with 'email' and 'language' keys.
    """
    success = True
    
    for admin in admins_data:
        email = admin.get("email")
        language = admin.get("language", "en")
        is_rtl = language == "ar"
        styles = get_common_styles(is_rtl)

        if language == "ar":
            subject = f"عرض خيل جديد بانتظار المراجعة: {horse_title}"
            html_content = f"""
            <html>
                <body style="{styles}">
                    <h2>عرض خيل جديد بانتظار المراجعة</h2>
                    <p>تم تقديم عرض خيل جديد وهو بانتظار موافقتك.</p>
                    <p><strong>عنوان الخيل:</strong> {horse_title}</p>
                    <p><strong>بريد البائع:</strong> {seller_email}</p>
                    <p>يرجى مراجعة العرض في لوحة التحكم وقبوله أو رفضه.</p>
                </body>
            </html>
            """
        else:
            subject = f"New Horse Listing Pending Review: {horse_title}"
            html_content = f"""
            <html>
                <body style="{styles}">
                    <h2>New Horse Listing Pending Review</h2>
                    <p>A new horse listing has been submitted and is waiting for your approval.</p>
                    <p><strong>Horse Title:</strong> {horse_title}</p>
                    <p><strong>Seller Email:</strong> {seller_email}</p>
                    <p>Please review the listing in your admin dashboard and approve or reject it.</p>
                </body>
            </html>
            """
            
        if not send_email(email, subject, html_content):
            success = False

    return success


def send_listing_approved_email(
    seller_email: str, horse_title: str, language: str = "en"
) -> bool:
    """
    Send notification to seller when their horse listing has been approved.
    """
    is_rtl = language == "ar"
    styles = get_common_styles(is_rtl)

    if language == "ar":
        subject = f"تم قبول عرض الخيل الخاص بك: {horse_title}"
        html_content = f"""
        <html>
            <body style="{styles}">
                <h2>تم قبول العرض!</h2>
                <p>تهانينا! تم قبول عرض الخيل الخاص بك وأصبح الآن معروضاً للجميع.</p>
                <p><strong>عنوان الخيل:</strong> {horse_title}</p>
                <p>شكراً لاستخدامك منصتنا!</p>
            </body>
        </html>
        """
    else:
        subject = f"Your Horse Listing Has Been Approved: {horse_title}"
        html_content = f"""
        <html>
            <body style="{styles}">
                <h2>Listing Approved!</h2>
                <p>Congratulations! Your horse listing has been approved and is now live.</p>
                <p><strong>Horse Title:</strong> {horse_title}</p>
                <p>Thank you for using our platform!</p>
            </body>
        </html>
        """

    return send_email(seller_email, subject, html_content)


def send_listing_rejected_email(
    seller_email: str, horse_title: str, reason: str, language: str = "en"
) -> bool:
    """
    Send notification to seller when their horse listing has been rejected.
    """
    is_rtl = language == "ar"
    styles = get_common_styles(is_rtl)

    if language == "ar":
        subject = f"لم تتم الموافقة على عرض الخيل الخاص بك: {horse_title}"
        html_content = f"""
        <html>
            <body style="{styles}">
                <h2>نتيجة مراجعة العرض</h2>
                <p>تمت مراجعة عرض الخيل الخاص بك ولم يتم قبوله في الوقت الحالي.</p>
                <p><strong>عنوان الخيل:</strong> {horse_title}</p>
                <p><strong>السبب:</strong></p>
                <p>{reason}</p>
                <p>يمكنك تعديل العرض وإعادة تقديمه للمراجعة.</p>
            </body>
        </html>
        """
    else:
        subject = f"Your Horse Listing Was Not Approved: {horse_title}"
        html_content = f"""
        <html>
            <body style="{styles}">
                <h2>Listing Review Result</h2>
                <p>Your horse listing has been reviewed and was not approved at this time.</p>
                <p><strong>Horse Title:</strong> {horse_title}</p>
                <p><strong>Reason:</strong></p>
                <p>{reason}</p>
                <p>You may revise your listing and resubmit it for review.</p>
            </body>
        </html>
        """

    return send_email(seller_email, subject, html_content)


def send_verification_email(
    user_email: str, verification_token: str, verification_link: str, language: str = "en"
) -> bool:
    """
    Send email verification link to new user.
    """
    is_rtl = language == "ar"
    styles = get_common_styles(is_rtl)
    
    if language == "ar":
        subject = "تحقق من عنوان بريدك الإلكتروني"
        html_content = f"""
        <html>
            <body style="{styles} line-height: 1.6; color: #333;">
                <h2>مرحبًا بك في سوق الخيل!</h2>
                <p>شكرًا لانضمامك إلينا. يرجى التحقق من عنوان بريدك الإلكتروني لإكمال التسجيل.</p>
                <p style="margin: 24px 0;">
                    <a href="{verification_link}" style="display: inline-block; padding: 12px 24px; background-color: #007AFF; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
                        تحقق من البريد الإلكتروني
                    </a>
                </p>
                <p style="color: #666; font-size: 14px;">
                    أو انسخ هذا الرابط والصقه في متصفحك:<br>
                    <code style="background-color: #f5f5f5; padding: 4px 8px; border-radius: 4px;">{verification_link}</code>
                </p>
                <p style="color: #999; font-size: 12px; margin-top: 24px;">
                    تنتهي صلاحية هذا الرابط خلال 24 ساعة.
                </p>
                <p style="color: #999; font-size: 12px;">
                    إذا لم تقم بإنشاء هذا الحساب، يرجى تجاهل هذا البريد.
                </p>
            </body>
        </html>
        """
    else:
        subject = "Verify Your Email Address"
        html_content = f"""
        <html>
            <body style="{styles} line-height: 1.6; color: #333;">
                <h2>Welcome to Horse Marketplace!</h2>
                <p>Thank you for joining our platform. Please verify your email address to complete your registration.</p>
                <p style="margin: 24px 0;">
                    <a href="{verification_link}" style="display: inline-block; padding: 12px 24px; background-color: #007AFF; color: white; text-decoration: none; border-radius: 6px; font-weight: bold;">
                        Verify Email Address
                    </a>
                </p>
                <p style="color: #666; font-size: 14px;">
                    Or copy and paste this link in your browser:<br>
                    <code style="background-color: #f5f5f5; padding: 4px 8px; border-radius: 4px;">{verification_link}</code>
                </p>
                <p style="color: #999; font-size: 12px; margin-top: 24px;">
                    This link will expire in 24 hours.
                </p>
                <p style="color: #999; font-size: 12px;">
                    If you didn't create this account, please ignore this email.
                </p>
            </body>
        </html>
        """

    return send_email(user_email, subject, html_content)


def send_otp_email(user_email: str, otp_code: str, language: str = "en") -> bool:
    """
    Send OTP email for verification.
    """
    is_rtl = language == "ar"
    styles = get_common_styles(is_rtl)

    if language == "ar":
        subject = "رمز التحقق الخاص بك"
        html_content = f"""
        <html>
            <body style="{styles} line-height: 1.6; color: #333;">
                <h2>تحقق من عنوان بريدك الإلكتروني</h2>
                <p>يرجى استخدام رمز المرور لمرة واحدة (OTP) التالي للتحقق من بريدك الإلكتروني. هذا الرمز صالح لمدة 10 دقائق.</p>
                <div style="margin: 24px 0; text-align: center;">
                    <span style="display: inline-block; padding: 12px 24px; background-color: #f0f0f0; border: 1px solid #ddd; border-radius: 6px; font-size: 24px; font-weight: bold; letter-spacing: 4px;">
                        {otp_code}
                    </span>
                </div>
                <p style="color: #666; font-size: 14px;">
                    إذا لم تطلب هذا التحقق، يرجى تجاهل هذا البريد.
                </p>
            </body>
        </html>
        """
    else:
        subject = "Your Verification Code"
        html_content = f"""
        <html>
            <body style="{styles} line-height: 1.6; color: #333;">
                <h2>Verify Your Email Address</h2>
                <p>Please use the following One-Time Password (OTP) to verify your email address. This code is valid for 10 minutes.</p>
                <div style="margin: 24px 0; text-align: center;">
                    <span style="display: inline-block; padding: 12px 24px; background-color: #f0f0f0; border: 1px solid #ddd; border-radius: 6px; font-size: 24px; font-weight: bold; letter-spacing: 4px;">
                        {otp_code}
                    </span>
                </div>
                <p style="color: #666; font-size: 14px;">
                    If you did not request this verification, please ignore this email.
                </p>
            </body>
        </html>
        """

    return send_email(user_email, subject, html_content)
