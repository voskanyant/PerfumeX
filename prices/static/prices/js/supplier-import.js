(function () {
    var config = document.getElementById("supplier-import-config");
    var fileInput = document.getElementById("id_file");
    var sheetRow = document.getElementById("sheet-row");
    var sheetSelect = document.getElementById("sheet-select");
    var preview = document.getElementById("preview-table");
    var mode = "sku";
    var supplierId = config ? String(config.getAttribute("data-supplier-id") || "") : "";
    var modeStatus = document.getElementById("mode-status");

    if (!fileInput || !supplierId) return;

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }

    function getCookie(name) {
        var value = "; " + document.cookie;
        var parts = value.split("; " + name + "=");
        if (parts.length === 2) {
            return decodeURIComponent(parts.pop().split(";").shift());
        }
        return "";
    }

    function setMode(next) {
        mode = next;
        if (modeStatus) {
            modeStatus.textContent = "Mode: " + next.replace("_", " ").toUpperCase();
        }
        document.querySelectorAll("[data-mode-button]").forEach(function (button) {
            button.classList.toggle("is-active", button.id === "mode-" + next.replace("_", "-"));
        });
    }
    document.getElementById("mode-sku").addEventListener("click", function () { setMode("sku"); });
    document.getElementById("mode-name").addEventListener("click", function () { setMode("name"); });
    document.getElementById("mode-name-add").addEventListener("click", function () { setMode("name_add"); });
    document.getElementById("mode-price").addEventListener("click", function () { setMode("price"); });
    document.getElementById("mode-currency").addEventListener("click", function () { setMode("currency"); });

    function updateField(fieldId, value, append) {
        var input = document.getElementById(fieldId);
        if (!input) return;
        if (!append) {
            input.value = value;
            return;
        }
        var existing = input.value ? input.value.split(",").map(function (v) { return v.trim(); }) : [];
        if (existing.indexOf(value) === -1) {
            existing.push(value);
        }
        input.value = existing.join(",");
    }

    function selectedColumns() {
        var ids = ["id_sku_column", "id_price_column", "id_currency_column", "id_name_columns"];
        var selected = [];
        ids.forEach(function (id) {
            var input = document.getElementById(id);
            if (!input || !input.value) return;
            input.value.split(",").forEach(function (value) {
                var cleaned = value.trim();
                if (cleaned && selected.indexOf(cleaned) === -1) {
                    selected.push(cleaned);
                }
            });
        });
        return selected;
    }

    function renderTable(rows, maxCols, colOffset) {
        if (!rows.length) {
            preview.innerHTML = "<div class='supplier-preview-empty'><span class='card-label'>No rows</span><p>No preview rows were detected in this sheet.</p></div>";
            return;
        }
        var selected = selectedColumns();
        var header = "<tr>";
        for (var i = 1; i <= maxCols; i++) {
            var displayIndex = i + (colOffset || 0);
            var selectedClass = selected.indexOf(String(i)) !== -1 ? " is-selected" : "";
            header += "<th data-col='" + i + "' class='preview-th" + selectedClass + "'>" + displayIndex + "</th>";
        }
        header += "</tr>";
        var body = rows.map(function (row) {
            var cells = "";
            for (var i = 0; i < maxCols; i++) {
                var val = row[i] || "";
                cells += "<td class='preview-td' title='" + escapeHtml(val) + "'>" + escapeHtml(val) + "</td>";
            }
            return "<tr>" + cells + "</tr>";
        }).join("");
        preview.innerHTML = "<table class='data-table import-preview-table space-bottom-none'>" + header + body + "</table>";
        preview.querySelectorAll("th[data-col]").forEach(function (th) {
            th.addEventListener("click", function () {
                var col = th.getAttribute("data-col");
                if (mode === "sku") updateField("id_sku_column", col, false);
                if (mode === "name") updateField("id_name_columns", col, false);
                if (mode === "name_add") updateField("id_name_columns", col, true);
                if (mode === "price") updateField("id_price_column", col, false);
                if (mode === "currency") updateField("id_currency_column", col, false);
                renderTable(rows, maxCols, colOffset);
            });
        });
    }

    function loadPreview(sheetIndex) {
        if (!fileInput.files.length) return;
        preview.innerHTML = "<div class='supplier-preview-empty'><span class='card-label'>Loading</span><p>Reading spreadsheet preview...</p></div>";
        var formData = new FormData();
        formData.append("file", fileInput.files[0]);
        if (sheetIndex !== null && sheetIndex !== undefined) {
            formData.append("sheet_index", sheetIndex);
        }
        var url = "/suppliers/" + supplierId + "/mapping-preview/";
        fetch(url, {
            method: "POST",
            headers: {
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRFToken": getCookie("csrftoken")
            },
            body: formData
        }).then(function (res) { return res.json(); })
          .then(function (data) {
              if (data.error) {
                  preview.innerHTML = "<div class='supplier-preview-empty'><span class='card-label'>Preview error</span><p>" + escapeHtml(data.error) + "</p></div>";
                  return;
              }
              if (data.sheet_names && data.sheet_names.length) {
                  if (sheetRow) {
                      sheetRow.classList.remove("is-hidden");
                  }
                  sheetSelect.innerHTML = data.sheet_names.map(function (name, index) {
                      return "<option value='" + index + "'>" + name + "</option>";
                  }).join("");
              }
              renderTable(data.rows || [], data.max_cols || 0, data.col_offset || 0);
          }).catch(function () {
              preview.innerHTML = "<div class='supplier-preview-empty'><span class='card-label'>Preview error</span><p>Could not load the mapping preview.</p></div>";
          });
    }

    if (fileInput) {
        fileInput.addEventListener("change", function () {
            loadPreview(sheetSelect.value || null);
        });
    }
    if (sheetSelect) {
        sheetSelect.addEventListener("change", function () {
            loadPreview(sheetSelect.value);
        });
    }
})();


