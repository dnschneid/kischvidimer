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
import * as DB from "database";

let filteredDiffRows = new Set();
let diffMap = {};
let mode = null;
let searchSetActive = null;
let highlightElems = null;
let svgPage = null;
let panToElems = null;

export function init(
  uiData,
  searchSetActiveFunc,
  highlightElemsFunc,
  svgPage_,
  panToElemsFunc,
) {
  mode = uiData.uiMode;
  // FIXME: remove this jankiness
  searchSetActive = searchSetActiveFunc;
  highlightElems = highlightElemsFunc;
  svgPage = svgPage_;
  panToElems = panToElemsFunc;

  if (mode >= 2) {
    initializeExclusions();
    initializeCheckModel();

    document.getElementById("diffbutton").getElementsByTagName("img")[0].src =
      uiData.diffIcon;
    document.getElementById("diffbutton").style.display = "";
  }

  for (let elem of document.getElementsByClassName("threewayshown")) {
    elem.style.display = mode < 3 ? "none" : "";
  }

  //hide all diff expandables
  for (let elem of document.querySelectorAll("table")) {
    if (elem.id != "changetable") {
      elem.parentNode.style.display = "none";
    }
  }

  if (mode > 1) {
    document
      .getElementById("diffbutton")
      .addEventListener("mousedown", function () {
        if (sidebarVisible()) {
          hideSidebar();
        } else {
          showSidebar();
        }
      });

    document
      .getElementById("reanimate-button")
      .addEventListener("click", function () {
        animateAll(0);
        setTimeout(function () {
          animateAll(1);
        }, 1000);
      });

    document
      .getElementById("highlight-changes-button")
      .addEventListener("click", function () {
        // Highlight everything
        let elems = [];
        DB.forEachDiff(DB.CUR, (diffPair) => {
          for (let diffs of diffPair)
            for (let diff of diffs)
              if (diff.checked)
                for (let anim of document.getElementsByClassName(diff.id))
                  elems.push(anim.parentElement);
        });
        highlightElems(elems);
      });

    document
      .getElementById("changefilter")
      .addEventListener("input", function () {
        filterChanges(this.value);
      });
    document
      .getElementById("changetable")
      .addEventListener("click", function (e) {
        changeTableClicked(e);
      });
  }

  //init submit prompt
  document
    .getElementById("submitbutton")
    .addEventListener("click", function () {
      let unresolvedConflictPages = {};
      let unselectedDiffList = [];

      // flag user if there are conflicts without any selection
      DB.forEachDiff(DB.ALL, (diffPair, pageIndex) => {
        let conflictChecked = 0;
        let rowIsConflict = false;
        for (let diffList of diffPair) {
          for (let diff of diffList) {
            if (!diff.checked) {
              unselectedDiffList.push(diff.id);
            }
            if (diff.c) {
              rowIsConflict = true;
              conflictChecked += diff.checked ? 1 : 0;
            }
          }
        }
        if (rowIsConflict && !conflictChecked) {
          if (!unresolvedConflictPages[pageIndex]) {
            unresolvedConflictPages[pageIndex] = 1;
          } else {
            unresolvedConflictPages[pageIndex] += 1;
          }
        }
      });

      if (Object.keys(unresolvedConflictPages).length) {
        document.getElementById("unresolvedWarning").style.display = null;
        document.getElementById("unresolvedWarning").innerHTML =
          "Some conflicts do not have any changes selected:<ul>" +
          Object.keys(unresolvedConflictPages)
            .map(
              (k) =>
                `<li>${unresolvedConflictPages[k]} unresolved in ${DB.pageName(k)}</li>`,
            )
            .join("") +
          "</ul>Unselected changes will be abandoned entirely.";
      } else {
        document.getElementById("unresolvedWarning").style.display = "none";
      }

      let dialog = document.getElementById("applydiffsdialog");
      Util.toggleDialog(dialog, true);
    });
  document
    .getElementById("closeapplydiffs")
    .addEventListener("click", function () {
      let dialog = document.getElementById("applydiffsdialog");
      Util.toggleDialog(dialog, false);
    });
  document.getElementById("applydiffs").addEventListener("click", function () {
    let unselectedDiffList = [];

    // push un-selected diffs to be POSTed
    DB.forEachDiff(DB.ALL, (diffPair) => {
      let conflictChecked = 0;
      let rowIsConflict = false;
      for (let diffList of diffPair) {
        for (let diff of diffList) {
          if (!diff.checked) {
            unselectedDiffList.push(diff.id);
          }
          if (diff.c) {
            rowIsConflict = true;
            conflictChecked += diff.checked ? 1 : 0;
          }
        }
      }
    });

    // body is simple json list of diff id's that are not checked
    fetch("./apply", {
      method: "POST",
      body: JSON.stringify(unselectedDiffList),
    })
      .then((res) => {
        window.close();
      })
      .catch((error) => {
        document.documentElement.innerHTML = error;
      });
  });
}

