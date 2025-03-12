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


class SExp:
  @classmethod
  def init(cls, data):
    if not data:
      return cls()
    if not isinstance(data[0], Atom):
      return cls(data)
    return getattr(handler, "_handlers", {}).get(data[0], cls)(data)

  def __init__(self, data=None):
    if isinstance(data, SExp):
      self.sexp = data.sexp
      self._subs = data._subs
      self._atoms = data._atoms
      return
    self.sexp = data or []
    self._subs = {}
    self._atoms = {}
    for item in self.sexp:
      if isinstance(item, SExp):
        self._subs.setdefault(item.type, []).append(item)
      elif isinstance(item, Atom):
        self._atoms[item] = self._atoms.get(item, 0) + 1

  def __getitem__(self, index_or_atom):
    try:
      return self.data[int(index_or_atom)]
    except ValueError:
      return self._subs[Atom(index_or_atom)]

  def __contains__(self, atm):
    return Atom(atm) in self._subs or Atom(atm) in self._atoms

  def __eq__(self, other):
    return self.sexp == other

  def __str__(self):
    return dump(self)

  def __repr__(self):
    return dump(self)

  @property
  def type(self):
    if self.sexp and isinstance(self.sexp[0], Atom):
      return self.sexp[0]
    return None

  @property
  def data(self):
    if self.sexp and isinstance(self.sexp[0], Atom):
      return self.sexp[1:]
    return self.sexp

  def enum(self, *atoms, start_i=0):
    for i, entry in enumerate(self.sexp):
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
    i = i or len(self.sexp)
    if isinstance(item, SExp):
      # Add to sub list, maintaining relative ordering
      subs = self._subs.setdefault(item.type, [])
      for j in range(len(subs) - 1, -1, -1):
        try:
          self.sexp.index(subs[j], i)
        except ValueError:
          subs.insert(j + 1, item)
          break
      else:
        subs.insert(0, item)
    elif isinstance(item, Atom):
      self._atoms[item] = self._atoms.get(item, 0) + 1
    self.sexp.insert(i)

  def remove(self, atoms=None, func=None):
    if atoms is None and func is None:
      return
    for i in range(len(self.sexp) - 1, -1, -1):
      if (atoms is None or is_atom(self.sexp[i], atoms)) and (
        func is None or func(self.sexp[i])
      ):
        if isinstance(self.sexp[i], SExp):
          _subs = self._subs.get(self.sexp[i].type, [])
          for j in range(len(_subs) - 1, -1, -1):
            if _subs[j] is self.sexp[i]:
              del _subs[j]
        elif isinstance(self.sexp[i], Atom):
          self._atoms[self.sexp[i]] -= 1
          if not self._atoms[self.sexp[i]]:
            del self._atoms[self.sexp[i]]
        del self.sexp[i]


def parse(data):
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
  return SExp.init(stack[-1])


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
