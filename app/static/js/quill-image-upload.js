/*
 * quill-image-upload.js — upload-on-paste/drop/toolbar for every Quill editor.
 *
 * THE BUG THIS FIXES
 * ------------------
 * Quill embeds a pasted or dropped image as a base64 `data:` URI. Every server
 * sanitizer runs bleach, whose default protocol allowlist is http/https/mailto
 * only. bleach therefore KEEPS the <img> element and DELETES its src, leaving
 * `<img width="200">` — an empty tag that renders as nothing. The image
 * silently vanishes on save with no error anywhere.
 *
 * The fix is NOT to allow `data:` (it bloats the row and re-admits
 * data:image/svg+xml as a script vector). The fix is to upload the blob and
 * embed the hosted https:// URL, which survives sanitization unchanged.
 *
 * WHAT THIS MODULE INTERCEPTS
 * ---------------------------
 *   1. the toolbar "image" button (file picker)
 *   2. native `paste` of image files      (e.clipboardData.items)
 *   3. native `drop` of image files       (e.dataTransfer.files)
 *   4. Quill's own built-in `uploader` module, which is what actually inserts
 *      the base64 — it exists in both Quill 1.3.x and 2.x and its default
 *      handler is a FileReader -> readAsDataURL -> insertEmbed. Overriding its
 *      handler is the belt to the native listeners' suspenders.
 *
 * The paste/drop listeners are registered on `document` in the CAPTURE phase,
 * not on quill.root, because Quill's clipboard/uploader modules bind their own
 * listeners to quill.root first and at-target listeners fire in registration
 * order. Capturing on an ancestor lets us run first and call stopPropagation()
 * so the base64 path never executes at all. We only intervene when the event
 * actually carries image files — plain text/HTML pastes fall straight through
 * to Quill untouched.
 *
 * Supports Quill 1.3.6 (events.html, event_detail.html) and Quill 2.0.3
 * (newsletter_edit.html, s1_email_blast.html).
 *
 * Usage:
 *     attachQuillImageUpload(quill);                      // default endpoint
 *     attachQuillImageUpload(quill, '/api/media/image-upload');
 *     attachQuillImageUpload(quill, endpoint, { onError: fn });
 */
