# SPDX-FileCopyrightText: (C) 2025 Rivos Inc.
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

"""
A mini python library for parsing s-expression files.
Based on https://gist.github.com/pib/240957

Atoms are output using an atom class rather than a single-element tuple.

Support for single quote has been removed, "float" and "int" have replaced eval,
and the code has been optimized to use regexes where possible.
"""

import gc
import re
import sys
from decimal import Decimal
from string import whitespace

from .diff import Comparable, Diff, Param, TargetDict, difflists

INT_DEC_ATOM_RE = re.compile(
  r"""
      ([+-]?[0-9]+)           # Group 0: Integer portion
      (                       # Group 1: Decimal portion
        (?:\.[0-9]+)?         # fractional
        (?:[eE][+-]?[0-9]+)?  # exponent
      )
      (?=[)\s]|$)             # but if there are more characters, it's an atom
    |
      ([^()"'\s]+)            # Group 2: Atom (when groups 0 and 1 are empty)
    """,
  re.VERBOSE,
)
LITERAL_RE = re.compile(r'"(?:[^"\\]|\\.)*"')
WHITESPACE_PLUS_PARENS = whitespace + "()"
BACKSLASH_RE = re.compile(r"\\.")


def backslash_sub(x):
  return x[0][1] if x[0][1] != "n" else "\n"


class BadStringError(Exception):
  pass


class Atom(str):
  pass


class InvalidAtomError(Exception):
  def __init__(self, s, atoms=None):
    text = f"Invalid atom: {s}"
    if atoms is not None:
      text += f": was expecting {atoms}"
    Exception.__init__(self, text)


def is_atom(s, atoms=None, recurse=True):
  if recurse and isinstance(s, (tuple, list)):
    if not s:
      return False
    return is_atom(
      s[0], atoms, recurse=True if recurse is True else recurse - 1
    )
  if not isinstance(s, Atom):
    return False
  if atoms is None:
    return True
  for a in (atoms,) if isinstance(atoms, str) else atoms:
    if s == a:
      return a
  return False


def check_atom(s, atoms=None, recurse=True):
  a = is_atom(s, atoms, recurse=recurse)
  if a:
    return a
  raise InvalidAtomError(s, atoms)


# Decorator: use like @sexp.handler('atom1', 'atom2')
def handler(*atoms):
  if not hasattr(handler, "_handlers"):
    handler._handlers = {}

  def register_me(cls):
    for atm in atoms:
      atm = Atom(atm)
      # Hacky check to make sure we properly handle overloaded atoms
      assert str(handler._handlers.get(atm, cls)) == str(cls)
      handler._handlers[atm] = cls
    return cls

  return register_me


# Decorator: use like @sexp.uses('atom1', 'atom2')
def uses(*atoms):
  if not hasattr(uses, "_uses"):
    uses._uses = {}

  def register_me(fn):
    for atm in atoms:
      atm = Atom(atm)
      uses._uses.setdefault(atm, []).append(fn)
    return fn

  return register_me


