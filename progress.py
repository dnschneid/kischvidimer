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
import sys

class Progress(object):
  """Renders a simple progress bar with text."""

  # Characters for a spinner!
  SPINNER = '|/-\\'

  def __init__(self, fout):
    """Instantiates a progress bar with a file object."""
    self._max = 1
    self._val = 0
    self._text = ''
    self._width = 60
    self._fout = fout
    self._spin = 0
    self._spinDir = 1

  def incr(self, amount=1):
    """Increments the value and returns itself."""
    return self.set_val(self._val+amount)

  def set_val(self, val):
    """Sets the value and returns itself."""
    self._val = min(val, self._max)
    return self

  def set_max(self, max_):
    """Sets the max value and returns itself."""
    self._max = max(max_, 1)
    self._val = min(self._val, max_)
    return self

  def set_text(self, text):
    """Sets the text to display in the bar and returns itself."""
    self._text = text
    return self

  def set_width(self, width):
    """Sets the total width of the bar in characters and returns itself."""
    if self._width == width:
      return self
    if self._width > width:
      self.clear()
    self._width = width
    return self

  def write(self):
    """Writes the bar to the file descriptor and returns itself."""
    if self._fout is None:
      return self
    rev = '\x1b[7m'
    endrev = '\x1b[0m'
    barwidth = self._width - 1
    textwidth = barwidth
    spin = Progress.SPINNER[self._spin % len(Progress.SPINNER)]
    self._spin += self._spinDir
    inner = '%s%s' % (self._text[:textwidth], '.'*(textwidth-len(self._text)))
    endpos = barwidth * max(self._val, 0) // self._max
    text = '\r%s%s%s%s%s\b' % (rev, inner[:endpos], endrev,
        inner[endpos:], spin)
    self._fout.write(text)
    self._fout.flush()
    return self

  def clear(self, spinDir=None):
    """Writes spaces to the file descriptor to clear the progress bar.
    Returns itself.
    spinDir -- picks a new direction for the spinner (instead of flipping)
    """
    if self._fout is not None:
      self._fout.write('\r' + ' '*self._width + '\r')
    if spinDir is None:
      self._spinDir = -self._spinDir
    else:
      self._spinDir = 1 if spinDir else -1
    return self


def main(_):
  """Does a quick render test of the progress bar"""
  import time
  text = [
    'Asserting Packed Exemplars',
    'Depixelating Inner Mountain Surface Back Faces',
    'Downloading Satellite Terrain Data',
    'Initializing Robotic Click-Path AI',
    'Lecturing Errant Subsystems',
    'Partitioning City Grid Singularities',
    'Retracting Phong Shader',
    'Routing Neural Network Infanstructure',
  ]
  p = Progress(sys.stdout).set_max(53).set_width(42)
  p.write()
  for step in range(53):
    time.sleep(0.2)
    if step % 6 == 5:
      p.set_text(text.pop())
    if step == 30:
      p.clear()
    p.incr().write()
  time.sleep(1)
  p.clear()
  sys.stdout.write('DONE! THAT WAS AN AMAZING EXPERIENCE\n')
  return 0
if __name__ == '__main__':
  sys.exit(main(sys.argv))