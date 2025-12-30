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

"""
Parses Kicad symbol files
Also acts as a deduplifying library cache when used with schematic-embedded
symbols
"""

import random
import sys
from decimal import Decimal

from . import sexp, svg
from .diff import Param
from .kicad_common import Drawable, rotated, translated
from .kicad_modifiers import HasModifiers


class PlaceholderHandler(sexp.SExp):
  def __init__(self, s):
    raise Exception(f'found "{s[0]}" in an unexpected context')


class AlternateDef(sexp.SExp):
  """One alternate definition on a pin"""

  LITERAL_MAP = {"name": 1, "type": 2, "style": 3}
  UNIQUE = "name"


class AlternateInst(PlaceholderHandler):
  pass


@sexp.handler("alternate")
def alternate_disambiguator(s):
  """the "alternate" atom has two uses, which we disambiguate in order:
  1) a definition, which has a name, type, and style
  2) an instance, which just has a name
  Figure out which of the two we're dealing with and instantiate the subclass
  """
  if len(s) > 2:
    return AlternateDef(s)
  return AlternateInst(s)


class PinDef(Drawable):
  """pins in a symbol definition"""

  LITERAL_MAP = {"type": 1, "style": 2}

  def __str__(self):
    return f"pin {self['number'][0][0]}"

  def distance(self, other, fast, diffparam):
    if self.type != other.type:
      return None
    if self["number"][0][0] == other["number"][0][0]:
      return 0
    if fast:
      return 1
    # FIXME: consider match distance for number/name
    # FIXME: consider location closeness?
    return (
      1
      + (self["name"][0][0] != other["name"][0][0]) * 2
      + (self["at"] != other["at"])
    )

  def name(self, diffs, context):
    return Param(
      lambda a, n: a or n,
      self.alternate(diffs, context),
      self["name"][0].param(diffs),
    )

  def num(self, diffs, context):
    return self["number"][0].param(diffs)

  def get_type_style(self, diffs, context):
    # FIXME: diffs
    target = self
    # FIXME: alternate can have diffs too
    alternate = self.alternate(diffs, context).v
    if alternate is not None:
      for alt in self["alternate"]:
        if alternate == alt[0]:
          target = alt
          break
      # TODO: what to do when alternate is not found?
    return (target.param(diffs, "type"), target.param(diffs, "style"))

  @sexp.uses("alternate")
  def alternate(self, diffs, context):
    # FIXME: diffs
    if "alternate" not in self:
      return Param(None)
    num = self["number"][0][0]
    for c in reversed(context):
      if hasattr(c, "get_alternates"):
        alternates = c.get_alternates(diffs, context)
        return alternates.map(lambda a, num: a.get(num), num)
    return Param(None)

  @sexp.uses("at")
  def pts(self, diffs, context):
    pos = self["at"][0].pos(diffs)
    for c in reversed(context):
      if hasattr(c, "transform_pin"):
        pos = c.transform_pin(pos, diffs)
        break
    return Param.array(pos)

  @sexp.uses("hide")
  def hide(self, diffs=None):
    return self.has_yes("hide", diffs)

  def fillnetlist(self, netlister, diffs, context):
    self.netbus = netlister.add_sympin(context, self)

  @sexp.uses(
    "clock",
    "clock_low",
    "edge_clock_high",
    "input_low",
    "inverted",
    "non_logic",
    "output_low",
    "length",
  )
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_PINS:
      return
    # REFERENCE: LIB_PIN::PlotSymbol, LIB_PIN::PlotPinTexts
    # FIXME: effects
    # FIXME: unconnected circle
    # FIXME: defaults?
    # FIXME: metadata (electrical type)
    # FIXME: redo rendering to turn rot+inst_rot_mirror into rot_90+mirror,
    #        where rot_90 is 0 or 90 and mirror can be x or y.  This will likely
    #        simplify things tremendously, as well as make diffs look better.

    name = self.name(diffs, context)
    num = self.num(diffs, context)
    pos = self["at"][0].pos(diffs)
    rot = self["at"][0].rot(diffs)
    semiunrot = Param(lambda r: r % 180 - r, rot)  # restricts to 0 or 90
    mirror = Param(lambda r: 1 if r in (0, 90) else -1, rot)
    length = Param.ify(self.get("length"), 0, diffs)

    svg.gstart(pos=pos, rotate=rot, path=num, hidden=self.hide(diffs))

    # Compensate for mirror/rotation in instantiations
    inst_rot_mirror = (0, None)
    for c in context:
      if c.type == "symbol" and "lib_id" in c:
        # NOTE: since we wrap this in <use>, we should ignore instance diffs
        inst_rot_mirror = c.rot_mirror(diffs=None)
        break

    # Render line ending
    flipy = Param(
      lambda rm: -1
      if rm
      in (
        (180, None),
        (270, None),
        (0, "x"),
        (270, "x"),
      )
      else 1,
      inst_rot_mirror,
    )

    typ, style = self.get_type_style(diffs, context)
    # no_connect type supersedes all styles
    nc = Param(lambda t: t == "no_connect", typ)

    sz = Decimal("0.635")

    # draw clk carrot
    svg.gstart(hidden=Param(lambda nc, s: nc or "clock" not in s, nc, style))
    svg.polyline(
      xys=Param(lambda x: ((x, sz), (x + sz, 0), (x, -sz)), length),
      color="device",
    )
    svg.gend()  # clock carrot

    # draw inversion circle
    svg.gstart(hidden=Param(lambda nc, s: nc or "inverted" not in s, nc, style))
    svg.circle(
      pos=Param(lambda x: (x - sz, 0), length), radius=sz, color="device"
    )
    svg.gend()  # inversion circle

    # Render main line
    svg.polyline(
      xys=Param(
        lambda nc, style, length, mirror, flipy, semiunrot: (
          # draw X at pin (supersedes non_logic style)
          (
            (-0.381, -0.381),
            (0.381, 0.381),
            (0, 0),
            (0.381, -0.381),
            (-0.381, 0.381),
            (0, 0),
            (length, 0),
          )
          if nc
          # draw X at end of line (not pin)
          else (
            (0, 0),
            (length, 0),
            (length - sz, -sz),
            (length + sz, sz),
            (length, 0),
            (length + sz, -sz),
            (length - sz, sz),
          )
          if style == "non_logic"
          # draw input-low
          else (
            (0, 0),
            (length, 0),
            translated(
              (length, 0),
              rotated((-sz * 2 * mirror, sz * 2 * flipy), semiunrot),
            ),
            translated((length, 0), rotated(-sz * 2 * mirror, semiunrot)),
          )
          if style in ("input_low", "clock_low", "edge_clock_high")
          # draw output-low
          else (
            (0, 0),
            (length, 0),
            translated((length, 0), rotated(-sz * 2 * mirror, semiunrot)),
            translated((length, 0), rotated((0, sz * 2 * flipy), semiunrot)),
          )
          if style == "output_low"
          # draw standard line
          else ((0, 0), (length - sz * 2 * (not nc and "inverted" in style), 0))
        ),
        *(nc, style, length, mirror, flipy, semiunrot),
      ),
      color="device",
    )

    # Render name and number
    pin_config = next(
      c.pin_config(diffs) for c in reversed(context) if isinstance(c, SymbolDef)
    )
    yoffset = -0.1016 - svg.THICKNESS["wire"]
    pin_config["number"]["xoffset"] = Param(lambda x: x / 2, length)
    pin_config["number"]["yoffset"] = yoffset
    pin_config["number"]["justify"] = "middle"
    # Adjust locations based on whether the pin name is visible
    # as of kicad#19649, a blank name also results in the pin being on top
    pin_on_top = Param(
      lambda h, x, n: h or x or not n or n == "~",
      pin_config["name"]["hide"],
      pin_config["name"]["xoffset"],
      name,
    )
    pin_config["name"]["xoffset"] = Param(
      lambda length, x, pot: length + x if pot else length / 2,
      *(length, pin_config["name"]["xoffset"], pin_on_top),
    )
    pin_config["name"]["yoffset"] = Param(
      lambda y, pot: y * (not pot),
      yoffset,
      pin_on_top,
    )
    pin_config["name"]["justify"] = Param(
      lambda rot, pot: rot if pot else "middle",
      rot,
      pin_on_top,
    )
    pin_config["name"]["vjustify"] = Param(
      lambda pot: "middle" if pot else "bottom",
      pin_on_top,
    )
    pin_config["number"]["vjustify"] = Param(
      lambda pot: "bottom" if pot else "top",
      pin_on_top,
    )
    # pin name should always be top/left, pin number below/right.
    # text should always be facing up/left
    swap_side = Param(
      lambda rot, rm: (
        (
          rot in (0, 180)
          and rm[0] in (180, 270)
          or rot in (90, 270)
          and rm[0] in (90, 180)
        )
        != (bool(rm[1]) and not (rot + rm[0]) % 180)
      ),
      rot,
      inst_rot_mirror,
    )

    for is_name, part in enumerate(("number", "name")):
      # FIXME: save the metadata
      text = Param(
        lambda t, h: "" if h or t == "~" else t,
        name if is_name else num,
        pin_config[part]["hide"],
      )
      xoffset = Param(lambda x: (x, 0), pin_config[part]["xoffset"])
      yoffset = Param(
        lambda y, semiunrot: rotated((0, y), semiunrot),
        pin_config[part]["yoffset"],
        semiunrot,
      )
      args = {
        "justify": pin_config[part]["justify"],
        "vjustify": pin_config[part]["vjustify"],
        "rotate": semiunrot,
        "textcolor": f"pin{part[:3]}",
        "prop": svg.PROP_PIN_NAME if is_name else svg.PROP_PIN_NUMBER,
      }
      # Handle side swap (to ensure text is always at 0 or 90)
      svg.gstart(
        pos=Param(lambda x, s: x if s else (0, 0), xoffset, swap_side),
        rotate=Param(lambda s: 180 * s, swap_side),
      )
      args["justify"] = Param(
        lambda j, s: (j + 180) % 360 if s and j != "middle" else j,
        args["justify"],
        swap_side,
      )
      args["pos"] = Param(
        lambda x, y, s: y if s else translated(x, y),
        xoffset,
        yoffset,
        swap_side,
      )
      HasModifiers.fillsvgargs(self[part][0], args, diffs)
      # FIXME: not sure why this was disabled: , context + (self,))
      svg.text(text, **args)
      svg.gend()

    svg.gend()  # pin pos, rot, path, hide


