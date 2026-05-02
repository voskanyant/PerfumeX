document.addEventListener("DOMContentLoaded", function () {
    var master = document.getElementById("select-all-preview-rows");
    if (!master) {
        return;
    }
    var boxes = Array.prototype.slice.call(document.querySelectorAll(".preview-row-checkbox"));
    master.addEventListener("change", function () {
        boxes.forEach(function (box) {
            box.checked = master.checked;
        });
    });
});
