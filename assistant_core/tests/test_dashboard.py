from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


User = get_user_model()


class AssistantDashboardTests(TestCase):
    def test_staff_user_can_open_dashboard(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Assistant control room")

    def test_non_staff_user_redirects_from_admin_assistant(self):
        user = User.objects.create_user(username="viewer", password="pass", is_staff=False)
        self.client.force_login(user)

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 302)