class PinSheet(PlaceholderHandler):
  pass


class PinInst(PlaceholderHandler):
  pass


@sexp.handler("pin")
def pin_disambiguator(s):
  """the "pin" atom has three uses, which we disambiguate in order:
  1) a pin definition, whose first data will be an atom of the electrical type
  2) a sheet pin, whose second data will be an atom of the pin direction
  3) a pin instance
  Figure out which of the three we're dealing with and instantiate the subclass
  """
  if len(s) >= 2 and isinstance(s[1], sexp.Atom):
    return PinDef(s)
  elif len(s) >= 3 and isinstance(s[2], sexp.Atom):
    return PinSheet(s)
  return PinInst(s)


class SymbolBody(Drawable):
  """The body of a symbol definition"""

  LITERAL_MAP = {"body": 1}

  def __str__(self):
    return f"symbol body u{self.unit} v{self.variant}"

  def fillnetlist(self, netlister, diffs, context):
    if "pin" not in self:
      return
    for pin in self["pin"]:
      pin.fillnetlist(netlister, diffs, context + (self,))

  @property
  def unit(self):
    return int(self[0].split("_")[-2])

  @property
  def variant(self):
    return int(self[0].rpartition("_")[-1])


class SymbolDef(sexp.SExp):
  """A single library symbol entity, either in a library or cache"""

  LITERAL_MAP = {"libname": 1}
  UNIQUE = "libname"

  def __str__(self):
    return f"libsymbol '{self[0]}'"

  def fillnetlist(self, netlister, diffs, context, unit, variant):
    for body in self._get_bodies(diffs, context, unit, variant).v:
      body.fillnetlist(netlister, diffs, context + (self,))

  def fillsvg(self, svg, diffs, draw, context, unit=1, variant=1):
    for body in self._get_bodies(diffs, context, unit, variant).v:
      body.fillsvg(svg, diffs, draw, context + (self,))
    draw_props = draw & (Drawable.DRAW_PROPS | Drawable.DRAW_PROPS_PG)
    if draw_props:
      # FIXME: diffs
      sym = self._sym(diffs, context).v
      properties = {p.name: p for p in sym["property"]}
      if sym is not self:
        properties.update((p.name, p) for p in self["property"])
      for field in properties.values():
        field.fillsvg(svg, diffs, draw_props, context + (self,))

  def get_pins(self, diffs, context, variant=1):
    # Returns a dict of pin names and a list of pin numbers for each name
    # FIXME: we don't have sufficient context if there are alternates on units
    #        across multiple pages
    cacheentry = None
    if not diffs:
      # cache per refdes, as a simple way of handling alternates.
      if not hasattr(self, "_get_pins_cache"):
        self._get_pins_cache = {}
      for c in reversed(context):
        if hasattr(c, "refdes"):
          cacheentry = c.refdes(None, context).v
          break
      if cacheentry in self._get_pins_cache:
        return self._get_pins_cache[cacheentry]
    pins = {}
    for body in self._get_bodies(diffs, context, variant=variant).v:
      if "pin" not in body:
        continue
      for pin in body["pin"]:
        name = pin.name(diffs, context).v  # FIXME: diffs
        num = pin.num(diffs, context).v  # FIXME: diffs
        pins.setdefault(name, []).append(num)
    pins = Param(pins)
    if cacheentry is not None:
      self._get_pins_cache[cacheentry] = pins
    return pins

  def get_con_pin_coords(self, diffs, context, unit, variant=1):
    # Returns a set of coordinates where unconnected markers can appear
    # FIXME: diffs?
    pins = set()
    for body in self._get_bodies(diffs, context, unit, variant).v:
      if "pin" not in body:
        continue
      for pin in body["pin"]:
        if (
          not pin.hide(diffs).v
          and pin.get_type_style(diffs, context)[0].v != "no_connect"
        ):
          pins.update(pin.pts(diffs, context).v)
    return pins

  def show_unit(self, diffs, context):
    return self.num_units(diffs, context).map(lambda u: u > 1)

  def num_units(self, diffs, context):
    return Param(
      lambda s: max(b.unit for b in s["symbol"]) if "symbol" in s else 0,
      self._sym(diffs, context),
    )

  def num_variants(self, diffs, context):
    return Param(
      lambda s: max(b.variant for b in s["symbol"]) if "symbol" in s else 0,
      self._sym(diffs, context),
    )

  @sexp.uses("pin_names", "offset", "pin_numbers", "hide")
  def pin_config(self, diffs=None):
    defx = Decimal("0.508")
    cfg = {
      "name": {
        "xoffset": defx,
        "hide": False,
      },
      "number": {
        "xoffset": 0,
        "hide": False,
      },
    }
    if "pin_names" in self:
      cfg["name"]["xoffset"] = Param.ify(
        self["pin_names"][0].get("offset"), defx, diffs
      )
      cfg["name"]["hide"] = self["pin_names"][0].has_yes("hide", diffs)
    if "pin_numbers" in self:
      assert "xoffset" not in self["pin_numbers"][0]
      cfg["number"]["hide"] = self["pin_numbers"][0].has_yes("hide", diffs)
    return cfg

  @sexp.uses("duplicate_pin_numbers_are_jumpers", "jumper_pin_groups")
  def jumpers(self, diffs=None):
    # Returns a tuple of (bool(dupes are jumpers), pin groups)
    # FIXME: diffs
    return Param(
      (
        self.get("duplicate_pin_numbers_are_jumpers", [0])[0] == "yes",
        self.get("jumper_pin_groups", []),
      )
    )

  @sexp.uses("extends")
  def _sym(self, diffs, context):
    # Returns the true symbol (e.g., if extending, returns that one)
    # FIXME: diffs
    if "extends" in self:
      for c in reversed(context):
        if isinstance(c, SymLib):
          return Param(c.symbol(self["extends"][0][0]))
      raise Exception("extended symbol with no library in context")
    return Param(self)

  @sexp.uses("symbol")
  def _get_bodies(self, diffs, context, unit=None, variant=None):
    bodies = self._sym(diffs, context)
    return Param(SymbolDef._filter_bodies, bodies, unit, variant)

  @staticmethod
  def _filter_bodies(bodies, unit, variant):
    # FIXME: diffs
    if not isinstance(bodies, list):
      try:
        bodies = bodies["symbol"]
      except KeyError:
        bodies = ()
    if unit is None and variant is None:
      return bodies
    elif unit is None:
      return (b for b in bodies if b.variant in (0, variant))
    elif variant is None:
      return (b for b in bodies if b.unit in (0, unit))
    to_render = ((0, 0), (0, variant), (unit, 0), (unit, variant))
    return (b for b in bodies if (b.unit, b.variant) in to_render)


