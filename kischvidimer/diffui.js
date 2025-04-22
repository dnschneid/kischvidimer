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
import * as Search from "search";
import * as Viewport from "viewport";
import * as DB from "database";

const uiData = {}; // diffui stub

let svgPage = null;

let xprobeEndpoint = "http://localhost:4241/xprobe";
let xprobe = null;

let filteredDiffRows = new Set();
let diffMap = {};

// Ordering creates precedence when matching to mouse actions
let ELEM_TYPE_SELECTORS = [
  ["net", ["[t]"]],
  ["component", ["[p]", "symbol"]],
  ["ghost", [".ghost"]],
];

function getSetting(name, defaultValue) {
  let stored = window.localStorage.getItem(name);
  if (!stored || stored === "null") {
    return defaultValue;
  }
  return stored;
}

function setSetting(name, value) {
  window.localStorage.setItem(name, value);
}

document.addEventListener("DOMContentLoaded", function () {
  svgPage = document.getElementById("svgPage");

  DB.init();
  Viewport.init();
  Search.init(DB, toggleDiffSidebar);

  // handle case with no pages
  if (!DB.numPages()) {
    svgPage.innerText =
      uiData.uiMode < 2 ? "No pages to display." : "No changes to display.";
    toggleDialog(document.getElementById("loadingdialog"), false);
    return;
  }

  // Initialize theme
  let tprev = "lwfbgphrv"; // order of the theme colors to use to render the label
  document.querySelector(".themeselect").innerHTML = Object.entries(
    uiData.themes,
  )
    .map(
      ([name, data]) => `
        <label style="margin-bottom:10px" class="mdl-radio mdl-js-radio mdl-js-ripple-effect"
            for="toption-${name}">
            <input type="radio" id="toption-${name}" class="mdl-radio__button" name="options" value="${name}">
            <span class="mdl-radio__label" style="background-color:${data.d}">
            ${name
              .split("")
              .map(
                (c, i) =>
                  `<span style="color:${data[tprev.substr(i % tprev.length, 1)]}">${c}</span>`,
              )
              .join("")}
            </span></label>`,
    )
    .join("");
  setTheme(getSetting("SchematicTheme", uiData.themeDefault));

  document.getElementById("zoomcontrols").style.display =
    getSetting("ShowZoomControls") == "shown" ? "inline" : "none";
  componentHandler.upgradeDom();
  fillPageList();

  if (uiData.uiMode >= 2) {
    initializeExclusions();
    initializeCheckModel();

    document.getElementById("diffbutton").getElementsByTagName("img")[0].src =
      uiData.diffIcon;
    document.getElementById("diffbutton").style.display = "";
  }
  for (let elem of document.getElementsByClassName("threewayshown")) {
    elem.style.display = uiData.uiMode < 3 ? "none" : "";
  }

  window.onpopstate();

  toggleDialog(document.getElementById("loadingdialog"), false);
  document.getElementById("pagefilter").addEventListener("input", function () {
    filterPages(this.value);
  });

  // add double click listeners
  window.ondblclick = function (evt) {
    let target = evt.target;
    // Process text doubleclicks
    // Clicking on tspan is the same as clicking on text
    if (target.tagName === "tspan") {
      target = target.closest("text");
    }
    if (target.tagName === "text") {
      copyToClipboard(target.textContent);
      if (!target.classList.contains("highlight")) {
        setTimeout(function () {
          target.classList.remove("highlight");
        }, 500);
      }
      target.classList.add("highlight");
      return;
      /* FIXME: this is probably for handling diffs
      for (let i = 0; i < target.childNodes.length; ++i) {
        if (target.childNodes[i].nodeType === 3 && !/^\s+$/.test(target.childNodes[i].textContent)) {
          copyToClipboard(target.childNodes[i].textContent);
      */
    }
    // Launch an associated, visible url first
    // FIXME: can this be implemented using closest?
    for (let targp = target; targp; targp = targp.parentElement) {
      if (targp.hasAttribute("p")) {
        let a = svgPage.querySelector(`[p="${targp.getAttribute("p")}"] a`);
        if (a) {
          a.onclick();
          return;
        }
        break;
      }
    }
    // Launch any prop url
    let result = lookupElem(target);
    if (result && result.data) {
      for (const val of Object.values(result.data)) {
        if (/^https?:[/][/]/.test(val)) {
          Viewport.openurl(val.split(/\s/)[0]);
          return;
        }
      }
    }
  };

  // Handle tooltips
  svgPage.onmouseover = function (e) {
    if (Viewport.Tooltip.isfixed() || e.buttons) {
      return;
    }
    let result = lookupElem(e.target);
    if (
      (result.type === "net" && result.value !== "GND") ||
      (result.type === "component" && result.data)
    ) {
      displayTooltip(result, false);
    } else {
      Viewport.Tooltip.hide(true);
    }
  };
  svgPage.onmousemove = function (evt) {
    Viewport.Tooltip.onSvgMouseMove(evt);
    // store the mouse event in case we need to emulate mousedown on ghost transition
    svgPage.mouseEvent = evt;
  };
  svgPage.onmouseout = function () {
    if (!Viewport.Tooltip.isfixed()) {
      Viewport.Tooltip.hide(true);
    }
  };
  svgPage.onmousedown = function () {
    Viewport.Tooltip.hide();
  };
  svgPage.onmouseup = function (e) {
    if (e.button === 3) {
      window.history.back();
    } else if (e.button === 4) {
      window.history.forward();
    } else {
      if (Viewport.Tooltip.isvisible()) {
        Viewport.Tooltip.show(true);
        let result = lookupElem(e.target);
        if (result.type === "net" && result.value !== "GND") {
          crossProbe("NET", result.value);
        } else if (result.type === "component" && result.data) {
          crossProbe("SELECT", result.value);
        }
      } else {
        svgPage.onmousemove(e);
        svgPage.onmouseover(e);
      }
    }
  };
  svgPage.oncontextmenu = function (e) {
    e.preventDefault();
  };

  document.onkeydown = function (e) {
    // prevents the fake mousedown from triggering on page switch
    svgPage.mouseEvent = null;

    if (e.target.tagName != "INPUT" && Viewport.onkeydown(e) !== false) {
      if (e.key == "PageUp") {
        cyclePage(-1);
      } else if (e.key == "PageDown") {
        cyclePage(1);
      }
    }
    if (e.key == "Enter" && Search.isFocused()) {
      Search.onEnterKey(e);
    } else if (e.keyCode === 114 || (e.ctrlKey && e.keyCode === 70)) {
      e.preventDefault();
      Search.setActive(true, true);
    } else if (e.key == "Escape") {
      Search.setActive(false);
      toggleDialog(null, false);
    } else if (e.ctrlKey && e.keyCode == 80) {
      genpdf();
    }
  };

  document.querySelectorAll(".dialogwrapper").forEach((wrapper) => {
    wrapper.addEventListener("click", (e) => {
      if (e.target !== wrapper) {
        return;
      }
      toggleDialog(null, false);
    });
  });

  document.getElementById("tooltiplink").addEventListener("click", function () {
    copyToClipboard(Viewport.Tooltip.url(), "link");
  });

  //init settings
  document
    .getElementById("settingsbutton")
    .addEventListener("click", function () {
      document.getElementById("uiversion").innerText = `UI: ${uiData.vers}`;

      // show zoom control selection
      if (getSetting("ShowZoomControls") == "shown") {
        document
          .getElementById("zoomcontrolcheckboxlabel")
          .MaterialCheckbox.check();
      } else {
        document
          .getElementById("zoomcontrolcheckboxlabel")
          .MaterialCheckbox.uncheck();
      }

      // zoom to content
      if (getSetting("ZoomToContent") == "zoom") {
        document
          .getElementById("zoomcontentcheckboxlabel")
          .MaterialCheckbox.check();
      } else {
        document
          .getElementById("zoomcontentcheckboxlabel")
          .MaterialCheckbox.uncheck();
      }

      let toption = document.getElementById(
        "toption-" + getSetting("SchematicTheme", uiData.themeDefault),
      );
      if (toption) {
        toption.parentNode.MaterialRadio.check();
      }
      let dialog = document.getElementById("settingsdialog");
      toggleDialog(dialog, true);
    });
  if (uiData.fbUrl && /https?:[/][/]/.test(uiData.fbUrl)) {
    document.getElementById("feedbackbutton").parentNode.style.display =
      "inline";
    document
      .getElementById("feedbackbutton")
      .addEventListener("click", function () {
        Viewport.openurl(uiData.fbUrl);
      });
  }
  document.getElementById("printbutton").addEventListener("click", function () {
    genpdf();
  });
  document.getElementById("uiversion").addEventListener("click", function () {
    copyToClipboard(uiData.vers, "kischvidimer version");
  });
  document
    .getElementById("closesettings")
    .addEventListener("click", function () {
      let dialog = document.getElementById("settingsdialog");
      toggleDialog(dialog, false);
    });
  document
    .getElementById("applysettings")
    .addEventListener("click", function () {
      setSetting(
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
      setSetting(
        "ZoomToContent",
        document.getElementById("zoomcontentcheckbox").checked ? "zoom" : "",
      );

      let dialog = document.getElementById("settingsdialog");
      let selectedTheme = document.querySelector("[id^=toption]:checked");
      setTheme(selectedTheme.getAttribute("value"));
      toggleDialog(dialog, false);
    });
  document.getElementById("xprobe").addEventListener("click", function (e) {
    e.target.disabled = true;
    e.target.innerText = "Cross-probe started";
    crossProbe();
  });

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
      toggleDialog(dialog, true);
    });
  document
    .getElementById("closeapplydiffs")
    .addEventListener("click", function () {
      let dialog = document.getElementById("applydiffsdialog");
      toggleDialog(dialog, false);
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

  if (uiData.uiMode > 1) {
    document
      .getElementById("diffbutton")
      .addEventListener("mousedown", function () {
        toggleDiffSidebar(!diffSidebarState());
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
        Viewport.highlightElems(elems);
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

  //hide all diff expandables
  for (let elem of document.querySelectorAll("table")) {
    if (elem.id != "changetable") {
      elem.parentNode.style.display = "none";
    }
  }

  document.getElementById("schematic-title").textContent = uiData.schTitle;
  document.getElementById("schematic-version").textContent = uiData.schVers;
});

function crossProbe(cmd, target) {
  // Don't cross-probe unless the button has been pressed
  if (!document.getElementById("xprobe").disabled) {
    return;
  }
  if (xprobe) {
    if (xprobe.readyState % 4) {
      xprobe.abort();
    }
    xprobe = null;
  }
  xprobe = new XMLHttpRequest();
  xprobe.onerror = xprobe.ontimeout = () => setTimeout(crossProbe, 1000);
  if (!cmd) {
    xprobe.open("GET", xprobeEndpoint, true);
    xprobe.onload = (e) => {
      // Use a nominal timeout to avoid huge CPU usage if we hit a standard webserver
      setTimeout(crossProbe, 100);
      // Try to parse the result; might throw an exception
      const resp = JSON.parse(e.target.response);
      if (
        resp &&
        "cmd" in resp &&
        "targets" in resp &&
        ["PART", "NET"].includes(resp.cmd)
      ) {
        pushHash(resp.targets);
        window.onpopstate();
      }
    };
  } else {
    xprobe.timeout = 1000;
    let url = `${xprobeEndpoint}?cmd=${escape(cmd)}&targets=${escape(target)}`;
    xprobe.open("GET", url, true);
    xprobe.onload = () => crossProbe();
  }
  xprobe.send();
}

function setTheme(name, target) {
  if (!(name in uiData.themes)) {
    name = "Default";
  }
  if (!target) {
    setSetting("SchematicTheme", name);
  }
  for (const v in uiData.themes[name]) {
    (target || document.body).style.setProperty(
      "--" + v,
      uiData.themes[name][v],
    );
  }
  applyAnimationColorWorkaround();
}

function copyToClipboard(text, indicator) {
  // https://stackoverflow.com/questions/33855641/copy-output-of-a-javascript-variable-to-the-clipboard
  let dummy = document.createElement("textarea");
  document.body.appendChild(dummy);
  dummy.value = text;
  dummy.select();
  document.execCommand("copy");
  document.body.removeChild(dummy);
  showCopyToast(text, indicator);
}

function showCopyToast(text, indicator) {
  document.getElementById("copy-toast").MaterialSnackbar.showSnackbar({
    message: "Copied " + (indicator || '"' + text + '"') + " to clipboard.",
  });
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

/** Updates the back/forward history with a new target (if not redundant).
 */
function pushHash(target) {
  window.history.pushState(null, "", "#" + target);
}

function fillPageList() {
  let listElem = document.getElementById("pagelist");
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
    spanElem.addEventListener("click", function () {
      // manual click on another page should hide search results...
      Search.setActive(false);
      pushHash(p.name);
      window.onpopstate();
    });
    listElem.appendChild(spanElem);
  });
  filterPages("");
}

function filterPages(filter) {
  let darken = false;
  for (let elem of document.querySelectorAll(".mdl-navigation__link")) {
    if (elem.textContent.toUpperCase().indexOf(filter.toUpperCase()) != -1) {
      elem.style.display = "inline";
      if (darken) {
        elem.classList.add("navitemeven");
        darken = false;
      } else {
        elem.classList.remove("navitemeven");
        darken = true;
      }
    } else {
      elem.style.display = "none";
    }
  }
}

function injectPage(pageIndex) {
  // first thing, we need to highlight in the left bar the page that has been loaded
  let allLinks = document.getElementById("pagelist").getElementsByTagName("a");

  // empty the change filter on page change
  document.getElementById("changefilter").value = "";

  for (let l = 0; l < allLinks.length; l++) {
    if (allLinks[l].id == `${pageIndex}_link`) {
      allLinks[l].classList.add("currentnavitem");
      allLinks[l].style.fontWeight = "bold";
      if (allLinks[l].classList.contains("conflictpagelink")) {
        allLinks[l].style.backgroundColor = "rgba(255,0,0,0.55)";
      } else {
        allLinks[l].style.backgroundColor = "black";
      }
      if (typeof allLinks[l].scrollIntoViewIfNeeded === "function") {
        allLinks[l].scrollIntoViewIfNeeded();
      }
    } else {
      allLinks[l].classList.remove("currentnavitem");
      allLinks[l].style.fontWeight = null;
      if (allLinks[l].classList.contains("conflictpagelink")) {
        allLinks[l].style.backgroundColor = "rgba(255,0,0,0.15)";
      } else {
        allLinks[l].style.backgroundColor = null;
      }
    }
  }

  // Load the library
  let svgLibrary = document.getElementById("svgLibrary");
  if (!svgLibrary.getElementsByTagName("svg").length) {
    svgLibrary.innerHTML = DB.getLibrarySvg();
  }

  let pgdata = DB.selectPage(pageIndex);
  if (pgdata !== null) {
    Viewport.loadPage(pgdata, DB.pageInstance(), DB.pageViewBox(), cyclePage);
    Viewport.createGhostPages(DB, pageIndex);
  }

  if (getSetting("ZoomToContent") === "zoom") {
    let fakeElem = {
      contentbox: DB.pageContentBox(),
      box: DB.pageBox(),
    };
    panToElems([fakeElem], 0.01); // the tiniest bit of margin
  }

  if (uiData.uiMode > 1) {
    fillDiffTable();
  }
  applyAnimationColorWorkaround();
  setAnimation(svgPage);
}

function applyAnimationColorWorkaround() {
  let theme = getSetting("SchematicTheme", uiData.themeDefault);
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
        .classList.add("conflictpagelink");
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
        .classList.add("conflictpagelink");
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

function fillDiffTable() {
  document.getElementById("changetablebody").innerHTML = "";
  document.getElementById("diffbutton").style.display = null;

  if (!svgPage.parentNode.getElementsByTagName("table")) {
    toggleDiffSidebar(false);
    setChecksFromModel();
    return;
  } else {
    toggleDiffSidebar(true);
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
  componentHandler.upgradeDom();
}

function highlightDiff(diffTR, pan) {
  let diffClass = diffTR.querySelector(".diffcheck").id;
  const elems = Array.from(svgPage.getElementsByClassName(diffClass)).map(
    (e) => e.parentNode,
  );
  Viewport.highlightElems(elems);
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
  componentHandler.upgradeDom();
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
  let max =
    uiData.uiMode < 3 ? 1 : Math.max(columnLengths[0], columnLengths[1]);
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
                  <div class="checktext">${columnLengths[j] > 1 && i == 0 ? diff[j][i].text + '<span class="expandmultiple" onclick="expandMultiple(this, this.parentElement.parentElement.parentElement, \'' + diff[j][i].id + "')\">&nbsp;(+ " + max.toString() + " more)</span>" : diff[j][i].text}</div>
                </td>`;

        // set initial collapse state to reflect class
        if (i > 0) {
          diff[j][i].collapsed = true;
        }
      } else if (uiData.uiMode > 2) {
        trs += `<td class="${tdClass}"></td><td class="${tdClass}"></td>`;
      }
    }
    trs += "</tr>";
  }
  return trs;
}

function expandMultiple(span, tr, diffId) {
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
    '&nbsp<span class="expandmultiple" onclick="collapseMultiple(this, this.parentElement.parentElement.parentElement, \'' +
    diff.id +
    "')\">(collapse)</span>";
  Array.from(tr.getElementsByClassName("multiplecheck")).forEach((c) => {
    c.classList.remove("multiplecheck");
    c.classList.add("multiplecheckshown");
  });
}

function collapseMultiple(span, tr, diffId) {
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
    '<span class="expandmultiple" onclick="expandMultiple(this, this.parentElement.parentElement.parentElement, \'' +
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

function cyclePage(delta, retainPan, mouseEvent, leftoverPanY) {
  let nextPageIndex = DB.curPageIndex + delta;
  if (nextPageIndex < 0 || nextPageIndex >= DB.numPages()) {
    return;
  }
  let origPos = Viewport.savePos();
  pushHash(DB.pageName(nextPageIndex));
  window.onpopstate();
  if (retainPan) {
    Viewport.restorePos(origPos, retainPan, leftoverPanY);
  }
  // emulate a mousedown to preserve the same pan across pages
  if (mouseEvent) {
    mouseEvent.initEvent("mousedown", true, true);
    document.getElementById("activesvg").dispatchEvent(mouseEvent);
  }
}

/** result comes from lookupElem() or null
 *  fix means the target was clicked
 */
function displayTooltip(result, fix) {
  if (!result || !result.value) {
    Viewport.Tooltip.hide(true);
    return;
  }

  let context = "";
  if (result.target.tagName === "text" && result.target.getAttribute("prop")) {
    // FIXME: handle diffs better
    if (true || !result.target.children.length) {
      context = `${result.target.getAttribute("prop")}: ${result.target.textContent}`;
    } else {
      context =
        result.target.getAttribute("prop") +
        ": " +
        result.target.children[0].textContent +
        " \u2192 " + // right arrow
        result.target.children[1].textContent;
      if (result.target.children.length >= 3) {
        context += " || " + result.target.children[2].textContent;
      }
    }
  } else if (result.type === "net") {
    // FIXME: show local net name somehow
    context = `Net: ${result.value}`;
  } else if (result.type === "component") {
    context = "Part symbol";
    let value = DB.compProp(result.data, "value");
    if (value) {
      context += `: ${value}`;
    }
  }

  // cycle instance with closest
  document.getElementById("nextinstance").onclick = function () {
    cycleInstance(result, true);
  };
  document.getElementById("previnstance").onclick = function () {
    cycleInstance(result, false);
  };

  Viewport.Tooltip.setResult(DB, result, context);
  Viewport.Tooltip.show(fix);
}

/* Takes in a random target/elem and returns the container element that
 * represents the object, which might be the same thing, along with database
 * information about it.
 */
function lookupElem(elem) {
  if (!elem) {
    return null;
  }
  // Clicking on a tspan is the same as clicking on the text.
  if (elem.tagName === "tspan") {
    elem = elem.closest("text");
  }
  let result = {
    distance: 0,
    type: "note",
    pages: [DB.curPageIndex],
    id: null,
    prop: "type",
    value: "NOTE",
    display: "",
    data: {},
    target: elem,
    container: elem,
  };
  // FIXME: there is no selector for notes; is that a problem?
  for (let [typ, selectors] of ELEM_TYPE_SELECTORS) {
    for (let s of selectors) {
      // exit the shadow dom if necessary
      // this is used to attach the right <use> to <symbol> elems
      let container = (elem.getRootNode().host || elem).closest(s);
      if (container) {
        if (typ === "component") {
          const path = container.getAttribute("p");
          result = DB.lookupComp(path);
        } else if (typ === "net") {
          const tid = container.getAttribute("t");
          result = DB.lookupNet(tid);
        } else if (typ === "ghost") {
          result.value = "GHOST";
        }
        result.target = elem;
        result.container = container;
        return result;
      }
    }
  }
  return result;
}

/** Navigates to the referenced target when back/forward are hit.
 */
window.onpopstate = function (evt) {
  DB.curPageIndex = null;
  let target = decodeURI(window.location.hash.replace("#", "")).toUpperCase();
  let pageName = "";
  if (target.indexOf(",") != -1) {
    // Page name with a specific target
    pageName = target.split(",")[0];
    target = target.split(",")[1];
  } else {
    // Maybe just a refdes by itself?
    let result = DB.lookupComp(target.split(".")[0].toUpperCase());
    if (result.distance !== DB.NO_MATCH) {
      pageName = result.pages[0];
    }
  }

  let pageIndex = DB.pageIndexFromName(target);
  if (pageIndex !== -1) {
    target = null; // consumed
  } else {
    pageIndex = DB.pageIndexFromName(pageName);
  }
  if (pageIndex !== -1) {
    injectPage(pageIndex);
  }

  Viewport.Tooltip.hide(true);

  if (!target) {
    if (pageIndex === -1) {
      window.location.hash = "#" + DB.pageName(0);
    }
    return;
  }

  // Match components
  const compIDs = DB.compIDs(target, pageIndex);
  if (compIDs.length) {
    const elems = svgPage.querySelectorAll(
      compIDs.map((p) => `[p="${p}"]`).join(", "),
    );
    if (elems.length) {
      Viewport.highlightElems(Array.from(elems));
      panToElems(Array.from(elems));
      return;
    }
  }

  // Match nets
  // FIXME: match local names too
  // FIXME: include bus membership (probably in netIDs)
  const netIDs = DB.netIDs(target, pageIndex);
  if (netIDs.length) {
    const elems = svgPage.querySelectorAll(
      netIDs.map((tid) => `[t='${tid}']`).join(", "),
    );
    if (elems.length) {
      Viewport.highlightElems(Array.from(elems));
      panToElems(Array.from(elems));
      return;
    }
  }

  // FIXME: this is broken
  let pinsMatched = [];
  for (let txt of getSymbolTexts()) {
    let result = DB.matchData(target, txt[0].textContent, "pin");
    if (result) {
      pinsMatched.push(txt);
    }
  }
  if (pinsMatched.length) {
    // highlight pins, but pan to their symbols
    Viewport.highlightElems(pinsMatched.map((p) => p[0]));
    panToElems(pinsMatched.map((p) => p[1]));
    return;
  }

  // FIXME: move elsewhere
  // newlines get messed up in svg. single newlines are deleted, and
  // empty lines are replaced with spaces
  // also, inline formatting (_{}, ^{}, ~{}) gets removed
  // also, everything that svg.encode() does needs to be replicated
  const re = /[_^~]\{((?:[^{}]|\{[^}]*\})*)\}/;
  target = unescape(target)
    .replace(re, (_, x) => x.replace(re, "$1"))
    .replace(/\{slash\}/g, "/")
    .split("\n")
    .map((x) => x || " ")
    .join("");

  let genericMatched = [];
  // filter net names. We want to search component props and notes...
  // exclude text matches from ghost pages
  for (let prop of Array.from(svgPage.getElementsByTagName("text")).filter(
    (p) => !["net", "ghost"].includes(lookupElem(p).type),
  )) {
    let result = DB.matchData(target, prop.textContent, "text");
    if (result) {
      // Highlight the prop
      genericMatched.push(prop);
    }
  }
  if (genericMatched.length) {
    Viewport.highlightElems(genericMatched);
    panToElems(genericMatched);
    return;
  }

  // No matches of any kind were found. Default to first page.
  if (DB.curPageIndex === null) {
    DB.curPageIndex = 0;
    window.location.hash = "#" + DB.pageName();
  }
};

function getSymbolTexts() {
  let symbolTexts = [];
  for (let elem of svgPage.getElementsByTagName("use")) {
    for (let txt of document
      .getElementById(elem.href.baseVal.replace("#", ""))
      .getElementsByTagName("text")) {
      symbolTexts.push([txt, elem]);
    }
  }
  return symbolTexts;
}

function panToElems(elems, padding) {
  padding = padding === undefined ? 0.8 : 1 - padding;

  // calculate svg viewport width offset based on open sidebars
  let widthOffset = 0;
  if (Search.isActive()) {
    widthOffset = -document.getElementById("searchpane").offsetWidth;
  } else if (diffSidebarState()) {
    widthOffset = -document.getElementById("animationtoolbox").offsetWidth;
  }

  Viewport.panToElems(elems, padding, widthOffset);
}

function genpdf() {
  let win = window.open("", "printwin", "height=600, width=800");
  win.document.write(
    "<html><head><title>Preparing to print " +
      Viewport.Tooltip.escapeHTML(uiData.schTitle) +
      " </title></head><body>",
  );
  win.document.write(
    '<style type="text/css" media="print">@page { size: 17in 11in; margin: 0.1in; }</style>',
  );
  win.document.write(
    '<style type="text/css" media="print">svg { page-break-after: always; }</style>',
  );
  win.document.write(
    '<style type="text/css" media="screen">svg { display: none }</style>',
  );

  win.onload = function () {
    win.print();
    win.close();
  };
  win.onbeforeprint = function () {
    win.document.title = uiData.schTitle;
  };

  // Copy in fonts
  document.fonts.forEach((f) => win.document.fonts.add(f));

  // Black-on-white theme
  setTheme(uiData.themeBW, win.document.body);

  win.document.write(DB.getLibrarySvg());
  win.document.querySelectorAll("svg")[0].style.display = "none";

  DB.forEachPageByNum((_, pageIndex) => {
    win.document.write(DB.getPageSvg(pageIndex));
    Viewport.selectInstance(win.document, DB.pageInstance(pageIndex));
  });

  win.document.write("</body></html>");
  win.document.close();
}

// Find the next instance of elem and go to it
// bool direction; True if cycling forwards, false if backwards.
// FIXME: rewrite this to store the results from mouseover and index through it
function cycleInstance(result, direction) {
  if (!["net", "component"].includes(result.type)) {
    return;
  }

  function getMatches(elem) {
    // Return list of all duplicate instances of elem on current page
    // FIXME: busentries are part of both a net and a bus, so rather than
    // tracking by .name (which only has one), this needs to build up the
    // list like in the history handler. Naturally that makes it difficult
    // to say "find stuff like this element", so cycling may need to be
    // restructured somehow
    return Array.from(
      svgPage.querySelectorAll(
        ELEM_TYPE_SELECTORS.find((x) => x[0] === elem.type)[1].join(", "),
      ),
    ).filter((e) => {
      return lookupElem(e).value == elem.value;
    });
  }

  const pageList = result.pages;
  let matches = getMatches(result);
  let current_target_index = matches.indexOf(result.target);
  if (current_target_index === -1) {
    // Our match list doesn't work at this level of detail, so go higher.
    result.target = result.container;
    current_target_index = matches.indexOf(result.container);
  }
  let target = result.target;
  // FIXME: eventually want to cycle between instances within a page
  let is_group = true;
  if (direction) {
    //forward click
    if (current_target_index != matches.length - 1) {
      //there is a next instance on this page
      target = matches[current_target_index + 1];
    } else if (pageList.length == 1) {
      // Only one page with this target and no next instance so cycle
      // back to first instance on page.
      target = matches[0];
    }
  } else {
    // Backwards click
    if (current_target_index == -1) {
      target = result.target;
    } else if (current_target_index != 0) {
      // there is a previous instance on this page
      target = matches[current_target_index - 1];
    } else if (pageList.length == 1) {
      // Only one page with this target and no prev instance so cycle
      // back to last instance on page.
      target = matches[matches.length - 1];
    }
  }

  if ((is_group || target.isSameNode(result.target)) && pageList.length > 1) {
    // Need to navigate to a different page
    let cycleIndex = 0;
    for (let i = 0; i < pageList.length; ++i) {
      if (pageList[i] == DB.curPageIndex) {
        cycleIndex = i;
      }
    }
    if (direction) {
      cycleIndex += 1;
      cycleIndex = cycleIndex % pageList.length;
    } else {
      cycleIndex -= 1;
      if (cycleIndex == -1) {
        cycleIndex = pageList.length - 1;
      }
    }
    let pageIndex = pageList[cycleIndex];
    injectPage(pageIndex);
    // FIXME: is this comparing objects across pages?
    matches = getMatches(result);
    // Going forward to next page should select first instance on page, similarly going backwards
    // to prev page should select last instance.
    if (direction) {
      target = matches[0];
    } else {
      target = matches[matches.length - 1];
    }
  }

  if (target.tagName === "text") {
    // Net targets do not have ids that can be referenced add one temporarily to follow link
    target.id = result.value;
  }
  let target_href = DB.pageName() + "," + target.id;

  Viewport.highlightElems(is_group ? matches : [target]);
  panToElems(is_group ? matches : [target]);
  pushHash(target_href);
  if (target.tagName === "text") {
    // Remove temporary id
    target.removeAttribute("id");
  }
  result.container = target;
  result.target = target;
  displayTooltip(result, true);
}

function toggleDialog(dialog, state) {
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

function toggleDiffSidebar(state) {
  document.getElementById("animationtoolbox").style.display = state
    ? "inline"
    : "none";
  document.getElementById("diffbutton").style.backgroundColor = state
    ? "lightgrey"
    : null;
  if (state) {
    Search.setActive(false);
  }
}

function diffSidebarState() {
  return document.getElementById("animationtoolbox").style.display != "none";
}
