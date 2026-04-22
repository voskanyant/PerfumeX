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
        el.classList.add("form-check-input");
    });

    document.querySelectorAll("table").forEach(function (table) {
        if (!table.classList.contains("table")) {
            table.classList.add("table", "table-sm", "align-middle");
        }
    });

    var path = window.location.pathname || "/";
    document.querySelectorAll(".sidebar .sidebar-link, #mobileNav .sidebar-link").forEach(function (link) {
        var href = link.getAttribute("href") || "";
        if (href && path.startsWith(href)) {
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

    if (window.bootstrap && window.bootstrap.Offcanvas) {
        document.querySelectorAll(".offcanvas").forEach(function (panel) {
            panel.addEventListener("show.bs.offcanvas", function () {
                document.querySelectorAll(".offcanvas.show").forEach(function (openPanel) {
                    if (openPanel === panel) return;
                    var instance = window.bootstrap.Offcanvas.getInstance(openPanel);
                    if (instance) {
                        instance.hide();
                    }
                });
            });
        });
    }
})();
