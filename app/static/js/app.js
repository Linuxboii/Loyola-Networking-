/* Loyola Networking — progressive enhancement only.
   Every interaction below also works as a plain form post if JavaScript
   fails; this file only removes the page reload. No framework, because a
   200 KB download on campus mobile data buys nothing here. */
(function () {
    "use strict";

    function csrf() {
        var el = document.querySelector('meta[name="csrf"]');
        return el ? el.content : "";
    }

    function post(url, data) {
        var body = new FormData();
        body.append("csrf", csrf());
        Object.keys(data || {}).forEach(function (k) { body.append(k, data[k]); });
        return fetch(url, {
            method: "POST",
            body: body,
            headers: { "HX-Request": "true" },
            credentials: "same-origin"
        });
    }

    /* ---------------------------------------------------------- voting */
    document.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-vote]");
        if (!btn) return;
        e.preventDefault();
        var box = btn.closest("[data-vote-box]");
        if (!box || box.dataset.busy === "1") return;
        box.dataset.busy = "1";

        post("/vote/" + box.dataset.targetType + "/" + box.dataset.targetId, {
            value: btn.dataset.vote
        }).then(function (r) {
            return r.text().then(function (html) {
                if (r.ok) {
                    box.outerHTML = html;
                } else {
                    var note = box.querySelector(".vote-error");
                    if (!note) {
                        note = document.createElement("span");
                        note.className = "vote-error";
                        box.appendChild(note);
                    }
                    note.textContent = html.replace(/<[^>]*>/g, "").trim() || "Could not vote";
                    box.dataset.busy = "";
                }
            });
        }).catch(function () { box.dataset.busy = ""; });
    });

    /* --------------------------------------------------- load more feed */
    document.addEventListener("click", function (e) {
        var more = e.target.closest("[data-more]");
        if (!more) return;
        e.preventDefault();
        more.disabled = true;
        more.textContent = "Loading…";
        fetch(more.dataset.more, {
            headers: { "HX-Request": "true" },
            credentials: "same-origin"
        }).then(function (r) { return r.text(); }).then(function (html) {
            var target = document.querySelector(more.dataset.target || ".board");
            var wrap = document.createElement("div");
            wrap.innerHTML = html;
            var next = wrap.querySelector("[data-next-url]");
            while (wrap.firstElementChild && wrap.firstElementChild.classList.contains("row")) {
                target.appendChild(wrap.firstElementChild);
            }
            if (next && next.dataset.nextUrl) {
                more.dataset.more = next.dataset.nextUrl;
                more.disabled = false;
                more.textContent = "Show more";
            } else {
                more.remove();
            }
        }).catch(function () {
            more.disabled = false;
            more.textContent = "Show more";
        });
    });

    /* ------------------------------------------------ confirm dangerous */
    document.addEventListener("submit", function (e) {
        var form = e.target;
        var msg = form.getAttribute("data-confirm");
        if (msg && !window.confirm(msg)) {
            e.preventDefault();
        }
    });

    /* ------------------------------------------------- character counts */
    document.querySelectorAll("[data-count-for]").forEach(function (out) {
        var input = document.getElementById(out.dataset.countFor);
        if (!input) return;
        var max = input.getAttribute("maxlength");
        function render() {
            out.textContent = max ? input.value.length + " / " + max : String(input.value.length);
        }
        input.addEventListener("input", render);
        render();
    });

    /* ------------------------------------------------ composer switcher */
    var kindPicker = document.querySelector("[data-kind-picker]");
    if (kindPicker) {
        kindPicker.addEventListener("change", function () {
            document.querySelectorAll("[data-for-kind]").forEach(function (el) {
                var kinds = el.dataset.forKind.split(" ");
                el.hidden = kinds.indexOf(kindPicker.value) === -1;
            });
        });
        kindPicker.dispatchEvent(new Event("change"));
    }

    /* ------------------------------------------------------ ID capture
       The PRD asks for guided live capture rather than a gallery upload.
       getUserMedia gives us that where it is available; where it is not
       (older Android browsers, denied permission, no HTTPS) the file input
       stays visible so nobody is locked out of verifying. */
    document.querySelectorAll("[data-capture]").forEach(function (root) {
        var video = root.querySelector("video");
        var canvas = root.querySelector("canvas");
        var preview = root.querySelector("[data-preview]");
        var fileInput = root.querySelector('input[type="file"]');
        var liveFlag = root.querySelector('input[name="live_capture"]');
        var startBtn = root.querySelector("[data-start]");
        var shootBtn = root.querySelector("[data-shoot]");
        var retryBtn = root.querySelector("[data-retry]");
        var submitBtn = root.querySelector('button[type="submit"]');
        var fallback = root.querySelector("[data-fallback]");
        var stream = null;
        var facing = root.dataset.capture === "selfie" ? "user" : "environment";

        function stop() {
            if (stream) {
                stream.getTracks().forEach(function (t) { t.stop(); });
                stream = null;
            }
        }

        function show(el, on) { if (el) el.hidden = !on; }

        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            // No camera API (older Android browser, or not a secure context):
            // leave the plain file input in place so nobody is locked out.
            show(startBtn, false);
            show(fallback, true);
            return;
        }
        show(fallback, false);

        if (startBtn) {
            startBtn.addEventListener("click", function () {
                navigator.mediaDevices.getUserMedia({
                    video: { facingMode: facing, width: { ideal: 1920 } },
                    audio: false
                }).then(function (s) {
                    stream = s;
                    video.srcObject = s;
                    video.play();
                    show(video.parentElement, true);
                    show(startBtn, false);
                    show(shootBtn, true);
                    show(fallback, false);
                }).catch(function () {
                    show(fallback, true);
                    var note = root.querySelector("[data-camera-error]");
                    if (note) note.hidden = false;
                });
            });
        }

        if (shootBtn) {
            shootBtn.addEventListener("click", function () {
                var w = video.videoWidth || 1280;
                var h = video.videoHeight || 720;
                canvas.width = w;
                canvas.height = h;
                canvas.getContext("2d").drawImage(video, 0, 0, w, h);
                canvas.toBlob(function (blob) {
                    if (!blob) return;
                    var dt = new DataTransfer();
                    dt.items.add(new File([blob], "capture.jpg", { type: "image/jpeg" }));
                    fileInput.files = dt.files;
                    if (liveFlag) liveFlag.value = "1";
                    if (preview) {
                        preview.src = URL.createObjectURL(blob);
                        show(preview, true);
                    }
                    stop();
                    show(video.parentElement, false);
                    show(shootBtn, false);
                    show(retryBtn, true);
                    if (submitBtn) submitBtn.disabled = false;
                }, "image/jpeg", 0.92);
            });
        }

        if (retryBtn) {
            retryBtn.addEventListener("click", function () {
                show(preview, false);
                show(retryBtn, false);
                show(startBtn, true);
                if (submitBtn) submitBtn.disabled = true;
                if (liveFlag) liveFlag.value = "0";
                fileInput.value = "";
            });
        }

        if (fileInput) {
            fileInput.addEventListener("change", function () {
                if (fileInput.files && fileInput.files[0] && submitBtn) {
                    submitBtn.disabled = false;
                    if (preview) {
                        preview.src = URL.createObjectURL(fileInput.files[0]);
                        show(preview, true);
                    }
                }
            });
        }

        var form = root.closest("form");
        if (form) {
            form.addEventListener("submit", function () {
                stop();
                if (submitBtn) {
                    submitBtn.disabled = true;
                    submitBtn.textContent = "Reading your card…";
                }
            });
        }
        window.addEventListener("pagehide", stop);
    });

    /* ------------------------------------------------ mark notifications */
    var bell = document.querySelector("[data-mark-read]");
    if (bell) {
        bell.addEventListener("click", function () {
            post("/notifications/read", {});
        });
    }
})();
