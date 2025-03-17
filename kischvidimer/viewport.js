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

import { Hammer } from "js-libraries/hammer";
import { svgPanZoom } from "js-libraries/svg-pan-zoom";
import * as Tooltip from "tooltip";
export { Tooltip };

const pageSeparation = 150; // svg coords
const panPageHysteresis = 100; // client coords

let svgPage = null;
let currentPanZoom = null;
let hammer = null;
let panCounter = 0;
let originalViewBox = null;

export function init() {
  svgPage = document.getElementById("svgPage");

  window.addEventListener("resize", function () {
    currentPanZoom.resize();
  });

  svgPage.addEventListener("touchstart", (evt) => {
    // with page changing, we expect a touch target to be removed from the DOM
    // https://developer.mozilla.org/en-US/docs/Web/API/Touch/target
    // ^ we need to attach the touch listeners to the target directly in order to preserve the touchmoves
    let panned = [evt.targetTouches[0].clientX, evt.targetTouches[0].clientY];
    let onTouchMove = (e) => {
      if (true || !evt.target.closest("#svgPage")) {
        let delta = [
          e.targetTouches[0].clientX - panned[0],
          e.targetTouches[0].clientY - panned[1],
        ];
        currentPanZoom.panBy({ x: delta[0], y: delta[1] });
        panned = [e.targetTouches[0].clientX, e.targetTouches[0].clientY];
      }
    };
    let onTouchEnd = () => {
      panned = [0, 0];
      evt.target.removeEventListener("touchmove", onTouchMove);
      evt.target.removeEventListener("touchend", onTouchEnd);
    };
    evt.target.addEventListener("touchmove", onTouchMove);
    evt.target.addEventListener("touchend", onTouchEnd);
  });

  initHammer();
}

function initHammer() {
  let initialScale = 1;

  hammer = Hammer(svgPage, {
    inputClass: Hammer.SUPPORT_POINTER_EVENTS
      ? Hammer.PointerEventInput
      : Hammer.TouchInput,
  });

  hammer.get("pinch").set({ enable: true });
  hammer.on("pinchstart pinchmove", function (ev) {
    // On pinch start remember initial zoom
    if (ev.type === "pinchstart") {
      initialScale = currentPanZoom.getZoom();
    }
    // ev.scale accumulates, so treat it as relative to the initial scale
    currentPanZoom.zoomAtPoint(initialScale * ev.scale, {
      x: ev.center.x,
      y: ev.center.y,
    });
  });
  // Prevent moving the page on some devices when panning over SVG
  svgPage.addEventListener("touchmove", function (e) {
    e.preventDefault();
  });
}

/** Launches a URL in a new window.
 * If we're in an isolated browser, request the python server to launch the URL
 * in a proper browser. Otherwise, just open a new window.
 */
