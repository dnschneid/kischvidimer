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

let currentPanZoom = null;
// internally we track the index onto data.pages, but externally it's the page name
let currentPageIndex = null;
let svgPage = document.getElementById('svgPage');

let xprobeEndpoint = "http://localhost:4241/xprobe";
let xprobe = null;

let tooltipFixed = false;

let filteredDiffRows = new Set();
let diffMap = {};

let matchPage = 0;
let matchesPerPage = 10;
let searchMatches = [];
let searchMatchesOnPage = [];

let pageSeparation = 150; // svg coords
let panPageHysteresis = 100 // client coords

let panCounter = 0;

let hammer = null;

let ELEM_TYPE_SELECTORS = {
    'component': [
        '[p]',
        'symbol'
    ],
    'net': [
        '[prop="SIG_NAME"]',
        '[prop="NETGROUP"]',
        '[prop="HDL_POWER"]'
    ],
    'ghost': [
        '.ghost'
    ]
};

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
    // Load schematic data
    if (typeof (data) === 'string') {
        data = JSON.parse(decodeData(data))
    }

    // handle case with no pages
    if (!data.pages.length) {
        svgPage.innerText = (uiMode < 2) ? 'No pages to display.' : 'No changes to display.';
        toggleDialog(document.getElementById('loadingdialog'), false);
        return;
    }

    // Initialize theme
    let tprev = "lwfbgphrv";  // order of the theme colors to use to render the label
    document.querySelector(".themeselect").innerHTML =
        Object.entries(themes).map(([name, data]) => `
        <label style="margin-bottom:10px" class="mdl-radio mdl-js-radio mdl-js-ripple-effect"
            for="toption-${name}">
            <input type="radio" id="toption-${name}" class="mdl-radio__button" name="options" value="${name}">
            <span class="mdl-radio__label" style="background-color:${data.d}">
            ${name.split("").map((c,i)=>`<span style="color:${data[tprev.substr(i%tprev.length,1)]}">${c}</span>`).join('')}
            </span></label>`).join('');
    setTheme(getSetting('SchematicTheme', themeDefault));

    document.getElementById('zoomcontrols').style.display = getSetting('ShowZoomControls') == 'shown' ? 'inline' : 'none';
    componentHandler.upgradeDom();
    fillPageList(data.pages);

    if (uiMode >= 2) {
        initializeExclusions();
        initializeCheckModel();
    }

    if (uiMode >= 2) {
        document.getElementById('diffbutton').getElementsByTagName('img')[0].src = diffIcon;
        document.getElementById('diffbutton').style.display = '';
    }
    for (let elem of document.getElementsByClassName('threewayshown')) {
        elem.style.display = (uiMode < 3) ? 'none' : '';
    }

    window.onpopstate();

    toggleDialog(document.getElementById('loadingdialog'), false);
    document.getElementById('pagefilter').addEventListener('input', function () {
        filterPages(this.value);
    });
    document.getElementById('resultpagenumber').addEventListener('keyup', function () {
        let enteredPage = validateResultPageNumber();
        if (enteredPage) {
            matchPage = enteredPage - 1;
            populateMatches();
        }
    });
    document.getElementById('previousresults').addEventListener('click', function () {
        cycleResultPage(-1);
    });
    document.getElementById('nextresults').addEventListener('click', function () {
        cycleResultPage(1);
    });
    document.getElementById('search-expandable').addEventListener('input', function () {
        filterSearch(this.value);
        // make unpopulated results not hoverable
        let searchList = document.getElementsByClassName('resultentry');
        for (let item of searchList) {
            if (!item.textContent || !item.textContent.trim()) {
                item.style.pointerEvents = 'none';
            } else {
                item.style.pointerEvents = 'auto';
            }
        }
    });

    document.getElementById('search-expandable').addEventListener('keydown', function (e) {
        setSearchActive(true, false);
    });

    document.getElementById('search-expandable').addEventListener('focus', function () {
        setSearchActive(true);
    });

    document.getElementById('expandsearchbutton').addEventListener('click', function (e) {
        e.preventDefault();
        setSearchActive(!searchIsActive());
    });

    // add double click listeners
    window.ondblclick = function (evt) {
        let target = evt.target;
        // Process text doubleclicks
        // Clicking on tspan is the same as clicking on text
        if (target.tagName === 'tspan')
            target = target.parentElement;
        if (target.tagName === 'text') {
            copyToClipboard(target.textContent);
            if (!target.classList.contains('highlight')) {
                setTimeout(function () {
                    target.classList.remove('highlight');
                }, 500);
            }
            target.classList.add('highlight');
            return;
            /* FIXME: this is probably for handling diffs
            for (let i = 0; i < target.childNodes.length; ++i) {
                if (target.childNodes[i].nodeType === 3 && !/^\s+$/.test(target.childNodes[i].textContent)) {
                    copyToClipboard(target.childNodes[i].textContent);
                    if (!target.classList.contains('highlight')) {
                        setTimeout(function () {
                            target.classList.remove('highlight');
                        }, 500);
                    }
                    target.classList.add('highlight');
                    return;
                }
            }
            */
        }
        // Launch an associated, visible url first
        if (target.hasAttribute('tag')) {
            let a = svgPage.querySelector(`[tag="${target.getAttribute('tag')}"]>a`);
            if (a) {
                a.onclick();
                return;
            }
        }
        // Launch any prop url
        let elem = getElem(target);
        if (elem && elem.indexed) {
            for (let prop in elem.indexed) {
                let val = elem.indexed[prop];
                if (/^https?:[/][/]/.test(val)) {
                    openurl(val.split(/\s/)[0]);
                    return;
                }
            }
        }
    };

    window.addEventListener('resize', function () {
        currentPanZoom.resize();
    });

    // Handle tooltips
    svgPage.onmouseover = function (e) {
        if (tooltipFixed || e.buttons) {
            return;
        }
        let elem = getElem(e.target);
        if (elem.type === 'net' && elem.name !== 'GND') {
            displayTooltip(elem, e.target);
        } else if (elem.type === 'component' && elem.indexed) {
            displayTooltip(elem, e.target);
        } else {
            displayTooltip(false);
        }
    };
    svgPage.onmousemove = function (evt) {
        if (!tooltipFixed) {
            let tooltip = document.getElementById('tooltip');
            tooltip.style.left = Math.min(evt.pageX + 20, window.innerWidth - tooltip.offsetWidth) + 'px';
            tooltip.style.top = (evt.pageY + tooltip.offsetHeight + 20 > window.innerHeight ?
                evt.pageY - tooltip.offsetHeight - 20 : evt.pageY + 20) + 'px';
        }
        // store the mouse event in case we need to emulate mousedown on ghost transition
        svgPage.mouseEvent = evt;
    };
    svgPage.addEventListener('touchstart', (evt) => {
        // with page changing, we expect a touch target to be removed from the DOM
        // https://developer.mozilla.org/en-US/docs/Web/API/Touch/target
        // ^ we need to attach the touch listeners to the target directly in order to preserve the touchmoves
        let panned = [evt.targetTouches[0].clientX, evt.targetTouches[0].clientY];
        let onTouchMove = (e) => {
            if (true || !evt.target.closest('#svgPage')) {
                let delta = [
                    e.targetTouches[0].clientX - panned[0],
                    e.targetTouches[0].clientY - panned[1]
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
    svgPage.onmouseout = function () {
        if (!tooltipFixed) {
            displayTooltip(false);
        }
    };
    svgPage.onmousedown = function () {
        if (tooltipFixed) {
            tooltipFixed = false;
            let tooltip = document.getElementById('tooltip');
            tooltip.style.display = 'none';
            tooltip.style.opacity = 0.8;
        }
    };
    svgPage.onmouseup = function (e) {
        if (e.button === 3) {
            window.history.back();
        } else if (e.button === 4) {
            window.history.forward();
        } else {
            let tooltip = document.getElementById('tooltip');
            if (tooltip.style.display !== 'none') {
                tooltip.style.opacity = 1;
                tooltipFixed = true;
                let elem = getElem(e.target);
                if (elem.type === 'net' && elem.name !== 'GND') {
                    crossProbe("NET", elem.name);
                } else if (elem.type === 'component' && elem.indexed) {
                    crossProbe("SELECT", elem.name);
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

        if (e.target.tagName != 'INPUT') {
            if (e.key == 'ArrowUp') {
                currentPanZoom.panBy({ x: 0, y: 100 });
            } else if (e.key == 'ArrowDown') {
                currentPanZoom.panBy({ x: 0, y: -100 });
            } else if (e.key == 'ArrowLeft') {
                currentPanZoom.panBy({ x: 100, y: 0 });
            } else if (e.key == 'ArrowRight') {
                currentPanZoom.panBy({ x: -100, y: 0 });
            } else if (e.key == 'PageUp') {
                cyclePage(-1);
            } else if (e.key == 'PageDown') {
                cyclePage(1);
            } else if (e.key == '=' || e.key == '+') {
                zoomIn();
            } else if (e.key == '-' || e.key == '_') {
                zoomOut();
            }
        }
        if (e.key == "Enter" && searchIsActive()) {
            // prevent focused input (not search input) enter from triggering search cycle
            if (document.activeElement != document.getElementById('search-expandable')
                && document.activeElement.tagName == 'INPUT') {
                return;
            }

            let searchResults = getResultLinks();
            if (!searchResults.length) {
                return;
            }
            let nextLinkToFocus = null;
            let selectedLinks = searchResults.filter(r => r.classList.contains('selectedsearch'));
            let nextIndex = searchResults.indexOf(selectedLinks[0]) + (e.shiftKey ? -1 : 1);
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
            nextLinkToFocus = searchResults[nextIndex > -1 ? nextIndex : searchResults.length - 1];
            clickedPageLink(nextLinkToFocus, { detail: 1 });
            // scroll the selected result into view
            nextLinkToFocus.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
        } else if (e.keyCode === 114 || (e.ctrlKey && e.keyCode === 70)) {
            e.preventDefault();
            setSearchActive(true, true);
        } else if (e.key == 'Escape') {
            setSearchActive(false);
            toggleDialog(null, false);
        } else if (e.ctrlKey && e.keyCode == 80) {
            genpdf();
        }
    }

    document.querySelectorAll('.dialogwrapper').forEach(wrapper => {
        wrapper.addEventListener('click', (e) => {
            if (e.target !== wrapper) {
                return;
            }
            toggleDialog(null, false);
        });
    });

    document.getElementById('tooltiplink').addEventListener('click', function () {
        let pageName = data.pages[currentPageIndex].name;
        copyToClipboard(
            window.location.href.split('#')[0]
            + '#' + pageName + ','
            + document.getElementById('tooltipname').textContent, 'link'
        );
    });

    document.getElementById('zoomcontrolout').addEventListener('click', zoomOut);
    document.getElementById('zoomcontrolfit').addEventListener('click', zoomFit);
    document.getElementById('zoomcontrolin').addEventListener('click', zoomIn);

    //init settings
    document.getElementById('settingsbutton').addEventListener('click', function () {
        document.getElementById('uiversion').innerText = `UI: ${uiVersion}`;

        // show zoom control selection
        if (getSetting('ShowZoomControls') == 'shown') {
            document.getElementById('zoomcontrolcheckboxlabel').MaterialCheckbox.check();
        } else {
            document.getElementById('zoomcontrolcheckboxlabel').MaterialCheckbox.uncheck();
        }

        // zoom to content
        if (getSetting('ZoomToContent') == 'zoom') {
            document.getElementById('zoomcontentcheckboxlabel').MaterialCheckbox.check();
        } else {
            document.getElementById('zoomcontentcheckboxlabel').MaterialCheckbox.uncheck();
        }

        let toption = document.getElementById('toption-' + getSetting('SchematicTheme', themeDefault));
        if (toption) {
            toption.parentNode.MaterialRadio.check();
        }
        let dialog = document.getElementById('settingsdialog');
        toggleDialog(dialog, true);
    });
    if (feedbackURL && /https?:[/][/]/.test(feedbackURL)) {
        document.getElementById('feedbackbutton').parentNode.style.display = 'inline';
        document.getElementById('feedbackbutton').addEventListener('click', function () {
            openurl(feedbackURL);
        });
    }
    document.getElementById('printbutton').addEventListener('click', function () {
        genpdf();
    });
    document.getElementById('uiversion').addEventListener('click', function() {
        copyToClipboard(uiVersion, 'kischvidimer version');
    });
    document.getElementById('closesettings').addEventListener('click', function () {
        let dialog = document.getElementById('settingsdialog');
        toggleDialog(dialog, false);
    });
    document.getElementById('applysettings').addEventListener('click', function () {
        setSetting('ShowZoomControls', document.getElementById('zoomcontrolcheckbox').checked ? 'shown' : 'hidden');
        if (document.getElementById('zoomcontrolcheckbox').checked) {
            document.getElementById('zoomcontrols').style.display = 'inline';
        } else {
            document.getElementById('zoomcontrols').style.display = 'none';
        }
        setSetting('ZoomToContent', document.getElementById('zoomcontentcheckbox').checked ? 'zoom' : '');

        let dialog = document.getElementById('settingsdialog');
        let selectedTheme = document.querySelector('[id^=toption]:checked');
        setTheme(selectedTheme.getAttribute('value'));
        toggleDialog(dialog, false);
    });
    document.getElementById('xprobe').addEventListener('click', function (e) {
        e.target.disabled = true;
        e.target.innerText = "Cross-probe started";
        crossProbe();
    });

    //init submit prompt
    document.getElementById('submitbutton').addEventListener('click', function () {
        let unresolvedConflictPages = {};
        let unselectedDiffList = [];

        // flag user if there are conflicts without any selection
        for (let page in data.diffs) {
            for (let diffPair of data.diffs[page]) {
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
                    if (!unresolvedConflictPages[page]) {
                        unresolvedConflictPages[page] = 1;
                    } else {
                        unresolvedConflictPages[page] += 1;
                    }
                }
            }
        }

        if (Object.keys(unresolvedConflictPages).length) {
            document.getElementById('unresolvedWarning').style.display = null;
            document.getElementById('unresolvedWarning').innerHTML = 'Some conflicts do not have any changes selected:<ul>'
                + (Object.keys(unresolvedConflictPages).map(k =>
                    `<li>${unresolvedConflictPages[k]} unresolved in ${data.pages[k].name}</li>`)).join('')
                + '</ul>Unselected changes will be abandoned entirely.'
        } else {
            document.getElementById('unresolvedWarning').style.display = 'none';
        }

        let dialog = document.getElementById('applydiffsdialog');
        toggleDialog(dialog, true);
    });
    document.getElementById('closeapplydiffs').addEventListener('click', function () {
        let dialog = document.getElementById('applydiffsdialog');
        toggleDialog(dialog, false);
    });
    document.getElementById('applydiffs').addEventListener('click', function () {
        let unselectedDiffList = [];

        // push un-selected diffs to be POSTed
        for (let page in data.diffs) {
            for (let diffPair of data.diffs[page]) {
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
            }
        }

        // body is simple json list of diff id's that are not checked
        fetch("./apply", {
            method: "POST",
            body: JSON.stringify(unselectedDiffList)
        }).then(res => {
            window.close();
        }).catch(error => {
            document.documentElement.innerHTML = error;
        });
    });

    if (uiMode > 1) {
        document.getElementById('diffbutton').addEventListener('mousedown', function () {
            toggleDiffSidebar(!diffSidebarState());
        });

        document.getElementById('reanimate-button').addEventListener('click', function () {
            animateAll(0);
            setTimeout(function () {
                animateAll(1);
            }, 1000);
        });

        document.getElementById('highlight-changes-button').addEventListener('click', function () {
            // Erase existing highlights
            highlight([]);
            // Highlight everything
            for (let diffPair of data.diffs[currentPageIndex])
                for (let diffs of diffPair)
                    for (let diff of diffs)
                        if (diff.checked)
                            for (let anim of document.getElementsByClassName(diff.id))
                                highlight([anim.parentElement], true, false, false);
        });

        document.getElementById('changefilter').addEventListener('input', function () {
            filterChanges(this.value);
        });
        document.getElementById('changetable').addEventListener('click', function (e) {
            changeTableClicked(e);
        });
    }

    //hide all diff expandables
    for (let elem of document.querySelectorAll('table')) {
        if (elem.id != 'changetable') {
            elem.parentNode.style.display = 'none';
        }
    }

    document.getElementById('schematic-title').textContent = schematicTitle;
    document.getElementById('schematic-version').textContent = schematicVersion;

    initHammer();
});

function initHammer() {
    let initialScale = 1;

    hammer = Hammer(svgPage, {
        inputClass: Hammer.SUPPORT_POINTER_EVENTS ? Hammer.PointerEventInput : Hammer.TouchInput
    });

    hammer.get('pinch').set({ enable: true });
    hammer.on('pinchstart pinchmove', function (ev) {
        // On pinch start remember initial zoom
        if (ev.type === 'pinchstart') {
            initialScale = currentPanZoom.getZoom();
        }
        // ev.scale accumulates, so treat it as relative to the initial scale
        currentPanZoom.zoomAtPoint(initialScale * ev.scale, { x: ev.center.x, y: ev.center.y });
    });
    // Prevent moving the page on some devices when panning over SVG
    svgPage.addEventListener('touchmove', function (e) { e.preventDefault(); });
}

function crossProbe(cmd, target) {
    // Don't cross-probe unless the button has been pressed
    if (!document.getElementById('xprobe').disabled) {
        return;
    }
    if (xprobe) {
        if (xprobe.readyState % 4) {
            xprobe.abort();
        }
        xprobe = null;
    }
    xprobe = new XMLHttpRequest();
    xprobe.onerror = xprobe.ontimeout = ()=>setTimeout(crossProbe, 1000);
    if (!cmd) {
        xprobe.open("GET", xprobeEndpoint, true);
        xprobe.onload = e => {
            // Use a nominal timeout to avoid huge CPU usage if we hit a standard webserver
            setTimeout(crossProbe, 100);
            // Try to parse the result; might throw an exception
            resp = JSON.parse(e.target.response);
            if (resp && "cmd" in resp && "targets" in resp
                    && ["PART", "NET"].includes(resp.cmd)) {
                pushHash(resp.targets);
                window.onpopstate();
            }
        };
    } else {
        xprobe.timeout = 1000;
        let url = `${xprobeEndpoint}?cmd=${escape(cmd)}&targets=${escape(target)}`;
        xprobe.open("GET", url, true);
        xprobe.onload = ()=>crossProbe();
    }
    xprobe.send();
}

function setTheme(name, target) {
    if (!(name in themes)) {
        name = "Default"
    }
    if (!target) {
        setSetting('SchematicTheme', name);
    }
    for (v in themes[name]) {
        (target || document.body).style.setProperty('--' + v, themes[name][v]);
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
    document.getElementById('copy-toast').MaterialSnackbar.showSnackbar({
        message: 'Copied ' + (indicator || ('"' + text + '"')) + ' to clipboard.'
    });
}

/** Handles restarting animations when a page header is held down.
 */
function animateAll(state) {
    for (let tag in {
        animate: 0,
        animateTransform: 0
    }) {
        let anims = svgPage.getElementsByTagName(tag);
        for (let i = 0; i < anims.length; i++) {
            let hasattr = anims[i].hasAttribute('oldDur');
            if (!state && !hasattr) {
                let olddur = anims[i].getAttribute('dur');
                if (olddur === null || olddur === 'indefinite')
                    continue;
                anims[i].setAttribute('oldDur', olddur);
                anims[i].beginElement();
                anims[i].setAttribute('dur', 'indefinite');
            } else if (state && hasattr) {
                anims[i].beginElement();
                anims[i].setAttribute('dur', anims[i].getAttribute('oldDur'));
                anims[i].removeAttribute('oldDur');
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
    page.querySelectorAll('animate,animateTransform').forEach((anim) => {
        if (anim.classList[0] !== "instance") {
            anim.setAttribute('fill', 'freeze');
            anim.beginElement();
            anim.setAttribute('dur', diffMap[anim.classList[0]].checked ? '1s' : 'indefinite');
        }
    });
}

/** Updates the back/forward history with a new target (if not redundant).
 */
function pushHash(target) {
    window.history.pushState(null, '', '#' + target);
}

function fillPageList(pages) {
    let listElem = document.getElementById('pagelist');
    for (let i = 0; i < pages.length; i++) {
        p = pages[i];
        if (!data.diffs[p.id]) {
            data.diffs[p.id] = [];
        }
        let spanElem = document.createElement('a');
        spanElem.classList.add("mdl-navigation__link");
        spanElem.classList.add("navitem");
        let prefix = "";
        for (let d = 1; d <= p.depth; d++) {
            let continues = false;
            for (let j = i+1; j < pages.length; j++) {
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
        name = name[name.length-1] || "root";
        spanElem.innerHTML = `<code>${prefix}${p.pn}&nbsp;</code>${name}`;
        spanElem.id = `${i}_link`;
        spanElem.addEventListener('click', function () {
            // manual click on another page should hide search results...
            setSearchActive(false);
            pushHash(data.pages[i].name);
            window.onpopstate();
        });
        listElem.appendChild(spanElem);
    }
    filterPages('');
}

function decodeData(data) {
    // Optimized base116 decoder assumes valid codepoints and full padding.
    // Gzip doesn't care about trailing null bytes, so no need to strip them.
    let code = '0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz!"#$%&()*+,-.:;<=>?@[]^_`{|}~ '
        + '\007\010\011\013\014\016\017\020\021\022\023\024\025\026\027\030\031\032\033\034\035\036\037\177';
    let dec = new Uint8Array(128);
    for (let i = 0; i < code.length; i++) {
        dec[code.charCodeAt(i)] = i;
    }
    let POW = [116, 116 ** 2, 116 ** 3, 116 ** 4, 116 ** 5, 116 ** 6];
    let BYTE = [2 ** 8, 2 ** 16, 2 ** 24, 2 ** 32, 2 ** 40];
    let buffer = new Uint8Array(6 * data.length / 7);
    let j = 0;
    for (let i = 0; i < data.length;) {
        let num = dec[data.charCodeAt(i++)] * POW[5]
            + dec[data.charCodeAt(i++)] * POW[4]
            + dec[data.charCodeAt(i++)] * POW[3]
            + dec[data.charCodeAt(i++)] * POW[2]
            + dec[data.charCodeAt(i++)] * POW[1]
            + dec[data.charCodeAt(i++)] * POW[0]
            + dec[data.charCodeAt(i++)];
        // Can't use bit arithmetic since Javascript will truncate to 32-bit ints
        buffer[j++] = num / BYTE[4];
        buffer[j++] = num / BYTE[3] % 256;
        buffer[j++] = num / BYTE[2] % 256;
        buffer[j++] = num / BYTE[1] % 256;
        buffer[j++] = num / BYTE[0] % 256;
        buffer[j++] = num % 256;
    }
    return pako.inflate(buffer, { to: 'string' });
}

function selectInstance(container, inst) {
    // Shows the specified instance and deletes all the rest
    Array.from(container.getElementsByClassName('instance')).forEach((anim) => {
        if (inst === undefined || anim.classList.contains(inst)) {
            anim.parentNode.removeAttribute('opacity');
            anim.outerHTML = "";
            inst = null;
        } else {
            anim.parentNode.outerHTML = "";
        }
    });
}

function injectPage(pageIndex) {
    // first thing, we need to highlight in the left bar the page that has been loaded
    let allLinks = document.getElementById('pagelist').getElementsByTagName('a');

    // empty the change filter on page change
    document.getElementById('changefilter').value = "";

    for (let l = 0; l < allLinks.length; l++) {
        if (allLinks[l].id == `${pageIndex}_link`) {
            allLinks[l].classList.add('currentnavitem');
            allLinks[l].style.fontWeight = 'bold';
            if (allLinks[l].classList.contains('conflictpagelink')) {
                allLinks[l].style.backgroundColor = "rgba(255,0,0,0.55)";
            } else {
                allLinks[l].style.backgroundColor = "black";
            }
            if (typeof allLinks[l].scrollIntoViewIfNeeded === "function") {
                allLinks[l].scrollIntoViewIfNeeded();
            }
        } else {
            allLinks[l].classList.remove('currentnavitem');
            allLinks[l].style.fontWeight = null;
            if (allLinks[l].classList.contains('conflictpagelink')) {
                allLinks[l].style.backgroundColor = "rgba(255,0,0,0.15)";
            } else {
                allLinks[l].style.backgroundColor = null;
            }
        }
    }

    // Load the library
    let svgLibrary = document.getElementById('svgLibrary');
    if (!svgLibrary.getElementsByTagName('svg').length) {
        svgLibrary.innerHTML = decodeData(pageData['library']);
    }

    let page = data.pages[pageIndex];
    let svgID = page.id;
    let instance = page.inst;
    let svg = null;
    if (currentPageIndex !== pageIndex || currentPageIndex === null) {
        currentPageIndex = pageIndex;
        // Load up the html data into a temporary div tag
        let svgData = document.createElement('div');
        svgData.innerHTML = decodeData(pageData[svgID]);
        // Configure the svg
        svg = svgData.firstElementChild;
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.id = 'activesvg';
        // Make the active instance visible and delete the rest
        selectInstance(svg, instance);
        // Upgrade any links to use openurl
        for (let a of svg.getElementsByTagName('a')) {
            a.removeAttribute("target");
            a.onclick = function() { openurl(a.href.animVal); return false; };
        }
        // Move contents of svg into a temporary svg, keeping just the top svg tag
        svgData = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svgData.append(...svg.children);
        // Replace the current page DOM with this single svg tag
        svgPage.replaceChildren(svg);
        svgPage.viewBoxOriginal = page.box;

        // Create the svgpanzoom with the shell SVG
        currentPanZoom = svgPanZoom(svg, {
            zoomScaleSensitivity: 0.2,
            dblClickZoomEnabled: false,
            onZoom: function () {
                if (tooltipFixed) {
                    tooltipFixed = false;
                    let tooltip = document.getElementById('tooltip')
                    tooltip.style.display = 'none';
                    tooltip.style.opacity = '0.8';
                }
                // we never want to emulate mousedown on ghost transition caused by zooming
                svgPage.mouseEvent = null;
            },
            onPan: function (c) {
                panCounter++;
                if (tooltipFixed) {
                    tooltipFixed = false;
                    let tooltip = document.getElementById('tooltip')
                    tooltip.style.display = 'none';
                    tooltip.style.opacity = '0.8';
                }

                let y = currentPanZoom.getPan().y;
                let panYExtents = getPanYPageExtents();

                // handle page transitions for pan-past-boundary
                if (y < panYExtents[0] - panPageHysteresis) {
                    cyclePage(1, 1, svgPage.mouseEvent, panYExtents[0] - panPageHysteresis - y);
                } else if (y > panYExtents[1] + panPageHysteresis) {
                    cyclePage(-1, -1, svgPage.mouseEvent, y - panYExtents[1] - panPageHysteresis);
                }
                // increase opacity of ghost pages as they approach the boundary
                Array.from(svgPage.getElementsByClassName('ghostafter')).forEach((g) => {
                    g.style.filter = `opacity(${Math.max(Math.exp((panYExtents[0] - y) / panPageHysteresis), 0.2)})`;
                });
                Array.from(svgPage.getElementsByClassName('ghostbefore')).forEach((g) => {
                    g.style.filter = `opacity(${Math.max(Math.exp((y - panYExtents[1]) / panPageHysteresis), 0.2)})`;
                });
            },
            customEventsHandler: {
                haltEventListeners: ['touchstart', 'touchend', 'touchmove', 'touchleave', 'touchcancel'],
                init: function () { },
                destroy: function () { }
            },
        });

        // Now that svgpanzoom is set up, add the content back in
        svg.firstElementChild.append(...svgData.children);

        // append ghost pages to the svg
        let ghostSvg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');

        // create ghost pages
        // only populate 4 ghost pages: 2 before and 2 after current page
        // from testing with default zoom min, seeing more than 2 past the current page is not likely
        let yOffset = 0;
        let pageOffset = 0;
        for (let i = Math.max(0, pageIndex - 2); i < Math.min(data.pages.length, pageIndex + 3); i++) {
            let targetSvg = ghostSvg;
            if (i == pageIndex) {
                pageOffset = yOffset;
                yOffset += page.box[3] + pageSeparation;
                continue;
            }
            yOffset += addGhostPage(ghostSvg, i, yOffset, i > pageIndex, svgPage.viewBoxOriginal[2]);
        }

        ghostSvg.setAttribute('x', svgPage.viewBoxOriginal[0]);
        ghostSvg.setAttribute('y', -pageOffset + svgPage.viewBoxOriginal[1]);
        ghostSvg.setAttribute('width', svgPage.viewBoxOriginal[2]);
        ghostSvg.setAttribute('height', (yOffset));
        ghostSvg.setAttribute('class', 'ghost');

        svg.firstElementChild.appendChild(ghostSvg);
    }

    if (getSetting('ZoomToContent') === "zoom" && page.contentbox[2] && page.contentbox[3]) {
        panToElems([page], 0.01);  // the tiniest bit of margin
    }

    if (uiMode > 1) {
        fillDiffTable();
    }
    applyAnimationColorWorkaround();
    setAnimation(svgPage);
}

function applyAnimationColorWorkaround() {
    let theme = getSetting('SchematicTheme', themeDefault);
    // workaround the fact that we cannot animate css variables.
    document.querySelectorAll('animate[fromvar]').forEach(animElem => {
        let fromVar = animElem.getAttribute('fromvar').match(/(?<=var\(--)[a-zA-Z]+(?=\))/);
        let toVar = animElem.getAttribute('tovar').match(/(?<=var\(--)[a-zA-Z]+(?=\))/);
        if (fromVar && toVar) {
            animElem.setAttribute('from', themes[theme][fromVar]);
            animElem.setAttribute('to', themes[theme][toVar]);
        }
    });
}

function addGhostPage(ghostSvg, pageIndex, yOffset, pageBelow, activeWidth) {
    let page = data.pages[pageIndex];
    let ghostRect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    let arrowChar = pageBelow ? '↓' : '↑';
    let viewBox = page.box;
    let xOffset = - (viewBox[2] - activeWidth) / 2;

    ghostRect.setAttribute('x', xOffset);
    ghostRect.setAttribute('y', yOffset);
    ghostRect.setAttribute('width', viewBox[2]);
    ghostRect.setAttribute('height', viewBox[3]);
    ghostRect.setAttribute('class', 'ghostpage');

    let ghostText = document.createElementNS('http://www.w3.org/2000/svg', 'text');

    ghostText.setAttribute('x', xOffset + viewBox[2] / 2);
    ghostText.setAttribute('y', yOffset + (pageBelow ? 300 : viewBox[3] - 300));
    ghostText.setAttribute('dominant-baseline', pageBelow ? 'hanging' : 'text-after-edge');
    ghostText.innerHTML = `${arrowChar} ${page.name} ${arrowChar}`;

    let ghostG = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    ghostG.setAttribute('class', pageBelow ? 'ghostafter' : 'ghostbefore');
    ghostG.append(ghostRect, ghostText);
    onPanlessClick(ghostG, () => {
        pushHash(data.pages[pageIndex].name);
        window.onpopstate();
    });
    ghostSvg.append(ghostG);
    return viewBox[3] + pageSeparation;
}

function onPanlessClick(elem, callback) {
    // something like onclick, but suppressed if there is any panning
    elem.addEventListener('mousedown', () => {
        elem.panCounter = panCounter;
    });
    elem.addEventListener('mouseup', () => {
        if (elem.panCounter == panCounter) {
            callback();
        }
    });
}

// called one time after page load
// also will apply red style to pagelist items with conflicts
function initializeCheckModel() {
    for (let page of data.pages) {
        let pageHasConflict = false;
        for (let diffs of data.diffs[page.id]) {
            // do the raw indexing
            for (let i of [0, 1]) {
                for (let diff of diffs[i]) {
                    diffMap[diff.id] = diff;
                    diff.parent = diffs;
                }
            }

            // do the check initialization
            if (diffs[0].length) {
                if (diffs[0][0].c) {
                    pageHasConflict = true;
                    continue;
                }
                for (let diff of diffs[0]) {
                    diff.checked = true;
                }
                for (let diff of diffs[1]) {
                    diff.checked = false;
                }
            } else {
                if (diffs[1][0].c) {
                    pageHasConflict = true;
                    continue;
                }
                for (let diff of diffs[1]) {
                    diff.checked = true;
                }
            }
        }

        if (pageHasConflict) {
            document.getElementById(page.id + '_link').classList.add('conflictpagelink');
        }
    }
}

function initializeExclusions() {
    for (let page of data.pages) {
        for (let diffPair of data.diffs[page.id]) {
            for (let i in diffPair) {
                if (diffPair[i].length && diffPair[i][0].c) {
                    for (let diff of diffPair[i]) {
                        diff.excluded = diffPair[1 - i];
                        diffMap[diff.id] = diff
                    }
                }
            }
        }
    }
}

function fillDiffTable() {
    document.getElementById('changetablebody').innerHTML = "";
    document.getElementById('diffbutton').style.display = null;

    if (!svgPage.parentNode.getElementsByTagName('table')) {
        toggleDiffSidebar(false);
        setChecksFromModel();
        return;
    } else {
        toggleDiffSidebar(true);
    }

    let diffIndex = 0;
    let tbody = '';
    for (let diffs of data.diffs[currentPageIndex]) {
        tbody += getDiffRow(diffs);
    }

    document.getElementById('changetablebody').innerHTML = tbody;

    setChecksFromModel();

    filterChanges();

    document.querySelectorAll('#changetablebody tr').forEach(tr => {
        tr.addEventListener('mouseenter', e => {
            highlightDiff(e.target.closest('tr'), false);
        });
        tr.addEventListener('dblclick', e => {
            // prevent checkmark clicks from panning and zooming to target
            if (e.target.classList.contains('diffcheck')) {
                return;
            }
            highlightDiff(e.target.closest('tr'), true);
        });
    });
    componentHandler.upgradeDom();
}

function highlightDiff(diffTR, pan) {
    let diffClass = diffTR.querySelector('.diffcheck').id;
    highlight(Array.from(svgPage.getElementsByClassName(diffClass)).map(e => e.parentNode), 1, pan, true);
}

function setChecksFromModel() {
    for (let diffPair of data.diffs[currentPageIndex]) {
        for (let diffs of diffPair) {
            for (let diff of diffs) {
                document.getElementById(diff.id).checked = diff.checked;
            }
        }
    }

    setHeaderChecks();
}

function setHeaderChecks() {
    // header checks follow the states of the filtered rows
    let checkCount = [
        [0, 0], // ours [total boxes, total checked]
        [0, 0] // theirs [total boxes, total checked]
    ];

    let rowCtr = 0;
    for (let diffPair of data.diffs[currentPageIndex]) {
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
    }

    let headerChecks = [
        document.getElementById('oursall').parentNode,
        document.getElementById('theirsall').parentNode
    ];

    for (let i in headerChecks) {
        let box = headerChecks[i];
        if (!checkCount[i][0]) {
            box.MaterialCheckbox.disable();
            box.MaterialCheckbox.uncheck();
            box.classList.add('indeterminate');
        } else if (checkCount[i][1] == 0) {
            box.MaterialCheckbox.enable();
            box.MaterialCheckbox.uncheck();
            box.classList.remove('indeterminate');
        } else if (checkCount[i][1] == checkCount[i][0]) {
            box.MaterialCheckbox.enable();
            box.MaterialCheckbox.check();
            box.classList.remove('indeterminate');
        } else {
            box.MaterialCheckbox.enable();
            box.MaterialCheckbox.uncheck();
            box.classList.add('indeterminate');
        }
    }

    setMultipleDiffChecks();
    componentHandler.upgradeDom();
}

function setMultipleDiffChecks() {
    // set uncollapsed multiple diffs to reflect their collapsed constituents
    // start by looping through all diff pairs containing multiple rows
    for (let diffPair of data.diffs[currentPageIndex].filter(dp => Math.max(dp[0].length, dp[1].length) > 1)) {
        for (let i of [0, 1]) {
            if (diffPair[i].length == 1 || (diffPair[i].length > 1 && diffPair[i][diffPair[i].length - 1].collapsed)) {
                let topCheck = document.getElementById(diffPair[i][0].id);
                let numChecked = diffPair[i].filter(d => d.checked).length;
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
}

function changeTableClicked(e) {
    if (e.target.id == 'oursall') { // ours header checkbox in clicked
        checkAll(0, e.target.checked);
    } else if (e.target.id == 'theirsall') { // theirs header checkbox in clicked
        checkAll(1, e.target.checked);
    } else if (e.target.id.startsWith('diff')) { // individual row item checkbox was clicked
        applyDiffCheckChange(getDiffById(e.target.id), e.target.checked, true);
        setHeaderChecks();
    }
    setMultipleDiffChecks();
}

function getDiffById(id) {
    // might want to optimize with an id->diff index
    for (let diffPair of data.diffs[currentPageIndex]) {
        for (let diffs of diffPair) {
            for (let diff of diffs) {
                if (diff.id == id) {
                    return diff;
                }
            }
        }
    }
    return null;
}

function checkAll(diffPairIndex, operation) {
    let rowCtr = 0;
    for (let i in data.diffs[currentPageIndex]) {
        let diffPair = data.diffs[currentPageIndex][i];
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
    }
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
        if (diff.parent[j].length > 1 && diff.parent[j][0] == diff && diff.parent[j][diff.parent[j].length - 1].collapsed) {
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
    let trs = '';
    let columnLengths = [diff[0].length, diff[1].length];
    let max = uiMode < 3 ? 1 : Math.max(columnLengths[0], columnLengths[1]);
    for (i = 0; i < max; i++) {
        let trClassList = [
            diff[0] && diff[0][0] && diff[0][0].c ? 'conflict' : '',
            i > 0 ? 'collapsedtr' : ''
        ].join(' ');

        trs += `<tr class="${trClassList}" title="double click to zoom to change">`;

        let tdClass = i == max - 1 ? '' : 'innertd';

        for (let j of [0, 1]) {
            if (diff[j][i]) {
                trs += `<td class="checkctrl ${tdClass}">
                  <input type="checkbox" class="diffcheck ${columnLengths[j] > 1 && i == 0 ? 'multiplecheck' : ''}" id="${diff[j][i].id}" />
                </td>
                <td class="${tdClass}">
                  <div class="checktext">${columnLengths[j] > 1 && i == 0 ? diff[j][i].text + '<span class="expandmultiple" onclick="expandMultiple(this, this.parentElement.parentElement.parentElement, \'' + diff[j][i].id + '\')">&nbsp;(+ ' + (max).toString() + ' more)</span>' : diff[j][i].text}</div>
                </td>`;

                // set initial collapse state to reflect class
                if (i > 0) {
                    diff[j][i].collapsed = true;
                }
            } else if (uiMode > 2) {
                trs += `<td class="${tdClass}"></td><td class="${tdClass}"></td>`;
            }
        }
        trs += '</tr>';
    }
    return trs;
}

function expandMultiple(span, tr, diffId) {
    let diff = diffMap[diffId];
    let trIndex = Array.from(tr.parentElement.children).indexOf(tr);
    // first, remove collapse style from hidden TRs
    for (let i = 1; i < Math.max(diff.parent[0].length, diff.parent[1].length); i++) {
        for (j of [0, 1]) {
            if (i in diff.parent[j]) {
                diff.parent[j][i].collapsed = false;
            }
        }
        tr.parentElement.children[trIndex + i].classList.remove('collapsedtr');
    }

    // then, replace the expand link(s) with the real diff text
    span.parentElement.innerHTML = diff.text + '&nbsp<span class="expandmultiple" onclick="collapseMultiple(this, this.parentElement.parentElement.parentElement, \'' + diff.id + '\')">(collapse)</span>';
    Array.from(tr.getElementsByClassName('multiplecheck')).forEach(c => {
        c.classList.remove('multiplecheck');
        c.classList.add('multiplecheckshown');
    });
}

function collapseMultiple(span, tr, diffId) {
    let diff = diffMap[diffId];
    let trIndex = Array.from(tr.parentElement.children).indexOf(tr);

    // first, add collapse style from hidden TRs
    let max = Math.max(diff.parent[0].length, diff.parent[1].length);
    for (let i = 1; i < max; i++) {
        for (j of [0, 1]) {
            if (i in diff.parent[j]) {
                diff.parent[j][i].collapsed = true;
            }
        }
        tr.parentElement.children[trIndex + i].classList.add('collapsedtr');
    }

    // then, replace the real diff text with the expand link
    span.parentElement.innerHTML = diff.text + '<span class="expandmultiple" onclick="expandMultiple(this, this.parentElement.parentElement.parentElement, \'' + diff.id + '\')">&nbsp;(and ' + (max).toString() + ' more)</span>';
    Array.from(tr.getElementsByClassName('multiplecheckshown')).forEach(c => {
        c.classList.remove('multiplecheckshown');
        c.classList.add('multiplecheck');
    });
}

function filterPages(filter) {
    let darken = false;
    for (let elem of document.querySelectorAll('.mdl-navigation__link')) {
        if (elem.textContent.toUpperCase().indexOf(filter.toUpperCase()) != -1) {
            elem.style.display = 'inline';
            if (darken) {
                elem.classList.add('navitemeven');
                darken = false;
            } else {
                elem.classList.remove('navitemeven');
                darken = true;
            }
        } else {
            elem.style.display = 'none';
        }
    }
}

function filterChanges(filter) {
    filteredDiffRows = new Set();
    let ufilter = filter ? filter.toUpperCase() : '';
    let rowCtr = 0;
    for (let diff of data.diffs[currentPageIndex]) {
        let diffContains = false;
        for (let i = 0; i < 2 && !diffContains; i++) {
            for (let j = 0; j < diff[i].length && !diffContains; j++) {
                diffContains = diff[i][j].text.toUpperCase().indexOf(ufilter) != -1;
            }
        }

        for (let i = 0; i < Math.max(diff[0].length, diff[1].length); i++) {
            document.getElementById('changetablebody').getElementsByTagName('tr')[rowCtr + i].style.display = diffContains ? null : 'none';
            if (!diffContains) {
                filteredDiffRows.add(rowCtr);
            }
        }
        rowCtr += Math.max(diff[0].length, diff[1].length);
    }

    setHeaderChecks();
    document.getElementById('changetablediv').scrollTop = 0;
}

function filterSearch(filter) {
    matchPage = 0;
    let matchThreshold = 2;
    let matchMatrix = [];
    for (let i = 0; i < matchThreshold; i++) {
        matchMatrix.push([]);
    }

    if (!filter) {
        populateMatches([], 0);
        return;
    }

    for (let comp in data.comps) {
        // we want to group components in the search bar only if they share
        // reference, mpn and pn
        let uniqueParts = [];
        for (let inst of data.comps[comp]) {
            if (!uniqueParts.filter(c => {
                return c.mpn == getProp(inst,'mpn',"") && c.pn == getProp(inst,'Value',"");
            }).length) {
                uniqueParts.push({
                    type: 'component',
                    pages: data.comps[comp].filter(i =>
                              getProp(i,'mpn',"") == getProp(inst,'mpn',"") &&
                              getProp(i,'Value',"") == getProp(inst,'Value',"")
                           ).map(i => getProp(i,0)),
                    mpn: getProp(inst,'mpn',""),
                    pn: getProp(inst,'Value',""),
                    indexed: inst,
                    display: comp
                });
            }
        }
        for (let part of uniqueParts) {
            let partMatchDistance = matchesComponent(filter, part);
            if (partMatchDistance[0] < matchThreshold) {
                part.substring = partMatchDistance[1];
                matchMatrix[partMatchDistance[0]].push(part);
            }
        }
    }
    for (let net in data.nets) {
        let matchDistance = matchesTerm(filter, net);
        if (matchDistance < matchThreshold) {
            matchMatrix[matchDistance].push({
                pages: data.nets[net],
                display: net,
                type: 'net'
            });
        }
    }
    for (let pin in data.pins) {
        let matchDistance = matchesTerm(filter, pin);
        if (matchDistance >= matchThreshold) {
            continue;
        }
        for (let symMatch of data.pins[pin]) {
            matchMatrix[matchDistance].push({
                pages: [symMatch[0]],
                pin: symMatch[1] + '[' + pin + ']',
                display: pin,
                type: 'pin'
            });
        }
    }
    for (let text of Object.keys(data.text)) {
        let matchDistance = matchesTerm(filter, text);
        if (matchDistance < matchThreshold) {
            matchMatrix[matchDistance].push({
                pages: data.text[text],
                text: text,
                display: text,
                type: 'text'
            });
        }
    }

    searchMatches = [].concat(...matchMatrix);
    populateMatches();
}

function validateResultPageNumber() {
    let pnInput = document.getElementById('resultpagenumber');
    let enteredPage = parseInt(pnInput.value);
    if (enteredPage > 0 && enteredPage <= Math.ceil(searchMatches.length / matchesPerPage)) {
        pnInput.style.borderBottomColor = 'grey';
        return enteredPage;
    } else {
        pnInput.style.borderBottomColor = 'red';
        return 0;
    }
}

function populateMatches() {
    //clear any old matches
    document.getElementById('matchlist').innerHTML = "";
    document.getElementById('resultpagenumber').value = matchPage + 1;
    document.getElementById('resultpagenumber').disabled = !searchMatches.length;
    validateResultPageNumber();

    searchMatchesOnPage = searchMatches.slice(matchesPerPage * matchPage, matchesPerPage * (matchPage + 1));

    // add this page's matches
    for (let match of searchMatchesOnPage) {
        document.getElementById('matchlist').innerHTML += `<div class="resultentry">
        <div style="height:auto">
          <span><span style="font-weight:bold">${match.type}</span>: <code>${match.display}</code></span>
          ${match.type == 'component' ? '<div style="font-size:0.8em"><span class="mdl-list__item-text-body" style="color:grey;height:auto">' + match.substring + '</span></div>' : ''}
          <span style="color:grey;height:auto"></span>
          <div class="resultpages">${getPages(match.pages, match.display, "yellow", true)}</div>
        </div>
      </div>`;
    }

    let matchCtr = matchesPerPage * matchPage + searchMatchesOnPage.length;

    document.getElementById('morematchescount').innerHTML = matchCtr ? `<b>${matchPage * matchesPerPage + 1}-${matchCtr}</b> of <b>${searchMatches.length}</b> results` : 'no matches found';

    document.getElementById('nextresults').disabled = matchCtr >= searchMatches.length;
    document.getElementById('previousresults').disabled = matchCtr <= matchesPerPage;
}

function cycleResultPage(delta) {
    let numPages = Math.ceil(searchMatches.length / matchesPerPage);
    matchPage = (matchPage + numPages + delta) % numPages;
    populateMatches();
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
    let pageHeight = svgPage.viewBoxOriginal[3];
    let realZoom = currentPanZoom.getSizes().realZoom;
    let centerOffset = svgPage.offsetHeight / 2;
    let viewBoxFactor = realZoom * (svgPage.viewBoxOriginal[1] + pageHeight);
    return [
        centerOffset - viewBoxFactor,
        centerOffset - viewBoxFactor + pageHeight * realZoom
    ]
}

function cyclePage(delta, retainPan, mouseEvent, leftoverPanY) {
    let initialPan = currentPanZoom.getPan();
    let initialZoom = currentPanZoom.getZoom();
    let initialRealZoom = currentPanZoom.getSizes().realZoom;
    let initialViewBox = svgPage.viewBoxOriginal;
    if (currentPageIndex + delta >= 0 && currentPageIndex + delta < data.pages.length) {
        pushHash(data.pages[currentPageIndex + delta].name);
        window.onpopstate();
        // FIXME: this code doesn't work correctly with tall pages
        // handle pan-caused page switch by immediately panning to the equivalent x,y,zoom that they came from
        let pageScaling = svgPage.viewBoxOriginal[2] / initialViewBox[2];
        // xOffset is caused by centering pages of different size
        let xOffset = (initialViewBox[2] - svgPage.viewBoxOriginal[2]) / 2;
        if (retainPan) {
            currentPanZoom.zoom(initialZoom * pageScaling);
            currentPanZoom.pan({
                x: initialPan.x + (xOffset + initialViewBox[0] - svgPage.viewBoxOriginal[0]) * initialRealZoom,
                y: getPanYFromPageRatio((retainPan + 1) / 2) + retainPan * (initialRealZoom * pageSeparation - panPageHysteresis) + leftoverPanY
            });
            // emulate a mousedown to preserve the same pan across pages
            if (mouseEvent) {
                mouseEvent.initEvent('mousedown', true, true);
                document.getElementById('activesvg').dispatchEvent(mouseEvent);
            }
        }
    }
}

function getPages(pList, ref, color, zebra) {
    let rawHTML = '';
    let pCounts = {};
    for (let p of pList) {
        pCounts[p] = (pCounts[p] || 0) + 1;
    }
    let pCounter = 1;
    for (let page in pCounts) {
        let p = parseInt(page);
        rawHTML += `<div${zebra && pCounter % 2 ? ' style="background-color:rgba(0,0,0,0.4)"' : ''}>`
                +  `<a class="itempagelink" style="color:${color}" `
                +  `href="#${data.pages[p].name},${escape(ref)}" `
                +  `onclick="clickedPageLink(this, event); return false">`
                +  `${data.pages[p].name}${pCounts[p] > 1 ? ' (' + pCounts[p] + ')' : ''}</a></div>`;
        pCounter++;
    }
    return rawHTML;
}

function clickedPageLink(elem, e) {
    // prevent enter key "clicks" from doubling this fn
    if (!e.detail) {
        e.preventDefault();
        return;
    }
    pushHash(elem.getAttribute('href').replace('#', ''));
    window.onpopstate();
    for (let e of document.getElementById("searchpane").getElementsByClassName('selectedsearch')) {
        e.classList.remove('selectedsearch');
    }
    elem.classList.add('selectedsearch');
}

function matchesComponent(term, component) {
    let bestMatch = [2];
    for (let prop in component.indexed) {
        if (!prop || prop[0] < " ") {
            continue;
        }
        let val = component.indexed[prop];
        let match = matchesTerm(term, val);
        if (match < bestMatch[0] || match == bestMatch[0] && val.length < bestMatch[2]) {
            bestMatch = [match, `${prop}: ${val}`, val.length];
            if (match == 0) {
                break;
            }
        }
    }
    return bestMatch;
}

function matchesTerm(x, y) {
    let uY = y.toUpperCase();
    let uX = x.toUpperCase();
    if (uY.indexOf(uX) != -1) {
        if (uY == uX) {
            return 0;
        }
        return 1;
    }
    return 2;
}

/** elem comes from getElem() or null
 *  target is the hovered element
 *  fix means target was clicked
 */
function displayTooltip(elem, target, fix) {
    let tooltip = document.getElementById('tooltip');
    if (!elem) {
        tooltip.style.display = 'none';
        tooltipFixed = false;
        return;
    }

    document.getElementById('tooltiptype').textContent = elem.type.substr(0, 1).toUpperCase() + elem.type.substr(1);
    document.getElementById('tooltipname').textContent = elem.name;

    // cycle instance with closest
    document.getElementById('nextinstance').onclick = function () {
        cycleInstance(true, target, elem);
    };
    document.getElementById('previnstance').onclick = function () {
        cycleInstance(false, target, elem);
    };

    document.getElementById('tooltipcontext').textContent = getTooltipContext(target);
    document.getElementById('tooltiplinks').innerHTML = getTooltipLinks(elem);

    //show properties
    if (elem.type === 'component' && getProp(elem.indexed,'Value')) {
        document.getElementById('propdiv').style.display = null;
        let html = Object.entries(elem.indexed).map(([prop, data]) => {
            let propl = prop && prop.toLowerCase();
            if (!prop || prop[0] < " " || !data || propl == "value" || propl == "reference") {
                return "";
            }
            let txt = prop + ": ";
            let is_link = /^https?:[/][/]/.test(data);
            if (is_link) {
                let href = data.replace(/["\\]/g, "");
                txt += `<a href="${href}" onclick="openurl(unescape('${escape(data)}')); return false;">`
                let split = data.split("/");
                if (split.length > 4) {
                    data = split.slice(0,3).concat(["...",split[split.length-1]]).join("/");
                }
            }
            txt += escapeHTML(data);
            if (is_link) {
                txt += "</a>"
            }
            return txt;
        }).filter(s=>s).join("<br>");
        document.getElementById('propdiv').innerHTML = html;
    } else {
        document.getElementById('propdiv').style.display = 'none';
    }

    tooltip.style.display = 'inline';
    if (fix) {
        tooltip.style.opacity = 1
    } else {
        tooltip.style.opacity = 0.8;
    }
    tooltipFixed = fix;
}

function getTooltipContext(elem) {
    let rawText = '';
    if (elem.tagName === 'text' && elem.getAttribute('prop')) {
        if (!elem.children.length) {
            rawText = elem.getAttribute('prop') + ': ' + elem.textContent;
        } else {
            rawText = elem.getAttribute('prop') + ': ' + elem.children[0].textContent
                + " \u2192 " + elem.children[1].textContent;
            if (elem.children.length >= 3) {
                rawText += ' || ' + elem.children[2].textContent;
            }
        }
    } else {
        rawText = 'Part symbol';
        let props = getElem(elem).indexed;
        let value = getProp(props, "value");
        if (value) {
            rawText += `: ${value}`
        }
    }
    return rawText;
}

function getTooltipLinks(elem) {
    if (elem.type === 'net') {
        return getPages(data.nets[elem.name], elem.name, "blue", false);
    } else if (elem.type === 'component') {
        return getPages(data.comps[elem.name].map(e => getProp(e,0)), elem.name, "blue", false);
    } else {
        return [];
    }
}

/* Takes in a random target/elem and returns the high-level text or use element
 * that represents the object, which might be the same thing.
 */
function getElem(elem) {
    if (!elem) {
        return [null, null];
    }
    // exit the shadow dom if necessary
    // this is used to attach the right <use> to <symbol> elems
    elem = elem.getRootNode().host || elem;
    // provide an explicit order for iterating through ELEM_TYPE_SELECTORS
    // because we want to find [prop="HDL_POWER"] labels inside of [p] symbols
    // and there is no good css selector (that I know) that can differentiate
    // [p] results by their child content...
    for (let typ of ['net', 'component', 'ghost']) {
        for (let s of ELEM_TYPE_SELECTORS[typ]) {
            let closest = elem.closest(s);
            if (closest) {
                let elemName = getElemName(closest, typ);
                return {
                    closest: closest,
                    type: typ,
                    name: elemName,
                    indexed: getIndexedElem(closest, elemName, typ)
                }
            }
        }
    }
    return {
        closest: elem,
        type: 'note',
        name: 'NOTE',
        indexed: null
    };
}

function getIndexedElem(closest, name, typ) {
    switch (typ) {
        case 'component':
            let path = closest.getAttribute('p');
            if (path && name) {
                return data.comps[name].find(i => getProp(i,1) == path);
            }
            return null;
        case 'net':
            return data.nets[name];
        default:
            return null;
    }
}

function getElemName(closest, typ) {
    switch (typ) {
        case 'component':
            return getLocation(currentPageIndex, closest.getAttribute('p'));
        case 'net':
            return closest.childNodes[closest.childNodes.length - 1].textContent;
        case 'ghost':
            return 'GHOST';
        default:
            return 'NOTE';
    }
}

/** Returns location for a given path (assuming on currentPageIndex) */
function getLocation(pageIndex, path) {
    // create a page,path -> refdes index if not already created
    if (!data.compsByPath) {
        data.compsByPath = {};
        for (let loc in data.comps) {
            for (let inst of data.comps[loc]) {
                let pg = getProp(inst,0);
                data.compsByPath[pg] || (data.compsByPath[pg] = {});
                data.compsByPath[pg][getProp(inst,1)] = loc;
            }
        }
    }
    if (data.compsByPath[pageIndex] && data.compsByPath[pageIndex][path]) {
        return data.compsByPath[pageIndex][path];
    } else {
        return null;
    }
}

/** Launches a URL in a new window.
 * If we're in an isolated browser, request the python server to launch the URL
 * in a proper browser. Otherwise, just open a new window.
 */
function openurl(url) {
    if (!url || url.startsWith("#")) {
        pushHash(url.substr(1));
        window.onpopstate();
    }
    else if (document.location.hostname !== 'localhost' || document.location.pathname !== '/') {
        window.open(url, '_blank');
    } else {
        fetch("./openurl", {
            method: "POST",
            body: JSON.stringify({ url: url })
        }).then(res => {
            if (res.status >= 300) {
                window.open(url, '_blank');
            }
        }).catch(error => {
            window.open(url, '_blank');
        });
    }
}

/** Navigates to the referenced target when back/forward are hit.
 */
window.onpopstate = function (evt) {
    currentPageIndex = null;
    let target = decodeURI(window.location.hash.replace('#', '')).toUpperCase();
    let pageName = '';
    if (target.indexOf(',') != -1) {
        pageName = target.split(',')[0];
        target = target.split(',')[1];
    } else {
        let comp = data.comps[target.split('.')[0].toUpperCase()] || '';
        if (comp) {
            pageName = getProp(comp[0],0);
        }
    }

    let pageIndex = getPageIndex(target);
    if (pageIndex == -1) {
        pageIndex = getPageIndex(pageName);
    }
    if (pageIndex != -1) {
        injectPage(pageIndex);
    }

    displayTooltip(false);

    if (!target && pageIndex == -1) {
        window.location.hash = '#' + data.pages[0].name;
        return;
    }

    // Match components
    let instancesMatched = [];
    if (data.comps[target]) {
        let pathsFromRefdes = data.comps[target].filter(i => getProp(i,0) == pageIndex).map(i => getProp(i,1));
        if (pathsFromRefdes.length) {
            instancesMatched = svgPage.querySelectorAll(pathsFromRefdes.map(p => `[p="${p}"]`).join(', '));
        }
        if (instancesMatched.length) {
            highlight(Array.from(instancesMatched), true, true, true);
            return;
        }
    }

    // Match nets
    let netsMatched = [];
    for (let prop of svgPage.getElementsByTagName('text')) {
        let elem = getElem(prop);
        if (elem.type === 'net' && elem.name === target) {
            // Highlight the surrounding group (net name + line)
            let grp = prop;
            while (grp && grp.tagName !== 'g') {
                grp = grp.parentElement;
            }
            netsMatched.push(grp);
        }
    }
    if (netsMatched.length) {
        highlight(netsMatched, true, true, true);
        return;
    }

    let pinsMatched = [];

    for (let txt of getSymbolTexts()) {
        if (matchesTerm(txt[0].textContent, target) === 0) {
            pinsMatched.push(txt);
        }
    }
    if (pinsMatched.length) {
        // highlight pins, but pan to their symbols
        highlight(pinsMatched.map(p => p[0]), true, false, true);
        highlight(pinsMatched.map(p => p[1]), false, true, false);
        return;
    }

    let genericMatched = [];

    // newlines get messed up in svg. single newlines are deleted, and
    // empty lines are replaced with spaces
    // also, inline formatting (_{}, ^{}, ~{}) gets removed
    // also, everything that svg.encode() does needs to be replicated
    const re = /[_^~]\{((?:[^{}]|\{[^}]*\})*)\}/;
    target = unescape(target)
             .replace(re, (_,x) => x.replace(re, "$1"))
             .replace(/\{slash\}/g, "/")
             .split("\n").map(x => x || " ")
             .join("");

    // filter net names. We want to search component props and notes...
    // exclude text matches from ghost pages
    for (let prop of Array.from(svgPage.getElementsByTagName('text')).filter(p =>
        ['net', 'ghost'].indexOf(getElem(p).type) == -1)
    ) {
        if (matchesTerm(prop.textContent, target) === 0) {
            // Highlight the prop
            genericMatched.push(prop);
        }
    }
    if (genericMatched.length) {
        highlight(genericMatched, true, true, true);
        return;
    }

    // No matches of any kind were found. Default to first page.
    if (currentPageIndex === null) {
        window.location.hash = '#' + data.pages[0].name;
    }
};

function getSymbolTexts() {
    let symbolTexts = [];
    for (let elem of svgPage.getElementsByTagName('use')) {
        for (let txt of document.getElementById(elem.href.baseVal.replace('#', '')).getElementsByTagName('text')) {
            symbolTexts.push([txt, elem]);
        }
    }
    return symbolTexts;
}

function getProp(props, key, def) {
    if (!props) {
        return def;
    }
    if (typeof (key) === 'number') {
        key = String.fromCharCode(key);
    }
    if (key in props) {
        return props[key];
    }
    key = Object.keys(props).find(k => k.toLowerCase() === key.toLowerCase());
    if (key) {
        return props[key];
    }
    return def;
}

function highlight(elems, state, scroll, unhighlightOthers) {
    if (unhighlightOthers) {
        Array.from(document.getElementsByClassName('highlight')).forEach((highlighted) => {
            highlighted.classList.remove('highlight');
        });
    }

    for (let elem of elems) {
        // actually change the highlight class
        if (state) {
            // Lines tend to have zero width or height and may result in a zero-area
            // highlight effect, making the element disappear. Detect this and add an
            // invisible 1x1 rect to ensure the effect does not get culled.
            let bbox = elem.getBBox();
            if (!bbox.width || !bbox.height) {
                let rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
                rect.setAttributeNS(null, 'x', bbox.x);
                rect.setAttributeNS(null, 'y', bbox.y);
                rect.setAttributeNS(null, 'width', 1);
                rect.setAttributeNS(null, 'height', 1);
                rect.setAttributeNS(null, 'stroke', 'none');
                rect.setAttributeNS(null, 'fill', 'none');
                elem.appendChild(rect);
            }
            elem.classList.add('highlight');
        } else {
            elem.classList.remove('highlight');
        }

    }

    if (scroll) {
        panToElems(elems);
    }
};

function getBounds(elems) {
    let clientRects = elems.map(e => {
        if (e.contentbox) {
            // back-calculate the page coordinates of the content box.
            // the box expands to fit the window so just use the center and zoom
            let center = getCenter(svgPage.getBoundingClientRect());
            let realZoom = currentPanZoom.getSizes().realZoom;
            return {
                  left: center.x + (e.contentbox[0] - e.box[0] - e.box[2]/2) * realZoom,
                   top: center.y + (e.contentbox[1] - e.box[1] - e.box[3]/2) * realZoom,
                 right: center.x + (e.contentbox[0] + e.contentbox[2] - e.box[0] - e.box[2]/2) * realZoom,
                bottom: center.y + (e.contentbox[1] + e.contentbox[3] - e.box[1] - e.box[3]/2) * realZoom,
            };
        } else {
            return e.getBoundingClientRect();
        }
    });
    let bounds = {
        left: Math.min(...clientRects.map(r => r.left)),
        right: Math.max(...clientRects.map(r => r.right)),
        top: Math.min(...clientRects.map(r => r.top)),
        bottom: Math.max(...clientRects.map(r => r.bottom))
    };

    if (bounds.right - bounds.left > 0 && bounds.bottom - bounds.top > 0) {
        return bounds;
    } else {
        let parentElems = elems.map(e => e.parentNode).filter(e => e);
        if (!parentElems.length) {
            return null;
        }
        return getBounds(parentElems);
    }
}

function panToElems(targetElems, padding) {
    let elemBounds = getBounds(targetElems);
    padding = padding === undefined ? 0.8 : 1 - padding;

    if (!elemBounds) {
        zoomFit();
        return;
    }

    panToCenter(getCenter(elemBounds));

    // calculate svg viewport width offset based on open sidebars
    let widthOffset = 0;
    if (searchIsActive()) {
        widthOffset = -document.getElementById('searchpane').offsetWidth;
    } else if (diffSidebarState()) {
        widthOffset = -document.getElementById('animationtoolbox').offsetWidth;
    }

    // zoom to a level that at least captures the bounds (0.8 sets 10% padding for zoom)
    currentPanZoom.zoom(Math.min(5, ...[
        padding * (currentPanZoom.getSizes().width + widthOffset) / (elemBounds.right - elemBounds.left),
        padding * currentPanZoom.getSizes().height / (elemBounds.bottom - elemBounds.top)
    ]));

    // pan left to center the target if a sidebar is open
    if (widthOffset) {
        currentPanZoom.panBy({
            x: widthOffset / 2,
            y: 0
        });
    }
}

function panToCenter(targetCenter) {
    let svgCenter = getCenter(svgPage.getBoundingClientRect());
    currentPanZoom.panBy({
        x: svgCenter.x - targetCenter.x,
        y: svgCenter.y - targetCenter.y
    });
}

function getCenter(bbox) {
    return {
        x: (bbox.left + bbox.right) / 2,
        y: (bbox.top + bbox.bottom) / 2
    }
}

function getPageIndex(pageName) {
    // handle the case where this is called with an index
    if (typeof (pageName) === 'number') {
        return pageName;
    }
    // handle the case where this is a page number
    if (/^\d+$/.test(pageName)) {
        let pn = parseInt(pageName);
        let index = data.pages.findIndex(p => p.pn == pn);
        if (index !== -1) {
            return index;
        }
    }
    // Search for the pageName
    return data.pages.map(p => p.name.toLowerCase()).indexOf(pageName.toLowerCase());
}

function genpdf() {
    let win = window.open("", "printwin", "height=600, width=800");
    win.document.write("<html><head><title>Preparing to print " + schematicTitle + " </title></head><body>");
    win.document.write("<style type=\"text/css\" media=\"print\">@page { size: 17in 11in; }</style>");
    win.document.write("<style type=\"text/css\" media=\"print\">svg { page-break-after: always; }</style>");
    win.document.write("<style type=\"text/css\" media=\"screen\">svg { display: none }</style>");

    win.onload = function () { win.print(); win.close(); };
    win.onbeforeprint = function () { win.document.title = schematicTitle; };

    // Copy in fonts
    document.fonts.forEach(f => win.document.fonts.add(f));

    // Black-on-white theme
    setTheme(themeBW, win.document.body);

    win.document.write(decodeData(pageData['library']));
    win.document.querySelectorAll("svg")[0].style.display = 'none';

    for (let p of [...data.pages].sort((a, b) => a.pn - b.pn)) {
        win.document.write(decodeData(pageData[p.id]));
        selectInstance(win.document, p.inst);
    }

    win.document.write("</body></html>");
    win.document.close();
}

// Find the next instance of elem and go to it
// bool direction; True if cycling forwards, false if backwards.
function cycleInstance(direction, elem, closest) {
    let pageList = [];

    let matches = getMatches(closest);
    let current_elem_index = matches.indexOf(elem);
    let target = elem;
    let is_group = false;

    if (closest.type === 'net') {
        pageList = data.nets[closest.name];
    } else if (closest.type === 'component') {
        pageList = data.comps[closest.name].map(i => getProp(i,0));
        is_group = true;
    } else {
        return;
    }
    if (direction) {
        //forward click
        if (current_elem_index != matches.length - 1) {
            //there is a next instance on this page
            target = matches[current_elem_index + 1];
        } else if (pageList.length == 1) {
            // Only one page with this target and no next instance so cycle
            // back to first instance on page.
            target = matches[0];
        }
    } else {
        // Backwards click
        if (current_elem_index == -1) {
            target = elem;
        } else if (current_elem_index != 0) {
            // there is a previous instance on this page
            target = matches[current_elem_index - 1];
        } else if (pageList.length == 1) {
            // Only one page with this target and no prev instance so cycle
            // back to last instance on page.
            target = matches[matches.length - 1];
        }
    }

    if (elem.tagName === "text" && (!target.isSameNode(elem) || pageList.length == 1)) {
        // Net targets do not have ids that can be referenced add one temporarily to follow link
        target.id = closest.name;
    }

    let target_href = data.pages[currentPageIndex].name + "," + target.id;

    if ((is_group || target.isSameNode(elem)) && pageList.length > 1) {
        // Need to navigate to a different page
        let cycleIndex = 0;
        for (let i = 0; i < pageList.length; ++i) {
            if (pageList[i] == currentPageIndex) {
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
        matches = getMatches(closest);
        // Going forward to next page should select first instance on page, similarly going backwards
        // to prev page should select last instance.
        if (direction) {
            target = matches[0];
        } else {
            target = matches[matches.length - 1];
        }
        if (target.tagName === "text") {
            // Net targets do not have ids that can be referenced add one temporarily to follow link
            target.id = closest.name;
        }
        target_href = data.pages[pageIndex].name + "," + target.id;
    }
    highlight(is_group ? matches : [target], true, true, true);
    pushHash(target_href);
    if (target.tagName === "text") {
        // Remove temporary id
        target.removeAttribute("id");
    }
    displayTooltip(getElem(target), target, true);
}

function getMatches(elem) {
    // Return list of all duplicate instances of elem on current page
    if (elem.type === "net" || elem.type === "component") {
        return Array.from(svgPage.querySelectorAll(
            ELEM_TYPE_SELECTORS[elem.type].join(', '))).filter(e => {
                return getElem(e).name == elem.name;
            });
    }
    return [];
}

function zoomIn() {
    currentPanZoom.zoomIn();
}

function zoomFit() {
    currentPanZoom.resize();
    currentPanZoom.fit();
    currentPanZoom.center();
    currentPanZoom.zoom(1);
}

function zoomOut() {
    currentPanZoom.zoomOut();
}

function searchIsActive() {
    return document.getElementById('searchpane').style.display != 'none';
}

function setSearchActive(state, select) {
    if (state) {
        document.getElementById('searchpane').style.display = 'inline';
        document.getElementById('expandsearchbutton').style.backgroundColor = 'lightgrey';
        document.getElementById('search-expandable').focus();
        toggleDiffSidebar(false);
        if (select) {
            document.getElementById('search-expandable').select();
        }
    } else {
        document.getElementById('searchpane').style.display = 'none';
        document.getElementById('expandsearchbutton').style.backgroundColor = null;
        document.getElementById('search-expandable').blur();
    }
}

function getResultLinks() {
    return Array.prototype.slice.call(document.getElementById("searchpane").getElementsByClassName('itempagelink'));
}

function toggleDialog(dialog, state) {
    // allow toggle-off without knowing which dialog
    if (state) {
        dialog.parentNode.style.display = 'flex';
    } else {
        document.querySelectorAll('.dialogwrapper').forEach(wrapper => {
            wrapper.style.display = 'none';
        });
    }
    document.getElementById('background').style.transition = 'filter 0.25s';
    document.getElementById('background').style.filter = state ? 'blur(4px)' : null;
}

function toggleDiffSidebar(state) {
    document.getElementById('animationtoolbox').style.display = state ? 'inline' : 'none';
    document.getElementById('diffbutton').style.backgroundColor = state ? 'lightgrey' : null;
    if (state) {
        setSearchActive(false);
    }
}

function diffSidebarState() {
    return document.getElementById('animationtoolbox').style.display != 'none';
}

function escapeHTML(unsafe)
{
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
}