export function sidebarVisible() {
  return document.getElementById("animationtoolbox").style.display != "none";
}

export function showSidebar() {
  document.getElementById("animationtoolbox").style.display = "inline";
  document.getElementById("diffbutton").style.backgroundColor = "lightgrey";
  searchSetActive(false);
}

export function hideSidebar() {
  document.getElementById("animationtoolbox").style.display = "none";
  document.getElementById("diffbutton").style.backgroundColor = null;
}

/** Handles restarting animations when a page header is held down.
 */
function animateAll(state) {
  for (let tag in {
    animate: 0,
    animateTransform: 0,
  }) {
    let anims = svgPage.getElementsByTagName(tag);
    for (let i = 0; i < anims.length; i++) {
      let hasattr = anims[i].hasAttribute("oldDur");
      if (!state && !hasattr) {
        let olddur = anims[i].getAttribute("dur");
        if (olddur === null || olddur === "indefinite") continue;
        anims[i].setAttribute("oldDur", olddur);
        anims[i].beginElement();
        anims[i].setAttribute("dur", "indefinite");
      } else if (state && hasattr) {
        anims[i].beginElement();
        anims[i].setAttribute("dur", anims[i].getAttribute("oldDur"));
        anims[i].removeAttribute("oldDur");
      } else {
        continue;
      }
    }
  }
}

/** Activates animations in a page
 * page  the svg to trigger anims
 */
function setAnimation(page) {
  page.querySelectorAll("animate,animateTransform").forEach((anim) => {
    if (anim.classList[0] !== "instance") {
      anim.setAttribute("fill", "freeze");
      anim.beginElement();
      anim.setAttribute(
        "dur",
        diffMap[anim.classList[0]].checked ? "1s" : "indefinite",
      );
    }
  });
}

export function applyAnimationColorWorkaround(uiData, theme) {
  // workaround the fact that we cannot animate css variables.
  document.querySelectorAll("animate[fromvar]").forEach((animElem) => {
    let fromVar = animElem
      .getAttribute("fromvar")
      .match(/(?<=var\(--)[a-zA-Z]+(?=\))/);
    let toVar = animElem
      .getAttribute("tovar")
      .match(/(?<=var\(--)[a-zA-Z]+(?=\))/);
    if (fromVar && toVar) {
      animElem.setAttribute("from", uiData.themes[theme][fromVar]);
      animElem.setAttribute("to", uiData.themes[theme][toVar]);
    }
  });
}

// called one time after page load
// also will apply red style to pagelist items with conflicts
function initializeCheckModel() {
  DB.forEachDiff(DB.ALL, (diffPair, pageIndex) => {
    // do the raw indexing
    for (let i of [0, 1]) {
      for (let diff of diffPair[i]) {
        diffMap[diff.id] = diff;
        diff.parent = diffPair;
      }
    }

    // do the check initialization
    if (diffPair[0].length && diffPair[0][0].c) {
      document
        .getElementById(`${pageIndex}_link`)
        .classList.add("conflictnavitem");
    } else if (diffPair[0].length) {
      for (let diff of diffPair[0]) {
        diff.checked = true;
      }
      for (let diff of diffPair[1]) {
        diff.checked = false;
      }
    } else if (diffPair[1][0].c) {
      document
        .getElementById(`${pageIndex}_link`)
        .classList.add("conflictnavitem");
    } else {
      for (let diff of diffPair[1]) {
        diff.checked = true;
      }
    }
  });
}

