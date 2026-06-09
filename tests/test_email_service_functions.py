"""
Tests for email sending functions in email_service.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from email.mime.multipart import MIMEMultipart

from app.email_service import (
    send_email,
    get_common_styles,
    send_pending_review_notification,
    send_listing_approved_email,
    send_listing_rejected_email,
    send_verification_email,
    send_otp_email,
    send_saved_search_match_email,
    send_offer_update_email,
    send_expo_push_notifications_result,
    send_expo_push_notifications,
)


class TestSendEmail:
    """Test the basic send_email function."""

    @patch('app.email_service.smtplib.SMTP')
    def test_send_email_success(self, mock_smtp_class):
        """send_email returns True on successful send."""
        # Setup mock SMTP server
        mock_server = MagicMock()
        mock_smtp_class.return_value.__enter__.return_value = mock_server
        
        result = send_email(
            to_email="user@example.com",
            subject="Test Subject",
            html_content="<p>Test content</p>"
        )
        
        assert result is True
        # Verify SMTP methods were called
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once()
        mock_server.sendmail.assert_called_once()

    @patch('app.email_service.smtplib.SMTP')
    def test_send_email_login_failure(self, mock_smtp_class):
        """send_email returns False when SMTP login fails."""
        mock_server = MagicMock()
        mock_server.login.side_effect = Exception("Authentication failed")
        mock_smtp_class.return_value.__enter__.return_value = mock_server
        
        result = send_email(
            to_email="user@example.com",
            subject="Test",
            html_content="<p>Test</p>"
        )
        
        assert result is False

    @patch('app.email_service.smtplib.SMTP')
    def test_send_email_connection_failure(self, mock_smtp_class):
        """send_email returns False when SMTP connection fails."""
        mock_smtp_class.side_effect = Exception("Connection refused")
        
        result = send_email(
            to_email="user@example.com",
            subject="Test",
            html_content="<p>Test</p>"
        )
        
        assert result is False

    @patch('app.email_service.smtplib.SMTP')
    def test_send_email_sendmail_failure(self, mock_smtp_class):
        """send_email returns False when sendmail fails."""
        mock_server = MagicMock()
        mock_server.sendmail.side_effect = Exception("Send failed")
        mock_smtp_class.return_value.__enter__.return_value = mock_server
        
        result = send_email(
            to_email="user@example.com",
            subject="Test",
            html_content="<p>Test</p>"
        )
        
        assert result is False


class TestCommonStyles:
    """Test the get_common_styles formatting function."""

    def test_get_common_styles_ltr_english(self):
        """get_common_styles for English (LTR) uses Arial."""
        styles = get_common_styles(is_rtl=False)
        
        assert "direction: ltr" in styles
        assert "text-align: left" in styles
        assert "Arial, sans-serif" in styles

    def test_get_common_styles_rtl_arabic(self):
        """get_common_styles for Arabic (RTL) uses Tahoma and RTL direction."""
        styles = get_common_styles(is_rtl=True)
        
        assert "direction: rtl" in styles
        assert "text-align: right" in styles
        assert "Tahoma" in styles


class TestSendPendingReviewNotification:
    """Test admin notification for pending horse listings."""

    @patch('app.email_service.send_email')
    def test_send_pending_review_notification_english_admin(self, mock_send):
        """Sends English notification to English admin."""
        mock_send.return_value = True
        admins_data = [
            {"email": "admin@example.com", "language": "en"}
        ]
        
        result = send_pending_review_notification(
            admins_data=admins_data,
            horse_title="Beautiful Stallion",
            seller_email="seller@example.com"
        )
        
        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "admin@example.com"
        assert "Beautiful Stallion" in call_args[0][2]
        assert "seller@example.com" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_pending_review_notification_arabic_admin(self, mock_send):
        """Sends Arabic notification to Arabic admin."""
        mock_send.return_value = True
        admins_data = [
            {"email": "admin@example.ar", "language": "ar"}
        ]
        
        result = send_pending_review_notification(
            admins_data=admins_data,
            horse_title="حصان جميل",
            seller_email="seller@example.com"
        )
        
        assert result is True
        call_args = mock_send.call_args
        # Arabic subject should be present
        assert "عرض خيل جديد" in call_args[0][1]

    @patch('app.email_service.send_email')
    def test_send_pending_review_notification_multiple_admins(self, mock_send):
        """Sends notification to all admins even if one fails."""
        # First call returns False (fails), others return True
        mock_send.side_effect = [False, True, True]
        admins_data = [
            {"email": "admin1@example.com", "language": "en"},
            {"email": "admin2@example.com", "language": "en"},
            {"email": "admin3@example.com", "language": "en"},
        ]
        
        result = send_pending_review_notification(
            admins_data=admins_data,
            horse_title="Test Horse",
            seller_email="seller@example.com"
        )
        
        # Result should be False (indicating at least one failure)
        assert result is False
        assert mock_send.call_count == 3

    @patch('app.email_service.send_email')
    def test_send_pending_review_notification_all_succeed(self, mock_send):
        """Returns True when all admin notifications send successfully."""
        mock_send.return_value = True
        admins_data = [
            {"email": "admin1@example.com", "language": "en"},
            {"email": "admin2@example.com", "language": "ar"},
        ]
        
        result = send_pending_review_notification(
            admins_data=admins_data,
            horse_title="Test Horse",
            seller_email="seller@example.com"
        )
        
        assert result is True
        assert mock_send.call_count == 2


class TestSendListingApprovedEmail:
    """Test seller notification when listing is approved."""

    @patch('app.email_service.send_email')
    def test_send_listing_approved_email_english(self, mock_send):
        """Sends English approval email to seller."""
        mock_send.return_value = True
        
        result = send_listing_approved_email(
            seller_email="seller@example.com",
            horse_title="Approved Horse",
            language="en"
        )
        
        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "seller@example.com"
        assert "Approved" in call_args[0][1] or "approved" in call_args[0][2]
        assert "Approved Horse" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_listing_approved_email_arabic(self, mock_send):
        """Sends Arabic approval email to seller."""
        mock_send.return_value = True
        
        result = send_listing_approved_email(
            seller_email="seller@example.ar",
            horse_title="حصان موافق",
            language="ar"
        )
        
        assert result is True
        call_args = mock_send.call_args
        # Arabic content should be present
        assert "تم قبول" in call_args[0][1] or "تم قبول" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_listing_approved_email_default_language(self, mock_send):
        """send_listing_approved_email defaults to English."""
        mock_send.return_value = True
        
        result = send_listing_approved_email(
            seller_email="seller@example.com",
            horse_title="Test Horse"
            # No language specified
        )
        
        assert result is True
        call_args = mock_send.call_args
        # Should contain English content
        assert "Approved" in call_args[0][1] or "approved" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_listing_approved_email_send_fails(self, mock_send):
        """Returns False when email send fails."""
        mock_send.return_value = False
        
        result = send_listing_approved_email(
            seller_email="seller@example.com",
            horse_title="Test Horse",
            language="en"
        )
        
        assert result is False


class TestSendListingRejectedEmail:
    """Test seller notification when listing is rejected."""

    @patch('app.email_service.send_email')
    def test_send_listing_rejected_email_english(self, mock_send):
        """Sends English rejection email with reason."""
        mock_send.return_value = True
        
        result = send_listing_rejected_email(
            seller_email="seller@example.com",
            horse_title="Rejected Horse",
            reason="Does not meet quality standards",
            language="en"
        )
        
        assert result is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][0] == "seller@example.com"
        assert "Rejected Horse" in call_args[0][2]
        assert "Does not meet quality standards" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_listing_rejected_email_arabic(self, mock_send):
        """Sends Arabic rejection email."""
        mock_send.return_value = True
        
        result = send_listing_rejected_email(
            seller_email="seller@example.ar",
            horse_title="حصان مرفوض",
            reason="لا يتم تقديم الوثائق المطلوبة",
            language="ar"
        )
        
        assert result is True
        call_args = mock_send.call_args
        # Check that Arabic content is present (check for title inclusion)
        assert "حصان مرفوض" in call_args[0][2]

    @patch('app.email_service.send_email')
    def test_send_listing_rejected_email_default_language(self, mock_send):
        """send_listing_rejected_email defaults to English."""
        mock_send.return_value = True
        
        result = send_listing_rejected_email(
            seller_email="seller@example.com",
            horse_title="Test Horse",
            reason="Invalid listing"
            # No language specified
        )
        
        assert result is True
        call_args = mock_send.call_args
        # Should contain indication of rejection (either "rejected", "reject", "not approved", etc)
        content_lower = (call_args[0][1] + call_args[0][2]).lower()
        assert any(word in content_lower for word in ["rejected", "reject", "not approved", "not accept"])


class TestAdditionalEmailTemplates:
    """Test additional email template helpers for both language paths."""

    @patch("app.email_service.send_email")
    def test_send_verification_email_english(self, mock_send):
        mock_send.return_value = True

        result = send_verification_email(
            user_email="buyer@example.com",
            verification_token="abc123",
            verification_link="https://example.com/verify?token=abc123",
            language="en",
        )

        assert result is True
        to_email, subject, html = mock_send.call_args[0]
        assert to_email == "buyer@example.com"
        assert "Verify" in subject
        assert "abc123" in html

    @patch("app.email_service.send_email")
    def test_send_verification_email_arabic(self, mock_send):
        mock_send.return_value = True

        result = send_verification_email(
            user_email="buyer@example.com",
            verification_token="xyz789",
            verification_link="https://example.com/verify?token=xyz789",
            language="ar",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "تحقق" in subject
        assert "xyz789" in html

    @patch("app.email_service.send_email")
    def test_send_otp_email_english(self, mock_send):
        mock_send.return_value = True

        result = send_otp_email(
            user_email="user@example.com",
            otp_code="123456",
            language="en",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "Verification Code" in subject
        assert "123456" in html

    @patch("app.email_service.send_email")
    def test_send_saved_search_match_email_arabic(self, mock_send):
        mock_send.return_value = True

        result = send_saved_search_match_email(
            user_email="buyer@example.com",
            horse_title="حصان سباق",
            horse_breed="عربي",
            horse_price=12000,
            search_name="خيول عربية",
            language="ar",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "يطابق تنبيهك" in subject
        assert "12,000" in html

    @patch("app.email_service.send_email")
    def test_send_otp_email_arabic(self, mock_send):
        mock_send.return_value = True

        result = send_otp_email(
            user_email="user@example.com",
            otp_code="654321",
            language="ar",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "رمز" in subject
        assert "654321" in html

    @patch("app.email_service.send_email")
    def test_send_saved_search_match_email_english(self, mock_send):
        mock_send.return_value = True

        result = send_saved_search_match_email(
            user_email="buyer@example.com",
            horse_title="Arabian Star",
            horse_breed="Arabian",
            horse_price=15000,
            search_name="My Arabian Alert",
            language="en",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "My Arabian Alert" in subject
        assert "Arabian Star" in html

    @patch("app.email_service.send_email")
    def test_send_offer_update_email_arabic(self, mock_send):
        mock_send.return_value = True

        result = send_offer_update_email(
            user_email="seller@example.com",
            horse_title="قمر الخيل",
            update_title="تم قبول عرضك",
            update_message="قبل البائع عرضك.",
            language="ar",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "قمر الخيل" in subject
        assert "تم قبول عرضك" in html

    @patch("app.email_service.send_email")
    def test_send_offer_update_email_default_language_english(self, mock_send):
        mock_send.return_value = True

        result = send_offer_update_email(
            user_email="buyer@example.com",
            horse_title="Star Mare",
            update_title="Offer Accepted",
            update_message="Seller accepted your offer.",
        )

        assert result is True
        _, subject, html = mock_send.call_args[0]
        assert "Offer update" in subject
        assert "Offer Accepted" in html


class TestExpoPushNotifications:
    """Test structured push sending outcomes and wrapper conversion."""

    def _mock_urlopen_with_payload(self, mock_urlopen, payload_obj):
        response = MagicMock()
        response.read.return_value = __import__("json").dumps(payload_obj).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = response

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_no_tokens(self, _mock_urlopen):
        result = send_expo_push_notifications_result(
            tokens=[],
            title="Test",
            body="Body",
        )

        assert result["status"] == "no_tokens"
        assert result["accepted_count"] == 0

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_success(self, mock_urlopen):
        self._mock_urlopen_with_payload(
            mock_urlopen,
            {"data": [{"status": "ok"}, {"status": "ok"}]},
        )

        result = send_expo_push_notifications_result(
            tokens=["ExponentPushToken[a]", "ExponentPushToken[b]"],
            title="Offer update",
            body="Your offer changed",
        )

        assert result["status"] == "success"
        assert result["accepted_count"] == 2
        assert result["failed_count"] == 0

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_partial(self, mock_urlopen):
        self._mock_urlopen_with_payload(
            mock_urlopen,
            {"data": [{"status": "ok"}, {"status": "error"}]},
        )

        result = send_expo_push_notifications_result(
            tokens=["ExponentPushToken[a]", "ExponentPushToken[b]"],
            title="Offer update",
            body="Your offer changed",
        )

        assert result["status"] == "partial"
        assert result["accepted_count"] == 1
        assert result["failed_count"] == 1

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_non_dict_response(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = b'"just a string"'
        mock_urlopen.return_value.__enter__.return_value = response

        result = send_expo_push_notifications_result(
            tokens=["ExponentPushToken[a]"],
            title="Offer update",
            body="Your offer changed",
        )

        assert result["status"] == "failed"
        assert result["accepted_count"] == 0

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_invalid_ticket_payload(self, mock_urlopen):
        self._mock_urlopen_with_payload(mock_urlopen, {"data": {"status": "ok"}})

        result = send_expo_push_notifications_result(
            tokens=["ExponentPushToken[a]"],
            title="Offer update",
            body="Your offer changed",
        )

        assert result["status"] == "failed"
        assert "Invalid ticket payload" in result["error_message"]

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_result_retries_then_fails(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("network down")

        result = send_expo_push_notifications_result(
            tokens=["ExponentPushToken[a]"],
            title="Offer update",
            body="Your offer changed",
            max_retries=1,
        )

        assert result["status"] == "failed"
        assert result["accepted_count"] == 0
        assert "network down" in result["error_message"]

    @patch("app.email_service.urllib.request.urlopen")
    def test_send_expo_push_notifications_returns_accepted_count(self, mock_urlopen):
        self._mock_urlopen_with_payload(
            mock_urlopen,
            {"data": [{"status": "ok"}, {"status": "error"}, {"status": "ok"}]},
        )

        accepted_count = send_expo_push_notifications(
            tokens=["ExponentPushToken[a]", "ExponentPushToken[b]", "ExponentPushToken[c]"],
            title="Offer update",
            body="Your offer changed",
        )

        assert accepted_count == 2
