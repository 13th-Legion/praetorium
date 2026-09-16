/* Quill image upload-on-paste — PP-327.
 *
 * Problem this solves
 * -------------------
 * Quill inserts a pasted or dropped image into the document as a base64
 * `data:` URI. Every rich-text field is sanitized server-side with bleach,
 * whose default protocol allowlist is http/https/mailto — `data:` is not in
 * it. bleach keeps the <img> element and deletes its src, so the image
 * silently vanishes on save with no error shown.
 *
 * Fix: intercept the image BEFORE it enters the document, upload it, and
 * insert a normal https:// URL, which passes sanitization unchanged.
 *
 * Covers all three ways an image can arrive:
 *   1. toolbar image button
 *   2. paste  (Quill does NOT handle pasted files itself)
 *   3. drag-and-drop
 *
 * Usage:
 *   attachQuillImageUpload(quillInstance);
 *   attachQuillImageUpload(quillInstance, '/api/s1/newsletter/image-upload');
 */
(function (global) {
  'use strict';

  var DEFAULT_ENDPOINT = '/api/media/image-upload';

  function showError(quill, msg) {
    // Never fail silently — a silent failure is the exact bug being fixed.
    try {
      var host = quill && quill.root ? quill.root.parentNode : null;
      if (host) {
        var box = host.querySelector('.quill-upload-error');
        if (!box) {
          box = document.createElement('div');
          box.className = 'quill-upload-error';
          box.setAttribute('role', 'alert');
          box.style.cssText =
            'color:#ef5350;background:#2a1a1a;border:1px solid #ef5350;' +
            'border-radius:4px;padding:6px 10px;margin-top:6px;font-size:13px;';
          host.appendChild(box);
        }
        box.textContent = msg;
        clearTimeout(box._t);
        box._t = setTimeout(function () {
          if (box && box.parentNode) box.parentNode.removeChild(box);
        }, 8000);
        return;
      }
    } catch (e) { /* fall through to alert */ }
    alert(msg);
  }

  function insertImage(quill, url) {
    var range = quill.getSelection(true) || { index: quill.getLength() };
    quill.insertEmbed(range.index, 'image', url);
    quill.setSelection(range.index + 1);
  }

  function uploadFile(quill, endpoint, file) {
    var fd = new FormData();
    fd.append('file', file);
    return fetch(endpoint, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin'
    })
      .then(function (r) {
        // An auth redirect returns HTML, not JSON; surface that clearly
        // instead of throwing an opaque JSON parse error.
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('application/json') === -1) {
          throw new Error(
            r.status === 401 || r.status === 403 || r.redirected
              ? 'Not signed in, or not authorized to upload images.'
              : 'Unexpected server response (' + r.status + ').'
          );
        }
        return r.json();
      })
      .then(function (d) {
        if (!d || d.error) throw new Error((d && d.error) || 'Upload failed.');
        if (!d.url) throw new Error('Server did not return an image URL.');
        insertImage(quill, d.url);
      })
      .catch(function (e) {
        showError(quill, 'Image upload failed: ' + (e && e.message ? e.message : e));
      });
  }

  function imageFilesFrom(list) {
    var out = [];
    if (!list) return out;
    for (var i = 0; i < list.length; i++) {
      var it = list[i];
      // DataTransferItem (paste) vs File (drop)
      if (it && typeof it.getAsFile === 'function') {
        if (it.kind === 'file' && it.type && it.type.indexOf('image/') === 0) {
          var f = it.getAsFile();
          if (f) out.push(f);
        }
      } else if (it && it.type && it.type.indexOf('image/') === 0) {
        out.push(it);
      }
    }
    return out;
  }

  function attachQuillImageUpload(quill, endpoint) {
    if (!quill || !quill.root) return;
    if (quill.__imageUploadAttached) return; // idempotent
    quill.__imageUploadAttached = true;
    endpoint = endpoint || DEFAULT_ENDPOINT;

    // 1. Toolbar button
    try {
      var toolbar = quill.getModule('toolbar');
      if (toolbar && typeof toolbar.addHandler === 'function') {
        toolbar.addHandler('image', function () {
          var input = document.createElement('input');
          input.setAttribute('type', 'file');
          input.setAttribute('accept', 'image/png,image/jpeg,image/gif,image/webp');
          input.click();
          input.onchange = function () {
            var file = input.files && input.files[0];
            if (file) uploadFile(quill, endpoint, file);
          };
        });
      }
    } catch (e) { /* toolbar is optional */ }

    // 2. Paste — must preventDefault so the base64 never enters the document.
    quill.root.addEventListener('paste', function (e) {
      var cd = e.clipboardData || global.clipboardData;
      if (!cd) return;
      var files = imageFilesFrom(cd.items && cd.items.length ? cd.items : cd.files);
      if (!files.length) return; // normal text paste — leave Quill alone
      e.preventDefault();
      e.stopPropagation();
      files.forEach(function (f) { uploadFile(quill, endpoint, f); });
    }, true);

    // 3. Drag and drop
    quill.root.addEventListener('drop', function (e) {
      var dt = e.dataTransfer;
      if (!dt) return;
      var files = imageFilesFrom(dt.files && dt.files.length ? dt.files : dt.items);
      if (!files.length) return;
      e.preventDefault();
      e.stopPropagation();
      files.forEach(function (f) { uploadFile(quill, endpoint, f); });
    }, true);
  }

  global.attachQuillImageUpload = attachQuillImageUpload;
})(window);
