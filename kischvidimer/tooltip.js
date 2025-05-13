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

let fixed = false;
let tt = null;
let curUrl = null;
export let curResult = null;
export let curPageElems = null; // list of groups of elements for curResult

export function init() {
  tt = document.getElementById("tooltip");

  document.getElementById("tooltiplink").addEventListener("click", () => {
    Util.copyToClipboard(curUrl, "link");
  });
}

export function isfixed() {
  return fixed;
}

export function isvisible() {
  return tt.style.display !== "none";
}

export function hide(always) {
  if (always || fixed) {
    fixed = false;
    tt.style.display = "none";
    tt.style.opacity = "0.8";
  }
}

export function fix() {
  if (tt.style.display !== "none") {
    tt.style.opacity = 1;
    fixed = true;
  }
}

export function show(fix) {
  tt.style.display = "inline";
  if (fix) {
    tt.style.opacity = 1;
  } else {
    tt.style.opacity = 0.8;
  }
  fixed = fix;
}

export function setResult(DB, result, context) {
  if (result !== curResult) {
    curPageElems = null;
  }
  curResult = result;
  curUrl = `${window.location.href.split("#")[0]}#${DB.pageName()},${result.value}`;
  document.getElementById("tooltiptype").textContent =
    result.type.substr(0, 1).toUpperCase() + result.type.substr(1);
  document.getElementById("tooltipname").textContent = result.display;
  document.getElementById("tooltipcontext").textContent = context;

  setProperties(Object.keys(result.data).length > 1 ? result.data : null);
  setPageList(DB, result.pages, result.value);
}

function setProperties(properties) {
  const propdiv = document.getElementById("propdiv");
  propdiv.style.display = properties ? null : "none";
  if (!properties) {
    return;
  }
  // Bubble Value to the top and do a case-insensitive sort on the rest
  let html = Object.entries(properties)
    .sort(
      (a, b) =>
        a[0] &&
        (-1 * (a[0].toLowerCase() == "value") ||
          a[0].localeCompare(b[0], undefined, { sensitivity: "base" })),
    )
    .map(([prop, data]) => {
      let propl = prop && prop.toLowerCase();
      if (!prop || prop[0] < " " || !data || propl == "reference") {
        return "";
      }
      let txt = prop + ": ";
      let is_link = /^https?:[/][/]/.test(data);
      if (is_link) {
        let href = data.replace(/["\\]/g, "");
        txt += `<a href="${href}" onclick="Util.openurl(unescape('${escape(data)}')); return false;">`;
        let split = data.split("/");
        if (split.length > 4) {
          data = split
            .slice(0, 3)
            .concat(["...", split[split.length - 1]])
            .join("/");
        }
      }
      txt += Util.escapeHTML(data);
      if (is_link) {
        txt += "</a>";
      }
      return txt;
    })
    .filter((s) => s)
    .join("<br>");
  propdiv.innerHTML = html;
}

function setPageList(DB, pages, id) {
  let pagelistHTML = "";
  if (pages) {
    let pCounts = {};
    for (let p of pages) {
      pCounts[p] = (pCounts[p] || 0) + 1;
    }
    for (let page in pCounts) {
      let p = parseInt(page);
      pagelistHTML +=
        '<div><a class="itempagelink" style="color:blue" ' +
        `href="#${DB.pageName(p)},${escape(id)}" ` +
        'onclick="Search.clickedPageLink(this, event); return false">' +
        `${DB.pageName(p)}${pCounts[p] > 1 ? " (" + pCounts[p] + ")" : ""}` +
        "</a></div>";
    }
  }
  document.getElementById("tooltiplinks").innerHTML = pagelistHTML;
}

export function onSvgMouseMove(evt) {
  if (isfixed()) {
    return;
  }
  tt.style.left =
    Math.min(evt.pageX + 20, window.innerWidth - tt.offsetWidth) + "px";
  tt.style.top =
    (evt.pageY + tt.offsetHeight + 20 > window.innerHeight
      ? evt.pageY - tt.offsetHeight - 20
      : evt.pageY + 20) + "px";
}
