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
import itertools
import re
import sys
from difflib import SequenceMatcher
import math
import time

from .diff import Comparable, Diff, Param, difflists, applylists, targetdict
from . import svg

"""
This file contains function definitions for writing a fileparser.
This guides a CAD-Tool specific parser.
"""


class pgFile(Comparable):
  """Tracks a page#.pgFile file
  """

  class FileError(Exception):
    """An exception that contains information about a file."""
    def __init__(self, f, text, is_conflict=False):
      """Generates an exception.
      f    -- file to reference in the exception text
      text -- a description of what went wrong
      is_conflict -- if True, flags that the commit is due to a git conflict
                     that was pushed as-is into the repo
      """
      Exception.__init__(self, '%s: %s' % (f.name, text))
      self.is_conflict = is_conflict

  class Coord(object):
    """A coordinate in a page file."""
    def __init__(self, s, relpos=None):
      """Parses a string to extract the coordinates.
      relpos -- Tuple to make the coordinate relative to
      """
      self.x, self.y = map(int, s.strip('()').split(' '))
      self._isrel = relpos is not None
      if relpos:
        self.x -= relpos[0]
        self.y -= relpos[1]
    def sortkey(self):
      """Sort key that tries to be more stable between changes."""
      # Add the two coordinates together in an attempt to get a sort key that's
      # less affected by small moves. Appends the x coordinate to disambiguate.
      return '%08d %08d' % (self.x + self.y, self.x)
    def distance(self, other, _fast):
      """Returns the distance between two points."""
      return math.hypot(self.x - other.x, self.y - other.y)
    def abs(self, relpos=None):
      if self._isrel != (relpos is not None):
        raise Exception('Coord.abs called on relative coordinate without rel')
      relpos = relpos or (0, 0)
      return (self.x + relpos[0], self.y + relpos[1])
    def hypot(self):
      return math.hypot(self.x, self.y)
    def untransform(self, spin, flip):
      cos = math.cos(math.radians(spin or 0))
      sin = math.sin(math.radians(spin or 0))
      fixed = (self.x*cos - self.y*sin, self.y*cos + self.x*sin)
      if flip:
        fixed = (-fixed[0], fixed[1])
      return fixed
    def __eq__(self, other):
      return (self._isrel == other._isrel and
              self.x == other.x and self.y == other.y)
    def __ne__(self, other):
      return not self.__eq__(other)
    def __str__(self):
      if self._isrel:
        return '(%+d %+d)' % (self.x, self.y)
      return '(%d %d)' % (self.x, self.y)
    def repr(self, relpos=None):
      return '(%d %d)' % self.abs(relpos)

  class Drawable(Comparable):
    """Superclass for blocks that have drawable elements, such as text or dots.
    """
    # The text justifications
    JUSTIFY = ()
    # The show types
    SHOW = ()
    def __init__(self):
      self.display = {}
      self._textposcheck = None
    def load(self, f):
      # removed CAD specific code
    
    def diff(self, other, _=None):
       # removed CAD specific code
      return diff
    def apply(self, key, data):
    def _forsvg(self, diffs, param, default):
    def repr(self, parent=None):
     # removed CAD specific code
      return s
    def __eq__(self, other):
      return Comparable.__eq__(self, other) and self.display == other.display

  class HasProperties(Comparable):
    """A superclass for any block."""
    def __init__(self):
      self._deletedprops = {}
      self.props = {}
      self.metaprops = {}
    def load(self, f):
      return statement
    def diff(self, other, _=None):
      diff = Comparable.diff(self, other)
      return diff
    def child_is_deleted(self, prop):
      return prop in self._deletedprops.get(prop.name, [])
    def apply(self, key, data):
    def fillsvg(self, svg, diffs):
      # Can't use dict because the hashes won't match due to deep copy of base
    def fillsvgpins(self, svg, pinbox, rotate, flip, pincolor):
    def fillsvgvars(self, svg, _diffs):
    def getpinbounds(self, spin, flip):
    def __repr__(self):
      s = ''
      allprops = list(self.props.values()) + list(self.metaprops.values())
      allprops.sort(key=lambda p: p[0].sortkey())
      for proplist in allprops:
        for prop in proplist:
          s += prop.repr(self)
      return s
    def __eq__(self, other):
      # Metaproperties are ignorable and not considered for equality
      return Comparable.__eq__(self, other) and self.props == other.props

  class Property(Drawable):
    """Properties of wires and component are defined in blocks."""
    def __init__(self, parent, statement):
    def sortkey(self):
    def diff(self, other, _=None):
      # If both name and value are different, declare the properties disparate
      # If the pin type doesn't match, definitely different
      return diff
    def fillsvg(self, svg, diffs):
    def _expandvar(self, svg, name):
      """Applies variable expansion rules to a <match>"""
    def _gentext(self, svg, label, mode, value):
      """Returns the text content given a mode, value, label, and svg"""
    def fillsvgpins(self, svg, pinbox, spin, flip, pincolor):
    def _justify(self):
      return self.justify
    def __str__(self):
    def repr(self, parent=None):
      return s

  class Header(Comparable):
    """Parses the header of a page file.
    """
    def __init__(self, statement):
    def load(self, f):
    def diff(self, other, _=None):
      return diff
    def apply(self, key, data):
    def fillsvg(self, svg, _diffs):
    def __str__(self):
      return 'header'
    def __repr__(self):  
    def __eq__(self, other):
     
  class Chip(HasProperties):
    def __init__(self, statement):
    def load(self, f):
    def path(self):
    def setpath(self, newpath):
    def location(self):
    def prop(self, propname, ptflib=None, prefer_lib=False):
      """Returns the "propname" property/refdes, or an empty string."""
      return prop
    def sortkey(self):
    def diff(self, other, _=None):
    def fillsvg(self, svg, diffs):
    def fillsvgplaceholder(self, svg):
    def __str__(self):
    def __repr__(self):

  class Wire(HasProperties):
    """Wires are start and end coordinates with properties (such as net name)
    """
    def __init__(self, statement):
    def set_inferred_signame(self, signame=None):
      """Stores a signal name that has been inferred from connectivity"""
    def signame(self):
      """Returns the signal name property of the wire, the inferred signal name, or
      an empty string.
      """
    def sortkey(self):
    def distance(self, other, fast, _=None):
      """Returns the difference in coordinates, plus some for other changes"""
    def fillsvg(self, svg, diffs):
    def __str__(self):
    def __repr__(self):
      return s

  class Dot(Drawable):
    """Dots on schematics are pretty much just the coordinate."""
    def __init__(self, statement):
    def set_inferred_signame(self, signame=None):
      """Stores a signal name that has been inferred from connectivity"""
    def signame(self):
      """Returns the inferred signal name or an empty string."""
    def sortkey(self):
      """Returns a sort key; gets sorted together with Wire when writing"""
    def distance(self, other, fast, _=None):
    def fillsvg(self, svg, diffs):
    def __str__(self):
    def __repr__(self):

  class Note(Drawable):
    def __init__(self, statement):
    def sortkey(self):
    def diff(self, other, _=None):
    def fillsvg(self, svg, diffs):
    def _isimage(self):
    def __str__(self):
    def __repr__(self):

  class Circle(Drawable):
    def __init__(self, statement):
    def sortkey(self):
    def distance(self, other, fast, _=None):
    def fillsvg(self, svg, diffs):
    def __str__(self):
    def __repr__(self):
      
 class Footer(Comparable):
  def _normalize(self):
    """Called to normalize the class; needed when outputting or comparing."""
  def _infer_connectivity(self):
    """Attempts to infer the signal names of all wires and dots.
    Generates fake netnames for everything without one.
    """
  def __eq__(self, other):
    """Returns True if the pages are functionally the same."""
  def component(self):
    """Returns a list of component in the page."""
  def write(self, fout):
    """Writes the page out to a file."""
    self._normalize()
  def fillsvg(self, svg, diffs):
  def pageno(self, default=None):
    """Determines the true page number, within the file."""
  def page_title(self, default=None):
    """Returns the page title as defined in the file."""
def library(self):
    """Returns the library part of the path, or an empty string."""
  def filename(self):
    """Returns the filename part of the path, or an empty string."""
  def get_timestamp(self, default=None):
    """Returns a timestamp if found in the page. """
  def diff(self, other, _=None):
    """Returns a list of differences between this and another page."""
  def distance(self, other, fast, diffparam=None):
    """Help match pages, even when they've been moved."""
  def child_is_deleted(self, obj):
    # This is a signal that a child diff was applied, so mark as unnormalized
  def apply(self, _key, data):
    """Applies a diff"""
  def get_nets(self, include_power):
  def get_components(self):
  def __str__(self):
    return self._name

  @staticmethod
  def _read_statement(f):
    """Reads a single statement, which can span multiple lines.
    Statements are returned without a trailing newline nor a semicolon."""

def main(argv):
  """Reads a page#.pgFile file from stdin and writes a new one to stdout.
  Specify a parameter to set any timestamps found in the file.
  """
if __name__ == '__main__':
  sys.exit(main(sys.argv))
