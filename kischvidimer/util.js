// SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
// SPDX-FileCopyrightText: Copyright 2024 Google LLC
//   Licensed under the Apache License, Version 2.0 (the "License");
//   you may not use this file except in compliance with the License.
//   You may obtain a copy of the License at
//
//       http://www.apache.org/licenses/LICENSE-2.0
//
//   Unless required by applicable law or agreed to in writing, software
//   distributed under the License is distributed on an "AS IS" BASIS,
//   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//   See the License for the specific language governing permissions and
//   limitations under the License.
// SPDX-License-Identifier: Apache-2.0

import { componentHandler } from "js-libraries/material";

export function copyToClipboard(text, indicator) {
  // https://stackoverflow.com/questions/33855641/copy-output-of-a-javascript-variable-to-the-clipboard
  let dummy = document.createElement("textarea");
  document.body.appendChild(dummy);
  dummy.value = text;
  dummy.select();
  document.execCommand("copy");
  document.body.removeChild(dummy);
  // Show the copy toast
  document.getElementById("copy-toast").MaterialSnackbar.showSnackbar({
    message: "Copied " + (indicator || '"' + text + '"') + " to clipboard.",
  });
}

export function toggleDialog(dialog, state) {
  // allow toggle-off without knowing which dialog
  if (state) {
    dialog.parentNode.style.display = "flex";
  } else {
    document.querySelectorAll(".dialogwrapper").forEach((wrapper) => {
      wrapper.style.display = "none";
    });
  }
  document.getElementById("background").style.transition = "filter 0.25s";
  document.getElementById("background").style.filter = state
    ? "blur(4px)"
    : null;
}

export function upgradeDom() {
  componentHandler.upgradeDom();
}

export function escapeHTML(unsafe) {
  return unsafe
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Updates the back/forward history with a new target
export function navigateTo(href, historyOnly) {
  window.history.pushState(null, "", "#" + href);
  if (!historyOnly) {
    window.onpopstate();
  }
}

/** Launches a URL in a new window.
 * If we're in an isolated browser, request the python server to launch the URL
 * in a proper browser. Otherwise, just open a new window.
 */
export function openurl(url) {
  if (!url || url.startsWith("#")) {
    navigateTo((url || "").substr(1)); // drop #
  } else if (
    document.location.hostname !== "localhost" ||
    document.location.pathname !== "/"
  ) {
    window.open(url, "_blank");
  } else {
    fetch("./openurl", {
      method: "POST",
      body: JSON.stringify({ url: url }),
    })
      .then((res) => {
        if (res.status >= 300) {
          window.open(url, "_blank");
        }
      })
      .catch((error) => {
        window.open(url, "_blank");
      });
  }
}
