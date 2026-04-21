(function () {
    var input = document.getElementById("live-search");
    var countEl = document.getElementById("search-count");
    var currencyFilter = document.getElementById("currency-filter");
    var supplierFilter = document.getElementById("supplier-filter");
    var supplierFilterSearch = document.getElementById("supplier-filter-search");
    var supplierSuggest = document.getElementById("supplier-suggest");
    var clearSearchBtn = document.getElementById("clear-search-btn");
    var supplierOptionSource = document.getElementById("supplier-option-source");
    var supplierSelectedChips = document.getElementById("supplier-selected-chips");
    var clearSupplierFilterBtn = document.getElementById("clear-supplier-filter-btn");
    var statusFilter = document.getElementById("status-filter");
    var showInactiveSwitch = document.getElementById("show-inactive-switch");
    var excludeFilter = document.getElementById("exclude-filter");
    var excludeApplyBtn = document.getElementById("exclude-apply-btn");
    var priceMinFilter = document.getElementById("price-min-filter");
    var priceMaxFilter = document.getElementById("price-max-filter");
    var priceApplyBtn = document.getElementById("price-apply-btn");
    if (!input) return;
    var table = document.querySelector("table");
    var tbody = table ? table.querySelector("tbody") : null;
    var pagination = document.getElementById("pagination-controls");
    var activeController = null;
    var requestId = 0;
    var lastRequestSignature = "";
    var currentAjaxPage = 1;
    var lastAppliedSearch = (input.value || "").trim();
    var supplierOptions = supplierOptionSource
        ? Array.from(supplierOptionSource.options).map(function (opt) {
            return { id: String(opt.value || ""), name: String(opt.text || "") };
        })
        : [];
    var selectedSupplierIds = [];
    var lastViewedKey = "products_last_viewed_id";
    var stickySearchItem = input.closest(".search-inline-row") || input.closest(".filter-item.search-full-width");
    var searchInputWrap = input.closest(".search-input-wrap");
    var stickyPlaceholder = null;
    var isPinnedSearch = false;
    var tableCard = document.querySelector(".products-table-shell, .generic-table-shell, .workspace-table-wrap, .card.p-2.p-md-3");
    var useServerSearch = input.getAttribute("data-server-search") === "1";

    function getStickyTopOffset() {
        return window.matchMedia("(max-width: 991.98px)").matches ? 64 : 72;
    }

    function ensureStickyPlaceholder() {
        if (!stickySearchItem) return null;
        if (stickyPlaceholder) return stickyPlaceholder;
        stickyPlaceholder = document.createElement("div");
        stickyPlaceholder.className = "search-sticky-placeholder";
        stickySearchItem.insertAdjacentElement("afterend", stickyPlaceholder);
        return stickyPlaceholder;
    }

    function setPinnedSearchState(shouldPin) {
        if (!stickySearchItem) return;
        var placeholder = ensureStickyPlaceholder();
        if (shouldPin && !isPinnedSearch) {
            var rect = stickySearchItem.getBoundingClientRect();
            if (placeholder) {
                placeholder.style.height = rect.height + "px";
                placeholder.style.display = "block";
            }
            stickySearchItem.classList.add("is-pinned");
            stickySearchItem.style.width = rect.width + "px";
            stickySearchItem.style.left = rect.left + "px";
            stickySearchItem.style.top = getStickyTopOffset() + "px";
            isPinnedSearch = true;
            return;
        }
        if (!shouldPin && isPinnedSearch) {
            stickySearchItem.classList.remove("is-pinned");
            stickySearchItem.style.width = "";
            stickySearchItem.style.left = "";
            stickySearchItem.style.top = "";
            if (placeholder) {
                placeholder.style.display = "none";
                placeholder.style.height = "";
            }
            isPinnedSearch = false;
        }
    }

    function syncPinnedSearchGeometry() {
        if (!stickySearchItem || !isPinnedSearch) return;
        var placeholder = ensureStickyPlaceholder();
        if (!placeholder) return;
        var rect = placeholder.getBoundingClientRect();
        stickySearchItem.style.width = rect.width + "px";
        stickySearchItem.style.left = rect.left + "px";
        stickySearchItem.style.top = getStickyTopOffset() + "px";
    }

    function updatePinnedSearchOnScroll() {
        if (!stickySearchItem) return;
        var topOffset = getStickyTopOffset();
        var referenceRect = isPinnedSearch && stickyPlaceholder
            ? stickyPlaceholder.getBoundingClientRect()
            : stickySearchItem.getBoundingClientRect();
        var tableBottom = tableCard
            ? tableCard.getBoundingClientRect().bottom
            : Number.POSITIVE_INFINITY;
        var thresholdBottom = topOffset + Math.max(stickySearchItem.offsetHeight, 72) + 8;
        var shouldPin = referenceRect.top <= topOffset && tableBottom > thresholdBottom;
        setPinnedSearchState(shouldPin);
        if (shouldPin) {
            syncPinnedSearchGeometry();
        }
    }

    function decorateButtons(container) {
        if (!container) return;
        container.querySelectorAll(".button").forEach(function (el) {
            if (el.classList.contains("icon")) {
                el.classList.add("btn-icon");
            }
        });
    }

    function decorateInputs(container) {
        if (!container) return;
        container.querySelectorAll("input[type='checkbox']").forEach(function (el) {
            el.classList.add("form-check-input");
        });
    }

    function getPriceMinValue() {
        return priceMinFilter ? priceMinFilter.value.trim() : "";
    }

    function getPriceMaxValue() {
        return priceMaxFilter ? priceMaxFilter.value.trim() : "";
    }

    function setCommonFilters(url) {
        if (currencyFilter) {
            if (currencyFilter.value) {
                url.searchParams.set("currency", currencyFilter.value);
            } else {
                url.searchParams.delete("currency");
            }
        }
        if (supplierFilter) {
            if (supplierFilter.value) {
                url.searchParams.set("supplier", supplierFilter.value);
            } else {
                url.searchParams.delete("supplier");
            }
        }
        var statusValue = getStatusFilterValue();
        if (statusValue && statusValue !== "all") {
            url.searchParams.set("status", statusValue);
        } else {
            url.searchParams.delete("status");
        }
        if (excludeFilter) {
            var excludeRaw = excludeFilter.value.trim();
            if (excludeRaw) {
                url.searchParams.set("exclude", excludeRaw);
            } else {
                url.searchParams.delete("exclude");
            }
        }
        var priceMinRaw = getPriceMinValue();
        var priceMaxRaw = getPriceMaxValue();
        if (priceMinRaw) {
            url.searchParams.set("price_min", priceMinRaw);
        } else {
            url.searchParams.delete("price_min");
        }
        if (priceMaxRaw) {
            url.searchParams.set("price_max", priceMaxRaw);
        } else {
            url.searchParams.delete("price_max");
        }
    }

    function updateSearchParamInUrl(query, page) {
        if (useServerSearch) return;
        var url = new URL(window.location.href);
        setCommonFilters(url);
        // Keep AJAX search term out of URL to avoid sticky/stacked q links.
        url.searchParams.delete("q");
        url.searchParams.delete("page");
        window.history.replaceState({}, "", url.toString());
    }

    function renderSearchPagination(data, query) {
        if (!pagination) return;
        var page = Number(data.page || 1);
        var hasKnownTotal = data.num_pages !== null && data.num_pages !== undefined;
        var html = "<nav class='mt-3' aria-label='Pagination'><ul class='pagination flex-wrap mb-0'>";
        if (hasKnownTotal) {
            var totalPages = Number(data.num_pages || 1);
            if (totalPages <= 1) {
                pagination.style.display = "none";
                return;
            }
            var start = Math.max(1, page - 2);
            var end = Math.min(totalPages, page + 2);
            if (data.has_previous) {
                html += "<li class='page-item'><a class='page-link' href='#' data-page='" + data.previous_page + "'>Previous</a></li>";
            }
            for (var p = start; p <= end; p += 1) {
                if (p === page) {
                    html += "<li class='page-item active'><span class='page-link'>" + p + "</span></li>";
                } else {
                    html += "<li class='page-item'><a class='page-link' href='#' data-page='" + p + "'>" + p + "</a></li>";
                }
            }
            if (data.has_next) {
                html += "<li class='page-item'><a class='page-link' href='#' data-page='" + data.next_page + "'>Next</a></li>";
            }
            html += "</ul><div class='muted mt-2'>Page " + page + " of " + totalPages + "</div></nav>";
        } else {
            if (!data.has_previous && !data.has_next) {
                pagination.style.display = "none";
                return;
            }
            if (data.has_previous) {
                html += "<li class='page-item'><a class='page-link' href='#' data-page='" + data.previous_page + "'>Previous</a></li>";
            }
            html += "<li class='page-item active'><span class='page-link'>" + page + "</span></li>";
            if (data.has_next) {
                html += "<li class='page-item'><a class='page-link' href='#' data-page='" + data.next_page + "'>Next</a></li>";
            }
            html += "</ul><div class='muted mt-2'>Page " + page + "</div></nav>";
        }
        pagination.innerHTML = html;
        pagination.style.display = "";
        pagination.querySelectorAll("a[data-page]").forEach(function (link) {
            link.addEventListener("click", function (event) {
                event.preventDefault();
                var nextPage = Number(link.getAttribute("data-page") || "1");
                runAjaxSearch(query, nextPage, true);
            });
        });
    }

    function applyLastViewedHighlight() {
        if (!tbody) return;
        tbody.querySelectorAll("tr.last-viewed").forEach(function (row) {
            row.classList.remove("last-viewed");
        });
        var lastId = sessionStorage.getItem(lastViewedKey);
        if (!lastId) return;
        var row = tbody.querySelector("tr[data-product-id='" + String(lastId).replace(/'/g, "\\'") + "']");
        if (!row) return;
        row.classList.add("last-viewed");
    }

    function syncClearSearchVisibility() {
        if (!clearSearchBtn) return;
        if ((input.value || "").trim()) {
            clearSearchBtn.classList.remove("is-hidden");
        } else {
            clearSearchBtn.classList.add("is-hidden");
        }
    }

    function setSearchLoading(isLoading) {
        if (!searchInputWrap) return;
        if (isLoading) {
            searchInputWrap.classList.add("is-loading");
        } else {
            searchInputWrap.classList.remove("is-loading");
        }
    }

    function resolveSupplierIdByName(name) {
        if (!supplierFilterSearch) return "";
        var term = (name || "").trim().toLowerCase();
        if (!term) return "";
        var exact = supplierOptions.find(function (opt) {
            return opt.name.trim().toLowerCase() === term;
        });
        if (exact) {
            return exact.id;
        }
        var prefix = supplierOptions.filter(function (opt) {
            return opt.name.trim().toLowerCase().indexOf(term) === 0;
        });
        if (prefix.length === 1) {
            return prefix[0].id;
        }
        return "";
    }

    function getStatusFilterValue() {
        if (showInactiveSwitch) {
            return showInactiveSwitch.checked ? "all" : "active";
        }
        if (statusFilter && statusFilter.value) {
            return String(statusFilter.value);
        }
        return "all";
    }

    function parseSupplierIds(raw) {
        var ids = [];
        if (!raw) return ids;
        String(raw).split(",").forEach(function (part) {
            var cleaned = String(part || "").trim();
            if (!cleaned || !/^\d+$/.test(cleaned)) return;
            if (ids.indexOf(cleaned) === -1) {
                ids.push(cleaned);
            }
        });
        return ids;
    }

    function syncSupplierHiddenValue() {
        if (!supplierFilter) return;
        supplierFilter.value = selectedSupplierIds.join(",");
    }

    function renderSupplierSelectedChips() {
        if (!supplierSelectedChips) return;
        if (!selectedSupplierIds.length) {
            supplierSelectedChips.innerHTML = "";
            return;
        }
        supplierSelectedChips.innerHTML = selectedSupplierIds.map(function (sid) {
            var match = supplierOptions.find(function (opt) { return opt.id === sid; });
            var label = match ? match.name : ("Supplier #" + sid);
            return (
                "<span class='supplier-selected-chip'>" +
                escapeHtml(label) +
                "<button type='button' data-remove-supplier-id='" + escapeHtml(sid) + "' aria-label='Remove supplier'>x</button>" +
                "</span>"
            );
        }).join("");
    }

    function hydrateSupplierSelection() {
        if (!supplierFilter) return;
        selectedSupplierIds = parseSupplierIds(supplierFilter.value);
        syncSupplierHiddenValue();
        renderSupplierSelectedChips();
    }

    function hideSupplierSuggest() {
        if (supplierSuggest) {
            supplierSuggest.style.display = "none";
            supplierSuggest.innerHTML = "";
        }
    }

    function showSupplierSuggest(query, showAllWhenEmpty) {
        if (!supplierSuggest) return;
        var term = (query || "").trim().toLowerCase();
        if (!term && !showAllWhenEmpty) {
            hideSupplierSuggest();
            return;
        }
        var matches = !term
            ? supplierOptions.slice(0, 20)
            : supplierOptions.filter(function (opt) {
                return opt.name.toLowerCase().indexOf(term) !== -1;
            });
        matches = matches.filter(function (opt) {
            return selectedSupplierIds.indexOf(opt.id) === -1;
        }).slice(0, 20);
        if (!matches.length) {
            hideSupplierSuggest();
            return;
        }
        supplierSuggest.innerHTML = matches.map(function (opt) {
            return "<div class='supplier-suggest-item' data-id='" + escapeHtml(opt.id) + "' data-name='" + escapeHtml(opt.name) + "'>" + escapeHtml(opt.name) + "</div>";
        }).join("");
        supplierSuggest.style.display = "block";
    }

    function applySupplierFilter() {
        if (!supplierFilter || !supplierFilterSearch) return;
        var resolvedId = resolveSupplierIdByName(supplierFilterSearch.value);
        if (resolvedId && selectedSupplierIds.indexOf(resolvedId) === -1) {
            selectedSupplierIds.push(resolvedId);
        }
        supplierFilterSearch.value = "";
        syncSupplierHiddenValue();
        renderSupplierSelectedChips();
        var url = new URL(window.location.href);
        setCommonFilters(url);
        url.searchParams.delete("page");
        window.location.href = url.toString();
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function renderRows(items) {
        var detailBase = input.getAttribute("data-detail-base") || "";
        var bulkEnabled = input.getAttribute("data-bulk") === "1";
        var showActions = input.getAttribute("data-show-actions") === "1";
        var editPattern = input.getAttribute("data-edit-pattern") || "";
        var deletePattern = input.getAttribute("data-delete-pattern") || "";
        var csrfEl = document.querySelector("input[name='csrfmiddlewaretoken']");
        var csrfValue = csrfEl ? csrfEl.value : "";
        var nextValue = window.location.pathname + window.location.search;
        var rows = "";
        var inactiveDividerInserted = false;
        items.forEach(function (item) {
            var supplier = escapeHtml(item.supplier);
            var supplierId = escapeHtml(String(item.supplier_id || ""));
            var sku = escapeHtml(item.supplier_sku);
            var rawName = String(item.name || "");
            var escapedName = escapeHtml(rawName);
            var name = "<span class='cell-name'>" + escapedName + "</span>";
            var price = escapeHtml(item.current_price);
            var deltaDirection = item.price_delta_direction || "";
            var deltaValue = escapeHtml(item.price_delta_value || "");
            var deltaPercent = escapeHtml(item.price_delta_percent || "");
            var desktopPriceHtml = "<div class='cell-price-main'>" + price + "</div>";
            if (deltaDirection && deltaValue) {
                var arrow = deltaDirection === "down" ? "↓" : "↑";
                var deltaTail = deltaPercent ? " (" + deltaPercent + ")" : "";
                desktopPriceHtml += "<div class='price-delta-wrap'><span class='price-delta-badge " +
                    escapeHtml(deltaDirection) + "'>" + arrow + " " + deltaValue + deltaTail +
                    "</span></div>";
            } else {
                desktopPriceHtml += "<div class='price-delta-wrap'><span class='price-delta-badge neutral'>- No change</span></div>";
            }
            var importedTitle = escapeHtml(item.last_imported_at_full || "");
            var importedAgeClass = escapeHtml(item.last_imported_age_class || "");
            var imported = "<span class='cell-imported " + importedAgeClass + "'" + (importedTitle ? " title='" + importedTitle + "' data-full-datetime='" + importedTitle + "'" : "") + ">" + escapeHtml(item.last_imported_at) + "</span>";
            var mobileSupplier = supplierId
                ? "<a href='?supplier=" + encodeURIComponent(String(item.supplier_id || "")) + "' class='supplier-filter-link cell-supplier' data-supplier-id='" + supplierId + "'>" + supplier + "</a>"
                : "<span class='cell-supplier'>" + supplier + "</span>";
            var priceHtml = "<div class='desktop-price-stack'>" + desktopPriceHtml + "</div>";

            if (detailBase && item.id) {
                name = "<a class='cell-name' href='" + detailBase + item.id + "/?next=" + encodeURIComponent(nextValue) + "&from=" + item.id + "'>" + escapedName + "</a>";
            }

            var checkboxCell = bulkEnabled
                ? "<td class='select-col bulk-col'><input type='checkbox' name='product_ids' value='" + item.id + "'></td>"
                : "";

            var actionsCell = "";
            if (showActions && (editPattern || deletePattern)) {
                var editUrl = editPattern ? editPattern.replace("/0/", "/" + item.id + "/") : "";
                var deleteUrl = deletePattern ? deletePattern.replace("/0/", "/" + item.id + "/") : "";
                actionsCell = "<td class='actions' data-label='Actions'>" +
                    "<div class='actions-desktop d-inline-flex align-items-center gap-2'>" +
                    "<details class='actions-menu-desktop'>" +
                    "<summary aria-label='More actions'>...</summary>" +
                    "<div class='actions-menu-pop d-flex flex-column gap-2'>";
                if (editUrl) {
                    actionsCell += "<a class='button secondary' href='" + editUrl + "'>Edit</a>";
                }
                if (deleteUrl) {
                    actionsCell += "<form method='post' action='" + deleteUrl + "'>" +
                        "<input type='hidden' name='csrfmiddlewaretoken' value='" + escapeHtml(csrfValue) + "'>" +
                        "<input type='hidden' name='next' value='" + escapeHtml(nextValue) + "'>" +
                        "<button class='button danger' type='submit'>Delete</button></form>";
                }
                actionsCell += "</div></details></div><div class='actions-mobile'>" +
                    "<details class='mobile-actions-menu'>" +
                    "<summary aria-label='Actions'>...</summary>" +
                    "<div class='mobile-actions-pop d-flex flex-column gap-2'>";
                if (editUrl) {
                    actionsCell += "<a class='button secondary' href='" + editUrl + "'>Edit</a>";
                }
                if (deleteUrl) {
                    actionsCell += "<form method='post' action='" + deleteUrl + "'>" +
                        "<input type='hidden' name='csrfmiddlewaretoken' value='" + escapeHtml(csrfValue) + "'>" +
                        "<input type='hidden' name='next' value='" + escapeHtml(nextValue) + "'>" +
                        "<button class='button danger' type='submit'>Delete</button></form>";
                }
                actionsCell += "</div></details></div></td>";
            }

            var hasActions = showActions && !!(editPattern || deletePattern);
            var colCount = bulkEnabled ? (hasActions ? 7 : 6) : (hasActions ? 6 : 5);
            var rowClass = item.is_active ? "" : " class='inactive-product-row'";
            if (item.is_active === false && !inactiveDividerInserted) {
                rows += "<tr class='inactive-divider-row'><td colspan='" + colCount + "'>Inactive products</td></tr>";
                inactiveDividerInserted = true;
            }

            rows += "<tr data-product-id='" + item.id + "'" + rowClass + ">" +
                checkboxCell +
                "<td data-field='supplier_sku' data-label='SKU'><span class='cell-sku'>" + sku + "</span></td>" +
                "<td data-field='name' data-label='Name'>" + name + "</td>" +
                "<td data-field='current_price' data-label='Price'>" + priceHtml + "</td>" +
                "<td data-field='supplier' data-label='Supplier'>" + mobileSupplier + "</td>" +
                "<td data-field='last_imported_at' data-label='Last imported'>" + imported + "</td>" +
                actionsCell +
            "</tr>";
        });

        if (!rows) {
            var hasActions = !!(editPattern || deletePattern);
            var emptyColspan = bulkEnabled ? (hasActions ? 7 : 6) : (hasActions ? 6 : 5);
            rows = "<tr><td colspan='" + emptyColspan + "' class='muted'>No records yet.</td></tr>";
        }
        tbody.innerHTML = rows;
        decorateButtons(tbody);
        bindImportedDatetimeTooltips(tbody);
        applyLastViewedHighlight();
    }

    function bindImportedDatetimeTooltips(container) {
        if (!container) return;
        var chips = container.querySelectorAll(".cell-imported[data-full-datetime]");
        chips.forEach(function (chip) {
            if (chip.dataset.tooltipBound === "1") return;
            chip.dataset.tooltipBound = "1";
            chip.addEventListener("click", function (event) {
                event.preventDefault();
                chips.forEach(function (item) {
                    if (item !== chip) item.classList.remove("show-full-datetime");
                });
                chip.classList.toggle("show-full-datetime");
                if (chip.classList.contains("show-full-datetime")) {
                    window.setTimeout(function () {
                        chip.classList.remove("show-full-datetime");
                    }, 1800);
                }
            });
        });
    }

    function runAjaxSearch(query, page, force) {
        if (!tbody) return;
        var safePage = page && Number(page) > 0 ? Number(page) : 1;
        var requestSignature = [
            query,
            currencyFilter ? currencyFilter.value : "",
            supplierFilter ? supplierFilter.value : "",
            getStatusFilterValue(),
            excludeFilter ? excludeFilter.value.trim() : "",
            getPriceMinValue(),
            getPriceMaxValue(),
            input.getAttribute("data-sort") || "",
            input.getAttribute("data-dir") || "",
            String(safePage)
        ].join("|");
        if (!force && requestSignature === lastRequestSignature) {
            return;
        }
        lastRequestSignature = requestSignature;
        currentAjaxPage = safePage;
        updateSearchParamInUrl(query, safePage);
        requestId += 1;
        var currentRequestId = requestId;
        setSearchLoading(true);

        if (activeController) {
            activeController.abort();
            activeController = null;
        }
        var searchBase = input.getAttribute("data-search-url") || "/products/search/";
        var url = searchBase + "?q=" + encodeURIComponent(query);
        var sort = input.getAttribute("data-sort");
        var dir = input.getAttribute("data-dir");
        if (sort) url += "&sort=" + encodeURIComponent(sort);
        if (dir) url += "&dir=" + encodeURIComponent(dir);
        if (currencyFilter && currencyFilter.value) {
            url += "&currency=" + encodeURIComponent(currencyFilter.value);
        }
        if (supplierFilter && supplierFilter.value) {
            url += "&supplier=" + encodeURIComponent(supplierFilter.value);
        }
        var statusValue = getStatusFilterValue();
        if (statusValue && statusValue !== "all") {
            url += "&status=" + encodeURIComponent(statusValue);
        }
        if (excludeFilter && excludeFilter.value.trim()) {
            url += "&exclude=" + encodeURIComponent(excludeFilter.value.trim());
        }
        if (getPriceMinValue()) {
            url += "&price_min=" + encodeURIComponent(getPriceMinValue());
        }
        if (getPriceMaxValue()) {
            url += "&price_max=" + encodeURIComponent(getPriceMaxValue());
        }
        url += "&page=" + encodeURIComponent(String(safePage));
        activeController = new AbortController();
        fetch(url, {
            headers: { "X-Requested-With": "XMLHttpRequest" },
            signal: activeController.signal
        })
            .then(function (response) { return response.json(); })
            .then(function (data) {
                if (currentRequestId !== requestId) return;
                renderRows(data.items || []);
                renderSearchPagination(data, query);
                if (countEl) {
                    var countValue = data.count;
                    if (countValue === null || countValue === undefined || countValue === "") {
                        countValue = data.count_display || data.shown || 0;
                    }
                    countEl.textContent = "Found: " + countValue;
                }
            })
            .catch(function (error) {
                if (error && error.name === "AbortError") return;
            })
            .finally(function () {
                if (currentRequestId === requestId) {
                    activeController = null;
                    setSearchLoading(false);
                }
            });
    }

    function applySearchNow(query) {
        query = (query || "").trim();
        if (useServerSearch) {
            setSearchLoading(true);
            var navUrl = new URL(window.location.href);
            if (query) {
                navUrl.searchParams.set("q", query);
            } else {
                navUrl.searchParams.delete("q");
            }
            setCommonFilters(navUrl);
            navUrl.searchParams.delete("page");
            if (navUrl.toString() !== window.location.href) {
                window.location.assign(navUrl.toString());
            }
            lastAppliedSearch = query;
            return;
        }
        if (!query) {
            runAjaxSearch("", 1, true);
            lastAppliedSearch = "";
            return;
        }
        runAjaxSearch(query, 1, false);
        lastAppliedSearch = query;
    }

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            event.preventDefault();
            applySearchNow(input.value.trim());
            input.blur();
        }
    });

    input.addEventListener("input", function () {
        syncClearSearchVisibility();
        if (!(input.value || "").trim() && lastAppliedSearch) {
            applySearchNow("");
        }
    });
    input.addEventListener("blur", function () {
        setTimeout(function () {
            var currentValue = (input.value || "").trim();
            if (currentValue !== lastAppliedSearch) {
                applySearchNow(currentValue);
            }
        }, 0);
    });
    if (clearSearchBtn) {
        clearSearchBtn.addEventListener("click", function () {
            input.value = "";
            syncClearSearchVisibility();
            applySearchNow("");
            input.focus();
        });
    }
    if (currencyFilter) {
        currencyFilter.addEventListener("change", function () {
            var url = new URL(window.location.href);
            setCommonFilters(url);
            url.searchParams.delete("page");
            window.location.href = url.toString();
        });
    }
    if (supplierFilter) {
        hydrateSupplierSelection();
    }
    if (supplierFilterSearch) {
        supplierFilterSearch.addEventListener("input", function () {
            showSupplierSuggest(supplierFilterSearch.value, false);
        });
        supplierFilterSearch.addEventListener("focus", function () {
            showSupplierSuggest(supplierFilterSearch.value, true);
        });
        supplierFilterSearch.addEventListener("click", function () {
            showSupplierSuggest(supplierFilterSearch.value, true);
        });
        supplierFilterSearch.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                applySupplierFilter();
            } else if (event.key === "Escape") {
                hideSupplierSuggest();
            } else if (event.key === "ArrowDown") {
                event.preventDefault();
                showSupplierSuggest(supplierFilterSearch.value, true);
            }
        });
        supplierFilterSearch.addEventListener("blur", function () {
            setTimeout(hideSupplierSuggest, 150);
        });
    }
    if (supplierSuggest) {
        supplierSuggest.addEventListener("mousedown", function (event) {
            var target = event.target.closest(".supplier-suggest-item");
            if (!target) return;
            var targetId = target.getAttribute("data-id") || "";
            if (targetId && selectedSupplierIds.indexOf(targetId) === -1) {
                selectedSupplierIds.push(targetId);
            }
            supplierFilterSearch.value = "";
            syncSupplierHiddenValue();
            renderSupplierSelectedChips();
            hideSupplierSuggest();
            var suggestUrl = new URL(window.location.href);
            setCommonFilters(suggestUrl);
            suggestUrl.searchParams.delete("page");
            window.location.href = suggestUrl.toString();
        });
    }
    if (supplierSelectedChips) {
        supplierSelectedChips.addEventListener("click", function (event) {
            var removeBtn = event.target.closest("button[data-remove-supplier-id]");
            if (!removeBtn) return;
            var removeId = removeBtn.getAttribute("data-remove-supplier-id") || "";
            selectedSupplierIds = selectedSupplierIds.filter(function (sid) { return sid !== removeId; });
            syncSupplierHiddenValue();
            renderSupplierSelectedChips();
            var chipsUrl = new URL(window.location.href);
            setCommonFilters(chipsUrl);
            chipsUrl.searchParams.delete("page");
            window.location.href = chipsUrl.toString();
        });
    }
    if (statusFilter) {
        statusFilter.addEventListener("change", function () {
            var url = new URL(window.location.href);
            setCommonFilters(url);
            url.searchParams.delete("page");
            window.location.href = url.toString();
        });
    }
    if (showInactiveSwitch) {
        showInactiveSwitch.addEventListener("change", function () {
            var url = new URL(window.location.href);
            setCommonFilters(url);
            url.searchParams.delete("page");
            window.location.href = url.toString();
        });
    }
    if (clearSupplierFilterBtn) {
        clearSupplierFilterBtn.addEventListener("click", function () {
            selectedSupplierIds = [];
            if (supplierFilter) supplierFilter.value = "";
            if (supplierFilterSearch) supplierFilterSearch.value = "";
            renderSupplierSelectedChips();
            var url = new URL(window.location.href);
            setCommonFilters(url);
            url.searchParams.delete("supplier");
            url.searchParams.delete("page");
            window.location.href = url.toString();
        });
    }
    function applyExcludeFilter() {
        if (!excludeFilter) return;
        var url = new URL(window.location.href);
        setCommonFilters(url);
        // Preserve explicit "clear" action so backend can store empty preference
        // instead of falling back to previously saved exclude terms.
        url.searchParams.set("exclude", excludeFilter.value.trim());
        url.searchParams.delete("page");
        window.location.href = url.toString();
    }
    function applyPriceFilter() {
        var url = new URL(window.location.href);
        setCommonFilters(url);
        url.searchParams.delete("page");
        window.location.href = url.toString();
    }
    if (excludeApplyBtn) {
        excludeApplyBtn.addEventListener("click", applyExcludeFilter);
    }
    if (priceApplyBtn) {
        priceApplyBtn.addEventListener("click", applyPriceFilter);
    }
    if (excludeFilter) {
        excludeFilter.addEventListener("keydown", function (event) {
            if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
                event.preventDefault();
                applyExcludeFilter();
            }
        });
    }
    if (priceMinFilter) {
        priceMinFilter.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                applyPriceFilter();
            }
        });
    }
    if (priceMaxFilter) {
        priceMaxFilter.addEventListener("keydown", function (event) {
            if (event.key === "Enter") {
                event.preventDefault();
                applyPriceFilter();
            }
        });
    }
    if (tbody) {
        tbody.addEventListener("click", function (event) {
            var isMobileProductsTable =
                table &&
                table.classList.contains("products-mobile") &&
                window.matchMedia("(max-width: 991.98px)").matches;
            if (isMobileProductsTable) {
                var tappedRow = event.target.closest("tr[data-product-id]");
                var interactiveTap = event.target.closest(
                    "a,button,input,summary,details,form,label,select,textarea"
                );
                if (tappedRow && !interactiveTap) {
                    tappedRow.classList.toggle("sku-expanded");
                    return;
                }
            }
            var supplierLink = event.target.closest("a.supplier-filter-link[data-supplier-id]");
            if (supplierLink) {
                event.preventDefault();
                var supplierId = supplierLink.getAttribute("data-supplier-id") || "";
                selectedSupplierIds = supplierId ? [supplierId] : [];
                syncSupplierHiddenValue();
                renderSupplierSelectedChips();
                if (supplierFilterSearch) supplierFilterSearch.value = "";
                var supplierUrl = new URL(window.location.href);
                setCommonFilters(supplierUrl);
                if (supplierId) {
                    supplierUrl.searchParams.set("supplier", supplierId);
                } else {
                    supplierUrl.searchParams.delete("supplier");
                }
                supplierUrl.searchParams.delete("page");
                window.location.href = supplierUrl.toString();
                return;
            }
            var link = event.target.closest("td[data-field='name'] a");
            if (!link) return;
            var row = event.target.closest("tr[data-product-id]");
            if (!row) return;
            var productId = row.getAttribute("data-product-id");
            if (productId) {
                sessionStorage.setItem(lastViewedKey, productId);
            }
        });
    }
    updatePinnedSearchOnScroll();
    window.addEventListener("scroll", updatePinnedSearchOnScroll, { passive: true });
    window.addEventListener("resize", function () {
        updatePinnedSearchOnScroll();
        syncPinnedSearchGeometry();
    });
    applyLastViewedHighlight();
    bindImportedDatetimeTooltips(document);
    var serverSearchOnLoad = input.getAttribute("data-server-search") === "1";
    syncClearSearchVisibility();
    if (!serverSearchOnLoad) {
        var cleanUrl = new URL(window.location.href);
        if (cleanUrl.searchParams.has("q")) {
            cleanUrl.searchParams.delete("q");
            cleanUrl.searchParams.delete("page");
            window.history.replaceState({}, "", cleanUrl.toString());
        }
    }
    if (input.value.trim() && !serverSearchOnLoad) {
        var initialPage = Number(new URL(window.location.href).searchParams.get("page") || "1");
        runAjaxSearch(input.value.trim(), initialPage, true);
    }
})();
