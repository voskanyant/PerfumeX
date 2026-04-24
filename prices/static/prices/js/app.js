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
        if (!table.classList.contains("table")) {
            table.classList.add("table", "table-sm");
        }
    });

    document.querySelectorAll(".product-filters-drawer[data-drawer]").forEach(function (drawer) {
        if (drawer.parentElement !== document.body) {
            document.body.appendChild(drawer);
        }
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
    }

    function openDrawer(drawer) {
        if (!drawer) return;
        if (activeDrawer && activeDrawer !== drawer) {
            closeDrawer(activeDrawer);
        }
        var name = drawer.getAttribute("data-drawer") || "";
        drawer.classList.add("is-open");
        drawer.setAttribute("aria-hidden", "false");
        syncDrawerTriggers(name, true);
        activeDrawer = drawer;
        if (drawerBackdrop) {
            drawerBackdrop.hidden = false;
        }
        document.body.classList.add("drawer-open");
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
    });
})();
