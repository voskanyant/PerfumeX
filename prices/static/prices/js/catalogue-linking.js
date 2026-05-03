(function () {
    var rows = Array.from(document.querySelectorAll("[data-linking-row]"));
    var panel = document.querySelector("[data-linking-panel]");
    var candidatesNode = document.querySelector("[data-linking-candidates]");
    var selectedNode = document.querySelector("[data-linking-selected]");
    var countNode = document.querySelector("[data-linking-candidate-count]");
    var selectionRoot = document.querySelector("[data-catalogue-selection-root]");

    if (!panel || !candidatesNode || !selectedNode) return;

    function clearNode(node) {
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function csrfToken() {
        var csrfInput = document.querySelector("input[name=csrfmiddlewaretoken]");
        return csrfInput ? csrfInput.value : "";
    }

    function scoreClass(score) {
        if (score >= 95) return "score-badge score-badge--success";
        if (score >= 90) return "score-badge score-badge--strong";
        return "score-badge score-badge--warning";
    }

    function appendHidden(form, name, value) {
        var input = document.createElement("input");
        input.type = "hidden";
        input.name = name;
        input.value = value || "";
        form.appendChild(input);
    }

    function appendIdentitySubname(parent, labelText, value) {
        if (!value) return;
        var subname = document.createElement("span");
        subname.className = "catalogue-identity-subname";
        var label = document.createElement("span");
        label.textContent = labelText;
        subname.appendChild(label);
        subname.appendChild(document.createTextNode(value));
        parent.appendChild(subname);
    }

    function appendCollectionSubname(parent, collection) {
        appendIdentitySubname(parent, "Collection", collection);
    }

    function appendProductIdentitySubnames(parent, selected) {
        appendIdentitySubname(parent, "Collection", selected.collection);
        appendIdentitySubname(parent, "Audience", selected.audience);
        appendIdentitySubname(parent, "Year", selected.release_year);
    }

    function renderSelected(selected) {
        clearNode(selectedNode);
        var title = document.createElement("span");
        title.className = "catalogue-linking-selected-title";
        title.textContent = selected.label;
        selectedNode.appendChild(title);
        appendProductIdentitySubnames(selectedNode, selected);
    }

    function renderEmpty(message) {
        clearNode(candidatesNode);
        var empty = document.createElement("div");
        empty.className = "empty-state";
        var text = document.createElement("p");
        text.textContent = message;
        empty.appendChild(text);
        candidatesNode.appendChild(empty);
    }

    function renderCandidate(selected, candidate) {
        var form = document.createElement("form");
        form.method = "post";
        form.action = candidate.link_url;
        form.className = "catalogue-linking-candidate";

        appendHidden(form, "csrfmiddlewaretoken", csrfToken());
        appendHidden(form, "next", panel.getAttribute("data-next-url") || window.location.pathname);
        appendHidden(form, "perfume_id", String(selected.id));
        appendHidden(form, "create_alias", candidate.creates_alias ? "1" : "0");
        appendHidden(form, "apply_identity_group", "1");

        var main = document.createElement("div");
        main.className = "catalogue-linking-candidate-main";

        var title = document.createElement("span");
        title.className = "catalogue-linking-candidate-title";
        title.textContent = candidate.label;

        var meta = document.createElement("span");
        meta.className = "catalogue-linking-candidate-meta";
        var metaParts = [candidate.reason, "Score " + candidate.score];
        if (candidate.audience) metaParts.push(candidate.audience);
        if (candidate.release_year) metaParts.push(String(candidate.release_year));
        meta.textContent = metaParts.filter(Boolean).join(" · ");

        main.appendChild(title);
        appendCollectionSubname(main, candidate.collection);
        main.appendChild(meta);

        var actions = document.createElement("div");
        actions.className = "catalogue-linking-candidate-actions";

        var badge = document.createElement("span");
        badge.className = scoreClass(candidate.score || 0);
        badge.textContent = String(candidate.score || 0);
        actions.appendChild(badge);

        if (candidate.source_href) {
            var open = document.createElement("a");
            open.className = "button ghost";
            open.href = candidate.source_href;
            open.target = "_blank";
            open.rel = "noopener";
            open.textContent = "Open";
            actions.appendChild(open);
        }

        if (candidate.match_status === "linked") {
            var linked = document.createElement("a");
            linked.className = "button secondary";
            linked.href = candidate.review_url || "#";
            linked.textContent = "Linked";
            actions.appendChild(linked);
        } else {
            var button = document.createElement("button");
            button.className = "button primary";
            button.type = "submit";
            button.textContent = "Link";
            actions.appendChild(button);
        }

        form.appendChild(main);
        form.appendChild(actions);
        candidatesNode.appendChild(form);
    }

    function renderLinkedSource(source) {
        var card = document.createElement("article");
        card.className = "catalogue-linking-candidate catalogue-linking-candidate-linked";

        var main = document.createElement("div");
        main.className = "catalogue-linking-candidate-main";

        var title = document.createElement("span");
        title.className = "catalogue-linking-candidate-title";
        title.textContent = source.label;

        var meta = document.createElement("span");
        meta.className = "catalogue-linking-candidate-meta";
        var metaParts = ["Linked Fragrantica row"];
        if (source.audience) metaParts.push(source.audience);
        if (source.release_year) metaParts.push(String(source.release_year));
        meta.textContent = metaParts.join(" - ");

        main.appendChild(title);
        appendCollectionSubname(main, source.collection);
        main.appendChild(meta);

        var actions = document.createElement("div");
        actions.className = "catalogue-linking-candidate-actions";

        var badge = document.createElement("span");
        badge.className = "score-badge score-badge--success";
        badge.textContent = "Linked";
        actions.appendChild(badge);

        if (source.source_href) {
            var open = document.createElement("a");
            open.className = "button ghost";
            open.href = source.source_href;
            open.target = "_blank";
            open.rel = "noopener";
            open.textContent = "Open";
            actions.appendChild(open);
        }

        var review = document.createElement("a");
        review.className = "button secondary";
        review.href = source.review_url || "#";
        review.textContent = "Review";
        actions.appendChild(review);

        card.appendChild(main);
        card.appendChild(actions);
        candidatesNode.appendChild(card);
    }

    function selectRow(row) {
        rows.forEach(function (item) {
            item.classList.toggle("is-selected", item === row);
        });
        selectedNode.textContent = "Loading Fragrantica suggestions...";
        if (countNode) countNode.textContent = "Loading";
        renderEmpty("Searching Fragrantica matches for the selected product.");
        fetch(row.getAttribute("data-candidates-url"), { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (response) {
                if (!response.ok) throw new Error("Candidate search failed");
                return response.json();
            })
            .then(function (data) {
                renderSelected(data.selected);
                clearNode(candidatesNode);
                if (data.linked_sources && data.linked_sources.length) {
                    if (countNode) {
                        countNode.textContent = data.linked_sources.length + " linked";
                    }
                    data.linked_sources.forEach(renderLinkedSource);
                    return;
                }
                if (countNode) {
                    countNode.textContent = data.candidates.length + " candidate" + (data.candidates.length === 1 ? "" : "s");
                }
                if (!data.candidates.length) {
                    renderEmpty("No Fragrantica suggestions meet the current confidence filter.");
                    return;
                }
                data.candidates.forEach(function (candidate) {
                    renderCandidate(data.selected, candidate);
                });
            })
            .catch(function () {
                selectedNode.textContent = "Candidate search failed.";
                if (countNode) countNode.textContent = "Error";
                renderEmpty("Reload the page or loosen the confidence filter.");
            });
    }

    if (selectionRoot) {
        selectionRoot.addEventListener("catalogue-selection:row-selected", function (event) {
            var row = event.detail && event.detail.row;
            if (row && row.matches("[data-linking-row]")) {
                selectRow(row);
            }
        });
    } else {
        rows.forEach(function (row) {
            var button = row.querySelector("[data-linking-select]");
            if (!button) return;
            button.addEventListener("click", function () {
                selectRow(row);
            });
        });
    }
})();
