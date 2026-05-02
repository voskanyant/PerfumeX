(function () {
    function setRowEditing(row, isEditing) {
        row.classList.toggle("is-editing", isEditing);
        row.querySelectorAll("[data-editable-cell]").forEach(function (cell) {
            cell.classList.remove("is-field-open");
        });
        var button = row.querySelector("[data-row-edit-toggle]");
        if (button) {
            button.title = isEditing ? "Save row" : "Edit row";
            button.setAttribute("aria-label", isEditing ? "Save row" : "Edit row");
        }
    }

    function openCellField(cell) {
        var row = cell.closest("[data-catalogue-row]");
        if (!row) return;
        if (!row.classList.contains("is-editing")) {
            setRowEditing(row, true);
        }
        var field = cell.querySelector(".catalogue-inline-input:not([type='hidden'])");
        if (!field) return;
        row.querySelectorAll("[data-editable-cell].is-field-open").forEach(function (openCell) {
            if (openCell !== cell) {
                openCell.classList.remove("is-field-open");
            }
        });
        field.disabled = false;
        cell.classList.add("is-field-open");
        field.focus();
        if (typeof field.select === "function") {
            field.select();
        }
    }

    function enableRowFields(row) {
        row.querySelectorAll(".catalogue-inline-input").forEach(function (field) {
            field.disabled = false;
        });
    }

    document.querySelectorAll("[data-catalogue-row]").forEach(function (row) {
        var form = row.querySelector(".catalogue-row-form");
        row.querySelectorAll("[data-editable-cell]").forEach(function (cell) {
            cell.addEventListener("click", function () {
                if (!window.matchMedia("(max-width: 767px)").matches) return;
                openCellField(cell);
            });
            cell.addEventListener("dblclick", function (event) {
                event.preventDefault();
                openCellField(cell);
            });
            cell.querySelectorAll(".catalogue-inline-input").forEach(function (field) {
                field.addEventListener("keydown", function (event) {
                    if (event.key === "Escape") {
                        event.preventDefault();
                        cell.classList.remove("is-field-open");
                        field.blur();
                    }
                    if (event.key === "Enter" && field.tagName !== "SELECT") {
                        event.preventDefault();
                        enableRowFields(row);
                        if (form) form.requestSubmit();
                    }
                });
            });
        });
        if (form) {
            form.addEventListener("submit", function () {
                enableRowFields(row);
            });
        }
        var button = row.querySelector("[data-row-edit-toggle]");
        if (!button) return;
        button.addEventListener("click", function (event) {
            if (!row.classList.contains("is-editing")) {
                event.preventDefault();
                setRowEditing(row, true);
                return;
            }
            enableRowFields(row);
        });
    });
})();