export function openurl(url) {
  if (!url || url.startsWith("#")) {
    window.history.pushState(null, "", url);
    window.onpopstate();
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

export function loadPage(pgdata, instance, viewBox, cyclePageFunc) {
  // Load up the html data into a temporary div tag
  let svgData = document.createElement("div");
  svgData.innerHTML = pgdata;
  // Configure the svg
  let svg = svgData.firstElementChild;
  svg.style.width = "100%";
  svg.style.height = "100%";
  svg.id = "activesvg";
  // Make the active instance visible and delete the rest
  selectInstance(svg, instance);
  // Upgrade any links to use openurl
  for (let a of svg.getElementsByTagName("a")) {
    a.removeAttribute("target");
    a.onclick = function () {
      openurl(a.href.animVal);
      return false;
    };
  }
  // Move contents of svg into a temporary svg, keeping just the top svg tag
  svgData = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svgData.append(...svg.children);
  // Replace the current page DOM with this single svg tag
  svgPage.replaceChildren(svg);
  originalViewBox = viewBox;

  // Create the svgpanzoom with the shell SVG
  currentPanZoom = svgPanZoom(svg, {
    zoomScaleSensitivity: 0.2,
    dblClickZoomEnabled: false,
    onZoom: function () {
      Tooltip.hide();
      // we never want to emulate mousedown on ghost transition caused by zooming
      svgPage.mouseEvent = null;
    },
    onPan: function (c) {
      panCounter++;
      Tooltip.hide();

      let y = currentPanZoom.getPan().y;
      let panYExtents = getPanYPageExtents();

      // handle page transitions for pan-past-boundary
      if (y < panYExtents[0] - panPageHysteresis) {
        cyclePageFunc(
          1,
          1,
          svgPage.mouseEvent,
          panYExtents[0] - panPageHysteresis - y,
        );
      } else if (y > panYExtents[1] + panPageHysteresis) {
        cyclePageFunc(
          -1,
          -1,
          svgPage.mouseEvent,
          y - panYExtents[1] - panPageHysteresis,
        );
      }
      // increase opacity of ghost pages as they approach the boundary
      Array.from(svgPage.getElementsByClassName("ghostafter")).forEach((g) => {
        g.style.filter = `opacity(${Math.max(Math.exp((panYExtents[0] - y) / panPageHysteresis), 0.2)})`;
      });
      Array.from(svgPage.getElementsByClassName("ghostbefore")).forEach((g) => {
        g.style.filter = `opacity(${Math.max(Math.exp((y - panYExtents[1]) / panPageHysteresis), 0.2)})`;
      });
    },
    customEventsHandler: {
      haltEventListeners: [
        "touchstart",
        "touchend",
        "touchmove",
        "touchleave",
        "touchcancel",
      ],
      init: function () {},
      destroy: function () {},
    },
  });

  // Now that svgpanzoom is set up, add the content back in
  svg.firstElementChild.append(...svgData.children);
}

export function selectInstance(container, inst) {
  // Shows the specified instance and deletes all the rest
  Array.from(container.getElementsByClassName("instance")).forEach((anim) => {
    if (inst === undefined || anim.classList.contains(inst)) {
      anim.parentNode.removeAttribute("opacity");
      anim.outerHTML = "";
      inst = null;
    } else {
      anim.parentNode.outerHTML = "";
    }
  });
}

export function createGhostPages(DB, pageIndex) {
  // append ghost pages to the svg
  let ghostSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");

  // create ghost pages
  // only populate 4 ghost pages: 2 before and 2 after current page
  // from testing with default zoom min, seeing more than 2 past the current page is not likely
  let yOffset = 0;
  let pageOffset = 0;
  for (
    let i = Math.max(0, pageIndex - 2);
    i < Math.min(DB.numPages(), pageIndex + 3);
    i++
  ) {
    let targetSvg = ghostSvg;
    if (i == pageIndex) {
      pageOffset = yOffset;
      yOffset += DB.pageViewBox(pageIndex)[3] + pageSeparation;
      continue;
    }
    yOffset += addGhostPage(
      ghostSvg,
      DB,
      i,
      yOffset,
      i > pageIndex,
      originalViewBox[2],
    );
  }

  ghostSvg.setAttribute("x", originalViewBox[0]);
  ghostSvg.setAttribute("y", -pageOffset + originalViewBox[1]);
  ghostSvg.setAttribute("width", originalViewBox[2]);
  ghostSvg.setAttribute("height", yOffset);
  ghostSvg.setAttribute("class", "ghost");

  // Append to SVG
  svgPage.firstElementChild.firstElementChild.appendChild(ghostSvg);
}

function addGhostPage(
  ghostSvg,
  DB,
  pageIndex,
  yOffset,
  pageBelow,
  activeWidth,
) {
  let ghostRect = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "rect",
  );
  let arrowChar = pageBelow ? "↓" : "↑";
  let viewBox = DB.pageViewBox(pageIndex);
  let xOffset = -(viewBox[2] - activeWidth) / 2;

  ghostRect.setAttribute("x", xOffset);
  ghostRect.setAttribute("y", yOffset);
  ghostRect.setAttribute("width", viewBox[2]);
  ghostRect.setAttribute("height", viewBox[3]);
  ghostRect.setAttribute("class", "ghostpage");

  let ghostText = document.createElementNS(
    "http://www.w3.org/2000/svg",
    "text",
  );

  ghostText.setAttribute("x", xOffset + viewBox[2] / 2);
  ghostText.setAttribute("y", yOffset + (pageBelow ? 300 : viewBox[3] - 300));
  ghostText.setAttribute(
    "dominant-baseline",
    pageBelow ? "hanging" : "text-after-edge",
  );
  ghostText.innerHTML = `${arrowChar} ${DB.pageName(pageIndex)} ${arrowChar}`;

  let ghostG = document.createElementNS("http://www.w3.org/2000/svg", "g");
  ghostG.setAttribute("class", pageBelow ? "ghostafter" : "ghostbefore");
  ghostG.append(ghostRect, ghostText);
  onPanlessClick(ghostG, () => {
    window.history.pushState(null, "", "#" + DB.pageName(pageIndex));
    window.onpopstate();
  });
  ghostSvg.append(ghostG);
  return viewBox[3] + pageSeparation;
}

function onPanlessClick(elem, callback) {
  // something like onclick, but suppressed if there is any panning
  elem.addEventListener("mousedown", () => {
    elem.panCounter = panCounter;
  });
  elem.addEventListener("mouseup", () => {
    if (elem.panCounter == panCounter) {
      callback();
    }
  });
}

export function savePos() {
  let state = {};
  state.pan = currentPanZoom.getPan();
  state.zoom = currentPanZoom.getZoom();
  state.realZoom = currentPanZoom.getSizes().realZoom;
  state.viewBox = originalViewBox;
  return state;
}

export function restorePos(state, panDir, panY) {
  // FIXME: this code doesn't work correctly with tall pages
  // handle pan-caused page switch by immediately panning to the equivalent x,y,zoom that they came from
  let pageScaling = originalViewBox[2] / state.viewBox[2];
  // xOffset is caused by centering pages of different size
  let xOffset = (state.viewBox[2] - originalViewBox[2]) / 2;
  currentPanZoom.zoom(state.zoom * pageScaling);
  currentPanZoom.pan({
    x:
      state.pan.x +
      (xOffset + state.viewBox[0] - originalViewBox[0]) * state.realZoom,
    y:
      getPanYFromPageRatio((panDir + 1) / 2) +
      panDir * (state.realZoom * pageSeparation - panPageHysteresis) +
      panY,
  });
}

function getPanYFromPageRatio(ratio) {
  // ratio = 0 returns the pan that centers at top of schematic
  // ratio = 0.5 returns the pan that centers the schematic in Y
  // ratio = 1 returns the pan that centers at bottom of schematic
  let extents = getPanYPageExtents();
  return extents[0] + ratio * (extents[1] - extents[0]);
}

function getPanYPageExtents() {
  // returns [a, b], where
  //   a = pan Y value that centers the top edge of the schematic page
  //   b = pan Y value that centers the bottom edge of the schematic page
  let pageHeight = originalViewBox[3];
  let realZoom = currentPanZoom.getSizes().realZoom;
  let centerOffset = svgPage.offsetHeight / 2;
  let viewBoxFactor = realZoom * (originalViewBox[1] + pageHeight);
  return [
    centerOffset - viewBoxFactor,
    centerOffset - viewBoxFactor + pageHeight * realZoom,
  ];
}

export function contentBoxToPageCoords(e) {
  // back-calculate the page coordinates of the content box.
  // the box expands to fit the window so just use the center and zoom
  let center = getCenter(svgPage.getBoundingClientRect());
  let realZoom = currentPanZoom.getSizes().realZoom;
  return {
    left: center.x + (e.contentbox[0] - e.box[0] - e.box[2] / 2) * realZoom,
    top: center.y + (e.contentbox[1] - e.box[1] - e.box[3] / 2) * realZoom,
    right:
      center.x +
      (e.contentbox[0] + e.contentbox[2] - e.box[0] - e.box[2] / 2) * realZoom,
    bottom:
      center.y +
      (e.contentbox[1] + e.contentbox[3] - e.box[1] - e.box[3] / 2) * realZoom,
  };
}

export function panToBounds(bounds, padding, widthOffset) {
  panToCenter(getCenter(bounds));

  // zoom to a level that at least captures the bounds (0.8 sets 10% padding for zoom)
  currentPanZoom.zoom(
    Math.min(
      5,
      ...[
        (padding * (currentPanZoom.getSizes().width + widthOffset)) /
          (bounds.right - bounds.left),
        (padding * currentPanZoom.getSizes().height) /
          (bounds.bottom - bounds.top),
      ],
    ),
  );

  // pan left to center the target if a sidebar is open
  if (widthOffset) {
    currentPanZoom.panBy({
      x: widthOffset / 2,
      y: 0,
    });
  }
}

function panToCenter(targetCenter) {
  let svgCenter = getCenter(svgPage.getBoundingClientRect());
  currentPanZoom.panBy({
    x: svgCenter.x - targetCenter.x,
    y: svgCenter.y - targetCenter.y,
  });
}

function getCenter(bbox) {
  return {
    x: (bbox.left + bbox.right) / 2,
    y: (bbox.top + bbox.bottom) / 2,
  };
}

export function zoomIn() {
  currentPanZoom.zoomIn();
}

export function zoomFit() {
  currentPanZoom.resize();
  currentPanZoom.fit();
  currentPanZoom.center();
  currentPanZoom.zoom(1);
}

export function zoomOut() {
  currentPanZoom.zoomOut();
}

export function onkeydown(e) {
  if (e.key == "ArrowUp") {
    currentPanZoom.panBy({ x: 0, y: 100 });
  } else if (e.key == "ArrowDown") {
    currentPanZoom.panBy({ x: 0, y: -100 });
  } else if (e.key == "ArrowLeft") {
    currentPanZoom.panBy({ x: 100, y: 0 });
  } else if (e.key == "ArrowRight") {
    currentPanZoom.panBy({ x: -100, y: 0 });
  } else if (e.key == "=" || e.key == "+") {
    zoomIn();
  } else if (e.key == "-" || e.key == "_") {
    zoomOut();
  } else {
    return true;
  }
  return false;
}
