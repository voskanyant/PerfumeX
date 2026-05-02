(function () {
    var form = document.querySelector("[data-bulk-link-form]");
    if (!form) return;
    var panel = document.querySelector("[data-bulk-progress]");
    var statusText = document.querySelector("[data-bulk-status]");
    var progressBar = document.querySelector("[data-bulk-progress-bar]");
    var counts = document.querySelector("[data-bulk-counts]");
    var undoLink = document.querySelector("[data-bulk-undo]");

    function csrfToken() {
        var token = form.querySelector("input[name='csrfmiddlewaretoken']");
        return token ? token.value : "";
    }

    function updateProgress(data) {
        if (!panel) return;
        panel.hidden = false;
        statusText.textContent = data.status || "RUNNING";
        progressBar.value = data.percent || 0;
        counts.textContent = "Matched " + (data.matched || 0) + ", linked " + (data.linked || 0) + ", skipped " + (data.skipped || 0);
        if (data.status === "COMPLETE" && data.undo_url) {
            undoLink.hidden = false;
            undoLink.href = data.undo_url;
        }
    }

    function pollStatus(url) {
        window.setTimeout(function () {
            fetch(url, {headers: {"X-Requested-With": "XMLHttpRequest"}})
                .then(function (response) { return response.json(); })
                .then(function (data) {
                    updateProgress(data);
                    if (data.status !== "COMPLETE" && data.status !== "FAILED") pollStatus(url);
                });
        }, 1000);
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        panel.hidden = false;
        statusText.textContent = "Submitting";
        fetch(form.action, {
            method: "POST",
            body: new FormData(form),
            headers: {"X-Requested-With": "XMLHttpRequest"}
        }).then(function (response) {
            if (response.status === 202) return response.json();
            window.location.reload();
            return null;
        }).then(function (data) {
            if (!data) return;
            statusText.textContent = "Accepted";
            if (data.undo_url) {
                undoLink.href = data.undo_url;
            }
            pollStatus(data.status_url);
        });
    });

    if (undoLink) {
        undoLink.addEventListener("click", function (event) {
            event.preventDefault();
            fetch(undoLink.href, {
                method: "POST",
                headers: {
                    "X-CSRFToken": csrfToken(),
                    "X-Requested-With": "XMLHttpRequest"
                }
            }).then(function (response) {
                if (response.ok) {
                    statusText.textContent = "UNDONE";
                    undoLink.hidden = true;
                }
            });
        });
    }
})();
