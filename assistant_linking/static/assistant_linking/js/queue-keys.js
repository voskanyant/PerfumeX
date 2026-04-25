(function () {
    var rows = Array.from(document.querySelectorAll("[data-queue-row]"));
    if (!rows.length) return;

    var currentIndex = 0;
    var helpDialog = document.querySelector("[data-shortcut-dialog]");
    var helpOpen = document.querySelector("[data-shortcut-help]");
    var undoButton = document.querySelector("[data-undo-link-action]");
    var focusableSelector = "a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex='-1'])";
    var savedFocus = null;

    function isTypingTarget(target) {
        return !!target.closest("input,textarea,select,[contenteditable='true']");
    }

    function focusRow(index) {
        if (!rows.length) return;
        currentIndex = Math.max(0, Math.min(index, rows.length - 1));
        rows[currentIndex].focus();
    }

    function currentRow() {
        return rows[currentIndex] || document.activeElement.closest("[data-queue-row]");
    }

    function activate(selector) {
        var row = currentRow();
        var target = row ? row.querySelector(selector) : null;
        if (!target && selector === "[data-queue-reject]" && row) {
            var checkbox = row.querySelector("input[type='checkbox']");
            if (checkbox && !checkbox.checked) checkbox.click();
            target = document.querySelector("[data-queue-reject]");
        }
        if (target) target.click();
    }

    function openHelp() {
        if (!helpDialog) return;
        savedFocus = document.activeElement;
        helpDialog.showModal();
        var focusable = Array.from(helpDialog.querySelectorAll(focusableSelector));
        (focusable[0] || helpDialog).focus();
    }

    function closeHelp() {
        if (!helpDialog || !helpDialog.open) return;
        helpDialog.close();
        if (savedFocus && document.contains(savedFocus)) savedFocus.focus();
        savedFocus = null;
    }

    function trapHelpFocus(event) {
        if (!helpDialog || !helpDialog.open || event.key !== "Tab") return;
        var focusable = Array.from(helpDialog.querySelectorAll(focusableSelector));
        if (!focusable.length) {
            event.preventDefault();
            helpDialog.focus();
            return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    rows.forEach(function (row, index) {
        row.tabIndex = 0;
        row.addEventListener("focus", function () {
            currentIndex = index;
        });
    });

    if (helpOpen) helpOpen.addEventListener("click", openHelp);
    if (helpDialog) {
        helpDialog.querySelectorAll("[data-dialog-close]").forEach(function (button) {
            button.addEventListener("click", closeHelp);
        });
    }

    document.addEventListener("keydown", function (event) {
        if (helpDialog && helpDialog.open) {
            if (event.key === "Escape") closeHelp();
            trapHelpFocus(event);
            return;
        }
        if (isTypingTarget(event.target)) return;
        if (event.key === "j" || event.key === "ArrowDown") {
            event.preventDefault();
            focusRow(currentIndex + 1);
        } else if (event.key === "k" || event.key === "ArrowUp") {
            event.preventDefault();
            focusRow(currentIndex - 1);
        } else if (event.key === "a" || event.key === "Enter") {
            event.preventDefault();
            activate("[data-queue-accept]");
        } else if (event.key === "r") {
            event.preventDefault();
            activate("[data-queue-reject]");
        } else if (event.key === "u") {
            if (undoButton && !undoButton.hidden) {
                event.preventDefault();
                undoButton.click();
            }
        } else if (event.key === "/") {
            var search = document.querySelector("[data-queue-search]");
            if (search) {
                event.preventDefault();
                search.focus();
            }
        } else if (event.key === "?") {
            event.preventDefault();
            openHelp();
        }
    });

    window.setTimeout(function () {
        if (undoButton) undoButton.hidden = true;
    }, 30000);
})();