class SExp(Comparable):
  """Superclass tracking SExps"""

  # Indicates that only one of these SExps should appear in a context
  # Specifying a literal name will enforce uniqueness across LITERAL_MAP[name]
  UNIQUE = True  # True by default since there a lot of singular data classes

  # If not UNIQUE, indicates that the order must be maintained
  # FIXME: actually use this somehow
  ORDERED = False

  # Contains a mapping of friendly names to ranges of literals/atoms, INCLUSIVE.
  # Index is based on sexp; subclasses should start from 1.
  # Negative range indices are relative to the index of the first sub-sexp.
  # Subclasses should override this. Default tries to cover everything.
  # Literals/atoms not covered by any range in the map raises an error.
  LITERAL_MAP: dict[str, int | tuple[int, int]] = {
    "value": 1,
    "first": (0, 0),
    "data": (2, -1),
  }

  @classmethod
  def init(cls, data):
    if not data:
      return cls()
    if not isinstance(data[0], Atom):
      return cls(data)
    return getattr(handler, "_handlers", {}).get(data[0], cls)(data)

  def __init__(self, data=None):
    self.parent = None
    if isinstance(data, SExp):
      self._sexp = data._sexp
      self._subs = data._subs
      self._atoms = data._atoms
      return
    self._sexp = data or []
    self._subs = {}
    self._atoms = {}
    for item in self._sexp:
      if isinstance(item, SExp):
        self._subs.setdefault(item.type, []).append(item)
      elif isinstance(item, Atom):
        self._atoms[item] = self._atoms.get(item, 0) + 1
    self._has_type = self._sexp and isinstance(self._sexp[0], Atom)
    # Assert that UNIQUE is accurate
    for atm, items in self._subs.items():
      unique = items[0].UNIQUE
      if unique is True:
        assert len(items) == 1, f"duplicate {atm} entries"
      elif unique:
        index = items[0].LITERAL_MAP.get(unique, unique)
        assert isinstance(index, int), "bad UNIQUE definition"
        nunique_values = len({item._sexp[index] for item in items})
        assert nunique_values == len(items), f"duplicate {atm}[{unique}]"

  def __getitem__(self, index_or_atom):
    if isinstance(index_or_atom, int):
      return self.data[int(index_or_atom)]
    return self._subs[Atom(index_or_atom)]

  def __contains__(self, atm):
    return Atom(atm) in self._subs or Atom(atm) in self._atoms

  def __eq__(self, other):
    return self._sexp == other._sexp

  def __str__(self):
    return self.type or repr(self)

  def __repr__(self):
    return dump(self)

  def param(self, diffs, key=None, base=None):
    """Convenience function to return a param even if no diffs are available.
    If key isn't provided, uses the first key in LITERAL_MAP.
    If base isn't provided, uses LITERAL_MAP to pull the data if no diffs.
    """
    if key is None:
      key = next(k for k in self.LITERAL_MAP)
    if base is None:
      start_end = self.LITERAL_MAP[key]
      if not isinstance(start_end, tuple):
        # Don't include the type if start is 0
        i = start_end + (not start_end and self._has_type)
        if len(self._sexp) > i and not isinstance(self._sexp[i], SExp):
          base = self._sexp[i]
      else:
        start, end = start_end
        # Don't include the type if start is 0
        start += not start and self._has_type
        end = (
          min(end, len(self._sexp) - 1) if end >= 0 else len(self._sexp) + end
        )
        while end >= start and isinstance(self._sexp[end], SExp):
          end -= 1
        if end >= start:
          base = tuple(self._sexp[start : end + 1])
    return TargetDict.param(diffs, self, key, base)

  def distance(self, other, fast, diffparam):
    """Enforces uniqueness by type; should be overridden for other purposes."""
    if self.type != other.type:
      return None
    if self.UNIQUE is True:
      return 0
    if self.UNIQUE:
      index = self.LITERAL_MAP.get(self.UNIQUE, self.UNIQUE)
      this = self.comp_sexp
      that = other.comp_sexp
      if index < len(this) and index < len(that) and this[index] == that[index]:
        return 0
    # Use the Comparable implementation, which will sum up the number of
    # differences returned by diff
    return super().distance(other, fast, diffparam)

  def diff(self, other, diffparam=None):
    """Returns a list of differences the other has. Akin to (other - self).
    Returns an empty list if the two are the same.
    Returns None if the two are disparate (shouldn't be compared).
    """
    if self.type != other.type:
      return None
    diffs = []

    # See when sub-sexps start; we expect no more literals/atoms after that
    this = self.comp_sexp
    that = other.comp_sexp
    this_sexp_i = len(this)
    that_sexp_i = len(that)
    for i, item in enumerate(this):
      if isinstance(item, SExp):
        this_sexp_i = i
        break
    for i, item in enumerate(that):
      if isinstance(item, SExp):
        that_sexp_i = i
        break
    assert all(isinstance(item, SExp) for item in this[this_sexp_i:]), this
    assert all(isinstance(item, SExp) for item in that[that_sexp_i:]), that

    # Handle modified/added/removed literals/atoms, grouped by the class
    max_end = -2
    for key, start_end in self.LITERAL_MAP.items():
      is_tuple = isinstance(start_end, tuple)
      if not is_tuple:
        start_end = (start_end, start_end)
      start, end = start_end
      # Don't include the type if start is 0
      start += not start and self._has_type
      this_end = min(end, this_sexp_i - 1) if end >= 0 else this_sexp_i + end
      that_end = min(end, that_sexp_i - 1) if end >= 0 else that_sexp_i + end
      max_end = max(max_end, this_end, that_end)
      this_chunk = this[start : this_end + 1]
      that_chunk = that[start : that_end + 1]
      if this_chunk != that_chunk:
        if not is_tuple:
          this_chunk = this_chunk[0]
          that_chunk = that_chunk[0]
        diffs.append(Diff((self, SExp), key, old=this_chunk, new=that_chunk))
    # Sanity-check that we didn't miss anything (not checking for gaps)
    assert max_end + 1 >= max(this_sexp_i, that_sexp_i), "unexpected data found"

    # Handle sub-sexps, which can be reordered
    diffs += difflists(
      (self, SExp),
      key=None,
      base=this[this_sexp_i:],
      other=that[that_sexp_i:],
      data=None,
    )

    return diffs

  def apply(self, key, data):
    """Applies a single difference. apply(d) for d in diff(other) => other
    Return an error string if the patch could not be applied due to conflict.
    Return True if the diff was redundant.
    key:  the key provided during instantiation of Diff
    data: One of a few things, depending on the type of diff.
          tuple(old, None) -> removal of "old" (value or instance)
          tuple(None, new) -> addition of "new" (value or instance)
          tuple(old, new) -> change from "old" to "new" (value)
    """
    # FIXME: need to define a relative sort order on each sexp subclass, if
    # using the type name isn't sufficient
    # FIXME: ensure Coord's apply gets handled correctly
    if key in self.LITERAL_MAP or key is None:
      pass
    else:
      raise ValueError(f"unhandled key {key}")
    #  if key in self.PROPS:
    #    # Add: None == data[0] (OK)
    #    # Mod: old == data[0] (OK)
    #    # Add-Add: new == data[1] or conflict
    #    # Mod-Mod: new == data[1] or conflict
    #    # Del-Mod: conflict (None not in data)
    #    if self.__dict__[key] not in data:
    #      return key
    #    if self.__dict__[key] == data[1]:
    #      return True
    #    self.__dict__[key] = data[1]
    #  else:
    #    raise Exception("unhandled diff")
    pass

  def child_is_deleted(self, child):
    # FIXME: is this correct?
    return not any(s is child for s in self._sexp)

  def hash(self):
    return hash(
      tuple((s.hash(),) if isinstance(s, SExp) else s for s in self._sexp)
    )

  @property
  def type(self):
    return self._sexp[0] if self._has_type else None

  @property
  def data(self):
    return self._sexp[1:] if self._has_type else self._sexp

  @property
  def sexp(self):
    """sexp for the purposes of outputting to a file."""
    return self._sexp

  @property
  def comp_sexp(self):
    """sexp for the purposes of comparison."""
    return self._sexp

  @property
  def yes(self):
    return len(self._sexp) == 1 or is_atom(self._sexp[1], "yes")

  @property
  def ancestry(self):
    parent = self.parent
    while parent is not None:
      yield parent
      parent = parent.parent

  def reparent(self, new_parent):
    self.parent = new_parent
    for item in self._sexp:
      if isinstance(item, SExp):
        item.reparent(self)

  def has_yes(self, atom, diffs=None):
    # FIXME: diffs
    item = self.get(atom)
    return Param(item.yes if isinstance(item, SExp) else bool(item))

  def enum(self, *atoms, start_i=0):
    for i, entry in enumerate(self._sexp):
      if i >= start_i and is_atom(entry, atoms[0]):
        if len(atoms) == 1:
          yield ((i, entry),)
        else:
          for tuples in SExp.enum(entry, *atoms[1:]):
            yield ((i, entry),) + tuples

  def get(self, atm, default_data=None, default=None):
    atm = Atom(atm)
    if atm in self._subs:
      return self._subs[atm][0]
    if atm in self._atoms:
      return atm
    if default_data is not None:
      if isinstance(default_data, (list, tuple)):
        return SExp.init([atm] + list(default_data))
      return SExp.init([atm, default_data])
    return default

  def add(self, item, i=None):
    i = i or len(self._sexp)
    if isinstance(item, SExp):
      # Add to sub list, maintaining relative ordering
      subs = self._subs.setdefault(item.type, [])
      for j in range(len(subs) - 1, -1, -1):
        try:
          self._sexp.index(subs[j], i)
        except ValueError:
          subs.insert(j + 1, item)
          break
      else:
        subs.insert(0, item)
    elif isinstance(item, Atom):
      self._atoms[item] = self._atoms.get(item, 0) + 1
    self._sexp.insert(i, item)
    self._has_type = self._sexp and isinstance(self._sexp[0], Atom)
    if isinstance(item, SExp):
      item.reparent(self)

  def remove(self, atoms=None, func=None):
    if atoms is None and func is None:
      return
    for i in range(len(self._sexp) - 1, -1, -1):
      if (atoms is None or is_atom(self._sexp[i], atoms)) and (
        func is None or func(self._sexp[i])
      ):
        if isinstance(self._sexp[i], SExp):
          _subs = self._subs.get(self._sexp[i].type, [])
          for j in range(len(_subs) - 1, -1, -1):
            if _subs[j] is self._sexp[i]:
              del _subs[j]
        elif isinstance(self._sexp[i], Atom):
          self._atoms[self._sexp[i]] -= 1
          if not self._atoms[self._sexp[i]]:
            del self._atoms[self._sexp[i]]
        del self._sexp[i]
    self._has_type = self._sexp and isinstance(self._sexp[0], Atom)


