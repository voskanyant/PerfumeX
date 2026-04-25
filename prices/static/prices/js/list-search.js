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
    var smartSearchSwitch = document.getElementById("smart-search-switch");
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
    var stickySearchStrip = input.closest(".search-strip");
    var stickySearchWrap = input.closest(".search-wrap");
    var searchInputWrap = input.closest(".search-input-wrap");
    var stickyPlaceholder = null;
    var isPinnedSearch = false;
    var tableCard = document.querySelector(".products-table-shell, .generic-table-shell, .workspace-table-wrap, .card.p-2.p-md-3");
    var useServerSearch = input.getAttribute("data-server-search") === "1";
    var SVG_NS = "http://www.w3.org/2000/svg";

    function clearNode(node) {
        if (!node) return;
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function el(tagName, className, text) {
        var node = document.createElement(tagName);
        if (className) {
            node.className = className;
        }
        if (text !== undefined && text !== null) {
            node.textContent = String(text);
        }
        return node;
    }

    function setAttrs(node, attrs) {
        Object.keys(attrs || {}).forEach(function (key) {
            var value = attrs[key];
            if (value === null || value === undefined || value === false) return;
            node.setAttribute(key, String(value));
        });
        return node;
    }

    function svgEl(tagName, attrs) {
        return setAttrs(document.createElementNS(SVG_NS, tagName), attrs || {});
    }

    function getStickyTopOffset() {
        var topbar = document.querySelector(".mobile-topbar") || document.querySelector(".topbar");
        if (!topbar) {
            return 54;
        }
        var rect = topbar.getBoundingClientRect();
        return Math.max(Math.round(rect.bottom), 0);
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
            if (stickySearchStrip) {
                stickySearchStrip.classList.add("is-pinned");
            }
            if (stickySearchWrap) {
                stickySearchWrap.classList.add("is-search-pinned");
            }
            stickySearchItem.style.width = rect.width + "px";
            stickySearchItem.style.left = rect.left + "px";
            stickySearchItem.style.top = getStickyTopOffset() + "px";
            isPinnedSearch = true;
            return;
        }
        if (!shouldPin && isPinnedSearch) {
            stickySearchItem.classList.remove("is-pinned");
            if (stickySearchStrip) {
                stickySearchStrip.classList.remove("is-pinned");
            }
            if (stickySearchWrap) {
                stickySearchWrap.classList.remove("is-search-pinned");
            }
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
            el.classList.add("check-input");
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
        if (smartSearchSwitch) {
            if (smartSearchSwitch.checked) {
                url.searchParams.set("smart", "1");
            } else {
                url.searchParams.delete("smart");
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
        clearNode(pagination);
        if (hasKnownTotal) {
            var totalPages = Number(data.num_pages || 1);
            if (totalPages <= 1) {
                pagination.style.display = "none";
                return;
            }
            var start = Math.max(1, page - 2);
            var end = Math.min(totalPages, page + 2);
            var knownNav = el("nav", "space-top-md");
            knownNav.setAttribute("aria-label", "Pagination");
            var knownList = el("ul", "pagination-list");
            knownNav.appendChild(knownList);
            if (data.has_previous) {
                knownList.appendChild(buildPageItem(data.previous_page, "Previous", false));
            }
            for (var p = start; p <= end; p += 1) {
                knownList.appendChild(buildPageItem(p, p, p === page));
            }
            if (data.has_next) {
                knownList.appendChild(buildPageItem(data.next_page, "Next", false));
            }
            knownNav.appendChild(el("div", "muted space-top-sm", "Page " + page + " of " + totalPages));
            pagination.appendChild(knownNav);
        } else {
            if (!data.has_previous && !data.has_next) {
                pagination.style.display = "none";
                return;
            }
            var unknownNav = el("nav", "space-top-md");
            unknownNav.setAttribute("aria-label", "Pagination");
            var unknownList = el("ul", "pagination-list");
            unknownNav.appendChild(unknownList);
            if (data.has_previous) {
                unknownList.appendChild(buildPageItem(data.previous_page, "Previous", false));
            }
            unknownList.appendChild(buildPageItem(page, page, true));
            if (data.has_next) {
                unknownList.appendChild(buildPageItem(data.next_page, "Next", false));
            }
            unknownNav.appendChild(el("div", "muted space-top-sm", "Page " + page));
            pagination.appendChild(unknownNav);
        }
        pagination.style.display = "";
        pagination.querySelectorAll("a[data-page]").forEach(function (link) {
            link.addEventListener("click", function (event) {
                event.preventDefault();
                var nextPage = Number(link.getAttribute("data-page") || "1");
                runAjaxSearch(query, nextPage, true);
            });
        });
    }

    function buildPageItem(pageNumber, label, active) {
        var item = el("li", active ? "page-item active" : "page-item");
        if (active) {
            item.appendChild(el("span", "page-link", label));
            return item;
        }
        var link = el("a", "page-link", label);
        link.href = "#";
        link.dataset.page = String(pageNumber || 1);
        item.appendChild(link);
        return item;
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
        clearNode(supplierSelectedChips);
        if (!selectedSupplierIds.length) {
            return;
        }
        selectedSupplierIds.forEach(function (sid) {
            var match = supplierOptions.find(function (opt) { return opt.id === sid; });
            var label = match ? match.name : ("Supplier #" + sid);
            var chip = el("span", "supplier-selected-chip", label);
            var button = el("button", "", "x");
            button.type = "button";
            button.dataset.removeSupplierId = sid;
            button.setAttribute("aria-label", "Remove supplier");
            chip.appendChild(button);
            supplierSelectedChips.appendChild(chip);
        });
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
            clearNode(supplierSuggest);
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
        clearNode(supplierSuggest);
        matches.forEach(function (opt) {
            var item = el("div", "supplier-suggest-item", opt.name);
            item.dataset.id = opt.id;
            item.dataset.name = opt.name;
            supplierSuggest.appendChild(item);
        });
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

    function buildSparkline(values, deltaDir, deltaPercent) {
        var w = 200, h = 32, pad = 3;
        var color = deltaDir === "down" ? "#22c55e" : deltaDir === "up" ? "#ef4444" : "#c8c8c8";
        var dayCount = values && values.length ? values.length : 0;
        var changeText = deltaPercent ? String(deltaPercent) + "% change" : "no percentage change";
        var svg = svgEl("svg", {
            class: "product-sparkline",
            width: "100%",
            height: h,
            viewBox: "0 0 " + w + " " + h,
            preserveAspectRatio: "none",
            fill: "none",
            role: "img",
            "aria-label": "Price trend over last " + dayCount + " days, " + changeText
        });
        // No data or single point: flat grey line.
        if (!values || values.length < 2) {
            var mid = (h / 2).toFixed(1);
            svg.appendChild(svgEl("line", {
                x1: "0",
                y1: mid,
                x2: w,
                y2: mid,
                stroke: "#e2e2e2",
                "stroke-width": "1.5"
            }));
            return svg;
        }
        var min = Math.min.apply(null, values);
        var max = Math.max.apply(null, values);
        var range = max - min || 1;
        var pts = values.map(function (v, i) {
            var x = pad + (i / (values.length - 1)) * (w - pad * 2);
            var y = (h - pad) - ((v - min) / range) * (h - pad * 2);
            return x.toFixed(1) + "," + y.toFixed(1);
        }).join(" ");
        svg.appendChild(svgEl("polyline", {
            points: pts,
            stroke: color,
            "stroke-width": "1.5",
            "stroke-linecap": "round",
            "stroke-linejoin": "round"
        }));
        return svg;
    }

    function buildProductNameCell(nameText, detailUrl) {
        var name = String(nameText || "");
        if (!detailUrl) {
            return el("span", "cell-name", name);
        }
        var wrap = el("span", "cell-name-wrap");
        var button = el("button", "cell-name cell-name-copy", name);
        button.type = "button";
        button.dataset.copyProductName = name;
        button.setAttribute("aria-label", "Copy product name");
        wrap.appendChild(button);
        var link = el("a", "cell-name-open");
        link.dataset.productDetailLink = "";
        link.href = detailUrl;
        link.setAttribute("aria-label", "Open product page");
        link.title = "Open product page";
        var icon = svgEl("svg", {
            viewBox: "0 0 16 16",
            fill: "none",
            stroke: "currentColor",
            "stroke-width": "1.6",
            "stroke-linecap": "round",
            "stroke-linejoin": "round",
            "aria-hidden": "true"
        });
        [
            "M6 3H3.75A1.75 1.75 0 0 0 2 4.75v7.5C2 13.216 2.784 14 3.75 14h7.5A1.75 1.75 0 0 0 13 12.25V10",
            "M9 2h5v5",
            "M14 2 7.5 8.5"
        ].forEach(function (pathData) {
            icon.appendChild(svgEl("path", { d: pathData }));
        });
        link.appendChild(icon);
        wrap.appendChild(link);
        return wrap;
    }

    function ensureCopyToast() {
        var existing = document.getElementById("copy-feedback-toast");
        if (existing) return existing;
        var toast = document.createElement("div");
        toast.id = "copy-feedback-toast";
        toast.className = "copy-feedback-toast";
        toast.setAttribute("aria-live", "polite");
        toast.setAttribute("aria-atomic", "true");
        document.body.appendChild(toast);
        return toast;
    }

    function showCopyToast(message) {
        var toast = ensureCopyToast();
        toast.textContent = message;
        toast.classList.add("is-visible");
        if (toast._hideTimer) {
            window.clearTimeout(toast._hideTimer);
        }
        toast._hideTimer = window.setTimeout(function () {
            toast.classList.remove("is-visible");
            toast._hideTimer = null;
        }, 1400);
    }

    function copyTextToClipboard(text) {
        if (!text) {
            return Promise.reject(new Error("Missing text"));
        }
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        return new Promise(function (resolve, reject) {
            var textarea = document.createElement("textarea");
            textarea.value = text;
            textarea.setAttribute("readonly", "");
            textarea.style.position = "fixed";
            textarea.style.opacity = "0";
            textarea.style.pointerEvents = "none";
            document.body.appendChild(textarea);
            textarea.focus();
            textarea.select();
            try {
                if (document.execCommand("copy")) {
                    resolve();
                } else {
                    reject(new Error("Copy command failed"));
                }
            } catch (error) {
                reject(error);
            } finally {
                document.body.removeChild(textarea);
            }
        });
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
        clearNode(tbody);
        var inactiveDividerInserted = false;
        var renderedCount = 0;
        var hasActions = showActions && !!(editPattern || deletePattern);
        var colCount = bulkEnabled ? (hasActions ? 8 : 7) : (hasActions ? 7 : 6);
        items.forEach(function (item) {
            var supplier = String(item.supplier || "");
            var supplierId = String(item.supplier_id || "");
            var sku = String(item.supplier_sku || "");
            var rawName = String(item.name || "");
            var price = String(item.current_price || "");
            var originalPrice = String(item.original_price || "");
            var deltaDirection = item.price_delta_direction || "";
            var deltaValue = String(item.price_delta_value || "");
            var deltaPercent = String(item.price_delta_percent || "");
            var originalPriceText = originalPrice ? "Original: " + originalPrice : "";
            var priceStack = el("div", "desktop-price-stack");
            var priceMain = el("div", "cell-price-main", price);
            if (originalPriceText) {
                priceMain.title = originalPriceText;
                priceMain.dataset.originalPrice = originalPriceText;
            }
            priceStack.appendChild(priceMain);
            var deltaWrap = el("div", "price-delta-wrap");
            var deltaBadge = el("span", "price-delta-badge");
            if (deltaDirection && deltaValue) {
                var arrow = deltaDirection === "down" ? "↓" : "↑";
                var deltaTail = deltaPercent ? " (" + deltaPercent + ")" : "";
                deltaBadge.classList.add(deltaDirection);
                deltaBadge.appendChild(el("span", "visually-hidden", deltaDirection === "down" ? "Decreased" : "Increased"));
                deltaBadge.appendChild(document.createTextNode(arrow + " " + deltaValue + deltaTail));
            } else {
                deltaBadge.classList.add("neutral");
                deltaBadge.appendChild(el("span", "visually-hidden", "Unchanged"));
                deltaBadge.appendChild(document.createTextNode("- No change"));
            }
            deltaWrap.appendChild(deltaBadge);
            priceStack.appendChild(deltaWrap);

            var importedTitle = String(item.last_imported_at_full || "");
            var importedAgeClass = String(item.last_imported_age_class || "");
            var imported = el("span", "cell-imported", item.last_imported_at || "");
            if (importedAgeClass) {
                imported.classList.add(importedAgeClass);
            }
            if (importedTitle) {
                imported.title = importedTitle;
                imported.dataset.fullDatetime = importedTitle;
            }

            var mobileSupplier = supplierId ? el("a", "supplier-filter-link cell-supplier", supplier) : el("span", "cell-supplier", supplier);
            if (supplierId) {
                mobileSupplier.href = "?supplier=" + encodeURIComponent(String(item.supplier_id || ""));
                mobileSupplier.dataset.supplierId = supplierId;
            }

            var detailUrl = "";
            if (detailBase && item.id) {
                detailUrl = detailBase + item.id + "/?next=" + encodeURIComponent(nextValue) + "&from=" + item.id;
            }

            if (item.is_active === false && !inactiveDividerInserted) {
                var dividerRow = el("tr", "inactive-divider-row");
                var dividerCell = el("td", "", "Inactive products");
                dividerCell.colSpan = colCount;
                dividerRow.appendChild(dividerCell);
                tbody.appendChild(dividerRow);
                inactiveDividerInserted = true;
            }

            var row = el("tr", item.is_active ? "" : "inactive-product-row");
            row.dataset.productId = String(item.id || "");
            if (deltaDirection) {
                row.dataset.delta = deltaDirection;
            }
            if (bulkEnabled) {
                var checkboxCell = el("td", "select-col bulk-col");
                var checkbox = document.createElement("input");
                checkbox.type = "checkbox";
                checkbox.name = "product_ids";
                checkbox.value = String(item.id || "");
                checkboxCell.appendChild(checkbox);
                row.appendChild(checkboxCell);
            }
            row.appendChild(fieldCell("supplier_sku", "SKU", el("span", "cell-sku", sku)));
            row.appendChild(fieldCell("name", "Name", buildProductNameCell(rawName, detailUrl)));
            row.appendChild(fieldCell("current_price", "Price", priceStack));
            row.appendChild(fieldCell("supplier", "Supplier", mobileSupplier));
            row.appendChild(fieldCell("last_imported_at", "Last imported", imported));
            row.appendChild(fieldCell("sparkline", "", buildSparkline(item.sparkline, deltaDirection, deltaPercent), "sparkline-cell"));
            if (hasActions) {
                row.appendChild(buildActionsCell(item.id, editPattern, deletePattern, csrfValue, nextValue));
            }
            tbody.appendChild(row);
            renderedCount += 1;
        });

        if (!renderedCount) {
            var emptyRow = el("tr");
            var emptyCell = el("td", "muted", "No records yet.");
            emptyCell.colSpan = colCount;
            emptyRow.appendChild(emptyCell);
            tbody.appendChild(emptyRow);
        }
        decorateButtons(tbody);
        bindMobileDetailTooltips(tbody);
        applyLastViewedHighlight();
    }

    function fieldCell(field, label, child, className) {
        var cell = el("td", className || "");
        cell.dataset.field = field;
        if (label) {
            cell.dataset.label = label;
        }
        if (child) {
            cell.appendChild(child);
        }
        return cell;
    }

    function buildActionsCell(itemId, editPattern, deletePattern, csrfValue, nextValue) {
        var cell = el("td", "actions");
        cell.dataset.label = "Actions";
        cell.appendChild(buildActionMenu("actions-desktop layout-inline items-center gap-sm", "actions-menu-desktop", "More actions", "actions-menu-pop layout-row layout-column gap-sm", itemId, editPattern, deletePattern, csrfValue, nextValue));
        cell.appendChild(buildActionMenu("actions-mobile", "mobile-actions-menu", "Actions", "mobile-actions-pop layout-row layout-column gap-sm", itemId, editPattern, deletePattern, csrfValue, nextValue));
        return cell;
    }

    function buildActionMenu(wrapperClass, detailsClass, label, popClass, itemId, editPattern, deletePattern, csrfValue, nextValue) {
        var wrapper = el("div", wrapperClass);
        var details = el("details", detailsClass);
        var summary = el("summary", "", "...");
        summary.setAttribute("aria-label", label);
        details.appendChild(summary);
        var pop = el("div", popClass);
        var editUrl = editPattern ? editPattern.replace("/0/", "/" + itemId + "/") : "";
        var deleteUrl = deletePattern ? deletePattern.replace("/0/", "/" + itemId + "/") : "";
        if (editUrl) {
            var editLink = el("a", "button secondary", "Edit");
            editLink.href = editUrl;
            pop.appendChild(editLink);
        }
        if (deleteUrl) {
            pop.appendChild(buildDeleteForm(deleteUrl, csrfValue, nextValue));
        }
        details.appendChild(pop);
        wrapper.appendChild(details);
        return wrapper;
    }

    function buildDeleteForm(deleteUrl, csrfValue, nextValue) {
        var form = document.createElement("form");
        form.method = "post";
        form.action = deleteUrl;
        var csrf = document.createElement("input");
        csrf.type = "hidden";
        csrf.name = "csrfmiddlewaretoken";
        csrf.value = csrfValue || "";
        form.appendChild(csrf);
        var next = document.createElement("input");
        next.type = "hidden";
        next.name = "next";
        next.value = nextValue || "";
        form.appendChild(next);
        var button = el("button", "button danger", "Delete");
        button.type = "submit";
        form.appendChild(button);
        return form;
    }

    function bindTooltipGroup(container, selector, activeClass, groupName) {
        if (!container) return;
        var items = container.querySelectorAll(selector);
        items.forEach(function (item) {
            var boundKey = groupName + "TooltipBound";
            if (item.dataset[boundKey] === "1") return;
            item.dataset[boundKey] = "1";
            item.addEventListener("click", function (event) {
                event.preventDefault();
                items.forEach(function (otherItem) {
                    if (otherItem !== item) {
                        otherItem.classList.remove(activeClass);
                    }
                });
                item.classList.toggle(activeClass);
                if (item._tooltipHideTimer) {
                    window.clearTimeout(item._tooltipHideTimer);
                    item._tooltipHideTimer = null;
                }
                if (item.classList.contains(activeClass)) {
                    item._tooltipHideTimer = window.setTimeout(function () {
                        item.classList.remove(activeClass);
                        item._tooltipHideTimer = null;
                    }, 1800);
                }
            });
        });
    }

    function bindMobileDetailTooltips(container) {
        bindTooltipGroup(container, ".cell-imported[data-full-datetime]", "show-full-datetime", "datetime");
        bindTooltipGroup(container, ".cell-price-main[data-original-price]", "show-original-price", "originalPrice");
    }

    function runAjaxSearch(query, page, force) {
        if (!tbody) return;
        var safePage = page && Number(page) > 0 ? Number(page) : 1;
        var requestSignature = [
            query,
            currencyFilter ? currencyFilter.value : "",
            supplierFilter ? supplierFilter.value : "",
            getStatusFilterValue(),
            smartSearchSwitch && smartSearchSwitch.checked ? "1" : "",
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
        if (smartSearchSwitch && smartSearchSwitch.checked) {
            url += "&smart=1";
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
    if (smartSearchSwitch) {
        smartSearchSwitch.addEventListener("change", function () {
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
            var copyButton = event.target.closest("[data-copy-product-name]");
            if (copyButton) {
                event.preventDefault();
                copyTextToClipboard(copyButton.getAttribute("data-copy-product-name") || "")
                    .then(function () {
                        showCopyToast("Copied");
                    })
                    .catch(function () {
                        showCopyToast("Copy failed");
                    });
                return;
            }
            var detailLink = event.target.closest("a[data-product-detail-link]");
            if (detailLink) {
                var detailRow = event.target.closest("tr[data-product-id]");
                if (detailRow) {
                    var detailProductId = detailRow.getAttribute("data-product-id");
                    if (detailProductId) {
                        sessionStorage.setItem(lastViewedKey, detailProductId);
                    }
                }
                return;
            }
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
            var link = event.target.closest("a[data-product-detail-link]");
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
    document.addEventListener("scroll", updatePinnedSearchOnScroll, { passive: true, capture: true });
    window.addEventListener("resize", function () {
        updatePinnedSearchOnScroll();
        syncPinnedSearchGeometry();
    });
    applyLastViewedHighlight();
    bindMobileDetailTooltips(document);
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