function initializeExclusions() {
  DB.forEachDiff(DB.ALL, (diffPair) => {
    for (let i in diffPair) {
      if (diffPair[i].length && diffPair[i][0].c) {
        for (let diff of diffPair[i]) {
          diff.excluded = diffPair[1 - i];
          diffMap[diff.id] = diff;
        }
      }
    }
  });
}

export function pageChanged(svgPage, uiData, theme) {
  // FIXME: page param shouldn't be needed
  if (mode > 1) {
    fillDiffTable();
  }
  applyAnimationColorWorkaround(uiData, theme);
  setAnimation(svgPage);
}

function fillDiffTable() {
  document.getElementById("changetablebody").innerHTML = "";
  document.getElementById("diffbutton").style.display = null;

  if (!svgPage.parentNode.getElementsByTagName("table")) {
    hideSidebar();
    setChecksFromModel();
    return;
  } else {
    showSidebar();
  }

  let diffIndex = 0;
  let tbody = "";
  DB.forEachDiff(DB.CUR, (diffPair) => (tbody += getDiffRow(diffPair)));

  document.getElementById("changetablebody").innerHTML = tbody;

  setChecksFromModel();

  filterChanges();

  document.querySelectorAll("#changetablebody tr").forEach((tr) => {
    tr.addEventListener("mouseenter", (e) => {
      highlightDiff(e.target.closest("tr"), false);
    });
    tr.addEventListener("dblclick", (e) => {
      // prevent checkmark clicks from panning and zooming to target
      if (e.target.classList.contains("diffcheck")) {
        return;
      }
      highlightDiff(e.target.closest("tr"), true);
    });
  });
  Util.upgradeDom();
}

function highlightDiff(diffTR, pan) {
  let diffClass = diffTR.querySelector(".diffcheck").id;
  const elems = Array.from(svgPage.getElementsByClassName(diffClass)).map(
    (e) => e.parentNode,
  );
  highlightElems(elems);
  if (pan) {
    panToElems(elems);
  }
}

function setChecksFromModel() {
  DB.forEachDiff(DB.CUR, (diffPair) => {
    for (let diffs of diffPair) {
      for (let diff of diffs) {
        document.getElementById(diff.id).checked = diff.checked;
      }
    }
  });

  setHeaderChecks();
}

function setHeaderChecks() {
  // header checks follow the states of the filtered rows
  let checkCount = [
    [0, 0], // ours [total boxes, total checked]
    [0, 0], // theirs [total boxes, total checked]
  ];

  let rowCtr = 0;
  DB.forEachDiff(DB.CUR, (diffPair) => {
    diffPair.forEach((_, diffPairIndex) => {
      let innerRowCtr = rowCtr;
      for (let diff of diffPair[diffPairIndex]) {
        if (filteredDiffRows.has(innerRowCtr)) {
          continue;
        }
        checkCount[diffPairIndex][0] += 1;
        if (diff.checked) {
          checkCount[diffPairIndex][1] += 1;
        }
        innerRowCtr += 1;
      }
    });
    rowCtr += Math.max(diffPair[0].length, diffPair[1].length);
  });

  let headerChecks = [
    document.getElementById("oursall").parentNode,
    document.getElementById("theirsall").parentNode,
  ];

  for (let i in headerChecks) {
    let box = headerChecks[i];
    if (!checkCount[i][0]) {
      box.MaterialCheckbox.disable();
      box.MaterialCheckbox.uncheck();
      box.classList.add("indeterminate");
    } else if (checkCount[i][1] == 0) {
      box.MaterialCheckbox.enable();
      box.MaterialCheckbox.uncheck();
      box.classList.remove("indeterminate");
    } else if (checkCount[i][1] == checkCount[i][0]) {
      box.MaterialCheckbox.enable();
      box.MaterialCheckbox.check();
      box.classList.remove("indeterminate");
    } else {
      box.MaterialCheckbox.enable();
      box.MaterialCheckbox.uncheck();
      box.classList.add("indeterminate");
    }
  }

  setMultipleDiffChecks();
  Util.upgradeDom();
}

