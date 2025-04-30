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

const resultsPerPage = 10;
let results = [];
let resultPage = 0;
let resultsOnPage = [];
let DB = null;
let diffHideSidebar = null;

export function init(db, diffHideSidebarFunc) {
  // FIXME: reduce this jankiness
  DB = db;
  diffHideSidebar = diffHideSidebarFunc;
  document
    .getElementById("resultpagenumber")
    .addEventListener("keyup", function () {
      let enteredPage = validateResultPageNumber();
      if (enteredPage) {
        resultPage = enteredPage - 1;
        populateMatches();
      }
    });
  document
    .getElementById("previousresults")
    .addEventListener("click", function () {
      cycleResultPage(-1);
    });
  document.getElementById("nextresults").addEventListener("click", function () {
    cycleResultPage(1);
  });
  document
    .getElementById("search-expandable")
    .addEventListener("input", function () {
      const filter = this.value;

      resultPage = 0;
      if (!filter) {
        populateMatches([], 0);
        return;
      }

      results = [];
      results.push(...DB.searchComps(filter));
      results.push(...DB.searchNets(filter));
      results.push(...DB.searchPins(filter));
      results.push(...DB.searchText(filter));
      results.sort((a, b) => a.distance - b.distance);

      populateMatches();

      // make unpopulated results not hoverable
      let searchList = document.getElementsByClassName("resultentry");
      for (let item of searchList) {
        if (!item.textContent || !item.textContent.trim()) {
          item.style.pointerEvents = "none";
        } else {
          item.style.pointerEvents = "auto";
        }
      }
    });

  document
    .getElementById("search-expandable")
    .addEventListener("keydown", function (e) {
      setActive(true, false);
    });

  document
    .getElementById("search-expandable")
    .addEventListener("focus", function () {
      setActive(true);
    });

  document
    .getElementById("expandsearchbutton")
    .addEventListener("click", function (e) {
      e.preventDefault();
      setActive(!isActive());
    });
}

export function isActive() {
  return document.getElementById("searchpane").style.display != "none";
}

export function setActive(active, selected) {
  if (active) {
    document.getElementById("searchpane").style.display = "inline";
    document.getElementById("expandsearchbutton").style.backgroundColor =
      "lightgrey";
    document.getElementById("search-expandable").focus();
    diffHideSidebar();
    if (selected) {
      document.getElementById("search-expandable").select();
    }
  } else {
    document.getElementById("searchpane").style.display = "none";
    document.getElementById("expandsearchbutton").style.backgroundColor = null;
    document.getElementById("search-expandable").blur();
  }
}

export function isFocused() {
  // not focused if another input element is focused
  return (
    isActive() &&
    (document.activeElement.tagName != "INPUT" ||
      document.activeElement == document.getElementById("search-expandable"))
  );
}

function getResultLinks() {
  return Array.prototype.slice.call(
    document
      .getElementById("searchpane")
      .getElementsByClassName("itempagelink"),
  );
}

export function clickedPageLink(elem, e) {
  // prevent enter key "clicks" from doubling this fn
  if (!e.detail) {
    e.preventDefault();
    return;
  }
  window.history.pushState(null, "", elem.getAttribute("href"));
  window.onpopstate();
  for (let e of document
    .getElementById("searchpane")
    .getElementsByClassName("selectedsearch")) {
    e.classList.remove("selectedsearch");
  }
  elem.classList.add("selectedsearch");
}

export function onEnterKey(e) {
  let searchResults = getResultLinks();
  if (!searchResults.length) {
    return;
  }
  let nextLinkToFocus = null;
  let selectedLinks = searchResults.filter((r) =>
    r.classList.contains("selectedsearch"),
  );
  let nextIndex =
    searchResults.indexOf(selectedLinks[0]) + (e.shiftKey ? -1 : 1);
  if (nextIndex == searchResults.length) {
    // wrap to next page
    cycleResultPage(1);
    searchResults = getResultLinks();
    nextIndex = 0;
  } else if (nextIndex == -1) {
    // wrap to previous page
    cycleResultPage(-1);
    searchResults = getResultLinks();
    nextIndex = searchResults.length - 1;
  }
  nextLinkToFocus =
    searchResults[nextIndex > -1 ? nextIndex : searchResults.length - 1];
  clickedPageLink(nextLinkToFocus, { detail: 1 });
  // scroll the selected result into view
  nextLinkToFocus.scrollIntoView({
    behavior: "smooth",
    block: "nearest",
    inline: "nearest",
  });
}

function validateResultPageNumber() {
  let pnInput = document.getElementById("resultpagenumber");
  let enteredPage = parseInt(pnInput.value);
  if (
    enteredPage > 0 &&
    enteredPage <= Math.ceil(results.length / resultsPerPage)
  ) {
    pnInput.style.borderBottomColor = "grey";
    return enteredPage;
  } else {
    pnInput.style.borderBottomColor = "red";
    return 0;
  }
}

function getPages(pList, ref, color, zebra) {
  let rawHTML = "";
  let pCounts = {};
  for (let p of pList) {
    pCounts[p] = (pCounts[p] || 0) + 1;
  }
  let pCounter = 1;
  for (let page in pCounts) {
    let p = parseInt(page);
    rawHTML +=
      `<div${zebra && pCounter % 2 ? ' style="background-color:rgba(0,0,0,0.4)"' : ""}>` +
      `<a class="itempagelink" style="color:${color}" ` +
      `href="#${DB.pageName(p)},${escape(ref)}" ` +
      `onclick="Search.clickedPageLink(this, event); return false">` +
      `${DB.pageName(p)}${pCounts[p] > 1 ? " (" + pCounts[p] + ")" : ""}</a></div>`;
    pCounter++;
  }
  return rawHTML;
}

function populateMatches() {
  //clear any old matches
  document.getElementById("matchlist").innerHTML = "";
  document.getElementById("resultpagenumber").value = resultPage + 1;
  document.getElementById("resultpagenumber").disabled = !results.length;
  validateResultPageNumber();

  resultsOnPage = results.slice(
    resultsPerPage * resultPage,
    resultsPerPage * (resultPage + 1),
  );

  // add this page's matches
  for (let match of resultsOnPage) {
    const subtitle = `<div style="font-size:0.8em"><span class="mdl-list__item-text-body" style="color:grey;height:auto">${match.prop}: ${match.value}</span></div>`;
    document.getElementById("matchlist").innerHTML += `<div class="resultentry">
        <div style="height:auto">
          <span><span style="font-weight:bold">${match.type}</span>: <code>${match.display}</code></span>
          ${match.type == "component" ? subtitle : ""}
          <span style="color:grey;height:auto"></span>
          <div class="resultpages">${getPages(match.pages, match.display, "yellow", true)}</div>
        </div>
      </div>`;
  }

  let matchCtr = resultsPerPage * resultPage + resultsOnPage.length;

  document.getElementById("morematchescount").innerHTML = matchCtr
    ? `<b>${resultPage * resultsPerPage + 1}-${matchCtr}</b> of <b>${results.length}</b> results`
    : "no matches found";

  document.getElementById("nextresults").disabled = matchCtr >= results.length;
  document.getElementById("previousresults").disabled =
    matchCtr <= resultsPerPage;
}

function cycleResultPage(delta) {
  let numPages = Math.ceil(results.length / resultsPerPage);
  resultPage = (resultPage + numPages + delta) % numPages;
  populateMatches();
}
