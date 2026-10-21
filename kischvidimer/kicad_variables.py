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
Common classes and routines for handling kicad variables
"""

import datetime
import math
import random
import re
import time

from . import sexp


class Variables:
  """Tracks a variable context, which can inherit other contexts.
  There are three categories of variable references:
    1. hierarchical variables are inherited from the parent and defined by
       anything with fields (although only sheet fields have descendents)
    2. global variables are defined by symbols and are indexed by the refdes.
       note that these variables can depend on variables in that symbol's
       context, which could in turn also be global variables dependent on other
       symbols' contexts.  ${refdes:variable}
    3. title_block variables, which are inherited both by the page's contents as
       well as by the sheet that instantiated the page
  Recursive resolution needs to be aware of the context of the variable being
  resolved! Example:
    Symbol A and Symbol B both have an "address" property, but the values are
    different.
    Symbol A has a property that references symbol B's "info" property
    Symbol B's "info" property references its address property.
    When recursively resolving Symbol A's property, the address of symbol B
    should be used, not symbol A.
  To achieve this, when recursively resolving a variable, any variable
  references that get returned must be annotated with the context of the
  variable. If no annotation is present, the variable is assumed to be in its
  own context. A refdes context gets mapped to the instance where the definition
  exists.
  Variable names are case-insensitively matched, but variables can be
  case-sensitively defined, so try to match exactly first.
  Make sure to use uuid(generate=true) in case UUIDs are missing.
  It is the responsibility of the relevant objects to fill in the following
  special variables:
  Special global variables:
    ${##} -> total page count (not max pn) (kicad_pro)
    ${CURRENT_DATE} -> current date (kicad_pro)
    ${PROJECTNAME} -> name of the project (kicad_pro)
    ${FILENAME} -> file name (seems to return root file name) (kicad_sch)
    ${FILEPATH} -> full path (seems to return root file path) (kicad_sch)
  Special sheet variables:
    ${SHEETPATH} -> full page path, ending in a slash
  Special page variables:
    ${#} -> page number (kicad_pro)
  Special symbol variables: (all handled by symbol_inst)
    ${ref:DNP} -> "DNP" or ""
    ${ref:EXCLUDE_FROM_BOARD} -> "Excluded from board" or ""
    ${ref:EXCLUDE_FROM_BOM} -> "Excluded from BOM" or ""
    ${ref:EXCLUDE_FROM_SIM} -> "Excluded from simulation" or ""
    ${ref:FOOTPRINT_LIBRARY} -> footprint field (prior to colon if present)
    ${ref:FOOTPRINT_NAME} -> footprint field, after colon. blank if no colon
    ${ref:NET_CLASS(<pin_number>)} -> net class of attached net to pin
    ${ref:NET_NAME(<pin_number>)} -> connection name of net attached to pin
    ${ref:OP} -> "--"? probably something to do with simulation
    ${ref:PIN_NAME(<pin_number>)} -> name of the pin
    ${ref:SHORT_NET_NAME(<pin_number>)} -> local name of the net attached to pin
    ${ref:SYMBOL_DESCRIPTION} -> description from the library cache
    ${ref:SYMBOL_KEYWORDS} -> keywords from the library cache
    ${ref:SYMBOL_LIBRARY} -> library name
    ${ref:SYMBOL_NAME} -> symbol name
    ${ref:UNIT} -> unit LETTER
  Special net field variables: (all handled by label)
    ${CONNECTION_TYPE} -> "Input", "Output", "Bidirectional", "Tri-State",
                          "Passive"; undefined for local nets
    ${NET_CLASS} -> net class
    ${NET_NAME} -> connection name
    ${OP} -> "--"? probably something to do with simulation
    ${SHORT_NET_NAME} -> local name
  FIXME: is there *any* sane way to handle diffs?
  """

  GLOBAL = ""
  PAGENO = "#"
  PAGECOUNT = "##"
  # FIXME: handle backslash escapes better
  RE_VAR = re.compile(r"(?<!\\)\${([^}:]+:)?([^}]+)}")
  ESCAPES = {
    "dblquote": '"',
    "quote": "'",
    "lt": "<",
    "gt": ">",
    "backslash": "\\",
    "slash": "/",
    "bar": "|",
    "comma": ",",
    "colon": ":",
    "space": " ",
    "dollar": "$",
    "tab": "\t",
    "return": "\n",
    "brace": "{",
  }
  RE_ESCAPE = re.compile(r"(?<![$~^_]){(" + "|".join(ESCAPES) + ")}")

  def __init__(self):
    # Maps a uuid to a dict of variable definitions. If a variable isn't defined
    # in a uuid's dict, go a step up the hierarchy. "" is a special context that
    # is global.
    self._contexts = {}

  def context(self):
    s = sexp.SExp.init(
      [
        sexp.Atom("~variables"),
      ]
    )
    s.variables = self
    return (s,)

  def _resolve_context(self, context):
    """Converts a context tuple, ref string, or UUID string into a UUID"""
    if not context:
      return ""
    elif isinstance(context, str):
      if len(context) == 36:  # kicad/19623
        return min(
          (c for c in self._contexts if c.endswith(context)), default=None
        )
      return context
    elements = [""]
    for c in context:
      if c.type == "path":
        elements = [c.uuid()]
      elif hasattr(c, "uuid"):
        elements.append(c.uuid(generate=True))
    return "/".join(elements)

  @staticmethod
  def v(context):
    """Finds the first variables instance in the context
    Returns a dummy class with expand/resolve if not found.
    """
    if isinstance(context, Variables):
      return context
    for c in context:
      if hasattr(c, "variables"):
        return c.variables

    class Dummy:
      def expand(self, context, text, hist=None):
        return text

      def resolve(self, context, variable, hist=None):
        return None

    return Dummy()

  def define(self, context, variable, value):
    if value is None:
      return
    value = str(value)
    context = self._resolve_context(context)
    vardict = self._contexts.setdefault(context, {})
    vardict[variable] = value
    # For case-insensitive fallback matching
    vardict.setdefault(variable.upper(), value)

  def expand(self, context, text, hist=None):
    text = Variables.RE_VAR.sub(lambda m: self.resolve(context, m, hist), text)
    text = self.evaluate_all(context, text)
    if hist is None:
      # Escaped references are shown without the backslash
      text = text.replace("\\${", "${").replace("\\@{", "@{")
    return text

  @staticmethod
  def unescape(text):
    """Decodes the escapes KiCad uses for special characters in text, such as
    {slash}. Like KiCad, this should happen before expanding variables."""
    return Variables.RE_ESCAPE.sub(lambda m: Variables.ESCAPES[m[1]], text)

  def resolve(self, context, variable, hist=None):
    """Variable can be x, x:y, or a match object.
    If the variable isn't found, returns None if variable was a string, or the
    full match text if the variable is a match object.
    """
    # FIXME: support querying the netlist
    hist = set() if hist is None else hist
    orig_variable = None
    orig_context_text = None
    # Custom ERC/DRC messages are hidden
    token = variable[0][2:-1] if isinstance(variable, re.Match) else variable
    if token.startswith(
      ("ERC_WARNING", "ERC_ERROR", "DRC_WARNING", "DRC_ERROR")
    ):
      return ""
    if isinstance(variable, re.Match):
      orig_variable = variable[0]
      if variable[1]:
        context = orig_context_text = variable[1][:-1].strip()
      variable = variable[2].strip()
    elif ":" in variable:
      context, _, variable = variable.partition(":")
      context = orig_context_text = context.strip()
      variable = variable.strip()
    context = self._resolve_context(context)
    while True:
      hist_entry = (context, variable)
      # If we've cycled, go up a level and continue if possible
      if hist_entry not in hist:
        hist.add(hist_entry)
        vardict = self._contexts.get(context, {})
        resolved = vardict.get(variable, vardict.get(variable.upper()))
        if resolved is not None:
          return self.expand(context, resolved, hist)
      if not context:
        if orig_context_text:
          return f"<Unresolved: {orig_context_text}>"
        return orig_variable
      context = context.rpartition("/")[0]

  def evaluate_all(self, context, text):
    """Evaluates each @{...} in text, except escaped ones"""
    out = []
    i = 0
    while True:
      start = text.find("@{", i)
      end = start >= 0 and expression_end(text, start + 2)
      if not end:
        break
      out.append(text[i:start])
      if text[start - 1 : start] == "\\":
        out.append(text[start:end])
      else:
        out.append(self.evaluate(context, text[start:end]))
      i = end
    return "".join(out) + text[i:]

  def evaluate(self, context, expr):
    """Evaluates an expression, including the @{}. Like KiCad, expressions
    that fail to evaluate are left as-is."""
    if expr == "@{}":
      return ""
    if len(expr) > 4096:
      return expr
    try:
      return text(Expression(expr[2:-1]).evaluate())
    except Exception:
      return expr


def expression_end(text, i):
  """Returns the index after the } that closes an expression starting at i,
  skipping over string literals, or None if the expression isn't closed."""
  depth = 1
  while i < len(text):
    m = RE_STRING.match(text, i)
    if m:
      i = m.end()
      continue
    depth += {"{": 1, "}": -1}.get(text[i], 0)
    i += 1
    if not depth:
      return i
  return None


def format_number(x):
  """Formats a number like KiCad: as an integer if it's close to one, otherwise
  with the shortest representation that round-trips."""
  if math.isfinite(x) and abs(x - round(x)) < 1e-10 and abs(round(x)) < 1e15:
    return str(round(x))
  if math.isnan(x):
    return "-nan" if math.copysign(1, x) < 0 else "nan"
  return repr(x).removesuffix(".0")


def page_sort_key(page):
  """Sort key for page numbers, which are strings that are usually numeric."""
  return [int(s) if s.isdigit() else s for s in re.split(r"(\d+)", str(page))]


# fmt: off
ESERIES_DATA = (
  (24,
    (
      100, 110, 120, 130, 150, 160, 180, 200, 220, 240, 270, 300, 330, 360, 390,
      430, 470, 510, 560, 620, 680, 750, 820, 910,
    ),
  ),
  (192,
    (
      100, 101, 102, 104, 105, 106, 107, 109, 110, 111, 113, 114, 115, 117, 118,
      120, 121, 123, 124, 126, 127, 129, 130, 132, 133, 135, 137, 138, 140, 142,
      143, 145, 147, 149, 150, 152, 154, 156, 158, 160, 162, 164, 165, 167, 169,
      172, 174, 176, 178, 180, 182, 184, 187, 189, 191, 193, 196, 198, 200, 203,
      205, 208, 210, 213, 215, 218, 221, 223, 226, 229, 232, 234, 237, 240, 243,
      246, 249, 252, 255, 258, 261, 264, 267, 271, 274, 277, 280, 284, 287, 291,
      294, 298, 301, 305, 309, 312, 316, 320, 324, 328, 332, 336, 340, 344, 348,
      352, 357, 361, 365, 370, 374, 379, 383, 388, 392, 397, 402, 407, 412, 417,
      422, 427, 432, 437, 442, 448, 453, 459, 464, 470, 475, 481, 487, 493, 499,
      505, 511, 517, 523, 530, 536, 542, 549, 556, 562, 569, 576, 583, 590, 597,
      604, 612, 619, 626, 634, 642, 649, 657, 665, 673, 681, 690, 698, 706, 715,
      723, 732, 741, 750, 759, 768, 777, 787, 796, 806, 816, 825, 835, 845, 856,
      866, 876, 887, 898, 909, 920, 931, 942, 953, 965, 976, 988,
    ),
  ),
)
# fmt: on


def eseries(mode, value, series):
  """Returns the nearest (mode 0), next higher (1) or next lower (-1) value in
  an E series, like KiCad's ESERIES_UTILS."""
  if series[:1] not in "eE" or series[1:] not in "3 6 12 24 48 96 192".split():
    raise ValueError("invalid E series")
  if value <= 0:
    raise ValueError("invalid value")
  # Smaller series are subsets of E24 or E192
  base, data = next(d for d in ESERIES_DATA if int(series[1:]) <= d[0])
  data = data[:: base // int(series[1:])]
  decade = math.floor(math.log10(value))
  value = value / math.pow(10, decade) * 100
  if mode == 0:
    found = min(data, key=lambda x: abs(value - x))
  elif mode > 0:
    found = next((x for x in data if x > value), None)
    if found is None:
      found, decade = data[0], decade + 1
  else:
    found = next((x for x in reversed(data) if x < value), None)
    if found is None:
      found, decade = data[-1], decade - 1
  return found / 100 * math.pow(10, decade)


# Expression evaluation for @{...}, following KiCad's text_eval: values are
# floats or strings, and any error leaves the expression as-is.

# Units convert to mm
UNITS = {
  "mm": 1,
  "cm": 10,
  "in": 25.4,
  '"': 25.4,
  "mil": 0.0254,
  "mils": 0.0254,
  "thou": 0.0254,
  "um": 0.001,
  "deg": 1,
  "°": 1,
  "ps": 1,
  "fs": 1,
}
# R and F are only markers for RKM notation (4R7)
PREFIXES = {
  "a": 1e-18,
  "f": 1e-15,
  "p": 1e-12,
  "n": 1e-9,
  "u": 1e-6,
  "µ": 1e-6,
  "μ": 1e-6,
  "m": 1e-3,
  "R": 1,
  "F": 1,
  "k": 1e3,
  "K": 1e3,
  "M": 1e6,
  "G": 1e9,
  "T": 1e12,
  "P": 1e15,
  "E": 1e18,
}
# Longest first
OPERATORS = "<= >= == != ( ) + - * / % ^ , < >".split()
STRING_ESCAPES = {
  "n": "\n",
  "t": "\t",
  "r": "\r",
  "\\": "\\",
  '"': '"',
  "'": "'",
  "0": "\0",
}
RE_EXPONENT = re.compile(r"[eE][+-]?[0-9]+")
RE_STRING = re.compile(r"\"(?:[^\"\\\n]|\\.)*\"|'(?:[^'\\\n]|\\.)*'")
RE_HEX = re.compile(r"[0-9a-fA-F]{1,2}")
# What KiCad accepts when converting a string to a number
RE_FLOAT = re.compile(
  r"-?(?:(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?|inf(?:inity)?|nan)",
  re.IGNORECASE,
)


def is_alpha(c):
  """KiCad treats every non-ASCII, non-space character as a letter"""
  return c.isalpha() if c.isascii() else c != "\ufffd" and not c.isspace()


def is_digit(c):
  return "0" <= c <= "9"


def tokenize(text):
  """Returns a list of (kind, value) tuples, where kind is "num", "str", "id",
  or "op". Raises ValueError on invalid input."""
  tokens = []
  i = 0
  while i < len(text):
    c = text[i]
    nxt = text[i + 1 : i + 2]
    if c.isspace():
      i += 1
    elif c in "\"'":
      value, i = tokenize_string(text, i)
      tokens.append(("str", value))
    elif is_digit(c) or c == "." and is_digit(nxt):
      value, i = tokenize_number(text, i)
      tokens.append(("num", value))
    elif is_alpha(c) or c == "_":
      start = i
      while i < len(text) and (is_alpha(text[i]) or text[i] in "0123456789_"):
        i += 1
      tokens.append(("id", text[start:i]))
    else:
      op = next((op for op in OPERATORS if text.startswith(op, i)), None)
      if not op:
        raise ValueError(f"unexpected {c!r}")
      tokens.append(("op", op))
      i += len(op)
  return tokens


def tokenize_string(text, i):
  quote = text[i]
  value = ""
  i += 1
  while i < len(text) and text[i] not in (quote, "\n"):
    if text[i] == "\\" and i + 1 < len(text):
      escape = text[i + 1]
      i += 2
      if escape == "x":
        m = RE_HEX.match(text, i)
        value += chr(int(m[0], 16)) if m else "\\x"
        i += len(m[0]) if m else 0
      else:
        value += STRING_ESCAPES.get(escape, "\\" + escape)
    else:
      value += text[i]
      i += 1
  if text[i : i + 1] != quote:
    raise ValueError("unterminated string")
  return value, i + 1


def tokenize_number(text, i):
  """Parses a number with an optional SI prefix, RKM notation (4k7) and unit"""

  def at(i):
    return text[i : i + 1]

  def digits(i):
    while is_digit(at(i)):
      i += 1
    return i

  def word(i):
    end = i
    while is_alpha(at(end)) or at(end) and at(end) in "\"'":
      end += 1
    return text[i:end]

  def prefix(i):
    """Returns the multiplier if text[i] is a prefix rather than a unit"""
    c = at(i)
    if c not in PREFIXES or word(i) in UNITS:
      return None
    if c in "RF" and (is_alpha(at(i + 1)) or at(i + 1) == "_"):
      return None
    return PREFIXES[c]

  end = digits(i)
  num = text[i:end]
  multiplier = None
  if at(end) == ".":
    num += "."
    end += 1
  elif not RE_EXPONENT.match(text, end):
    multiplier = prefix(end)
    if multiplier is not None:
      end += 1
      if is_digit(at(end)):
        num += "."  # RKM: the prefix takes the place of the decimal point
  i, end = end, digits(end)
  num += text[i:end]
  m = RE_EXPONENT.match(text, end)
  if m:
    num += m[0]
    end = m.end()
  if multiplier is None:
    multiplier = prefix(end)
    end += multiplier is not None
  value = float(num) * (multiplier or 1)
  if not math.isfinite(value):
    raise ValueError("number out of range")
  # A unit may follow after whitespace
  i = end
  while at(i).isspace():
    i += 1
  unit = word(i)
  if unit in UNITS:
    return value * UNITS[unit], i + len(unit)
  return value, end


class Expression:
  """Recursive descent parser for KiCad's expression grammar, evaluating as it
  goes. Both arguments of if() are evaluated, like in KiCad."""

  # Binary operators by increasing precedence; ^ is handled in unary()
  BINARY = (("<", ">", "<=", ">=", "==", "!="), ("+", "-"), ("*", "/", "%"))

  def __init__(self, text):
    self.tokens = tokenize(text)
    self.pos = 0

  def evaluate(self):
    value = self.binary(0)
    if self.pos != len(self.tokens):
      raise SyntaxError("trailing tokens")
    return value

  def peek(self):
    return self.tokens[self.pos] if self.pos < len(self.tokens) else ("", "")

  def accept(self, op):
    if self.peek() == ("op", op):
      self.pos += 1
      return True
    return False

  def expect(self, op):
    if not self.accept(op):
      raise SyntaxError(f"expected {op}")

  def binary(self, level):
    if level == len(self.BINARY):
      return self.unary()
    value = self.binary(level + 1)
    while True:
      kind, op = self.peek()
      if kind != "op" or op not in self.BINARY[level]:
        return value
      self.pos += 1
      value = binop(op, value, self.binary(level + 1))

  def unary(self):
    if self.accept("-"):
      return binop("-", 0.0, self.unary())
    if self.accept("+"):
      return self.unary()
    value = self.primary()
    if self.accept("^"):  # right-associative, and binds tighter than unary -
      return binop("^", value, self.unary())
    return value

  def primary(self):
    kind, value = self.peek()
    self.pos += 1
    if kind in ("num", "str"):
      return value
    if (kind, value) == ("op", "("):
      value = self.binary(0)
      self.expect(")")
      return value
    if kind != "id":
      raise SyntaxError(f"unexpected {value or 'end'}")
    self.expect("(")
    args = []
    if not self.accept(")"):
      args.append(self.binary(0))
      while self.accept(","):
        args.append(self.binary(0))
      self.expect(")")
    return call(value, args)


def number(x):
  """Converts a value to a number like KiCad, which only accepts plain numbers
  in strings"""
  if isinstance(x, str) and not RE_FLOAT.fullmatch(x):
    raise ValueError(f"not a number: {x}")
  return float(x)


def text(x):
  """Converts a value to a string like the result of an expression"""
  return x if isinstance(x, str) else format_number(x)


def power(a, b):
  """Like C's pow(), returning infinity or NaN rather than raising"""
  try:
    return math.pow(a, b)
  except (OverflowError, ValueError):
    if a < 0 and b != int(b):
      return -math.nan
    return math.copysign(math.inf, a) if b % 2 == 1 else math.inf


BINOPS = {
  "+": lambda a, b: a + b,
  "-": lambda a, b: a - b,
  "*": lambda a, b: a * b,
  "/": lambda a, b: a / b,
  "%": math.fmod,
  "^": power,
  "<": lambda a, b: float(a < b),
  ">": lambda a, b: float(a > b),
  "<=": lambda a, b: float(a <= b),
  ">=": lambda a, b: float(a >= b),
  "==": lambda a, b: float(a == b),
  "!=": lambda a, b: float(a != b),
}


def binop(op, a, b):
  if op == "+" and (isinstance(a, str) or isinstance(b, str)):
    return text(a) + text(b)
  if op in ("==", "!=") and isinstance(a, str) and isinstance(b, str):
    return BINOPS[op](a, b)
  return BINOPS[op](number(a), number(b))


def call(name, args):
  """Calls a function, checking its number of arguments. Numeric functions
  get their arguments converted to numbers."""
  if name not in FUNCTIONS:
    raise NameError(f"unknown function {name}")
  low, high, numeric, func = FUNCTIONS[name]
  if not low <= len(args) <= high:
    raise TypeError(f"wrong number of arguments for {name}")
  value = func(*(number(a) for a in args) if numeric else args)
  return value if isinstance(value, str) else float(value)


def kround(x, n=0):
  # Round half away from zero, unlike Python
  m = math.pow(10, int(n))
  return math.copysign(math.floor(abs(x * m) + 0.5), x) / m


def total(*a):
  t = 0.0
  for x in a:
    t += x
  return t


def decimals(x, n=2):
  """Formats with n decimals; like KiCad, ignores an n that isn't a number"""
  try:
    n = int(number(n))
  except (ValueError, OverflowError):
    n = 2
  if n > 1000:
    raise ValueError("too many decimals")
  return f"{number(x):.{n}f}"


def days(y, m, d):
  epoch = datetime.date(1970, 1, 1).toordinal()
  return datetime.date(y, m, 1).toordinal() - epoch + d - 1


def datestring(s):
  s = text(s)
  cjk = re.search(r"(\d+)\s*[年년]\s*(\d+)\s*[月월]\s*(\d+)\s*[日일]", s)
  sep = next((c for c in "-/." if c in s), None)
  if cjk:
    p = [int(x) for x in cjk.groups()]
  elif sep:
    p = [int(x) for x in s.split(sep)]
  elif len(s) == 8:
    return days(int(s[:4]), int(s[4:6]), int(s[6:]))
  else:
    raise ValueError("invalid date")
  p += [1] * (3 - len(p))
  if not cjk and sep == "/" and p[0] <= 12 and p[1] <= 31:
    p = [p[2], p[0], p[1]]
  elif not cjk and sep == "/" and p[1] <= 12:
    p = p[::-1]
  datetime.date(*p)  # validate
  return days(*p)


MONTHS = (
  "January February March April May June July August September October "
  "November December"
).split()
WEEKDAYS = "Monday Tuesday Wednesday Thursday Friday Saturday Sunday".split()


def dateformat(d, fmt="ISO", *_):
  date = datetime.date(1970, 1, 1) + datetime.timedelta(days=int(number(d)))
  y, m, d = date.year, date.month, date.day
  fmt = text(fmt)
  if fmt in ("US", "us"):
    return f"{m:02}/{d:02}/{y:04}"
  if fmt in ("EU", "european"):
    return f"{d:02}/{m:02}/{y:04}"
  if fmt in ("long", "short"):
    month = MONTHS[m - 1] if fmt == "long" else MONTHS[m - 1][:3]
    return f"{month} {d}, {y}"
  if fmt in "Chinese chinese CN cn 中文 Japanese japanese JP jp 日本語".split():
    return f"{y}年{m:02}月{d:02}日"
  if fmt in ("Korean", "korean", "KR", "kr", "한국어"):
    return f"{y}년 {m:02}월 {d:02}일"
  return f"{y:04}-{m:02}-{d:02}"


def timeformat(t, fmt="ISO", *_):
  t = time.localtime(int(number(t)))
  h, m, s = t.tm_hour, t.tm_min, t.tm_sec
  fmt = text(fmt)
  if fmt == "12h":
    return f"{h % 12 or 12}:{m:02}:{s:02} {'PM' if h >= 12 else 'AM'}"
  if fmt in ("HH_MM_SS", "filename"):
    return f"{h:02}h{m:02}m{s:02}s"
  if fmt == "short":
    return f"{h:02}:{m:02}"
  return f"{h:02}:{m:02}:{s:02}"


def char(c):
  """The first character of a value, or a space"""
  return (text(c) or " ")[0]


def replace(a, b, c):
  return text(a).replace(text(b), text(c)) if text(b) else text(a)


ANY = math.inf
# name: (min args, max args, numeric args, function)
FUNCTIONS = {
  "abs": (1, 1, True, abs),
  "sqrt": (1, 1, True, math.sqrt),
  "pow": (2, 2, True, power),
  "floor": (1, 1, True, math.floor),
  "ceil": (1, 1, True, math.ceil),
  "round": (1, ANY, True, lambda x, n=0, *_: kround(x, n)),
  "min": (1, ANY, True, min),
  "max": (1, ANY, True, max),
  "sum": (1, ANY, True, total),
  "avg": (1, ANY, True, lambda *a: total(*a) / len(a)),
  "shunt": (2, 2, True, lambda x, y: x * y / (x + y) if x + y > 0 else 0),
  "db": (1, 1, True, lambda x: 10 * math.log10(x)),
  "dbv": (1, 1, True, lambda x: 20 * math.log10(x)),
  "fromdb": (1, 1, True, lambda x: power(10, x / 10)),
  "fromdbv": (1, 1, True, lambda x: power(10, x / 20)),
  "enearest": (1, 2, False, lambda x, s="E24": eseries(0, number(x), text(s))),
  "eup": (1, 2, False, lambda x, s="E24": eseries(1, number(x), text(s))),
  "edown": (1, 2, False, lambda x, s="E24": eseries(-1, number(x), text(s))),
  "today": (0, 0, False, lambda: time.time() // (24 * 3600)),
  "now": (0, 0, False, lambda: time.time() // 1),
  "random": (0, 0, False, random.random),
  "upper": (1, 1, False, lambda a: text(a).upper()),
  "lower": (1, 1, False, lambda a: text(a).lower()),
  "concat": (2, ANY, False, lambda *a: "".join(map(text, a))),
  "beforefirst": (2, 2, False, lambda a, c: text(a).partition(char(c))[0]),
  "beforelast": (2, 2, False, lambda a, c: text(a).rpartition(char(c))[0]),
  "afterfirst": (2, 2, False, lambda a, c: text(a).partition(char(c))[2]),
  "afterlast": (2, 2, False, lambda a, c: text(a).rpartition(char(c))[2]),
  "replace": (3, 3, False, replace),
  "if": (3, 3, False, lambda c, a, b: a if number(c) else b),
  "format": (1, ANY, False, lambda x, n=2, *_: decimals(x, n)),
  "fixed": (1, ANY, False, lambda x, n=2, *_: decimals(x, n)),
  "currency": (1, ANY, False, lambda x, s="$", *_: text(s) + decimals(x)),
  "datestring": (1, 1, False, datestring),
  "dateformat": (1, ANY, False, dateformat),
  "weekdayname": (1, 1, False, lambda d: WEEKDAYS[(int(number(d)) + 3) % 7]),
  "timeformat": (1, ANY, False, timeformat),
}
