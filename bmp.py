# Copyright 2024 Google LLC

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     https://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

#!/usr/bin/env python3
import io
import itertools
import sys

import png


def to_png(f):
  """Returns a Bytes() object of a PNG file"""
  if isinstance(f, str):
    f = open(f, 'rb')
  if isinstance(f, io.IOBase):
    f = f.read()

  # Bitmap file header
  if f[0:2] != b'BM':
    raise Exception('not a BMP file')
  data_offset = int.from_bytes(f[10:14], byteorder='little')
  data = f[data_offset:]

  # Bitmap core header
  header_size = int.from_bytes(f[14:18], byteorder='little')
  w = int.from_bytes(f[18:22], byteorder='little')
  h = int.from_bytes(f[22:26], byteorder='little')
  if int.from_bytes(f[26:28], byteorder='little') != 1:
    raise Exception('unsupported color plane count')
  bpp = int.from_bytes(f[28:30], byteorder='little')
  if bpp not in (1, 2, 4, 8, 16, 24, 32):
    raise Exception('unsupported bit depth')

  # Extended Windows header
  bitmask = None
  num_colors = 2**bpp
  if header_size >= 40:
    compression = int.from_bytes(f[30:34], byteorder='little')
    if compression not in (0, 3):
      raise Exception('compression (%d) not supported' % compression)
    num_colors = min(int.from_bytes(f[46:50], byteorder='little'),
                     num_colors) or num_colors
    if compression == 3:
      if bpp not in (16, 32):
        raise Exception('invalid bit depth (%d) for bitmask' % bpp)
      if header_size < 52 and header_size != 40:
        raise Exception('bad header size (%d) for bitmask' % header_size)
      bitmask = tuple(int.from_bytes(f[i:i+4], byteorder='little')
          for i in range(54, 54+4*(3 if header_size < 56 else 4), 4))
      if len(bitmask) == 4 and not bitmask[3]:
        bitmask = bitmask[:3]

  # Color table
  color_table = None
  if bpp <= 8:
    color_table = tuple(f[x+2:x-1:-1] for x in
        range(14+header_size, 14+header_size+num_colors*4, 4))
  elif bpp == 32:
    # With compression==0, the alpha component is ignored.
    # Create an alpha table for quick assignment
    color_table = (0xFF,)*w

  # Row data extraction
  def getrows():
    step = (w*bpp+7)//8
    padding = (4-(step%4)) % 4
    for y in range(h-1, -1, -1):
      start = y*(step+padding)
      if bpp == 1:
        # Pixels are stored with the leftmost in the most significant bit
        row = list(itertools.chain.from_iterable(
          map(lambda a: color_table[a>>7] + color_table[(a>>6) & 0x1]
                      + color_table[(a>>5) & 0x1] + color_table[(a>>4) & 0x1]
                      + color_table[(a>>3) & 0x1] + color_table[(a>>2) & 0x1]
                      + color_table[(a>>1) & 0x1] + color_table[a & 0x1],
            data[start:start+w//8])))
        for remainder in range(w % 8):
          row += color_table[(data[start+w//8] >> (7-remainder)) & 0x1]
        yield row
      elif bpp == 2:
        row = list(itertools.chain.from_iterable(
          map(lambda a: color_table[a >> 6] + color_table[(a >> 4) & 0x3]
                      + color_table[(a >> 2) & 0x3] + color_table[a & 0x3],
            data[start:start+w//4])))
        for remainder in range(w % 4):
          row += color_table[(data[start+w//4] >> (6-2*remainder)) & 0x3]
        yield row
      elif bpp == 4:
        row = list(itertools.chain.from_iterable(
          map(lambda a: color_table[a >> 4] + color_table[a & 0xF],
            data[start:start+w//2])))
        if w % 2:
          row += color_table[data[start+w//2] >> 4]
        yield row
      elif bpp == 8:
        yield list(itertools.chain.from_iterable(
          map(lambda a: color_table[a], data[start:start+w])))
      elif bpp == 16 and not bitmask:
        # Somewhat optimized conversion from {1'bx, 5'bB, 5'bG, 5'bR},
        # combined with scaling up to 8-bit (since pypng's scaling is slow)
        yield list(itertools.chain.from_iterable(map(lambda a,b: (
              (b & 0b1111100) * 255 // 0b1111100,
              (((b << 8) | a) & 0b1111100000) * 255 // 0b1111100000,
              (a & 0b11111) * 255 // 0b11111
            ), data[start:start+w*2:2], data[start+1:start+w*2:2])))
      elif bpp in (24, 32) and not bitmask:
        # B G R [A]
        row = list(data[start:start+w*bpp//8])
        row[0::bpp//8] = row[2::bpp//8]
        row[2::bpp//8] = data[start:start+w*bpp//8:bpp//8]
        if bpp == 32:
          row[3::bpp//8] = color_table
        yield row
      elif bitmask:
        # Combine bitmask with scaling up to 8-bit
        yield list(itertools.chain.from_iterable(map(lambda offs:
          ((int.from_bytes(data[offs:offs+(bpp//8)], byteorder='little')
            & mask) * 255 // mask for mask in bitmask)
          , range(start, start+w*(bpp//8), (bpp//8)))))

  # Convert
  has_alpha = bpp==32 if not bitmask else len(bitmask) == 4
  output = png.Writer(width=w, height=h, alpha=has_alpha, bitdepth=8,
      greyscale=False, compression=9)
  with io.BytesIO() as f:
    output.write(f, getrows())
    return f.getvalue()


def main(argv):
  infile = sys.stdin.buffer
  outfile = sys.stdout.buffer
  if len(sys.argv) == 2:
    infile = sys.argv[1]
    outfile = open(sys.argv[1].rpartition('.')[0] + '.png', 'wb')
  elif len(sys.argv) >= 3:
    infile = sys.argv[1]
    outfile = open(sys.argv[2], 'wb')
  outfile.write(to_png(infile))

if __name__ == '__main__':
  sys.exit(main(sys.argv))