def parse(data, parent=None):
  gc_enabled = gc.isenabled()
  gc.disable()
  stack = [[]]
  i = 0
  len_data = len(data)
  while i < len_data:
    c = data[i]
    if c in WHITESPACE_PLUS_PARENS:
      if c == "(":
        stack.append([])
      elif c == ")":
        stack[-2].append(SExp.init(stack.pop()))
      i += 1
    elif c == '"':
      literal = LITERAL_RE.match(data, i).group()
      if "\n" in literal:
        literal = literal.partition("\n")[0] + " <--should be \\n"
        raise BadStringError(
          f"unescaped newline in string literal at offset {i}: {literal}"
        )
      i += len(literal)
      stack[-1].append(BACKSLASH_RE.sub(backslash_sub, literal[1:-1]))
    else:
      a = INT_DEC_ATOM_RE.match(data, i)
      i = a.end()
      a = a.groups()
      if a[1]:
        stack[-1].append(Decimal(a[0] + a[1]))
      elif a[0]:
        stack[-1].append(int(a[0]))
      else:
        stack[-1].append(Atom(a[2]))
  if gc_enabled:
    gc.enable()
  ret = SExp.init(stack[-1])
  ret.reparent(parent)
  return ret


def dump(data):
  # Output format follows the Prettify definition in kicad:
  #   kicad/common/io/kicad/kicad_io_utils.cpp
  # Matches git bce982877c643bcdd8e6f3b2bb002d3a06c986ad
  indent_char = "\t"
  xy_special_case_column_limit = 99
  consecutive_token_wrap_threshold = 72
  in_multiline_list = False
  in_xy = False
  stack = [iter(data if isinstance(data, list) else data.sexp)]

  out = ["("]
  while stack:
    data = next(stack[-1], None)
    if data is None:
      # End of block
      stack.pop()
      if in_multiline_list or out[-1].endswith(")"):
        out.append(f"{indent_char * len(stack)})")
      else:
        out[-1] += ")"
      in_multiline_list = False
    elif isinstance(data, (list, SExp)):
      # Start of block
      out.append(f"{indent_char * len(stack)}(")
      stack.append(iter(data if isinstance(data, list) else data.sexp))
    else:
      txt = str(data)
      if isinstance(data, Atom):
        # Combine chains of XYs into a single line
        was_xy = in_xy
        in_xy = data == "xy"
        if in_xy and was_xy and len(out[-2]) < xy_special_case_column_limit:
          out.pop()
          out[-1] += " ("
      elif not isinstance(data, (int, Decimal)):
        txt = txt.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        txt = f'"{txt}"'
      if in_xy or len(out[-1]) < consecutive_token_wrap_threshold:
        if out[-1].endswith("("):
          out[-1] += txt
        else:
          out[-1] = f"{out[-1]} {txt}"
      else:
        out.append(f"{indent_char * len(stack)}{txt}")
        in_multiline_list = True
  return "\n".join(out)


def main(argv):
  """Reads an s-exp file from stdin and writes a new one to stdout."""
  print(repr(parse(sys.stdin.read())))
  return 0


if __name__ == "__main__":
  sys.exit(main(sys.argv))
