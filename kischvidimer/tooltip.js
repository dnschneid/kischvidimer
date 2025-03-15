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

let fixed = false;

export function isfixed() {
  return fixed;
}

export function isvisible() {
  let tooltip = document.getElementById("tooltip");
  return tooltip.style.display !== "none";
}

export function hide(always) {
  if (always || fixed) {
    fixed = false;
    let tooltip = document.getElementById("tooltip");
    tooltip.style.display = "none";
    tooltip.style.opacity = "0.8";
  }
}

export function fix() {
  let tooltip = document.getElementById("tooltip");
  if (tooltip.style.display !== "none") {
    tooltip.style.opacity = 1;
    fixed = true;
  }
}

export function show(fix) {
  let tooltip = document.getElementById("tooltip");
  tooltip.style.display = "inline";
  if (fix) {
    tooltip.style.opacity = 1;
  } else {
    tooltip.style.opacity = 0.8;
  }
  fixed = fix;
}
