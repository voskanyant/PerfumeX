(function () {
    var rows = Array.from(document.querySelectorAll("[data-linking-row]"));
    var panel = document.querySelector("[data-linking-panel]");
    var candidatesNode = document.querySelector("[data-linking-candidates]");
    var selectedNode = document.querySelector("[data-linking-selected]");
    var countNode = document.querySelector("[data-linking-candidate-count]");
    var selectionRoot = document.querySelector("[data-catalogue-selection-root]");
    var searchForm = document.querySelector("[data-fragrantica-search-form]");
    var searchInput = document.querySelector("[data-fragrantica-search-input]");
    var searchResultsNode = document.querySelector("[data-fragrantica-search-results]");

    if (!panel || !candidatesNode || !selectedNode) return;

    function clearNode(node) {
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function csrfToken() {
        var csrfInput = document.querySelector("input[name=csrfmiddlewaretoken]");
        if (csrfInput && csrfInput.value) return csrfInput.value;
        var match = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : "";
    }

    function ajaxHeaders() {
        var headers = { "X-Requested-With": "XMLHttpRequest" };
        var token = csrfToken();
        if (token) headers["X-CSRFToken"] = token;
        return headers;
    }

    function parseJsonResponse(response, fallbackMessage) {
        return response.text().then(function (text) {
            var data = null;
            if (text) {
                try {
                    data = JSON.parse(text);
                } catch {
                    throw {
                        error: fallbackMessage || "Request failed. Reload and try again.",
                        detail: text.slice(0, 160)
                    };
                }
            }
            data = data || {};
            if (!response.ok || data.error || data.ok === false) throw data;
            return data;
        });
    }

    function submitAction(form, submitter) {
        if (
            submitter &&
            submitter.hasAttribute &&
            submitter.hasAttribute("formaction")
        ) {
            return submitter.getAttribute("formaction") || "";
        }
        return form.getAttribute("action") || "";
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
        selectedNode.dataset.selectedPerfumeId = String(selected.id || "");
        var title = document.createElement("span");
        title.className = "catalogue-linking-selected-title";
        title.textContent = selected.label;
        selectedNode.appendChild(title);
        appendProductIdentitySubnames(selectedNode, selected);
    }

    function renderEmpty(message, targetNode) {
        var target = targetNode || candidatesNode;
        clearNode(target);
        var empty = document.createElement("div");
        empty.className = "empty-state";
        var text = document.createElement("p");
        text.textContent = message;
        empty.appendChild(text);
        target.appendChild(empty);
    }

    function clearSearchResults() {
        if (!searchResultsNode) return;
        clearNode(searchResultsNode);
        searchResultsNode.hidden = true;
    }

    function renderAIAdvice(advice) {
        if (!advice) return;
        var card = document.createElement("article");
        card.className = "catalogue-linking-candidate catalogue-linking-ai-advice";

        var main = document.createElement("div");
        main.className = "catalogue-linking-candidate-main";

        var title = document.createElement("span");
        title.className = "catalogue-linking-candidate-title";
        title.textContent = advice.recommended_label
            ? "AI advice: " + advice.recommended_label
            : "AI advice";

        var meta = document.createElement("span");
        meta.className = "catalogue-linking-candidate-meta";
        var metaParts = [];
        if (advice.model_name) metaParts.push(advice.model_name);
        if (advice.risk_level) metaParts.push("Risk " + advice.risk_level);
        if (advice.status) metaParts.push(advice.status);
        meta.textContent = metaParts.join(" - ");

        var reason = document.createElement("span");
        reason.className = "tone-muted text-small";
        reason.textContent = advice.reasoning || "AI returned no reasoning.";

        main.appendChild(title);
        main.appendChild(meta);
        main.appendChild(reason);
        if (advice.learning_proposal) {
            var proposal = document.createElement("span");
            proposal.className = "tone-muted text-small";
            proposal.textContent = "Learning proposal: " + advice.learning_proposal.label;
            main.appendChild(proposal);
        }

        var actions = document.createElement("div");
        actions.className = "catalogue-linking-candidate-actions";

        var badge = document.createElement("span");
        badge.className = scoreClass(advice.confidence || 0);
        badge.textContent = String(advice.confidence || 0);
        actions.appendChild(badge);

        if (advice.can_review && advice.review_url) {
            var accept = document.createElement("form");
            accept.method = "post";
            accept.action = advice.review_url;
            accept.setAttribute("data-ai-advice-review-form", "1");
            appendHidden(accept, "csrfmiddlewaretoken", csrfToken());
            appendHidden(accept, "action", "accept");
            var acceptButton = document.createElement("button");
            acceptButton.className = "button secondary";
            acceptButton.type = "submit";
            acceptButton.textContent = "Accept";
            accept.appendChild(acceptButton);
            actions.appendChild(accept);

            var reject = document.createElement("form");
            reject.method = "post";
            reject.action = advice.review_url;
            reject.setAttribute("data-ai-advice-review-form", "1");
            appendHidden(reject, "csrfmiddlewaretoken", csrfToken());
            appendHidden(reject, "action", "reject");
            var rejectButton = document.createElement("button");
            rejectButton.className = "button ghost";
            rejectButton.type = "submit";
            rejectButton.textContent = "Reject";
            reject.appendChild(rejectButton);
            actions.appendChild(reject);
        }

        card.appendChild(main);
        card.appendChild(actions);
        candidatesNode.appendChild(card);
    }

    function renderAIAdviceAction(selected) {
        var form = document.createElement("form");
        form.method = "post";
        form.action = panel.getAttribute("data-ai-advice-url") || "";
        form.className = "catalogue-linking-candidate catalogue-linking-ai-action";
        form.setAttribute("data-ai-advice-form", "1");

        appendHidden(form, "csrfmiddlewaretoken", csrfToken());
        appendHidden(form, "perfume", String(selected.id));
        var minScoreSelect = document.querySelector("[data-linking-min-score]");
        appendHidden(form, "min_score", minScoreSelect ? minScoreSelect.value : "");

        var main = document.createElement("div");
        main.className = "catalogue-linking-candidate-main";

        var title = document.createElement("span");
        title.className = "catalogue-linking-candidate-title";
        title.textContent = "AI advice";

        var meta = document.createElement("span");
        meta.className = "catalogue-linking-candidate-meta";
        meta.textContent = "Review-only rerank of the visible Fragrantica candidates.";

        main.appendChild(title);
        main.appendChild(meta);

        var actions = document.createElement("div");
        actions.className = "catalogue-linking-candidate-actions";

        var button = document.createElement("button");
        button.className = "button ghost";
        button.type = "submit";
        button.setAttribute("data-ai-advice-submit", "1");
        button.textContent = "Ask AI";
        actions.appendChild(button);

        form.appendChild(main);
        form.appendChild(actions);
        candidatesNode.appendChild(form);
    }

    function renderCandidate(selected, candidate, targetNode) {
        var target = targetNode || candidatesNode;
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
        var metaParts = [candidate.reason];
        if (typeof candidate.score === "number") {
            metaParts.push("Score " + candidate.score);
        }
        if (candidate.audience) metaParts.push(candidate.audience);
        if (candidate.release_year) metaParts.push(String(candidate.release_year));
        meta.textContent = metaParts.filter(Boolean).join(" · ");

        main.appendChild(title);
        appendCollectionSubname(main, candidate.collection);
        main.appendChild(meta);

        var actions = document.createElement("div");
        actions.className = "catalogue-linking-candidate-actions";

        var badge = document.createElement("span");
        if (typeof candidate.score === "number" || candidate.manual_review_reason) {
            badge.className = scoreClass(candidate.score || 0);
            badge.textContent = candidate.manual_review_reason ? "Review" : String(candidate.score || 0);
            actions.appendChild(badge);
        }

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
        target.appendChild(form);
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

        if (source.unlink_url) {
            var unlinkForm = document.createElement("form");
            unlinkForm.className = "catalogue-linking-inline-form";
            unlinkForm.method = "post";
            unlinkForm.action = source.unlink_url;
            appendHidden(unlinkForm, "csrfmiddlewaretoken", csrfToken());
            appendHidden(unlinkForm, "next", panel.getAttribute("data-next-url") || window.location.pathname);
            appendHidden(unlinkForm, "perfume_id", String(selectedNode.dataset.selectedPerfumeId || ""));

            var unlink = document.createElement("button");
            unlink.className = "button danger";
            unlink.type = "submit";
            unlink.setAttribute("data-confirm", "Unlink this Fragrantica row from the selected Our Products row?");
            unlink.textContent = "Unlink";
            unlinkForm.appendChild(unlink);
            actions.appendChild(unlinkForm);
        }

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
        renderAIAdvice(data.ai_advice);
        if (!data.ai_advice || data.ai_advice.status !== "pending") {
            renderAIAdviceAction(data.selected);
        }
        candidates.forEach(function (candidate) {
            renderCandidate(data.selected, candidate);
        });
    }

    function renderManualSearchResults(data) {
        if (!searchResultsNode) return;
        clearNode(searchResultsNode);
        searchResultsNode.hidden = false;
        var results = data.results || [];
        if (!results.length) {
            renderEmpty(
                data.message || "No Fragrantica rows matched that search.",
                searchResultsNode
            );
            return;
        }
        results.forEach(function (candidate) {
            renderCandidate(data.selected, candidate, searchResultsNode);
        });
    }

    function rowPayload(row) {
        var rawPayload = row.getAttribute("data-linking-payload");
        if (!rawPayload) return null;
        try {
            return JSON.parse(rawPayload);
        } catch {
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

    function setSubmitterBusy(button, busy, busyText) {
        if (!button) return;
        if (busy) {
            button.dataset.originalText = button.textContent || "";
            button.disabled = true;
            button.textContent = busyText || "Linking...";
            return;
        }
        button.disabled = false;
        button.textContent = button.dataset.originalText || "Link";
    }

    function submitFragranticaLink(form, submitter) {
        var action = submitAction(form, submitter);
        if (!action) return;
        var formData = new FormData(form);
        if (submitter && submitter.name) {
            formData.append(submitter.name, submitter.value || "");
        }
        setSubmitterBusy(submitter, true);
        fetch(action, {
            method: "POST",
            body: formData,
            headers: ajaxHeaders()
        })
            .then(function (response) {
                return parseJsonResponse(response, "Link failed. Reload and try again.");
            })
            .then(function (data) {
                var payload = linkingPayloadFromResponse(data);
                updateRowAfterLink(data);
                renderPayload(payload);
                clearSearchResults();
            })
            .catch(function (data) {
                renderEmpty((data && (data.message || data.error)) || "Link failed. Reload and try again.");
            })
            .finally(function () {
                delete form.dataset.noSubmitDisable;
                setSubmitterBusy(submitter, false);
            });
    }

    function submitAIAdvice(form, submitter) {
        var action = submitAction(form, submitter);
        if (!action) return;
        setSubmitterBusy(submitter, true, "Thinking...");
        fetch(action, {
            method: "POST",
            body: new FormData(form),
            headers: ajaxHeaders()
        })
            .then(function (response) {
                return parseJsonResponse(response, "AI advice failed. Check OpenAI settings and try again.");
            })
            .then(function (data) {
                var row = rows.find(function (item) {
                    return item.getAttribute("data-perfume-id") === String(data.selected.id);
                });
                if (row) {
                    row.setAttribute("data-linking-payload", JSON.stringify(data));
                }
                renderPayload(data);
            })
            .catch(function (data) {
                renderEmpty((data && data.error) || "AI advice failed. Check OpenAI settings and try again.");
            })
            .finally(function () {
                setSubmitterBusy(submitter, false);
            });
    }

    function currentSelectedRow() {
        return rows.find(function (item) {
            return item.classList.contains("is-selected");
        });
    }

    function submitManualSearch(form, submitter) {
        var searchUrl = panel.getAttribute("data-fragrantica-search-url");
        var selectedId = selectedNode.dataset.selectedPerfumeId;
        var query = searchInput ? searchInput.value.trim() : "";
        if (!searchUrl || !selectedId) {
            if (searchResultsNode) {
                searchResultsNode.hidden = false;
                renderEmpty("Choose an Our Products row first.", searchResultsNode);
            }
            return;
        }
        if (query.length < 2) {
            if (searchResultsNode) {
                searchResultsNode.hidden = false;
                renderEmpty("Type at least 2 characters to search Fragrantica.", searchResultsNode);
            }
            return;
        }
        var url = new URL(searchUrl, window.location.origin);
        url.searchParams.set("perfume", selectedId);
        url.searchParams.set("q", query);
        setSubmitterBusy(submitter, true, "Searching...");
        fetch(url.toString(), { headers: { "X-Requested-With": "XMLHttpRequest" } })
            .then(function (response) {
                return parseJsonResponse(response, "Fragrantica search failed. Reload and try again.");
            })
            .then(renderManualSearchResults)
            .catch(function (data) {
                if (searchResultsNode) {
                    searchResultsNode.hidden = false;
                    renderEmpty(
                        (data && (data.message || data.error)) || "Fragrantica search failed. Reload and try again.",
                        searchResultsNode
                    );
                }
            })
            .finally(function () {
                setSubmitterBusy(submitter, false);
            });
    }

    function submitAIAdviceReview(form, submitter) {
        var action = submitAction(form, submitter);
        if (!action) return;
        setSubmitterBusy(submitter, true, "Saving...");
        fetch(action, {
            method: "POST",
            body: new FormData(form),
            headers: ajaxHeaders()
        })
            .then(function (response) {
                return parseJsonResponse(response, "AI review failed. Reload and try again.");
            })
            .then(function (data) {
                var row = currentSelectedRow();
                var payload = row ? rowPayload(row) : null;
                if (!payload) {
                    renderEmpty("AI review saved. Reload this row to refresh the advice.");
                    return;
                }
                payload.ai_advice = data.ai_advice;
                row.setAttribute("data-linking-payload", JSON.stringify(payload));
                try {
                    renderPayload(payload);
                } catch {
                    renderEmpty("AI review saved. Reload this row to refresh the advice.");
                }
            })
            .catch(function (data) {
                renderEmpty((data && data.error) || "AI review failed. Reload and try again.");
            })
            .finally(function () {
                setSubmitterBusy(submitter, false);
            });
    }

    function selectRow(row) {
        rows.forEach(function (item) {
            item.classList.toggle("is-selected", item === row);
        });
        clearSearchResults();
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
                row.setAttribute("data-linking-payload", JSON.stringify(data));
                renderPayload(data);
            })
            .catch(function () {
                selectedNode.textContent = "Candidate search failed.";
                if (countNode) countNode.textContent = "Error";
                renderEmpty("Reload the page or loosen the confidence filter.");
            });
    }

    function handleLinkingSubmit(event) {
        var form = event.target;
        if (!(form instanceof HTMLFormElement)) return;
        if (form.matches("[data-ai-advice-form]")) {
            event.preventDefault();
            event.stopPropagation();
            submitAIAdvice(form, event.submitter);
            return;
        }
        if (form.matches("[data-ai-advice-review-form]")) {
            event.preventDefault();
            event.stopPropagation();
            submitAIAdviceReview(form, event.submitter);
            return;
        }
        if (!form.matches("[data-fragrantica-link-form]")) return;
        event.preventDefault();
        event.stopPropagation();
        form.dataset.noSubmitDisable = "1";
        submitFragranticaLink(form, event.submitter);
    }

    candidatesNode.addEventListener("submit", handleLinkingSubmit);
    if (searchResultsNode) {
        searchResultsNode.addEventListener("submit", handleLinkingSubmit);
    }
    if (searchForm) {
        searchForm.addEventListener("submit", function (event) {
            event.preventDefault();
            event.stopPropagation();
            submitManualSearch(searchForm, event.submitter);
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
