#!/usr/bin/env python3
# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
# SPDX-FileCopyrightText: Copyright 2024 Google LLC
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.
# SPDX-License-Identifier: Apache-2.0

import sys


def getsize(d):
  # Check header
  if not d or len(d) < 24:
    return None
  if d[ 0: 4] != b"\xFF\xD8\xFF\xE0":
    return None
  if d[ 6:11] != b"JFIF\x00":
    return None
  pos = 2
  while pos < len(d):
    pos += 2+int.from_bytes(d[pos+2:pos+4], "big", signed=False)
    if d[pos:pos+2] != b"\xFF\xC0":
      continue
    w = int.from_bytes(d[pos+5:pos+7], "big", signed=False)
    h = int.from_bytes(d[pos+7:pos+9], "big", signed=False)
    return (w, h)
  return None


def main(argv):
  data = open(argv[1] if len(argv) > 1 else sys.stdin, "rb").read()
  sz = getsize(data)
  if sz is None:
    return 1
  print("Size:", sz)
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
