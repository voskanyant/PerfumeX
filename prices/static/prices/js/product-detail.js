(function () {
    var labels = JSON.parse(document.getElementById("chart-labels").textContent || "[]");
    var values = JSON.parse(document.getElementById("chart-values").textContent || "[]");
    var currencySymbol = JSON.parse(document.getElementById("chart-currency-symbol").textContent || "\"\"");
    var ctx = document.getElementById("price-chart");
    var chartWrap = document.getElementById("price-chart-wrap");
    var chartEmptyState = document.getElementById("chart-empty-state");
    var fullscreenButton = document.getElementById("chart-fullscreen-toggle");
    var exportButton = document.getElementById("chart-export-png");
    var snapToggleButton = document.getElementById("chart-snap-toggle");
    var chartCurrencyInput = document.getElementById("chart-currency-input");
    var filterForm = document.querySelector("form.row.g-3.align-items-end.mt-2");
    var summaryMin = document.getElementById("chart-summary-min");
    var summaryMax = document.getElementById("chart-summary-max");
    var summaryLatest = document.getElementById("chart-summary-latest");
    var summaryChange = document.getElementById("chart-summary-change");
    var isMobileViewport = (window.innerWidth || document.documentElement.clientWidth || 1024) <= 768;
    var snapToPoints = isMobileViewport;
    var lastTapPointIndex = null;
    document.querySelectorAll("button[data-chart-currency]").forEach(function (button) {
        button.addEventListener("click", function () {
            var mode = button.getAttribute("data-chart-currency");
            if (chartCurrencyInput) {
                chartCurrencyInput.value = mode;
            }
            if (filterForm) {
                filterForm.submit();
            }
        });
    });
    if (!ctx) return;
    var setChartHeight = function () {
        if (!chartWrap) return;
        var isFullscreen = document.fullscreenElement === chartWrap;
        var width = window.innerWidth || document.documentElement.clientWidth || 1024;
        // Mobile-first readability: taller chart so labels and crosshair are easier to use.
        var height = isFullscreen ? Math.floor((window.innerHeight || 800) * 0.9) : (width <= 768 ? 460 : 390);
        chartWrap.style.height = height + "px";
        if (priceChart) {
            priceChart.resize();
        }
    };
    setChartHeight();
    window.addEventListener("resize", setChartHeight);
    document.addEventListener("fullscreenchange", setChartHeight);

    if (fullscreenButton && chartWrap) {
        fullscreenButton.addEventListener("click", function () {
            if (document.fullscreenElement === chartWrap) {
                document.exitFullscreen && document.exitFullscreen();
                return;
            }
            if (chartWrap.requestFullscreen) {
                chartWrap.requestFullscreen();
            }
        });
    }
    if (exportButton) {
        exportButton.addEventListener("click", function () {
            if (!priceChart) return;
            var url = priceChart.toBase64Image("image/png", 1);
            var a = document.createElement("a");
            a.href = url;
            a.download = "price-history-" + (new Date().toISOString().slice(0, 10)) + ".png";
            a.click();
        });
    }

    var formatAxisNumber = function (value) {
        var num = Number(value);
        if (!isFinite(num)) return value;
        if (Math.abs(num - Math.round(num)) < 0.000001) {
            return String(Math.round(num));
        }
        var fixed = num.toFixed(2);
        return fixed.replace(/\.?0+$/, "");
    };

    var setActiveRangeButton = function (range) {
        document.querySelectorAll("button[data-range]").forEach(function (btn) {
            var btnRange = btn.getAttribute("data-range");
            var isActive = btnRange === range;
            if (isActive) {
                btn.classList.remove("secondary");
            } else {
                if (!btn.classList.contains("secondary")) btn.classList.add("secondary");
            }
        });
    };

    var detectActiveRangeFromInputs = function () {
        var startInput = document.getElementById("start");
        var endInput = document.getElementById("end");
        if (!startInput || !endInput || !startInput.value || !endInput.value) {
            setActiveRangeButton("reset");
            return;
        }
        var start = new Date(startInput.value);
        var end = new Date(endInput.value);
        if (!isFinite(start.getTime()) || !isFinite(end.getTime())) {
            setActiveRangeButton("reset");
            return;
        }
        var dayMs = 24 * 60 * 60 * 1000;
        var diffDays = Math.round((end - start) / dayMs);
        if (Math.abs(diffDays - 182) <= 3) {
            setActiveRangeButton("6m");
        } else if (Math.abs(diffDays - 365) <= 3) {
            setActiveRangeButton("1y");
        } else if (Math.abs(diffDays - 730) <= 4) {
            setActiveRangeButton("2y");
        } else {
            setActiveRangeButton("reset");
        }
    };

    document.querySelectorAll("button[data-range]").forEach(function (button) {
        button.addEventListener("click", function () {
            var range = button.getAttribute("data-range");
            var startInput = document.getElementById("start");
            var endInput = document.getElementById("end");
            if (!startInput || !endInput) return;
            if (range === "6m") {
                var end = new Date();
                var start = new Date(end.getTime());
                start.setMonth(start.getMonth() - 6);
                if (startInput) {
                    startInput.value = start.toISOString().slice(0, 16);
                }
                if (endInput) {
                    endInput.value = end.toISOString().slice(0, 16);
                }
            } else if (range === "1y") {
                var end = new Date();
                var start = new Date(end.getTime());
                start.setFullYear(start.getFullYear() - 1);
                if (startInput) {
                    startInput.value = start.toISOString().slice(0, 16);
                }
                if (endInput) {
                    endInput.value = end.toISOString().slice(0, 16);
                }
            } else if (range === "2y") {
                var end = new Date();
                var start = new Date(end.getTime());
                start.setFullYear(start.getFullYear() - 2);
                if (startInput) {
                    startInput.value = start.toISOString().slice(0, 16);
                }
                if (endInput) {
                    endInput.value = end.toISOString().slice(0, 16);
                }
            } else if (range === "reset") {
                if (startInput) {
                    startInput.value = "";
                }
                if (endInput) {
                    endInput.value = "";
                }
                startInput.form.submit();
                return;
            }
            setActiveRangeButton(range);
            startInput.form.submit();
        });
    });
    detectActiveRangeFromInputs();
    var crosshair = {
        id: "crosshair",
        afterEvent: function(chart, args) {
            var event = args.event;
            if (!event || event.x === null || event.y === null) return;
            var chartArea = chart.chartArea || {};
            var inside =
                event.x >= (chartArea.left || 0) &&
                event.x <= (chartArea.right || 0) &&
                event.y >= (chartArea.top || 0) &&
                event.y <= (chartArea.bottom || 0);
            if (!inside) return;

            var eventType = (event.type || "").toLowerCase();
            var isTap =
                eventType === "click" ||
                eventType === "touchstart" ||
                eventType === "touchend" ||
                eventType === "pointerup";

            // Mobile behavior: tap once shows crosshair, tap again hides it.
            if (isTap) {
                if (snapToPoints) {
                    var nearest = chart.getElementsAtEventForMode(event, "nearest", { intersect: false }, false);
                    if (nearest && nearest.length) {
                        var idx = nearest[0].index;
                        if (lastTapPointIndex === idx) {
                            chart._crosshair = null;
                            chart.setActiveElements([]);
                            chart.tooltip.setActiveElements([], { x: 0, y: 0 });
                            lastTapPointIndex = null;
                        } else {
                            var point = chart.getDatasetMeta(nearest[0].datasetIndex).data[idx];
                            var p = point.getProps(["x", "y"], true);
                            chart._crosshair = { x: p.x, y: p.y };
                            chart.setActiveElements([{ datasetIndex: nearest[0].datasetIndex, index: idx }]);
                            chart.tooltip.setActiveElements([{ datasetIndex: nearest[0].datasetIndex, index: idx }], { x: p.x, y: p.y });
                            lastTapPointIndex = idx;
                        }
                    } else {
                        chart._crosshair = null;
                        lastTapPointIndex = null;
                    }
                } else {
                    chart._crosshair = chart._crosshair ? null : { x: event.x, y: event.y };
                    lastTapPointIndex = null;
                }
                args.changed = true;
                return;
            }

            if (eventType === "mousemove") {
                if (snapToPoints) {
                    var nearestMove = chart.getElementsAtEventForMode(event, "nearest", { intersect: false }, false);
                    if (nearestMove && nearestMove.length) {
                        var idxMove = nearestMove[0].index;
                        var pointMove = chart.getDatasetMeta(nearestMove[0].datasetIndex).data[idxMove];
                        var pm = pointMove.getProps(["x", "y"], true);
                        chart._crosshair = { x: pm.x, y: pm.y };
                    } else {
                        chart._crosshair = { x: event.x, y: event.y };
                    }
                } else {
                    chart._crosshair = { x: event.x, y: event.y };
                }
                args.changed = true;
                return;
            }

            if (eventType === "mouseout" || eventType === "mouseleave") {
                chart._crosshair = null;
                args.changed = true;
            }
        },
        afterDraw: function(chart) {
            if (!chart._crosshair) return;
            var ctx = chart.ctx;
            var x = chart._crosshair.x;
            var yScale = chart.scales.y;
            var xScale = chart.scales.x;
            if (!xScale || !yScale) return;
            ctx.save();
            ctx.strokeStyle = "rgba(37,99,235,0.4)";
            ctx.lineWidth = 1;
            ctx.setLineDash([4, 4]);
            ctx.beginPath();
            ctx.moveTo(x, yScale.top);
            ctx.lineTo(x, yScale.bottom);
            ctx.stroke();
            ctx.beginPath();
            ctx.moveTo(xScale.left, chart._crosshair.y);
            ctx.lineTo(xScale.right, chart._crosshair.y);
            ctx.stroke();
            ctx.restore();
        }
    };
    var markerPlugin = {
        id: "visibleRangeMarkers",
        afterDatasetsDraw: function (chart) {
            var xScale = chart.scales.x;
            var yScale = chart.scales.y;
            var meta = chart.getDatasetMeta(0);
            if (!xScale || !yScale || !meta || !meta.data || !meta.data.length) return;
            var points = meta.data;
            var startIndex = Math.max(0, Math.floor(Number.isFinite(xScale.min) ? xScale.min : 0));
            var endIndex = Math.min(points.length - 1, Math.ceil(Number.isFinite(xScale.max) ? xScale.max : (points.length - 1)));
            var minIdx = -1;
            var maxIdx = -1;
            var minVal = Infinity;
            var maxVal = -Infinity;
            for (var i = startIndex; i <= endIndex; i += 1) {
                var val = values[i];
                if (!Number.isFinite(val)) continue;
                if (val < minVal) {
                    minVal = val;
                    minIdx = i;
                }
                if (val > maxVal) {
                    maxVal = val;
                    maxIdx = i;
                }
            }
            if (minIdx < 0 || maxIdx < 0) return;
            var drawPoint = function (idx, color, label) {
                var point = points[idx];
                if (!point) return;
                var p = point.getProps(["x", "y"], true);
                var ctx = chart.ctx;
                ctx.save();
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(p.x, p.y, 4, 0, Math.PI * 2);
                ctx.fill();
                ctx.font = "12px sans-serif";
                ctx.fillStyle = color;
                ctx.textAlign = "center";
                ctx.fillText(label, p.x, p.y - 10);
                ctx.restore();
            };
            drawPoint(minIdx, "#16a34a", "Min");
            drawPoint(maxIdx, "#dc2626", "Max");
        }
    };

    var updateSummary = function (chart) {
        if (!summaryMin || !summaryMax || !summaryLatest || !summaryChange) return;
        if (!chart || !values.length) {
            summaryMin.textContent = "-";
            summaryMax.textContent = "-";
            summaryLatest.textContent = "-";
            summaryChange.textContent = "-";
            return;
        }
        var xScale = chart.scales.x;
        var startIndex = Math.max(0, Math.floor(Number.isFinite(xScale.min) ? xScale.min : 0));
        var endIndex = Math.min(values.length - 1, Math.ceil(Number.isFinite(xScale.max) ? xScale.max : (values.length - 1)));
        var visible = [];
        for (var i = startIndex; i <= endIndex; i += 1) {
            if (Number.isFinite(values[i])) {
                visible.push(values[i]);
            }
        }
        if (!visible.length) {
            summaryMin.textContent = "-";
            summaryMax.textContent = "-";
            summaryLatest.textContent = "-";
            summaryChange.textContent = "-";
            return;
        }
        var min = Math.min.apply(null, visible);
        var max = Math.max.apply(null, visible);
        var latest = visible[visible.length - 1];
        var first = visible[0];
        var changePct = first !== 0 ? ((latest - first) / first) * 100 : 0;
        summaryMin.textContent = formatAxisNumber(min) + (currencySymbol ? " " + currencySymbol : "");
        summaryMax.textContent = formatAxisNumber(max) + (currencySymbol ? " " + currencySymbol : "");
        summaryLatest.textContent = formatAxisNumber(latest) + (currencySymbol ? " " + currencySymbol : "");
        summaryChange.textContent = (changePct >= 0 ? "+" : "") + changePct.toFixed(2) + "%";
    };

    var applySnapMode = function () {
        if (!priceChart) return;
        if (snapToggleButton) {
            snapToggleButton.textContent = "Snap: " + (snapToPoints ? "On" : "Off");
            if (snapToPoints) {
                snapToggleButton.classList.remove("secondary");
            } else if (!snapToggleButton.classList.contains("secondary")) {
                snapToggleButton.classList.add("secondary");
            }
        }
        priceChart.options.interaction.mode = snapToPoints ? "nearest" : "index";
        priceChart.options.plugins.tooltip.mode = snapToPoints ? "nearest" : "index";
        priceChart.update("none");
    };

    if (!labels.length || !values.length) {
        if (chartEmptyState) {
            chartEmptyState.style.display = "flex";
        }
        if (ctx) {
            ctx.style.display = "none";
        }
        updateSummary(null);
        return;
    }

    var priceChart = new Chart(ctx, {
        type: "line",
        data: {
            labels: labels,
            datasets: [{
                label: "Price",
                data: values,
                borderColor: "#111827",
                backgroundColor: "rgba(17,24,39,0.04)",
                pointRadius: 0,
                pointHoverRadius: 4,
                tension: 0.2,
                borderWidth: 2,
                fill: false
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            animations: false,
            layout: {
                padding: {
                    top: 18,
                    bottom: 18
                }
            },
            transitions: {
                active: { animation: { duration: 0 } },
                resize: { animation: { duration: 0 } },
                show: { animation: { duration: 0 } },
                hide: { animation: { duration: 0 } }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    mode: snapToPoints ? "nearest" : "index",
                    intersect: false,
                    callbacks: {
                        label: function (ctx) {
                            var suffix = currencySymbol ? " " + currencySymbol : "";
                            return "Price: " + ctx.parsed.y.toFixed(2) + suffix;
                        }
                    }
                }
            },
            interaction: {
                mode: snapToPoints ? "nearest" : "index",
                intersect: false
            },
            scales: {
                x: {
                    ticks: {
                        maxTicksLimit: 8,
                        callback: function (value, index) {
                            var width = window.innerWidth || document.documentElement.clientWidth || 1024;
                            if (width > 768) return this.getLabelForValue(value);
                            var step = Math.max(1, Math.floor(labels.length / 4));
                            if (index % step !== 0 && index !== labels.length - 1) return "";
                            return this.getLabelForValue(value);
                        }
                    },
                    grid: { color: "rgba(17,24,39,0.06)" }
                },
                y: {
                    beginAtZero: false,
                    grid: { color: "rgba(17,24,39,0.06)" },
                    ticks: {
                        callback: function (value) { return formatAxisNumber(value); }
                    }
                }
            }
        },
        plugins: [crosshair, markerPlugin]
    });
    if (snapToggleButton) {
        snapToggleButton.addEventListener("click", function () {
            snapToPoints = !snapToPoints;
            applySnapMode();
        });
    }
    applySnapMode();
    updateSummary(priceChart);
    priceChart.options.onResize = function (chart) {
        updateSummary(chart);
    };
    priceChart.options.animation = {
        onComplete: function () {
            updateSummary(priceChart);
        }
    };
})();
