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
import zlib

MAGIC = b"\x89PNG\r\n\x1a\n"


def getsize_mm(d):
  # Check header
  if not d or len(d) < 24:
    return None
  if d[0:8] != MAGIC:
    return None
  offset = 8
  w = h = None
  mm_per_x = mm_per_y = 25.4 / 300
  while offset < len(d):
    hdrlen = int.from_bytes(d[offset : offset + 4], "big")
    hdrtyp = d[offset + 4 : offset + 8]
    if hdrtyp == b"IHDR":
      w = int.from_bytes(d[offset + 8 : offset + 12], "big")
      h = int.from_bytes(d[offset + 12 : offset + 16], "big")
    elif hdrtyp == b"pHYs":
      if d[offset + 16] == 1:  # meter
        mm_per_x = 1000 / int.from_bytes(d[offset + 8 : offset + 12], "big")
        mm_per_y = 1000 / int.from_bytes(d[offset + 12 : offset + 16], "big")
    offset += hdrlen + 12
  return (w * mm_per_x, h * mm_per_y)


def encode(rows, width, height, has_alpha, bitdepth):
  def block(typ, data):
    crc = zlib.crc32(data, zlib.crc32(typ))
    return [
      int.to_bytes(len(data), 4, "big"),
      typ,
      data,
      int.to_bytes(crc, 4, "big"),
    ]

  png = [MAGIC]
  # IHDR
  ihdr = int.to_bytes(width, 4, "big")
  ihdr += int.to_bytes(height, 4, "big")
  ihdr += int.to_bytes(bitdepth, 1, "big")
  ihdr += b"\x06" if has_alpha else b"\x02"  # colortype
  ihdr += b"\x00" * 3
  png += block(b"IHDR", ihdr)
  # IDAT
  raw = b"\0" + b"\0".join(map(bytes, rows))
  compressed = zlib.compress(raw, level=9)
  png += block(b"IDAT", compressed)
  # IEND
  png += block(b"IEND", b"")
  return b"".join(png)


def main(argv):
  data = (open(argv[1], "rb") if len(argv) > 1 else sys.stdin).read()
  sz = getsize_mm(data)
  if sz is None:
    return 1
  print("Size:", sz)
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
