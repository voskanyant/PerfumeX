(function () {
    function clearNode(node) {
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function linkedSourceText(source) {
        var parts = [
            "Linked Fragrantica: " + source.brand,
            source.name,
            source.collection,
            source.release_year ? String(source.release_year) : "",
            source.audience
        ].filter(Boolean);
        return parts.join(" / ");
    }

    function setSubmitterBusy(button, busy) {
        if (!button) return;
        if (busy) {
            button.dataset.originalText = button.textContent || "";
            button.disabled = true;
            button.textContent = "Linking...";
            return;
        }
        button.disabled = false;
        button.textContent = button.dataset.originalText || "Link";
    }

    function updateRowActionLink(row, source) {
        var actions = row.querySelector(".our-products-card-main > .layout-inline");
        if (!actions || !source) return;
        var link = actions.querySelector("a.button");
        if (!link) return;
        link.className = "button secondary";
        link.href = source.review_url || link.href;
        link.textContent = "Linked";
    }

    function markRowLinked(row, data) {
        if (!data || !data.linked_source) return;
        row.classList.add("is-linked");
        var body = row.querySelector(".our-products-card-body");
        if (!body) return;
        var matchPanel = body.querySelector(".fragrantica-match-panel");
        if (matchPanel) {
            matchPanel.remove();
        }
        var linkedInline = body.querySelector(".fragrantica-linked-inline");
        if (!linkedInline) {
            linkedInline = document.createElement("div");
            linkedInline.className = "fragrantica-linked-inline";
            linkedInline.setAttribute("aria-label", "Linked Fragrantica");
            body.appendChild(linkedInline);
        }
        clearNode(linkedInline);
        var text = document.createElement("span");
        text.textContent = linkedSourceText(data.linked_source);
        linkedInline.appendChild(text);
        updateRowActionLink(row, data.linked_source);
    }

    function submitFragranticaLink(row, form, submitter) {
        var action = (submitter && submitter.formAction) || form.action;
        if (!action) return;
        var formData = new FormData(form);
        if (submitter && submitter.name) {
            formData.append(submitter.name, submitter.value || "");
        }
        setSubmitterBusy(submitter, true);
        fetch(action, {
            method: "POST",
            body: formData,
            headers: { "X-Requested-With": "XMLHttpRequest" }
        })
            .then(function (response) {
                return response.json().then(function (data) {
                    if (!response.ok || !data.ok) throw data;
                    return data;
                });
            })
            .then(function (data) {
                markRowLinked(row, data);
            })
            .catch(function (data) {
                window.alert((data && data.message) || "Link failed. Reload and try again.");
            })
            .finally(function () {
                delete form.dataset.noSubmitDisable;
                setSubmitterBusy(submitter, false);
            });
    }

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
            button.title = isEditing ? "Save row" : "Edit row";
            button.setAttribute("aria-label", isEditing ? "Save row" : "Edit row");
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
            form.addEventListener("submit", function (event) {
                var submitter = event.submitter;
                if (submitter && submitter.matches("[data-fragrantica-link-submit]")) {
                    event.preventDefault();
                    event.stopPropagation();
                    form.dataset.noSubmitDisable = "1";
                    submitFragranticaLink(row, form, submitter);
                    return;
                }
                enableRowFields(row);
            });
        }
        var button = row.querySelector("[data-row-edit-toggle]");
        if (!button) return;
        button.addEventListener("click", function (event) {
            event.preventDefault();
            if (row.classList.contains("is-editing")) {
                enableRowFields(row);
                if (form) form.requestSubmit();
                return;
            }
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
