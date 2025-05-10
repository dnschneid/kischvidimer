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
import * as Diffs from "diffs";
import * as PageList from "pagelist";
import * as Search from "search";
import * as Settings from "settings";
import * as Util from "util";
import * as Viewport from "viewport";

let svgPage = null;

let xprobeEndpoint = "http://localhost:4241/xprobe";
let xprobe = null;

// Ordering creates precedence when matching to mouse actions
let ELEM_TYPE_SELECTORS = [
  ["net", ["[t]"]],
  ["component", ["[p]", "symbol"]],
  ["ghost", [".ghost"]],
];

document.addEventListener("DOMContentLoaded", () => {
  svgPage = document.getElementById("svgPage");

  DB.init();
  Viewport.init();
  Search.init(Diffs.hideSidebar);
  Settings.init(setTheme);
  PageList.init(Search.setActive);

  // handle case with no pages
  if (!DB.numPages()) {
    svgPage.innerText =
      DB.ui.uiMode < 2 ? "No pages to display." : "No changes to display.";
    Util.toggleDialog(document.getElementById("loadingdialog"), false);
    return;
  }

  // initialize UI
  setTheme(Settings.get("SchematicTheme", DB.ui.themeDefault));
  document.getElementById("zoomcontrols").style.display =
    Settings.get("ShowZoomControls") == "shown" ? "inline" : "none";
  Util.upgradeDom();

  Diffs.init(Search.setActive, Viewport.highlightElems, svgPage, panToElems);

  window.onpopstate();

  Util.toggleDialog(document.getElementById("loadingdialog"), false);

  // add double click listeners
  window.ondblclick = function (evt) {
    let target = evt.target;
    // Process text doubleclicks
    // Clicking on tspan is the same as clicking on text
    if (target.tagName === "tspan") {
      target = target.closest("text");
    }
    if (target.tagName === "text") {
      Util.copyToClipboard(target.textContent);
      if (!target.classList.contains("highlight")) {
        setTimeout(() => target.classList.remove("highlight"), 500);
      }
      target.classList.add("highlight");
      /* FIXME: this is probably for handling diffs
      for (let i = 0; i < target.childNodes.length; ++i) {
        if (target.childNodes[i].nodeType === 3 && !/^\s+$/.test(target.childNodes[i].textContent)) {
          Util.copyToClipboard(target.childNodes[i].textContent);
      */
      return;
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
          Util.openurl(val.split(/\s/)[0]);
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
    if (e.key == "Enter") {
      if (Search.isFocused()) {
        Search.onEnterKey(e);
      } else if (PageList.isFocused()) {
        PageList.onEnterKey(e);
      }
    } else if (e.keyCode === 114 || (e.ctrlKey && e.keyCode === 70)) {
      e.preventDefault();
      Search.setActive(true, true);
    } else if (e.key == "Escape") {
      Search.setActive(false);
      Util.toggleDialog(null, false);
    } else if (e.ctrlKey && e.keyCode == 80) {
      genpdf();
    }
  };

  document.querySelectorAll(".dialogwrapper").forEach((wrapper) => {
    wrapper.addEventListener("click", (e) => {
      if (e.target !== wrapper) {
        return;
      }
      Util.toggleDialog(null, false);
    });
  });

  document.getElementById("tooltiplink").addEventListener("click", () => {
    Util.copyToClipboard(Viewport.Tooltip.url(), "link");
  });

  // Toolbar
  if (DB.ui.fbUrl && /https?:[/][/]/.test(DB.ui.fbUrl)) {
    document.getElementById("feedbackbutton").parentNode.style.display =
      "inline";
    document.getElementById("feedbackbutton").addEventListener("click", () => {
      Util.openurl(DB.ui.fbUrl);
    });
  }
  if (DB.ui.license) {
    document.getElementById("licensecontent").innerHTML =
      `<p>${Util.escapeHTML(DB.ui.license).replaceAll("\n\n", "</p><p>")}</p>`;
    document.getElementById("licensebutton").parentNode.style.display =
      "inline";
    document.getElementById("closelicense").addEventListener("click", () => {
      Util.toggleDialog(null, false);
    });
    document.getElementById("licensebutton").addEventListener("click", () => {
      let dialog = document.getElementById("licensedialog");
      Util.toggleDialog(dialog, true);
    });
  }
  document.getElementById("printbutton").addEventListener("click", () => {
    genpdf();
  });
  document.getElementById("xprobe").addEventListener("click", (e) => {
    e.target.disabled = true;
    e.target.innerText = "Cross-probe started";
    crossProbe();
  });

  document.getElementById("schematic-title").textContent = DB.ui.schTitle;
  document.getElementById("schematic-version").textContent = DB.ui.schVers;
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
        Util.navigateTo(resp.targets);
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
  if (!(name in DB.ui.themes)) {
    name = "Default";
  }
  if (!target) {
    Settings.set("SchematicTheme", name);
  }
  for (const v in DB.ui.themes[name]) {
    (target || document.body).style.setProperty(
      "--" + v,
      DB.ui.themes[name][v],
    );
  }
  Diffs.applyAnimationColorWorkaround(name);
}

function injectPage(pageIndex) {
  PageList.select(pageIndex);

  // empty the change filter on page change
  document.getElementById("changefilter").value = "";

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

  Diffs.pageChanged(
    svgPage,
    Settings.get("SchematicTheme", DB.ui.themeDefault),
  );
}

function cyclePage(delta, retainPan, mouseEvent, leftoverPanY) {
  let nextPageIndex = DB.curPageIndex + delta;
  if (nextPageIndex < 0 || nextPageIndex >= DB.numPages()) {
    return;
  }
  let origPos = Viewport.savePos();
  Util.navigateTo(DB.pageName(nextPageIndex));
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
    context = DB.compProp(result.data, DB.KEY_LIB_ID) || "Part symbol";
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

  // No matches of any kind were found. Default to first page.
  if ((!target && pageIndex === -1) || DB.curPageIndex === null) {
    window.location.hash = "#" + DB.pageName(0);
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

  if (target) {
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
  }

  // Zoom to content if requested and nothing matched
  if (Settings.get("ZoomToContent") === "zoom") {
    let fakeElem = {
      contentbox: DB.pageContentBox(),
      box: DB.pageViewBox(),
    };
    panToElems([fakeElem], 0.01); // the tiniest bit of margin
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
  } else if (Diffs.sidebarVisible()) {
    widthOffset = -document.getElementById("animationtoolbox").offsetWidth;
  }

  Viewport.panToElems(elems, padding, widthOffset);
}

function genpdf() {
  let win = window.open("", "printwin", "height=600, width=800");
  win.document.write(
    "<html><head><title>Preparing to print " +
      Util.escapeHTML(DB.ui.schTitle) +
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
    win.document.title = DB.ui.schTitle;
  };

  // Copy in fonts
  document.fonts.forEach((f) => win.document.fonts.add(f));

  // Black-on-white theme
  setTheme(DB.ui.themeBW, win.document.body);

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
  Util.navigateTo(target_href, true);
  if (target.tagName === "text") {
    // Remove temporary id
    target.removeAttribute("id");
  }
  result.container = target;
  result.target = target;
  displayTooltip(result, true);
}
