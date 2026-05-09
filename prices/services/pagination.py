from __future__ import annotations

from dataclasses import dataclass

from django.core.paginator import EmptyPage, PageNotAnInteger


def parse_page_number(raw) -> int:
    try:
        return max(int(raw), 1)
    except (TypeError, ValueError):
        return 1


@dataclass
class CountlessPage:
    object_list: list
    number: int
    paginator: "CountlessPaginator"
    _has_next: bool

    def has_next(self) -> bool:
        return self._has_next

    def has_previous(self) -> bool:
        return self.number > 1

    def has_other_pages(self) -> bool:
        return self.has_previous() or self.has_next()

    def next_page_number(self) -> int:
        if not self.has_next():
            raise EmptyPage("That page has no next page")
        return self.number + 1

    def previous_page_number(self) -> int:
        if not self.has_previous():
            raise EmptyPage("That page has no previous page")
        return self.number - 1

    def start_index(self) -> int:
        if not self.object_list:
            return 0
        return (self.number - 1) * self.paginator.per_page + 1

    def end_index(self) -> int:
        return self.start_index() + len(self.object_list) - 1


class CountlessPaginator:
    ELLIPSIS = "..."
    count = None
    has_exact_count = False

    def __init__(
        self,
        *,
        per_page: int,
        current_page: int,
        has_next: bool,
        object_list=None,
    ):
        self.per_page = per_page
        self.num_pages = current_page + 1 if has_next else current_page
        self.object_list = object_list

    def validate_number(self, number) -> int:
        try:
            number = int(number)
        except (TypeError, ValueError) as exc:
            raise PageNotAnInteger("That page number is not an integer") from exc
        if number < 1:
            raise EmptyPage("That page number is less than 1")
        return number

    def get_elided_page_range(
        self,
        number=1,
        *,
        on_each_side=1,
        on_ends=2,
    ):
        start = max(1, number - on_each_side)
        end = min(self.num_pages, number + on_each_side)
        pages = set(range(start, end + 1))
        pages.update(range(1, min(self.num_pages, on_ends) + 1))
        tail_start = max(1, self.num_pages - on_ends + 1)
        pages.update(range(tail_start, self.num_pages + 1))
        previous = 0
        for page in sorted(pages):
            if previous and page > previous + 1:
                yield self.ELLIPSIS
            yield page
            previous = page


def paginate_queryset_without_count(queryset, *, page_number, page_size: int):
    page_number = parse_page_number(page_number)
    offset = (page_number - 1) * page_size
    items = list(queryset[offset : offset + page_size + 1])
    has_next = len(items) > page_size
    object_list = items[:page_size]
    paginator = CountlessPaginator(
        per_page=page_size,
        current_page=page_number,
        has_next=has_next,
        object_list=queryset,
    )
    page = CountlessPage(
        object_list=object_list,
        number=page_number,
        paginator=paginator,
        _has_next=has_next,
    )
    return paginator, page, object_list, page.has_other_pages()
