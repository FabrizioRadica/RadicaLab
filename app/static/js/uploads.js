/* Source image upload with browser preview (Image2Video) */

window.WVGUpload = (function () {
  "use strict";

  var input, dropzone;

  function pick() { if (input) input.click(); }

  async function upload(file) {
    if (!file || !WVG.project) return;
    var form = new FormData();
    form.append("file", file, file.name);
    dropzone.textContent = "Uploading " + file.name + "…";
    try {
      var res = await WVG.api("/api/projects/" + WVG.project.id + "/source-image", {
        method: "POST",
        body: form
      });
      WVG.project.source_image = res.filename;
      var img = document.getElementById("source-preview-img");
      img.src = res.url + "?t=" + Date.now();
      document.getElementById("source-preview-wrap").style.display = "";
      dropzone.style.display = "none";
      WVG.toast("Source image uploaded", "success");
    } catch (e) {
      WVG.toast("Upload failed", "error", e.message);
      resetDropzone();
    }
  }

  function resetDropzone() {
    dropzone.innerHTML = "<strong>Upload source image</strong><br>Drop an image here or click to browse<br>" +
      '<span class="small muted">jpg, jpeg, png, webp</span>';
    dropzone.style.display = "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    input = document.getElementById("source-file-input");
    dropzone = document.getElementById("dropzone");
    if (!input || !dropzone) return;

    dropzone.addEventListener("click", pick);
    input.addEventListener("change", function () { upload(input.files[0]); input.value = ""; });

    ["dragenter", "dragover"].forEach(function (evt) {
      dropzone.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (evt) {
      dropzone.addEventListener(evt, function (e) { e.preventDefault(); dropzone.classList.remove("drag"); });
    });
    dropzone.addEventListener("drop", function (e) {
      if (e.dataTransfer.files.length) upload(e.dataTransfer.files[0]);
    });
  });

  return { pick: pick };
})();
