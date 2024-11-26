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
import zlib

MAGIC = b"\x89PNG\r\n\x1A\n"


def getsize(d):
  # Check header
  if not d or len(d) < 24:
    return None
  if d[ 0: 8] != MAGIC:
    return None
  if d[12:16] != b"IHDR":
    return None
  w = int.from_bytes(d[16:20], "big", signed=False)
  h = int.from_bytes(d[20:24], "big", signed=False)
  return (w, h)


def encode(rows, width, height, has_alpha, bitdepth):
  def block(typ, data):
    crc = zlib.crc32(data, zlib.crc32(typ))
    return [
        int.to_bytes(len(data), 4, "big", signed=False),
        typ,
        data,
        int.to_bytes(crc, 4, "big", signed=False),
        ]

  png = [MAGIC]
  # IHDR
  IHDR  = int.to_bytes(width, 4, "big", signed=False)
  IHDR += int.to_bytes(height, 4, "big", signed=False)
  IHDR += int.to_bytes(bitdepth, 1, "big", signed=False)
  IHDR += b'\x06' if has_alpha else b'\x02'  # colortype
  IHDR += b'\x00'*3
  png += block(b"IHDR", IHDR)
  # IDAT
  raw = b"\0" + b"\0".join(map(bytes, rows))
  compressed = zlib.compress(raw, level=9)
  png += block(b"IDAT", compressed)
  # IEND
  png += block(b"IEND", b"")
  return b''.join(png)


def main(argv):
  data = open(argv[0] if argv else sys.stdin, "rb").read()
  sz = getsize(data)
  if sz is None:
    return 1
  print("Size:", sz)
  return 0

if __name__ == '__main__':
  sys.exit(main(sys.argv[1:]))
