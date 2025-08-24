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


def getsize_mm(d):
  # Check header
  if not d or len(d) < 24:
    return None
  if d[0:4] not in (b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1"):
    return None
  if d[6:11] not in (b"JFIF\x00", b"Exif\x00"):
    return None
  w = h = None
  mm_per_x = mm_per_y = 25.4 / 300
  pos = 2
  while pos < len(d):
    pos += 2 + int.from_bytes(d[pos + 2 : pos + 4], "big")
    if d[pos : pos + 2] in (b"\xff\xc0", b"\xff\xc1", b"\xff\xc2", b"\xff\xc3"):
      w = int.from_bytes(d[pos + 5 : pos + 7], "big")
      h = int.from_bytes(d[pos + 7 : pos + 9], "big")
    elif (
      d[pos : pos + 2] == b"\xff\xe0" and d[pos + 4 : pos + 9] == b"JFIF\x00"
    ):
      if d[pos + 11] == 1:
        factor = 25.4
      elif d[pos + 11] == 2:
        factor = 10
      else:
        continue
      mm_per_x = factor / int.from_bytes(d[pos + 12 : pos + 14], "big")
      mm_per_y = factor / int.from_bytes(d[pos + 14 : pos + 16], "big")
    elif (
      d[pos : pos + 2] == b"\xff\xe1" and d[pos + 4 : pos + 9] == b"Exif\x00"
    ):
      # TODO: it's complicated to extract the density from exif...
      pass
  return (w * mm_per_x, h * mm_per_y)


def main(argv):
  data = (open(argv[1], "rb") if len(argv) > 1 else sys.stdin.buffer).read()
  sz = getsize_mm(data)
  if sz is None:
    return 1
  print("Size:", sz)
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
