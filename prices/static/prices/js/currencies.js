(function () {
    var selectAll = document.getElementById("rate-select-all");
    if (!selectAll) return;
    var boxes = Array.from(document.querySelectorAll('input[name="rate_ids"]'));
    selectAll.addEventListener("change", function () {
        boxes.forEach(function (box) {
            box.checked = selectAll.checked;
        });
    });
})();
