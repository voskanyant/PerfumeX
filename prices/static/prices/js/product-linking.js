(function () {
    var selectedRow = null;
    var sourceInput = document.getElementById("link-source-id");
    var linkSelection = document.getElementById("link-selection");
    var keywordPanel = document.getElementById("keyword-panel");
    var ourResults = document.getElementById("our-product-results");
    var supplierResults = document.getElementById("supplier-product-results");
    var targetOur = document.getElementById("link-target-our");
    var targetSupplier = document.getElementById("link-target-supplier");
    var autoSuggestBtn = document.getElementById("auto-suggest-btn");
    var autoSuggestHint = document.getElementById("auto-suggest-hint");

    function clearNode(node) {
        if (!node) return;
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function setTarget(type, id) {
        targetOur.value = "";
        targetSupplier.value = "";
        if (type === "our") {
            targetOur.value = id;
        } else {
            targetSupplier.value = id;
        }
        document.querySelectorAll("[data-target-select]").forEach(function (el) {
            el.checked = false;
        });
        var current = document.querySelector("[data-target-select='" + type + ":" + id + "']");
        if (current) current.checked = true;
    }

    function tokenize(text) {
        if (!text) return [];
        if (window.Intl && Intl.Segmenter) {
            var segmenter = new Intl.Segmenter("en", { granularity: "word" });
            return Array.from(segmenter.segment(text))
                .filter(function (part) { return part.isWordLike; })
                .map(function (part) { return part.segment; });
        }
        try {
            var unicodeTokens = text.match(/[\p{L}\p{N}]+/gu);
            if (unicodeTokens) return unicodeTokens;
        } catch {
            // Ignore and fall back.
        }
        return text
            .replace(/[^A-Za-z0-9\u0400-\u04FF]+/g, " ")
            .trim()
            .split(/\s+/)
            .filter(Boolean);
    }

    function renderKeywords(name) {
        clearNode(keywordPanel);
        if (!name) return;
        var tokens = tokenize(name);
        var seen = new Set();
        tokens.filter(function (token) {
            var key = token.toLowerCase();
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        }).slice(0, 18).forEach(function (token) {
            var id = "kw-" + token.replace(/[^a-z0-9]/gi, "").toLowerCase();
            var wrapper = document.createElement("div");
            wrapper.className = "span-12 md-span-6";
            var label = document.createElement("label");
            label.className = "layout-row items-center gap-sm";
            var input = document.createElement("input");
            input.type = "checkbox";
            input.value = token;
            input.checked = true;
            input.id = id;
            input.addEventListener("change", searchMatches);
            label.appendChild(input);
            label.appendChild(document.createTextNode(token));
            wrapper.appendChild(label);
            keywordPanel.appendChild(wrapper);
        });
    }

    function buildQuery() {
        return Array.from(keywordPanel.querySelectorAll("input[type=checkbox]"))
            .filter(function (input) { return input.checked; })
            .map(function (input) { return input.value; })
            .join(" ");
    }

    function scoreBadge(score) {
        if (score >= 92) return "score-badge score-badge--success";
        if (score >= 80) return "score-badge score-badge--strong";
        if (score >= 65) return "score-badge score-badge--warning";
        return "score-badge score-badge--muted";
    }

    function renderEmptyRow(target, message) {
        var row = document.createElement("tr");
        var cell = document.createElement("td");
        cell.colSpan = 3;
        cell.className = "table-empty-cell";
        cell.textContent = message;
        row.appendChild(cell);
        target.appendChild(row);
    }

    function appendTextWithMeta(cell, label, meta) {
        cell.appendChild(document.createTextNode(label || ""));
        if (meta) {
            var detail = document.createElement("div");
            detail.className = "text-small tone-muted";
            detail.textContent = meta;
            cell.appendChild(detail);
        }
    }

    function renderMatchRow(target, type, item, label, metaLabel, meta) {
        var row = document.createElement("tr");
        var selectCell = document.createElement("td");
        selectCell.setAttribute("data-label", "Select");
        var input = document.createElement("input");
        input.type = "checkbox";
        input.setAttribute("data-target-select", type + ":" + item.id);
        input.setAttribute("aria-label", "Select " + metaLabel);
        input.addEventListener("change", function () {
            setTarget(type, item.id);
        });
        selectCell.appendChild(input);

        var nameCell = document.createElement("td");
        nameCell.setAttribute("data-label", metaLabel);
        appendTextWithMeta(nameCell, label, meta || "");

        var scoreCell = document.createElement("td");
        scoreCell.setAttribute("data-label", "Score");
        var badge = document.createElement("span");
        badge.className = scoreBadge(item.score || 0);
        badge.textContent = (item.score || 0) + "%";
        scoreCell.appendChild(badge);

        row.appendChild(selectCell);
        row.appendChild(nameCell);
        row.appendChild(scoreCell);
        target.appendChild(row);
    }

    function searchMatches(autoMode) {
        var query = buildQuery();
        if (!query && !autoMode) return;
        if (!sourceInput.value) return;
        var url = "/linking/search/?supplier_product=" + encodeURIComponent(sourceInput.value);
        if (query) {
            url += "&terms=" + encodeURIComponent(query);
        }
        if (autoMode) {
            url += "&auto=1";
        }
        fetch(url)
            .then(function (res) { return res.json(); })
            .then(function (data) {
                clearNode(ourResults);
                clearNode(supplierResults);
                if (autoSuggestHint) {
                    autoSuggestHint.textContent = autoMode ? "Auto ranked by name/brand/size." : "";
                }
                if (!data.our_products.length) {
                    renderEmptyRow(ourResults, "No matches yet.");
                } else {
                    data.our_products.forEach(function (item) {
                        var label = item.name;
                        if (item.brand) label += " | " + item.brand;
                        if (item.size) label += " | " + item.size;
                        renderMatchRow(ourResults, "our", item, label, "Our products", item.reason || "");
                    });
                }
                if (!data.supplier_products.length) {
                    renderEmptyRow(supplierResults, "No matches yet.");
                } else {
                    data.supplier_products.forEach(function (item) {
                        var meta = [item.supplier || "", item.sku || ""].filter(Boolean).join(" ");
                        if (item.reason) {
                            meta = meta ? meta + " | " + item.reason : item.reason;
                        }
                        renderMatchRow(supplierResults, "supplier", item, item.name, "Supplier products", meta);
                    });
                }
            });
    }

    document.querySelectorAll("#supplier-products-table tbody tr.row-selectable").forEach(function (row) {
        row.addEventListener("click", function () {
            if (selectedRow) selectedRow.classList.remove("is-selected-row");
            selectedRow = row;
            row.classList.add("is-selected-row");
            var id = row.getAttribute("data-id");
            var name = row.getAttribute("data-name");
            sourceInput.value = id;
            linkSelection.textContent = "Selected: " + name;
            renderKeywords(name);
            searchMatches(true);
        });
    });

    if (autoSuggestBtn) {
        autoSuggestBtn.addEventListener("click", function () {
            searchMatches(true);
        });
    }

    document.getElementById("link-filter-apply").addEventListener("click", function () {
        var supplier = document.getElementById("link-supplier-filter").value;
        var q = document.getElementById("link-search").value;
        var url = "?";
        if (supplier) url += "supplier=" + supplier + "&";
        if (q) url += "q=" + encodeURIComponent(q);
        window.location.href = url;
    });
})();
