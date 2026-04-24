from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from assistant_linking.models import ManualLinkDecision, MatchGroupItem
from assistant_linking.services.grouping import rebuild_groups
from assistant_linking.services.normalizer import save_parse
from catalog.models import Brand, Perfume
from prices.models import Supplier, SupplierProduct


User = get_user_model()


class GroupingWorkbenchTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.brand = Brand.objects.create(name="Brand")
        self.perfume = Perfume.objects.create(brand=self.brand, name="Hero", concentration="edp")
        self.s1 = Supplier.objects.create(name="S1", code="s1")
        self.s2 = Supplier.objects.create(name="S2", code="s2")
        self.p1 = SupplierProduct.objects.create(supplier=self.s1, identity_key="1", name="Brand Hero EDP 100ml")
        self.p2 = SupplierProduct.objects.create(supplier=self.s2, identity_key="2", name="Brand Hero EDP 100ml", is_active=False)

    def test_grouping_includes_inactive_rows(self):
        save_parse(self.p1)
        save_parse(self.p2)
        rebuild_groups()

        self.assertEqual(MatchGroupItem.objects.count(), 2)
        self.assertTrue(MatchGroupItem.objects.filter(supplier_product=self.p2).exists())

    def test_bulk_link_does_not_overwrite_without_confirmation(self):
        self.p2.catalog_perfume = self.perfume
        self.p2.save()
        self.client.force_login(self.staff)

        response = self.client.post(
            reverse("assistant_linking:bulk_link", args=[self.p1.id]),
            {"supplier_product_ids": [self.p2.id], "perfume_id": self.perfume.id, "reason": "test"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ManualLinkDecision.objects.count(), 0)
