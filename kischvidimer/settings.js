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

import * as Util from "util";

export function init(uiData, setTheme) {
  const tprev = "lwfbgphrv"; // order of the theme colors to use to render the label
  document.querySelector(".themeselect").innerHTML = Object.entries(
    uiData.themes,
  )
    .map(
      ([name, data]) => `
      <label for="toption-${name}"
        style="margin-bottom:10px" class="mdl-radio mdl-js-radio mdl-js-ripple-effect">
        <input type="radio" id="toption-${name}" class="mdl-radio__button" name="options" value="${name}">
        <span class="mdl-radio__label" style="background-color:${data.d}">
          ${name
            .split("")
            .map(
              (c, i) =>
                `<span style="color:${data[tprev.substr(i % tprev.length, 1)]}">${c}</span>`,
            )
            .join("")}
        </span>
      </label>`,
    )
    .join("");

  document.getElementById("settingsbutton").addEventListener("click", () => {
    document.getElementById("uiversion").innerText = `UI: ${uiData.vers}`;

    // show zoom control selection
    if (get("ShowZoomControls") == "shown") {
      document
        .getElementById("zoomcontrolcheckboxlabel")
        .MaterialCheckbox.check();
    } else {
      document
        .getElementById("zoomcontrolcheckboxlabel")
        .MaterialCheckbox.uncheck();
    }

    // zoom to content
    if (get("ZoomToContent") == "zoom") {
      document
        .getElementById("zoomcontentcheckboxlabel")
        .MaterialCheckbox.check();
    } else {
      document
        .getElementById("zoomcontentcheckboxlabel")
        .MaterialCheckbox.uncheck();
    }

    let toption = document.getElementById(
      "toption-" + get("SchematicTheme", uiData.themeDefault),
    );
    if (toption) {
      toption.parentNode.MaterialRadio.check();
    }
    let dialog = document.getElementById("settingsdialog");
    Util.toggleDialog(dialog, true);
  });

  document.getElementById("uiversion").addEventListener("click", () => {
    Util.copyToClipboard(uiData.vers, "kischvidimer version");
  });

  document.getElementById("closesettings").addEventListener("click", () => {
    let dialog = document.getElementById("settingsdialog");
    Util.toggleDialog(dialog, false);
  });

  document.getElementById("applysettings").addEventListener("click", () => {
    set(
      "ShowZoomControls",
      document.getElementById("zoomcontrolcheckbox").checked
        ? "shown"
        : "hidden",
    );
    if (document.getElementById("zoomcontrolcheckbox").checked) {
      document.getElementById("zoomcontrols").style.display = "inline";
    } else {
      document.getElementById("zoomcontrols").style.display = "none";
    }
    set(
      "ZoomToContent",
      document.getElementById("zoomcontentcheckbox").checked ? "zoom" : "",
    );

    let dialog = document.getElementById("settingsdialog");
    let selectedTheme = document.querySelector("[id^=toption]:checked");
    setTheme(selectedTheme.getAttribute("value"));
    Util.toggleDialog(dialog, false);
  });
}

export function get(name, defaultValue) {
  let stored = window.localStorage.getItem(name);
  if (!stored || stored === "null") {
    return defaultValue;
  }
  return stored;
}

export function set(name, value) {
  window.localStorage.setItem(name, value);
}