function setMultipleDiffChecks() {
  // set uncollapsed multiple diffs to reflect their collapsed constituents
  // start by looping through all diff pairs containing multiple rows
  DB.forEachDiff(DB.CUR, (diffPair) => {
    if (diffPair[0].length > 1 || diffPair[1].length > 1) {
      for (let i of [0, 1]) {
        if (
          diffPair[i].length == 1 ||
          (diffPair[i].length > 1 &&
            diffPair[i][diffPair[i].length - 1].collapsed)
        ) {
          let topCheck = document.getElementById(diffPair[i][0].id);
          let numChecked = diffPair[i].filter((d) => d.checked).length;
          if (numChecked == 0) {
            topCheck.indeterminate = false;
          } else if (numChecked < diffPair[i].length) {
            topCheck.indeterminate = true;
          } else {
            topCheck.indeterminate = false;
            topCheck.checked = true;
          }
        } else if (diffPair[i].length > 1) {
          let topCheck = document.getElementById(diffPair[i][0].id);
          topCheck.indeterminate = false;
        }
      }
    }
  });
}

function changeTableClicked(e) {
  if (e.target.id == "oursall") {
    // ours header checkbox in clicked
    checkAll(0, e.target.checked);
  } else if (e.target.id == "theirsall") {
    // theirs header checkbox in clicked
    checkAll(1, e.target.checked);
  } else if (e.target.id.startsWith("diff")) {
    // individual row item checkbox was clicked
    applyDiffCheckChange(getDiffById(e.target.id), e.target.checked, true);
    setHeaderChecks();
  }
  setMultipleDiffChecks();
}

function getDiffById(id) {
  // might want to optimize with an id->diff index
  let ret = null;
  DB.forEachDiff(DB.CUR, (diffPair) => {
    for (let diffs of diffPair) {
      for (let diff of diffs) {
        if (diff.id == id) {
          ret = diff;
        }
      }
    }
  });
  return ret;
}

function checkAll(diffPairIndex, operation) {
  let rowCtr = 0;
  DB.forEachDiff(DB.CUR, (diffPair) => {
    let innerRowCounter = rowCtr;
    for (let diff of diffPair[diffPairIndex]) {
      if (filteredDiffRows.has(parseInt(innerRowCounter))) {
        continue;
      }
      applyDiffCheckChange(diff, operation, true);
      innerRowCounter += 1;
    }
    innerRowCounter = rowCtr;
    if (operation && diffPair[diffPairIndex].length) {
      for (let diff of diffPair[1 - diffPairIndex]) {
        if (filteredDiffRows.has(parseInt(innerRowCounter))) {
          continue;
        }
        applyDiffCheckChange(diff, false, true);
        innerRowCounter += 1;
      }
    }
    rowCtr += Math.max(diffPair[0].length, diffPair[1].length);
  });
  setChecksFromModel();
}

function applyDiffCheckChange(diff, operation, applyExclusions) {
  // first, de-select conflicts, if any
  if (operation && applyExclusions && diff.c) {
    for (let e of diff.excluded) {
      applyDiffCheckChange(e, false, false);
      document.getElementById(e.id).checked = false;
    }
  }

  let needToApplyModel = false;

  // second, if this is the top member of a multiple-diff group, the members need to follow
  for (let j of [0, 1]) {
    if (
      diff.parent[j].length > 1 &&
      diff.parent[j][0] == diff &&
      diff.parent[j][diff.parent[j].length - 1].collapsed
    ) {
      needToApplyModel = true;
      for (let i = 1; i < diff.parent[j].length; i++) {
        applyDiffCheckChange(diff.parent[j][i], operation, false);
      }
    }
  }

  if (operation != diff.checked) {
    diff.checked = operation;
    let elems = getAnimationElement(diff.id);
    if (elems) {
      setAnimation(elems);
    }
  }

  if (needToApplyModel) {
    setChecksFromModel();
  }
}

function getAnimationElement(name) {
  if (svgPage.getElementsByClassName(name)[0]) {
    return svgPage.getElementsByClassName(name)[0].parentNode;
  } else {
    return false;
  }
}

