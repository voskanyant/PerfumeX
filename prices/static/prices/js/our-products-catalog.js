(function () {
    function setRowEditing(row, isEditing) {
        row.classList.toggle("is-editing", isEditing);
        var editor = row.querySelector("[data-row-editor]");
        if (editor) {
            editor.hidden = !isEditing;
        }
        row.querySelectorAll(".catalogue-inline-input").forEach(function (field) {
            field.disabled = !isEditing;
        });
        var button = row.querySelector("[data-row-edit-toggle]");
        if (button) {
            button.title = isEditing ? "Close editor" : "Edit row";
            button.setAttribute("aria-label", isEditing ? "Close editor" : "Edit row");
            button.setAttribute("aria-expanded", isEditing ? "true" : "false");
        }
        if (isEditing) {
            var firstField = row.querySelector(".catalogue-inline-input:not([type='hidden'])");
            if (firstField) {
                firstField.focus();
                if (typeof firstField.select === "function") {
                    firstField.select();
                }
            }
        }
    }

    function enableRowFields(row) {
        row.querySelectorAll(".catalogue-inline-input").forEach(function (field) {
            field.disabled = false;
        });
    }

    document.querySelectorAll("[data-catalogue-row]").forEach(function (row) {
        var form = row.querySelector(".catalogue-row-form");
        if (form) {
            form.addEventListener("submit", function () {
                enableRowFields(row);
            });
        }
        var button = row.querySelector("[data-row-edit-toggle]");
        if (!button) return;
        button.addEventListener("click", function (event) {
            event.preventDefault();
            setRowEditing(row, !row.classList.contains("is-editing"));
        });
        row.querySelectorAll("[data-row-edit-cancel]").forEach(function (cancelButton) {
            cancelButton.addEventListener("click", function () {
                if (form) form.reset();
                setRowEditing(row, false);
            });
        });
        row.querySelectorAll(".catalogue-inline-input").forEach(function (field) {
            field.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    event.preventDefault();
                    if (form) form.reset();
                    setRowEditing(row, false);
                }
                if (event.key === "Enter" && field.tagName !== "SELECT") {
                    event.preventDefault();
                    enableRowFields(row);
                    if (form) form.requestSubmit();
                }
            });
        });
    });
})();
