(function () {
    function isEditableTarget(target) {
        if (!target) return false;
        return Boolean(target.closest("input:not([type='checkbox']):not([type='radio']), textarea, select, [contenteditable='true']"));
    }

    function rowsForRoot(root) {
        return Array.from(root.querySelectorAll("[data-catalogue-selectable-row]"));
    }

    function checkboxForRow(row) {
        return row.querySelector("[data-catalogue-select-checkbox]");
    }

    function isRowSelected(row) {
        var checkbox = checkboxForRow(row);
        return checkbox ? checkbox.checked : row.classList.contains("is-bulk-selected");
    }

    function canSelectRow(row) {
        var checkbox = checkboxForRow(row);
        return !checkbox || !checkbox.disabled;
    }

    function setRowSelected(row, selected) {
        var checkbox = checkboxForRow(row);
        if (checkbox) {
            checkbox.checked = selected;
        }
        row.classList.toggle("is-bulk-selected", selected);
        row.setAttribute("aria-selected", selected ? "true" : "false");
    }

    function selectedRows(root) {
        return rowsForRoot(root).filter(isRowSelected);
    }

    function actionRows(root, action) {
        var rows = selectedRows(root);
        if (action.getAttribute("data-catalogue-selected-source") === "link-pair") {
            return rows.filter(function (row) {
                return Boolean(row.getAttribute("data-catalogue-link-pair"));
            });
        }
        return rows;
    }

    function selectedValueForRow(row, action) {
        if (action.getAttribute("data-catalogue-selected-source") === "link-pair") {
            return row.getAttribute("data-catalogue-link-pair") || "";
        }
        return row.getAttribute("data-catalogue-row-id") || "";
    }

    function prepareSelectionSubmit(button) {
        if (!button || button.disabled) return false;
        var form = button.form || button.closest("form");
        if (!form) return false;
        var root = button.closest("[data-catalogue-selection-root]") || document;
        var actionValue = button.getAttribute("data-catalogue-action-value");
        var selectedName = button.getAttribute("data-catalogue-selected-name");
        if (actionValue) {
            var actionInput = form.querySelector("input[name='action']");
            if (actionInput) {
                actionInput.value = actionValue;
            }
        }
        if (!selectedName) return true;
        form.querySelectorAll("[data-catalogue-generated-selection-input]").forEach(function (input) {
            input.remove();
        });
        var rows = actionRows(root, button);
        rows.forEach(function (row) {
            var value = selectedValueForRow(row, button);
            if (!value) return;
            var input = document.createElement("input");
            input.type = "hidden";
            input.name = selectedName;
            input.value = value;
            input.setAttribute("data-catalogue-generated-selection-input", "1");
            form.appendChild(input);
        });
        return rows.length > 0;
    }

    function updateCheckAll(root) {
        var checkAll = root.querySelector("[data-catalogue-check-all]");
        if (!checkAll) return;
        var selectableRows = rowsForRoot(root).filter(canSelectRow);
        var selectedCount = selectableRows.filter(isRowSelected).length;
        checkAll.checked = selectableRows.length > 0 && selectedCount === selectableRows.length;
        checkAll.indeterminate = selectedCount > 0 && selectedCount < selectableRows.length;
    }

    function updateStatus(root) {
        var count = selectedRows(root).length;
        root.querySelectorAll("[data-catalogue-selected-count]").forEach(function (node) {
            node.textContent = String(count);
        });
        root.querySelectorAll("[data-catalogue-selection-bar]").forEach(function (bar) {
            bar.hidden = count === 0;
        });
        root.querySelectorAll("[data-catalogue-selection-action]").forEach(function (action) {
            action.disabled = actionRows(root, action).length === 0;
        });
        updateCheckAll(root);
    }

    function dispatchSelection(root, row, originalEvent) {
        root.dispatchEvent(new window.CustomEvent("catalogue-selection:row-selected", {
            bubbles: true,
            detail: {
                row: row,
                originalEvent: originalEvent,
                selectedRows: selectedRows(root)
            }
        }));
    }

    function clearSelection(root) {
        rowsForRoot(root).forEach(function (row) {
            setRowSelected(row, false);
        });
        root.dataset.selectionAnchor = "";
        updateStatus(root);
    }

    function selectRange(root, row, additive) {
        var rows = rowsForRoot(root);
        var targetIndex = rows.indexOf(row);
        var anchorIndex = Number(root.dataset.selectionAnchor || targetIndex);
        if (!Number.isFinite(anchorIndex) || anchorIndex < 0 || anchorIndex >= rows.length) {
            anchorIndex = targetIndex;
        }
        if (!additive) {
            rows.forEach(function (item) {
                setRowSelected(item, false);
            });
        }
        var start = Math.min(anchorIndex, targetIndex);
        var end = Math.max(anchorIndex, targetIndex);
        rows.slice(start, end + 1).forEach(function (item) {
            if (canSelectRow(item)) {
                setRowSelected(item, true);
            }
        });
    }

    function handleRowSelection(root, row, event) {
        if (!row || !canSelectRow(row)) return;
        var rows = rowsForRoot(root);
        if (event && event.shiftKey) {
            selectRange(root, row, Boolean(event.ctrlKey || event.metaKey));
        } else if (event && (event.ctrlKey || event.metaKey)) {
            setRowSelected(row, !isRowSelected(row));
            root.dataset.selectionAnchor = String(rows.indexOf(row));
        } else {
            rows.forEach(function (item) {
                setRowSelected(item, item === row);
            });
            root.dataset.selectionAnchor = String(rows.indexOf(row));
        }
        updateStatus(root);
        dispatchSelection(root, row, event || null);
    }

    function submitWithButton(button) {
        if (!button || button.disabled) return;
        if (!prepareSelectionSubmit(button)) return;
        var form = button.form || button.closest("form");
        if (!form) return;
        if (typeof form.requestSubmit === "function") {
            form.requestSubmit(button);
        } else {
            button.click();
        }
    }

    function initRoot(root) {
        rowsForRoot(root).forEach(function (row) {
            row.setAttribute("aria-selected", isRowSelected(row) ? "true" : "false");
            row.querySelectorAll("[data-catalogue-select-toggle]").forEach(function (toggle) {
                toggle.addEventListener("click", function (event) {
                    event.preventDefault();
                    handleRowSelection(root, row, event);
                });
            });
            var checkbox = checkboxForRow(row);
            if (checkbox) {
                checkbox.addEventListener("click", function (event) {
                    event.preventDefault();
                    event.stopPropagation();
                    handleRowSelection(root, row, event);
                });
            }
        });

        root.querySelectorAll("[data-catalogue-clear-selection]").forEach(function (button) {
            button.addEventListener("click", function () {
                clearSelection(root);
            });
        });

        root.querySelectorAll("[data-catalogue-check-all]").forEach(function (checkAll) {
            checkAll.addEventListener("change", function () {
                rowsForRoot(root).forEach(function (row) {
                    if (canSelectRow(row)) {
                        setRowSelected(row, checkAll.checked);
                    }
                });
                root.dataset.selectionAnchor = "";
                updateStatus(root);
            });
        });

        root.querySelectorAll("[data-catalogue-selection-action]").forEach(function (button) {
            button.addEventListener("click", function (event) {
                if (!prepareSelectionSubmit(button)) {
                    event.preventDefault();
                }
            });
        });

        updateStatus(root);
    }

    document.querySelectorAll("[data-catalogue-selection-root]").forEach(initRoot);

    document.addEventListener("keydown", function (event) {
        var roots = Array.from(document.querySelectorAll("[data-catalogue-selection-root]"));
        if (!roots.length || isEditableTarget(event.target)) return;
        var root = roots.find(function (candidate) {
            return candidate.querySelector("[data-catalogue-selectable-row]");
        });
        if (!root) return;

        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") {
            event.preventDefault();
            rowsForRoot(root).forEach(function (row) {
                if (canSelectRow(row)) {
                    setRowSelected(row, true);
                }
            });
            updateStatus(root);
            return;
        }

        if (event.key === "Escape") {
            clearSelection(root);
            return;
        }

        if ((event.key === "Delete" || event.key === "Backspace") && selectedRows(root).length) {
            var deleteButton = root.querySelector("[data-catalogue-bulk-delete]");
            if (deleteButton) {
                event.preventDefault();
                submitWithButton(deleteButton);
            }
            return;
        }

        if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && selectedRows(root).length) {
            var primaryButton = root.querySelector("[data-catalogue-bulk-primary]");
            if (primaryButton) {
                event.preventDefault();
                submitWithButton(primaryButton);
            }
        }
    });
})();