function getDiffRow(diff) {
  let trs = "";
  let columnLengths = [diff[0].length, diff[1].length];
  let max = mode < 3 ? 1 : Math.max(columnLengths[0], columnLengths[1]);
  for (let i = 0; i < max; i++) {
    let trClassList = [
      diff[0] && diff[0][0] && diff[0][0].c ? "conflict" : "",
      i > 0 ? "collapsedtr" : "",
    ].join(" ");

    trs += `<tr class="${trClassList}" title="double click to zoom to change">`;

    let tdClass = i == max - 1 ? "" : "innertd";

    for (let j of [0, 1]) {
      if (diff[j][i]) {
        trs += `<td class="checkctrl ${tdClass}">
                  <input type="checkbox" class="diffcheck ${columnLengths[j] > 1 && i == 0 ? "multiplecheck" : ""}" id="${diff[j][i].id}" />
                </td>
                <td class="${tdClass}">
                  <div class="checktext">${columnLengths[j] > 1 && i == 0 ? diff[j][i].text + '<span class="expandmultiple" onclick="Diffs.expandMultiple(this, this.parentElement.parentElement.parentElement, \'' + diff[j][i].id + "')\">&nbsp;(+ " + max.toString() + " more)</span>" : diff[j][i].text}</div>
                </td>`;

        // set initial collapse state to reflect class
        if (i > 0) {
          diff[j][i].collapsed = true;
        }
      } else if (mode > 2) {
        trs += `<td class="${tdClass}"></td><td class="${tdClass}"></td>`;
      }
    }
    trs += "</tr>";
  }
  return trs;
}

export function expandMultiple(span, tr, diffId) {
  let diff = diffMap[diffId];
  let trIndex = Array.from(tr.parentElement.children).indexOf(tr);
  // first, remove collapse style from hidden TRs
  for (
    let i = 1;
    i < Math.max(diff.parent[0].length, diff.parent[1].length);
    i++
  ) {
    for (let j of [0, 1]) {
      if (i in diff.parent[j]) {
        diff.parent[j][i].collapsed = false;
      }
    }
    tr.parentElement.children[trIndex + i].classList.remove("collapsedtr");
  }

  // then, replace the expand link(s) with the real diff text
  span.parentElement.innerHTML =
    diff.text +
    '&nbsp<span class="expandmultiple" onclick="Diffs.collapseMultiple(this, this.parentElement.parentElement.parentElement, \'' +
    diff.id +
    "')\">(collapse)</span>";
  Array.from(tr.getElementsByClassName("multiplecheck")).forEach((c) => {
    c.classList.remove("multiplecheck");
    c.classList.add("multiplecheckshown");
  });
}

export function collapseMultiple(span, tr, diffId) {
  let diff = diffMap[diffId];
  let trIndex = Array.from(tr.parentElement.children).indexOf(tr);

  // first, add collapse style from hidden TRs
  let max = Math.max(diff.parent[0].length, diff.parent[1].length);
  for (let i = 1; i < max; i++) {
    for (let j of [0, 1]) {
      if (i in diff.parent[j]) {
        diff.parent[j][i].collapsed = true;
      }
    }
    tr.parentElement.children[trIndex + i].classList.add("collapsedtr");
  }

  // then, replace the real diff text with the expand link
  span.parentElement.innerHTML =
    diff.text +
    '<span class="expandmultiple" onclick="Diffs.expandMultiple(this, this.parentElement.parentElement.parentElement, \'' +
    diff.id +
    "')\">&nbsp;(and " +
    max.toString() +
    " more)</span>";
  Array.from(tr.getElementsByClassName("multiplecheckshown")).forEach((c) => {
    c.classList.remove("multiplecheckshown");
    c.classList.add("multiplecheck");
  });
}

function filterChanges(filter) {
  filteredDiffRows = new Set();
  let ufilter = filter ? filter.toUpperCase() : "";
  let rowCtr = 0;
  DB.forEachDiff(DB.CUR, (diffPair) => {
    let diffContains = false;
    for (let i = 0; i < 2 && !diffContains; i++) {
      for (let j = 0; j < diffPair[i].length && !diffContains; j++) {
        diffContains = diffPair[i][j].text.toUpperCase().indexOf(ufilter) != -1;
      }
    }

    for (let i = 0; i < Math.max(diffPair[0].length, diffPair[1].length); i++) {
      document.getElementById("changetablebody").getElementsByTagName("tr")[
        rowCtr + i
      ].style.display = diffContains ? null : "none";
      if (!diffContains) {
        filteredDiffRows.add(rowCtr);
      }
    }
    rowCtr += Math.max(diffPair[0].length, diffPair[1].length);
  });

  setHeaderChecks();
  document.getElementById("changetablediv").scrollTop = 0;
}
