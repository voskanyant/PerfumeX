from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import BrandAlias
from catalog.models import Brand


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

    def test_knowledge_card_counts_taught_aliases(self):
        user = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.client.force_login(user)
        brand = Brand.objects.create(name="Montale")
        BrandAlias.objects.create(brand=brand, alias_text="mntl", normalized_alias="mntl")

        response = self.client.get(reverse("assistant_core:dashboard"))

        self.assertEqual(response.status_code, 200)
        cards = {title: count for title, _route, count in response.context["cards"]}
        self.assertEqual(cards["Knowledge Base"], 1)
