from __future__ import annotations

import math
from dataclasses import dataclass

from django.core.paginator import EmptyPage, PageNotAnInteger
from django.db.models import Q


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
    _has_previous: bool | None = None
    next_cursor: str = ""
    previous_cursor: str = ""

    def has_next(self) -> bool:
        return self._has_next

    def has_previous(self) -> bool:
        if self._has_previous is not None:
            return self._has_previous
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
        uses_cursor: bool = False,
        total_count: int | None = None,
    ):
        self.per_page = per_page
        self.count = total_count
        self.has_exact_count = total_count is not None
        calculated_pages = (
            max(math.ceil(total_count / per_page), 1) if total_count is not None else 0
        )
        cursor_pages = current_page + 1 if has_next else current_page
        self.num_pages = max(calculated_pages, cursor_pages)
        self.object_list = object_list
        self.uses_cursor = uses_cursor

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


def paginate_queryset_without_count(
    queryset, *, page_number, page_size: int, total_count: int | None = None
):
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
        total_count=total_count,
    )
    page = CountlessPage(
        object_list=object_list,
        number=page_number,
        paginator=paginator,
        _has_next=has_next,
    )
    return paginator, page, object_list, page.has_other_pages()


def parse_keyset_cursor(raw) -> tuple[int, int] | None:
    if not raw:
        return None
    try:
        first, second = str(raw).split(":", 1)
        first_id = int(first)
        second_id = int(second)
    except (TypeError, ValueError):
        return None
    if first_id <= 0 or second_id <= 0:
        return None
    return first_id, second_id


def make_keyset_cursor(item, first_field: str, second_field: str) -> str:
    return f"{getattr(item, first_field)}:{getattr(item, second_field)}"


def _keyset_after_filter(first_field: str, second_field: str, cursor: tuple[int, int]):
    first_value, second_value = cursor
    return Q(**{f"{first_field}__gt": first_value}) | Q(
        **{first_field: first_value, f"{second_field}__gt": second_value}
    )


def _keyset_before_filter(first_field: str, second_field: str, cursor: tuple[int, int]):
    first_value, second_value = cursor
    return Q(**{f"{first_field}__lt": first_value}) | Q(
        **{first_field: first_value, f"{second_field}__lt": second_value}
    )


def paginate_queryset_by_keyset(
    queryset,
    *,
    page_number,
    page_size: int,
    after=None,
    before=None,
    first_field: str = "id",
    second_field: str = "pk",
    total_count: int | None = None,
):
    page_number = parse_page_number(page_number)
    after_cursor = parse_keyset_cursor(after)
    before_cursor = parse_keyset_cursor(before)
    has_previous: bool | None = None

    if before_cursor:
        page_number = max(page_number, 1)
        page_queryset = queryset.filter(
            _keyset_before_filter(first_field, second_field, before_cursor)
        ).order_by(f"-{first_field}", f"-{second_field}")
        items = list(page_queryset[: page_size + 1])
        has_previous = len(items) > page_size
        object_list = list(reversed(items[:page_size]))
        has_next = True
    elif after_cursor:
        page_queryset = queryset.filter(
            _keyset_after_filter(first_field, second_field, after_cursor)
        )
        items = list(page_queryset[: page_size + 1])
        object_list = items[:page_size]
        has_next = len(items) > page_size
        has_previous = True
    else:
        # Keep direct page jumps working, but normal next/previous links use cursors.
        offset = (page_number - 1) * page_size
        items = list(queryset[offset : offset + page_size + 1])
        object_list = items[:page_size]
        has_next = len(items) > page_size

    previous_cursor = (
        make_keyset_cursor(object_list[0], first_field, second_field)
        if object_list
        and (has_previous if has_previous is not None else page_number > 1)
        else ""
    )
    next_cursor = (
        make_keyset_cursor(object_list[-1], first_field, second_field)
        if object_list and has_next
        else ""
    )
    paginator = CountlessPaginator(
        per_page=page_size,
        current_page=page_number,
        has_next=has_next,
        object_list=queryset,
        uses_cursor=True,
        total_count=total_count,
    )
    page = CountlessPage(
        object_list=object_list,
        number=page_number,
        paginator=paginator,
        _has_next=has_next,
        _has_previous=has_previous,
        next_cursor=next_cursor,
        previous_cursor=previous_cursor,
    )
    return paginator, page, object_list, page.has_other_pages()
