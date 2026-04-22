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
        } catch (err) {
            // Ignore and fall back.
        }
        return text
            .replace(/[^A-Za-z0-9\u0400-\u04FF]+/g, " ")
            .trim()
            .split(/\s+/)
            .filter(Boolean);
    }

    function renderKeywords(name) {
        keywordPanel.innerHTML = "";
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
            wrapper.className = "col-6";
            var label = document.createElement("label");
            label.className = "d-flex align-items-center gap-2";
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
        if (score >= 92) return "bg-success-subtle text-success-emphasis";
        if (score >= 80) return "bg-primary-subtle text-primary-emphasis";
        if (score >= 65) return "bg-warning-subtle text-warning-emphasis";
        return "bg-secondary-subtle text-secondary-emphasis";
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
                ourResults.innerHTML = "";
                supplierResults.innerHTML = "";
                if (autoSuggestHint) {
                    autoSuggestHint.textContent = autoMode ? "Auto ranked by name/brand/size." : "";
                }
                if (!data.our_products.length) {
                    ourResults.innerHTML = "<tr><td colspan='3' class='muted'>No matches yet.</td></tr>";
                } else {
                    data.our_products.forEach(function (item) {
                        var row = document.createElement("tr");
                        var label = item.name;
                        if (item.brand) label += " | " + item.brand;
                        if (item.size) label += " | " + item.size;
                        row.innerHTML = "<td data-label='Select'><input type='checkbox' data-target-select='our:" + item.id + "'></td>" +
                            "<td data-label='Our products'>" + label + "<div class='small muted'>" + (item.reason || "") + "</div></td>" +
                            "<td data-label='Score'><span class='badge " + scoreBadge(item.score || 0) + "'>" + (item.score || 0) + "%</span></td>";
                        row.querySelector("input").addEventListener("change", function () {
                            setTarget("our", item.id);
                        });
                        ourResults.appendChild(row);
                    });
                }
                if (!data.supplier_products.length) {
                    supplierResults.innerHTML = "<tr><td colspan='3' class='muted'>No matches yet.</td></tr>";
                } else {
                    data.supplier_products.forEach(function (item) {
                        var row = document.createElement("tr");
                        row.innerHTML = "<td data-label='Select'><input type='checkbox' data-target-select='supplier:" + item.id + "'></td>" +
                            "<td data-label='Supplier products'>" + item.name + "<div class='small muted'>" + (item.supplier || "") + " " + (item.sku || "") + " | " + (item.reason || "") + "</div></td>" +
                            "<td data-label='Score'><span class='badge " + scoreBadge(item.score || 0) + "'>" + (item.score || 0) + "%</span></td>";
                        row.querySelector("input").addEventListener("change", function () {
                            setTarget("supplier", item.id);
                        });
                        supplierResults.appendChild(row);
                    });
                }
            });
    }

    document.querySelectorAll("#supplier-products-table tbody tr").forEach(function (row) {
        row.addEventListener("click", function () {
            if (selectedRow) selectedRow.classList.remove("table-active");
            selectedRow = row;
            row.classList.add("table-active");
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
