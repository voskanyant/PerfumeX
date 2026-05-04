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
        form.setAttribute("data-fragrantica-link-form", "1");

        appendHidden(form, "csrfmiddlewaretoken", csrfToken());
        appendHidden(form, "next", panel.getAttribute("data-next-url") || window.location.pathname);
        appendHidden(form, "perfume_id", String(selected.id));
        appendHidden(form, "create_alias", candidate.creates_alias ? "1" : "0");
        appendHidden(form, "apply_identity_group", "1");
        if (candidate.manual_review_link) {
            appendHidden(form, "manual_review_link", "1");
        }

        var main = document.createElement("div");
        main.className = "catalogue-linking-candidate-main";

        var title = document.createElement("span");
        title.className = "catalogue-linking-candidate-title";
        title.textContent = candidate.label;
        if (candidate.manual_review_reason) {
            var reviewReason = document.createElement("span");
            reviewReason.className = "tone-muted text-small";
            reviewReason.textContent = candidate.manual_review_reason;
            title.appendChild(reviewReason);
        }

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
        badge.textContent = candidate.manual_review_reason ? "Review" : String(candidate.score || 0);
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

        if (candidate.match_status === "linked" && !candidate.can_link) {
            var linked = document.createElement("a");
            linked.className = "button secondary";
            linked.href = candidate.review_url || "#";
            linked.textContent = "Linked";
            actions.appendChild(linked);
        } else {
            var button = document.createElement("button");
            button.className = "button primary";
            button.type = "submit";
            button.setAttribute("data-fragrantica-link-submit", "1");
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

    function renderPayload(data) {
        renderSelected(data.selected);
        clearNode(candidatesNode);
        if (data.linked_sources && data.linked_sources.length) {
            if (countNode) {
                countNode.textContent = data.linked_sources.length + " linked";
            }
            data.linked_sources.forEach(renderLinkedSource);
            return;
        }
        var candidates = data.candidates || [];
        if (countNode) {
            countNode.textContent = candidates.length + " candidate" + (candidates.length === 1 ? "" : "s");
        }
        if (!candidates.length) {
            renderEmpty("No Fragrantica suggestions meet the current confidence filter.");
            return;
        }
        candidates.forEach(function (candidate) {
            renderCandidate(data.selected, candidate);
        });
    }

    function rowPayload(row) {
        var rawPayload = row.getAttribute("data-linking-payload");
        if (!rawPayload) return null;
        try {
            return JSON.parse(rawPayload);
        } catch (error) {
            return null;
        }
    }

    function linkingPayloadFromResponse(data) {
        return {
            selected: data.selected,
            linked_sources: data.linked_source ? [data.linked_source] : [],
            candidates: []
        };
    }

    function updateRowAfterLink(data) {
        if (!data || !data.selected || !data.linked_source) return;
        var row = rows.find(function (item) {
            return item.getAttribute("data-perfume-id") === String(data.selected.id);
        });
        if (!row) return;
        var payload = linkingPayloadFromResponse(data);
        row.classList.add("is-linked");
        row.removeAttribute("data-catalogue-link-pair");
        row.setAttribute("data-linking-payload", JSON.stringify(payload));

        var title = row.querySelector(".catalogue-linking-row-title");
        if (title) {
            title.textContent = data.selected.label;
        }

        var suggestion = row.querySelector(".catalogue-linking-row-suggestion");
        if (!suggestion) return;
        clearNode(suggestion);
        suggestion.classList.add("catalogue-linking-row-linked");
        var badge = document.createElement("span");
        badge.className = "score-badge score-badge--success";
        badge.textContent = "Linked";
        suggestion.appendChild(badge);
        suggestion.appendChild(document.createTextNode(data.linked_source.label));
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

    function submitFragranticaLink(form, submitter) {
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
                var payload = linkingPayloadFromResponse(data);
                updateRowAfterLink(data);
                renderPayload(payload);
            })
            .catch(function (data) {
                renderEmpty((data && data.message) || "Link failed. Reload and try again.");
            })
            .finally(function () {
                delete form.dataset.noSubmitDisable;
                setSubmitterBusy(submitter, false);
            });
    }

    function selectRow(row) {
        rows.forEach(function (item) {
            item.classList.toggle("is-selected", item === row);
        });
        var preloaded = rowPayload(row);
        if (preloaded) {
            renderPayload(preloaded);
            return;
        }
        selectedNode.textContent = "Loading Fragrantica suggestions...";
        if (countNode) countNode.textContent = "Loading";
        renderEmpty("Searching Fragrantica matches for the selected product.");
        fetch(row.getAttribute("data-candidates-url"), { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (response) {
                if (!response.ok) throw new Error("Candidate search failed");
                return response.json();
            })
            .then(function (data) {
                renderPayload(data);
            })
            .catch(function () {
                selectedNode.textContent = "Candidate search failed.";
                if (countNode) countNode.textContent = "Error";
                renderEmpty("Reload the page or loosen the confidence filter.");
            });
    }

    candidatesNode.addEventListener("submit", function (event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement) || !form.matches("[data-fragrantica-link-form]")) return;
        event.preventDefault();
        event.stopPropagation();
        form.dataset.noSubmitDisable = "1";
        submitFragranticaLink(form, event.submitter);
    });

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
