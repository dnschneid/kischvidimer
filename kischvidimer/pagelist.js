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

import * as DB from "database";
import * as Util from "util";

export function init(searchSetActive) {
  document.getElementById("pagefilter").addEventListener("input", (e) => {
    update(states(e.target.value));
  });

  DB.forEachPage((p, i, pages) => {
    let spanElem = document.createElement("a");
    spanElem.classList.add("mdl-navigation__link");
    spanElem.classList.add("navitem");
    let prefix = "";
    for (let d = 1; d <= p.depth; d++) {
      let continues = false;
      for (let j = i + 1; j < pages.length; j++) {
        if (pages[j].depth <= d) {
          continues = pages[j].depth == d;
          break;
        }
      }
      if (d == p.depth) {
        prefix += continues ? "├" : "└";
      } else {
        prefix += continues ? "│" : "&nbsp";
      }
      prefix += "&nbsp";
    }
    let name = p.name.split("/");
    name = name[name.length - 1] || "root";
    spanElem.innerHTML = `<code>${prefix}${p.pn}&nbsp;</code>${name}`;
    spanElem.id = `${i}_link`;
    spanElem.addEventListener("click", () => {
      // manual click on another page should hide search results...
      searchSetActive(false);
      Util.navigateTo(p.name);
    });
    document.getElementById("pagelist").appendChild(spanElem);
  });
  update();
}

export function isFocused() {
  return document.activeElement == document.getElementById("pagefilter");
}

function states(filter) {
  let pgstates = [];
  DB.forEachPage((p, i, pages) => {
    pgstates.push(false);
    if (filter && DB.matchDistance(filter, p.name) == DB.NO_MATCH) {
      return;
    }
    pgstates[i] = true;
    // Flag unselected parents to be grayed
    for (let d = p.depth - 1; i >= 0 && d >= 0; d--) {
      for (i--; i >= 0 && pages[i].depth !== d; i--);
      if (i >= 0) {
        if (pgstates[i]) {
          break;
        }
        pgstates[i] = 2;
      }
    }
  });
  return pgstates;
}

function update(pgstates) {
  for (let i = 0; i < DB.numPages(); i++) {
    let elem = document.getElementById(`${i}_link`);
    let state = !pgstates || pgstates[i];
    if (state) {
      elem.style.display = "inline";
      if (i % 2) {
        elem.classList.add("navitemeven");
      } else {
        elem.classList.remove("navitemeven");
      }
      if (state == 2) {
        elem.classList.add("navitemdim");
      } else {
        elem.classList.remove("navitemdim");
      }
    } else {
      elem.style.display = "none";
    }
  }
}

export function onEnterKey(e) {
  let pgstates = states(e.target.value);
  // force in the current page to make iteration easier
  pgstates[DB.curPageIndex] = true;
  let pages = [];
  pgstates.forEach((state, i) => state === true && pages.push(i));
  if (!pages.length) {
    return;
  }
  let nextIndex = pages.indexOf(DB.curPageIndex) + (e.shiftKey ? -1 : 1);
  if (nextIndex == pages.length) {
    nextIndex = 0;
  } else if (nextIndex == -1) {
    nextIndex = pages.length - 1;
  }
  Util.navigateTo(DB.pageName(pages[nextIndex]));
}

export function select(pageIndex) {
  for (let i = 0; i < DB.numPages(); i++) {
    let elem = document.getElementById(`${i}_link`);
    if (i === pageIndex) {
      elem.classList.add("currentnavitem");
      if (typeof elem.scrollIntoViewIfNeeded === "function") {
        elem.scrollIntoViewIfNeeded();
      }
    } else {
      elem.classList.remove("currentnavitem");
    }
  }
}
