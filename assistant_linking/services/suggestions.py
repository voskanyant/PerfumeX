from __future__ import annotations

from dataclasses import dataclass

from assistant_linking.services.mock_suggester import generate_link_suggestions


@dataclass(frozen=True)
class GenerateSuggestionsResult:
    suggestions: list[dict]

    @property
    def count(self):
        return len(self.suggestions)

    @property
    def message(self):
        return f"Generated {self.count} suggestions."


def generate_suggestions_for_product(
    supplier_product_id,
    *,
    generator=generate_link_suggestions,
):
    return GenerateSuggestionsResult(
        suggestions=generator(supplier_product_id),
    )
