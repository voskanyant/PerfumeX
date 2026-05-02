(function () {
    var root = document.querySelector("[data-supplier-overview]");
    if (!root) {
        return;
    }

    var statusUrl = root.getAttribute("data-email-status-url");
    var hasSeenRunning = root.getAttribute("data-any-running") === "1";
    var intervalId = null;

    function applyProgress(row, progress) {
        var wrap = row.querySelector("[data-email-progress-wrap]");
        var bar = row.querySelector("[data-email-progress]");
        if (!wrap || !bar) {
            return;
        }
        if (progress === null || progress === undefined || window.isNaN(progress)) {
            wrap.classList.add("is-hidden");
            bar.style.width = "";
            return;
        }
        wrap.classList.remove("is-hidden");
        bar.style.width = progress + "%";
    }

    function applyRelativeField(button, relative, full, extraClass) {
        if (!button) {
            return;
        }
        button.textContent = relative || "";
        button.classList.remove("import-relative-time--empty", "age-fresh", "age-warn", "age-stale");
        if (extraClass) {
            button.classList.add(extraClass);
        }
        if (full) {
            button.setAttribute("data-full-datetime", full);
        } else {
            button.removeAttribute("data-full-datetime");
            button.classList.add("import-relative-time--empty");
        }
    }

    function applyChip(element, baseClass, className, label) {
        if (!element) {
            return;
        }
        element.textContent = label || "";
        element.className = baseClass + " " + (className || "is-neutral");
    }

    function applyText(element, value) {
        if (!element) {
            return;
        }
        var safeValue = value || "";
        element.textContent = safeValue;
        element.title = safeValue;
    }

    function currentText(selector, row) {
        var element = row.querySelector(selector);
        return element ? element.textContent.trim() : "";
    }

    function currentFullDateTime(row) {
        var label = row.querySelector("[data-last-import-label]");
        return label ? label.getAttribute("data-full-datetime") || "" : "";
    }

    function currentAgeClass(row) {
        var label = row.querySelector("[data-last-import-label]");
        if (!label) {
            return "";
        }
        if (label.classList.contains("age-fresh")) return "age-fresh";
        if (label.classList.contains("age-warn")) return "age-warn";
        if (label.classList.contains("age-stale")) return "age-stale";
        return "";
    }

    function currentHealthClass(row) {
        var chip = row.querySelector("[data-health-chip]");
        if (!chip) {
            return "is-neutral";
        }
        return Array.from(chip.classList).filter(function (className) {
            return className.indexOf("is-") === 0;
        })[0] || "is-neutral";
    }

    function buildPendingState(row) {
        return {
            is_running: true,
            has_email_route: true,
            last_import_relative: currentText("[data-last-import-label]", row),
            last_import_full: currentFullDateTime(row),
            last_import_age_class: currentAgeClass(row),
            last_import_note: currentText("[data-last-import-note]", row),
            check_label: "updating",
            check_class: "is-running",
            check_code: "running",
            check_note: "Starting email scan...",
            check_relative: "Now",
            check_full: "",
            check_progress: 8,
            check_has_time: false,
            health_label: currentText("[data-health-chip]", row),
            health_class: currentHealthClass(row),
            health_code: "",
            health_note: currentText("[data-health-note]", row),
            problem_note: "",
            source_mailbox_folder: currentText("[data-source-mailbox-folder]", row)
        };
    }

    function applyRowState(row, state) {
        if (!row || !state) {
            return;
        }
        applyRelativeField(
            row.querySelector("[data-last-import-label]"),
            state.last_import_relative,
            state.last_import_full,
            state.last_import_age_class
        );
        applyText(row.querySelector("[data-last-import-note]"), state.last_import_note);
        applyRelativeField(
            row.querySelector("[data-check-time-label]"),
            state.check_relative,
            state.check_full,
            ""
        );
        applyChip(
            row.querySelector("[data-check-status-chip]"),
            "import-status-chip",
            state.check_class,
            state.check_label
        );
        applyText(row.querySelector("[data-check-status-note]"), state.check_note);
        applyChip(
            row.querySelector("[data-health-chip]"),
            "import-health-chip",
            state.health_class,
            state.health_label
        );
        applyText(row.querySelector("[data-health-note]"), state.health_note);
        applyText(row.querySelector("[data-problem-note]"), state.problem_note);
        applyText(row.querySelector("[data-source-mailbox-folder]"), state.source_mailbox_folder || "");
        applyProgress(row, state.is_running ? state.check_progress : null);

        var updateBtn = row.querySelector("[data-email-update]");
        var cancelWrap = row.querySelector("[data-email-cancel-wrap]");
        if (updateBtn) {
            updateBtn.disabled = !!state.is_running || !state.has_email_route;
        }
        if (cancelWrap) {
            cancelWrap.classList.toggle("is-hidden", !state.is_running);
        }
    }

    function applySummary(summary) {
        if (!summary) {
            return;
        }
        window.Object.keys(summary).forEach(function (key) {
            var target = document.querySelector('[data-summary-value="' + key + '"]');
            if (target) {
                target.textContent = summary[key];
            }
        });
    }

    function applyScanner(scanner) {
        if (!scanner) {
            return;
        }
        applyText(document.querySelector("[data-scanner-last-run]"), scanner.last_run || "Never");
        var scannerLastRun = document.querySelector("[data-scanner-last-run]");
        if (scannerLastRun) {
            scannerLastRun.title = scanner.last_run_full || "";
        }
        applyText(document.querySelector("[data-scanner-next-target]"), scanner.next_target || "-");
        applyText(document.querySelector("[data-scanner-backlog]"), scanner.remaining_backlog || "0");
    }

    document.querySelectorAll("[data-email-progress][data-progress-value]").forEach(function (bar) {
        var initial = window.Number(bar.getAttribute("data-progress-value") || "");
        if (!window.isNaN(initial)) {
            bar.style.width = initial + "%";
        }
    });

    document.addEventListener("click", function (event) {
        var quickUploadTrigger = event.target.closest("[data-quick-upload-trigger]");
        if (quickUploadTrigger) {
            var quickUploadForm = quickUploadTrigger.closest("[data-quick-upload-form]");
            var quickUploadInput = quickUploadForm ? quickUploadForm.querySelector("[data-quick-upload-input]") : null;
            if (quickUploadInput) {
                quickUploadInput.click();
            }
            return;
        }

        var trigger = event.target.closest("[data-relative-time-button][data-full-datetime]");
        document.querySelectorAll("[data-relative-time-button].show-full-datetime").forEach(function (button) {
            if (button !== trigger) {
                button.classList.remove("show-full-datetime");
            }
        });
        if (!trigger) {
            return;
        }
        event.preventDefault();
        trigger.classList.toggle("show-full-datetime");
        if (trigger.classList.contains("show-full-datetime")) {
            window.setTimeout(function () {
                trigger.classList.remove("show-full-datetime");
            }, 2200);
        }
    });

    document.addEventListener("change", function (event) {
        var quickUploadInput = event.target.closest("[data-quick-upload-input]");
        if (!quickUploadInput || !quickUploadInput.files || !quickUploadInput.files.length) {
            return;
        }
        var quickUploadForm = quickUploadInput.closest("[data-quick-upload-form]");
        if (quickUploadForm) {
            quickUploadForm.submit();
        }
    });

    document.addEventListener("submit", function (event) {
        var singleUpdateForm = event.target.closest("[data-email-update-form]");
        if (singleUpdateForm) {
            var row = singleUpdateForm.closest("[data-supplier-id]");
            if (row) {
                row.classList.add("is-row-pending");
                applyRowState(row, buildPendingState(row));
            }
            return;
        }

        var updateAllForm = event.target.closest("[data-email-update-all-form]");
        if (updateAllForm) {
            var updateAllBtn = updateAllForm.querySelector("[data-email-update-all]");
            var banner = document.querySelector("[data-email-update-banner]");
            if (updateAllBtn) {
                updateAllBtn.disabled = true;
                updateAllBtn.setAttribute("aria-busy", "true");
                updateAllBtn.classList.add("is-loading");
                updateAllBtn.textContent = "Starting...";
            }
            if (banner) {
                banner.classList.add("is-visible");
                banner.textContent = "Email scan is starting. This board will refresh automatically.";
            }
            var scannerLive = document.querySelector("[data-scanner-live]");
            if (scannerLive) {
                scannerLive.classList.remove("is-hidden");
            }
        }
    });

    function updateAll() {
        if (!statusUrl) {
            return;
        }
        fetch(statusUrl)
            .then(function (res) { return res.json(); })
            .then(function (data) {
                var rows = data.rows || {};
                var anyRunning = !!data.worker_busy;
                document.querySelectorAll("[data-supplier-id]").forEach(function (row) {
                    var supplierId = row.getAttribute("data-supplier-id");
                    var state = rows[supplierId];
                    if (!state) {
                        return;
                    }
                    applyRowState(row, state);
                    row.classList.toggle("is-row-pending", !!state.is_running);
                    if (state.is_running) {
                        anyRunning = true;
                    }
                });
                applySummary(data.summary || {});
                applyScanner(data.scanner || {});
                var updateAllBtn = document.querySelector("[data-email-update-all]");
                var banner = document.querySelector("[data-email-update-banner]");
                if (updateAllBtn) {
                    updateAllBtn.disabled = anyRunning;
                    updateAllBtn.classList.toggle("is-loading", anyRunning);
                    updateAllBtn.setAttribute("aria-busy", anyRunning ? "true" : "false");
                    if (!anyRunning) {
                        updateAllBtn.textContent = "Scan mailboxes now";
                    }
                }
                if (banner) {
                    banner.classList.toggle("is-visible", anyRunning);
                    banner.textContent = anyRunning ? "Email scan is running. This board will refresh automatically." : "";
                }
                var scannerLive = document.querySelector("[data-scanner-live]");
                if (scannerLive) {
                    scannerLive.classList.toggle("is-hidden", !anyRunning);
                }
                if (anyRunning) {
                    hasSeenRunning = true;
                    if (!intervalId) {
                        intervalId = setInterval(updateAll, 3000);
                    }
                }
                if (!anyRunning && hasSeenRunning && intervalId) {
                    window.clearInterval(intervalId);
                    intervalId = null;
                }
            });
    }

    intervalId = setInterval(updateAll, 3000);
    updateAll();
})();
