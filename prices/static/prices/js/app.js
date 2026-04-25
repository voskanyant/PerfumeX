(function () {
    document.querySelectorAll(".helptext").forEach(function (el) {
        var text = (el.textContent || "").trim();
        if (!text) return;
        el.setAttribute("data-tip", text);
        el.textContent = "";
    });

    document.querySelectorAll(".button").forEach(function (el) {
        if (el.classList.contains("icon")) {
            el.classList.add("btn-icon");
        }
    });

    document.querySelectorAll("input, textarea").forEach(function (el) {
        if (el.type === "checkbox" || el.type === "radio" || el.type === "hidden") return;
        if (!el.classList.contains("form-control") && !el.classList.contains("form-select")) {
            el.classList.add("form-control");
        }
    });

    document.querySelectorAll("select").forEach(function (el) {
        if (!el.classList.contains("form-select")) {
            el.classList.add("form-select");
        }
    });

    document.querySelectorAll("input[type=checkbox]").forEach(function (el) {
        el.classList.add("check-input");
    });

    document.querySelectorAll(".flash").forEach(function (flash) {
        var btn = flash.querySelector(".flash-close");
        if (!btn) return;
        btn.addEventListener("click", function () {
            flash.style.transition = "opacity 0.18s ease";
            flash.style.opacity = "0";
            setTimeout(function () {
                if (flash.parentElement) {
                    flash.parentElement.removeChild(flash);
                }
            }, 200);
        });
    });

    document.querySelectorAll("table").forEach(function (table) {
        if (!table.classList.contains("data-table")) {
            table.classList.add("data-table");
        }
    });

    document.querySelectorAll(".product-filters-drawer[data-drawer]").forEach(function (drawer) {
        if (drawer.parentElement !== document.body) {
            document.body.appendChild(drawer);
        }
    });

    document.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        var submitter = event.submitter || form.querySelector('button[type="submit"], input[type="submit"]');
        var confirmMessage = (submitter && submitter.getAttribute("data-confirm")) || form.getAttribute("data-confirm");
        if (confirmMessage && !window.confirm(confirmMessage)) {
            event.preventDefault();
            return;
        }
        if (form.dataset.noSubmitDisable === "1") return;
        if (event.submitter && event.submitter.name && !event.submitter.disabled) {
            var submitterValue = document.createElement("input");
            submitterValue.type = "hidden";
            submitterValue.name = event.submitter.name;
            submitterValue.value = event.submitter.value || "";
            form.appendChild(submitterValue);
        }
        var buttons = form.querySelectorAll('button[type="submit"], input[type="submit"]');
        buttons.forEach(function (button) {
            if (button.disabled) return;
            button.disabled = true;
            if (button instanceof HTMLInputElement) {
                button.dataset.originalText = button.value || "";
                button.value = button.dataset.busyText || "Working…";
                return;
            }
            button.dataset.originalText = button.textContent || "";
            button.textContent = button.dataset.busyText || "Working…";
        });
    });

    var path = window.location.pathname || "/";
    document.querySelectorAll(".sidebar .sidebar-link, #mobileNav .sidebar-link").forEach(function (link) {
        var href = link.getAttribute("href") || "";
        if (href && href.length > 1 && path.startsWith(href)) {
            link.classList.add("active");
        }
    });

    document.addEventListener("click", function (event) {
        document.querySelectorAll("details.actions-menu-desktop[open], details.mobile-actions-menu[open]").forEach(function (details) {
            if (!details.contains(event.target)) {
                details.removeAttribute("open");
            }
        });
    });

    var drawerBackdrop = document.querySelector("[data-drawer-backdrop]");
    var activeDrawer = null;
    var savedDrawerFocus = null;
    var focusableSelector = [
        "a[href]",
        "button:not([disabled])",
        "textarea:not([disabled])",
        "input:not([disabled]):not([type='hidden'])",
        "select:not([disabled])",
        "[tabindex]:not([tabindex='-1'])"
    ].join(",");

    function syncDrawerTriggers(name, isOpen) {
        document.querySelectorAll("[data-drawer-toggle='" + name + "']").forEach(function (trigger) {
            trigger.setAttribute("aria-expanded", isOpen ? "true" : "false");
        });
    }

    function closeDrawer(drawer) {
        if (!drawer) return;
        var name = drawer.getAttribute("data-drawer") || "";
        drawer.classList.remove("is-open");
        drawer.setAttribute("aria-hidden", "true");
        syncDrawerTriggers(name, false);
        if (activeDrawer === drawer) {
            activeDrawer = null;
            if (drawerBackdrop) {
                drawerBackdrop.hidden = true;
            }
            document.body.classList.remove("drawer-open");
        }
        if (savedDrawerFocus && typeof savedDrawerFocus.focus === "function" && document.contains(savedDrawerFocus)) {
            savedDrawerFocus.focus();
        }
        savedDrawerFocus = null;
    }

    function openDrawer(drawer) {
        if (!drawer) return;
        if (activeDrawer && activeDrawer !== drawer) {
            closeDrawer(activeDrawer);
        }
        savedDrawerFocus = document.activeElement;
        var name = drawer.getAttribute("data-drawer") || "";
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        syncDrawerTriggers(name, true);
        activeDrawer = drawer;
        if (drawerBackdrop) {
            drawerBackdrop.hidden = false;
        }
        document.body.classList.add("drawer-open");
        window.setTimeout(function () {
            var focusable = getDrawerFocusable(drawer);
            var focusTarget = focusable[0] || drawer;
            if (!focusTarget.hasAttribute("tabindex") && focusTarget === drawer) {
                focusTarget.setAttribute("tabindex", "-1");
            }
            focusTarget.focus();
        }, 0);
    }

    function getDrawerFocusable(drawer) {
        if (!drawer) return [];
        return Array.from(drawer.querySelectorAll(focusableSelector)).filter(function (node) {
            return node.offsetParent !== null || node === document.activeElement;
        });
    }

    function trapDrawerFocus(event) {
        if (!activeDrawer || event.key !== "Tab") return;
        var focusable = getDrawerFocusable(activeDrawer);
        if (!focusable.length) {
            event.preventDefault();
            activeDrawer.focus();
            return;
        }
        var first = focusable[0];
        var last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
            return;
        }
        if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }

    document.querySelectorAll("[data-drawer-toggle]").forEach(function (trigger) {
        trigger.addEventListener("click", function () {
            var name = trigger.getAttribute("data-drawer-toggle") || "";
            var drawer = document.querySelector("[data-drawer='" + name + "']");
            if (!drawer) return;
            if (drawer === activeDrawer) {
                closeDrawer(drawer);
            } else {
                openDrawer(drawer);
            }
        });
    });

    document.querySelectorAll("[data-drawer-close]").forEach(function (button) {
        button.addEventListener("click", function () {
            closeDrawer(button.closest("[data-drawer]"));
        });
    });

    if (drawerBackdrop) {
        drawerBackdrop.addEventListener("click", function () {
            closeDrawer(activeDrawer);
        });
    }

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape" && activeDrawer) {
            closeDrawer(activeDrawer);
            return;
        }
        trapDrawerFocus(event);
    });
})();