class SymbolInst(PlaceholderHandler):
  pass


@sexp.handler("symbol")
def symbol_disambiguator(s):
  """the "symbol" atom has three uses, which we disambiguate in order:
  1) a symbol instance, which contains a library refrence (lib_id)
  2) a symbol definition, which contains properties (property)
  3) a symbol body
  Figure out which of the three we're dealing with and instantiate the subclass
  """
  # We probably shouldn't assume correct ordering of subexpressions, so we have
  # to search the entire expression for a lib_id before we can look for property
  if any(isinstance(item, sexp.SExp) and item.type == "lib_id" for item in s):
    return SymbolInst(s)
  if any(isinstance(item, sexp.SExp) and item.type == "property" for item in s):
    return SymbolDef(s)
  return SymbolBody(s)


@sexp.handler("kicad_symbol_lib", "lib_symbols")
class SymLib(sexp.SExp):
  """Tracks a kicad_sym file or lib_symbols database"""

  UNIQUE = True

  def __str__(self):
    return "library"

  def symbols(self):
    return {s[0]: s for s in self["symbol"]}

  def symbol(self, name):
    for s in self["symbol"]:
      if s[0] == name:
        return s
    return None

  def sym_hash(self, name, cache=True):
    """calculates and returns the hash for a symbol, keeping track of the hash
    for later lookup. The hash is an integer.
    If cache is true, assumes the symbol library hasn't been modified.
    Returns 0 if the symbol cannot be found
    """
    self._hashcache = getattr(self, "_hashcache", {})
    sym = self.symbol(name)
    if sym is None:
      return 0
    if cache and hasattr(sym, "_hashcache"):
      h = sym._hashcache
    else:
      h = sym._hashcache = sym.hash()
    self._hashcache[h] = sym
    return h

  def hash_lookup(self, h):
    """returns the symbol for a previously-output sym_hash"""
    return getattr(self, "_hashcache", {}).get(
      int(h, 16) if isinstance(h, str) else int(h)
    )


def main(argv):
  """USAGE: kicad_sym.py [symfile [symname]]
  Reads a kicad_sym from stdin or symfile and renders a random symbol or the
  provided symname as an svg to stdout.
  """
  s = svg.Svg(theme="default")
  s.push_invert_y()
  path = argv[1] if len(argv) > 1 else None
  with open(path) if path else sys.stdin as f:
    data = sexp.parse(f.read())
  params = {
    "svg": s,
    "diffs": None,
    "draw": Drawable.DRAW_ALL,
    "context": (data[0],),
  }
  if len(argv) > 2:
    sym = data[0].symbols()[argv[2]]
  else:
    sym = random.choice(list(data[0].symbols().values()))
  params["unit"] = (
    int(argv[3])
    if len(argv) > 3
    else random.randint(1, sym.num_units(None, params["context"]).v)
  )
  params["variant"] = (
    int(argv[4])
    if len(argv) > 4
    else random.randint(1, sym.num_variants(None, params["context"]).v)
  )

  sym.fillsvg(**params)
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