(function () {
    "use strict";

    var DEFAULT_ENDPOINT = "/api/media/image-upload";

    // Mirrors ALLOWED_IMAGE_MIMES in app/newsletter_assets.py. The server is
    // authoritative; this only gives a faster, clearer message. SVG is
    // deliberately absent — data:image/svg+xml is a script vector.
    var ALLOWED_MIMES = ["image/png", "image/jpeg", "image/gif", "image/webp"];
    var MAX_BYTES = 5 * 1024 * 1024; // mirrors MAX_IMAGE_BYTES

    function isImage(file) {
        return !!file && typeof file.type === "string" &&
            file.type.toLowerCase().indexOf("image/") === 0;
    }

    function isAllowed(file) {
        return !!file && ALLOWED_MIMES.indexOf(String(file.type).toLowerCase()) !== -1;
    }

    // ── Visible feedback ────────────────────────────────────────────────────
    // Silent failure is the exact bug being fixed, so every failure path lands
    // here. The notice is inserted directly above the editor container.

    function noticeEl(quill) {
        var container = quill && quill.root && quill.root.parentNode;
        if (!container) { return null; }
        if (container._qiuNotice && container._qiuNotice.parentNode) {
            return container._qiuNotice;
        }
        var el = document.createElement("div");
        el.className = "quill-image-upload-notice";
        el.setAttribute("role", "status");
        el.setAttribute("aria-live", "polite");
        el.style.cssText = "display:none;margin:6px 0;padding:8px 10px;" +
            "border-radius:4px;font-size:13px;font-weight:600;line-height:1.4;";
        if (container.parentNode) {
            container.parentNode.insertBefore(el, container);
        } else {
            container.appendChild(el);
        }
        container._qiuNotice = el;
        return el;
    }

    function notify(quill, message, kind) {
        var el = noticeEl(quill);
        if (!el) {
            // No place to render — never swallow the message.
            if (kind === "error") { window.alert(message); }
            return;
        }
        if (el._qiuTimer) { window.clearTimeout(el._qiuTimer); el._qiuTimer = null; }
        el.textContent = message;
        el.style.display = "block";
        if (kind === "error") {
            el.style.background = "rgba(239,83,80,0.12)";
            el.style.border = "1px solid #ef5350";
            el.style.color = "#ef5350";
        } else {
            el.style.background = "rgba(255,255,255,0.06)";
            el.style.border = "1px solid #666";
            el.style.color = "#bbb";
        }
        // Errors linger long enough to actually be read; progress clears fast.
        var ttl = (kind === "error") ? 10000 : 2000;
        el._qiuTimer = window.setTimeout(function () {
            el.style.display = "none";
        }, ttl);
    }

    function fail(quill, message, opts) {
        if (window.console && window.console.error) {
            window.console.error("[quill-image-upload] " + message);
        }
        if (opts && typeof opts.onError === "function") {
            try { opts.onError(message); return; } catch (e) { /* fall through */ }
        }
        notify(quill, message, "error");
    }

    // ── Upload + insert ─────────────────────────────────────────────────────

    function uploadOne(file, endpoint) {
        var fd = new FormData();
        // Filenames are never trusted for the stored extension (the server
        // derives it from the validated MIME), but send one so the multipart
        // part is a proper file part in every browser.
        fd.append("file", file, file.name || "pasted-image");
        return window.fetch(endpoint, {
            method: "POST",
            body: fd,
            credentials: "same-origin"
        }).then(function (resp) {
            return resp.json().catch(function () {
                throw new Error("Server returned a non-JSON response (HTTP " + resp.status + ").");
            }).then(function (data) {
                if (!resp.ok || (data && data.error)) {
                    throw new Error(
                        (data && (data.error || data.detail)) ||
                        ("Upload failed (HTTP " + resp.status + ").")
                    );
                }
                if (!data || !data.url) {
                    throw new Error("Server did not return an image URL.");
                }
                return data.url;
            });
        });
    }

    /* Upload files one at a time and insert each at the caret, so multi-image
     * pastes keep their order and we never fan out N parallel requests. */
    function uploadAndInsert(quill, files, startIndex, endpoint, opts) {
        var queue = [];
        var rejected = [];
        for (var i = 0; i < files.length; i++) {
            var f = files[i];
            if (!isImage(f)) { continue; }
            if (!isAllowed(f)) {
                rejected.push((f.type || "unknown") + " is not a supported image type");
            } else if (f.size > MAX_BYTES) {
                rejected.push((f.name || "image") + " is larger than 5MB");
            } else {
                queue.push(f);
            }
        }
        if (rejected.length) {
            fail(quill, "Image not inserted — " + rejected.join("; ") +
                ". Allowed: PNG, JPEG, GIF, WebP up to 5MB.", opts);
        }
        if (!queue.length) { return; }

        notify(quill, queue.length > 1
            ? "Uploading " + queue.length + " images…"
            : "Uploading image…", "info");

        var index = (typeof startIndex === "number" && startIndex >= 0)
            ? startIndex : quill.getLength();

        var chain = Promise.resolve();
        queue.forEach(function (file) {
            chain = chain.then(function () {
                return uploadOne(file, endpoint).then(function (url) {
                    quill.insertEmbed(index, "image", url, "user");
                    index += 1;
                    quill.setSelection(index, 0, "silent");
                    notify(quill, "Image uploaded.", "info");
                });
            });
        });
        chain.catch(function (err) {
            fail(quill, "Image upload failed: " + (err && err.message ? err.message : err), opts);
        });
    }

    // Best-effort caret index for a drop, so the image lands where it was
    // dropped rather than wherever the caret happened to be.
    function indexFromPoint(quill, x, y) {
        try {
            var native = null;
            if (document.caretRangeFromPoint) {
                native = document.caretRangeFromPoint(x, y);
            } else if (document.caretPositionFromPoint) {
                var pos = document.caretPositionFromPoint(x, y);
                if (pos) {
                    native = document.createRange();
                    native.setStart(pos.offsetNode, pos.offset);
                    native.setEnd(pos.offsetNode, pos.offset);
                }
            }
            if (native && quill.selection && quill.selection.normalizeNative) {
                var normalized = quill.selection.normalizeNative(native);
                var range = quill.selection.normalizedToRange(normalized);
                if (range && typeof range.index === "number") { return range.index; }
            }
        } catch (e) { /* fall through to the current selection */ }
        var sel = quill.getSelection();
        return sel ? sel.index : quill.getLength();
    }

    function currentIndex(quill) {
        var sel = quill.getSelection();
        if (sel) { return sel.index; }
        try {
            var forced = quill.getSelection(true);
            if (forced) { return forced.index; }
        } catch (e) { /* editor may not be focused */ }
        return quill.getLength();
    }

    function inEditor(quill, target) {
        return !!(target && quill.root &&
            (target === quill.root || quill.root.contains(target)));
    }

    // ── Public entry point ──────────────────────────────────────────────────

    function attachQuillImageUpload(quill, endpoint, options) {
        if (!quill || !quill.root) { return; }
        if (quill._qiuAttached) { return; }   // idempotent
        quill._qiuAttached = true;

        var opts = options || {};
        var url = DEFAULT_ENDPOINT;
        if (typeof endpoint === "string" && endpoint) {
            url = endpoint;
        } else if (endpoint && typeof endpoint === "object") {
            opts = endpoint;
            if (opts.endpoint) { url = opts.endpoint; }
        }

        // 1. Toolbar image button (no-op when the toolbar has no image control).
        try {
            var toolbar = quill.getModule("toolbar");
            if (toolbar && typeof toolbar.addHandler === "function") {
                toolbar.addHandler("image", function () {
                    var input = document.createElement("input");
                    input.setAttribute("type", "file");
                    input.setAttribute("accept", ALLOWED_MIMES.join(","));
                    input.style.display = "none";
                    input.onchange = function () {
                        if (input.files && input.files.length) {
                            uploadAndInsert(quill, input.files, currentIndex(quill), url, opts);
                        }
                    };
                    input.click();
                });
            }
        } catch (e) { /* editor configured without a toolbar */ }

        // 2. Neutralize Quill's built-in uploader, which is the module that
        //    actually performs the base64 insert on paste/drop. Present in
        //    Quill >= 1.3.0 and in 2.x.
        try {
            var uploader = quill.getModule("uploader");
            if (uploader) {
                uploader.handler = function (range, files) {
                    var at = (range && typeof range.index === "number")
                        ? range.index : currentIndex(quill);
                    uploadAndInsert(quill, files || [], at, url, opts);
                };
            }
        } catch (e) { /* older build without the uploader module */ }

        // 3. Native paste. Capture phase on document so we run before Quill's
        //    own root-bound listeners and can stop them entirely.
        document.addEventListener("paste", function (e) {
            if (!inEditor(quill, e.target)) { return; }
            var cd = e.clipboardData || window.clipboardData;
            if (!cd) { return; }
            var files = [];
            var items = cd.items;
            if (items) {
                for (var i = 0; i < items.length; i++) {
                    var item = items[i];
                    if (item.kind === "file" && String(item.type).indexOf("image/") === 0) {
                        var f = item.getAsFile();
                        if (f) { files.push(f); }
                    }
                }
            }
            // Safari/older paths expose clipboardData.files instead of items.
            if (!files.length && cd.files && cd.files.length) {
                for (var j = 0; j < cd.files.length; j++) {
                    if (isImage(cd.files[j])) { files.push(cd.files[j]); }
                }
            }
            if (!files.length) { return; }  // plain text/HTML — let Quill handle it

            // Stop the browser AND Quill: base64 must never enter the document.
            e.preventDefault();
            e.stopPropagation();
            if (e.stopImmediatePropagation) { e.stopImmediatePropagation(); }

            uploadAndInsert(quill, files, currentIndex(quill), url, opts);
        }, true);

        // 4. Native drop.
        document.addEventListener("drop", function (e) {
            if (!inEditor(quill, e.target)) { return; }
            var dt = e.dataTransfer;
            if (!dt) { return; }
            var files = [];
            if (dt.files && dt.files.length) {
                for (var i = 0; i < dt.files.length; i++) {
                    if (isImage(dt.files[i])) { files.push(dt.files[i]); }
                }
            }
            if (!files.length) { return; }  // dragged text/HTML — leave it to Quill

            e.preventDefault();
            e.stopPropagation();
            if (e.stopImmediatePropagation) { e.stopImmediatePropagation(); }

            uploadAndInsert(quill, files, indexFromPoint(quill, e.clientX, e.clientY), url, opts);
        }, true);
    }

    window.attachQuillImageUpload = attachQuillImageUpload;
})();
