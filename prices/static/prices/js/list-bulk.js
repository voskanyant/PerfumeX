(function () {
    var selectAll = document.getElementById("product-select-all");
    var bulkButton = document.querySelector("[data-bulk-delete-button]");
    var bulkForm = document.getElementById("bulk-delete-form");

    if (selectAll) {
        selectAll.addEventListener("change", function () {
            var checked = selectAll.checked;
            document.querySelectorAll("input[name='product_ids']").forEach(function (box) {
                box.checked = checked;
            });
        });
    }

    if (bulkButton && bulkForm) {
        bulkButton.addEventListener("click", function () {
            bulkForm.querySelectorAll("input[name='product_ids']").forEach(function (el) {
                el.remove();
            });
            var selected = Array.from(document.querySelectorAll("input[name='product_ids']:checked"));
            if (!selected.length) return;
            if (!window.confirm(bulkButton.getAttribute("data-confirm") || "Delete selected records?")) return;
            selected.forEach(function (box) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "product_ids";
                input.value = box.value;
                bulkForm.appendChild(input);
            });
            bulkForm.submit();
        });
    }

    var root = document.getElementById("supplier-bulk-root");
    var toggleBtn = document.getElementById("bulk-mode-toggle");
    var cancelBtn = document.getElementById("bulk-mode-cancel");
    var bulkChangeBtn = document.getElementById("bulk-change-btn");
    if (!root || !toggleBtn) return;

    function setBulkMode(enabled) {
        root.classList.toggle("bulk-mode-on", enabled);
        root.classList.toggle("bulk-mode-off", !enabled);
        if (!enabled) {
            if (selectAll) {
                selectAll.checked = false;
            }
            document.querySelectorAll("input[name='product_ids']").forEach(function (box) {
                box.checked = false;
            });
        }
    }

    toggleBtn.addEventListener("click", function () {
        setBulkMode(!root.classList.contains("bulk-mode-on"));
    });

    if (bulkChangeBtn) {
        bulkChangeBtn.addEventListener("click", function () {
            setBulkMode(true);
        });
    }

    if (cancelBtn) {
        cancelBtn.addEventListener("click", function () {
            setBulkMode(false);
        });
    }
})();
