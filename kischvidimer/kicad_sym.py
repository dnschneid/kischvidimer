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

import operator
import random
import sys

from . import sexp, svg
from .diff import Param
from .kicad_common import Drawable, rotated, translated


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
      operator.or_,
      self.alternate(diffs, context),
      self["name"][0].param(),
    )

  def num(self, diffs, context):
    return self["number"][0].param()

  def get_type_style(self, diffs, context):
    # FIXME: diffs
    # FIXME: alternate can have diffs too
    alternate = self.alternate(diffs, context).v
    target = self
    if alternate is not None:
      for alt in self["alternate"]:
        if alternate == alt[0]:
          target = alt
          break
      # TODO: what to do when alternate is not found?
    return (target.param("type"), target.param("style"))

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
    # FIXME: diffs
    # FIXME: effects
    # FIXME: unconnected circle
    # FIXME: defaults?
    # FIXME: metadata (electrical type)

    name = self.name(diffs, context)
    num = self.num(diffs, context)
    pos = self["at"][0].pos(diffs)
    rot = self["at"][0].rot(diffs)
    semiunrot = Param(lambda r: rot % 180 - rot, rot)  # restricts to 0 or 90
    mirror = Param(lambda r: 1 if r in (0, 90) else -1, rot)
    length = Param.ify(self.get("length"), 0)

    svg.gstart(pos=pos, rotate=rot, path=num, hidden=self.hide(diffs))

    # Compensate for mirror/rotation in instantiations
    inst_mirror = None
    inst_rot = 0
    for c in context:
      if c.type == "symbol" and "lib_id" in c:
        inst_rot, inst_mirror = c.rot_mirror(diffs)
        break

    # Render line ending
    flipy = Param(
      lambda m, r: -1
      if (m, r)
      in (
        (None, 180),
        (None, 270),
        ("x", 0),
        ("x", 270),
      )
      else 1,
      inst_mirror,
      inst_rot,
    )

    typ, style = self.get_type_style(diffs, context)
    # no_connect type supersedes all styles
    nc = typ == "no_connect"
    if not nc and "clock" in style:
      # draw clk carrot
      svg.polyline(
        xys=[
          (length, 0.635),
          (length + 0.635, 0),
          (length, -0.635),
        ],
        color="device",
      )
    if not nc and "inverted" in style:
      # draw dot
      svg.circle(
        pos=(length - 0.635, 0),
        radius=0.635,
        color="device",
      )
    if nc:
      # draw X at pin (supersedes non_logic style)
      xys = [
        (-0.381, -0.381),
        (0.381, 0.381),
        (0, 0),
        (0.381, -0.381),
        (-0.381, 0.381),
        (0, 0),
        (length, 0),
      ]
    elif style == "non_logic":
      # draw X at end of line (not pin)
      xys = [
        (0, 0),
        (length, 0),
        (length - 0.635, -0.635),
        (length + 0.635, 0.635),
        (length, 0),
        (length + 0.635, -0.635),
        (length - 0.635, 0.635),
      ]
    elif style in ("input_low", "clock_low", "edge_clock_high"):
      # draw input-low
      end = (length, 0)
      xys = [
        (0, 0),
        end,
        translated(end, rotated((-1.27 * mirror, 1.27 * flipy), semiunrot)),
        translated(end, rotated(-1.27 * mirror, semiunrot)),
      ]
    elif style == "output_low":
      # draw output-low
      end = (length, 0)
      xys = [
        (0, 0),
        end,
        translated(end, rotated(-1.27 * mirror, semiunrot)),
        translated(end, rotated((0, 1.27 * flipy), semiunrot)),
      ]
    else:
      dot = not nc and "inverted" in style
      xys = [(0, 0), (length - 1.27 * dot, 0)]
    # Render main line
    svg.polyline(xys=xys, color="device")

    # Render name and number
    pin_config = next(
      c.pin_config(diffs) for c in reversed(context) if isinstance(c, SymbolDef)
    )
    yoffset = -0.1016 - svg.THICKNESS["wire"]
    pin_config["number"]["xoffset"] = length / 2
    pin_config["number"]["yoffset"] = yoffset
    pin_config["number"]["justify"] = "middle"
    if pin_config["name"]["hide"] or pin_config["name"]["xoffset"]:
      pin_config["name"]["xoffset"] = length + pin_config["name"]["xoffset"]
      pin_config["name"]["yoffset"] = 0
      pin_config["name"]["justify"] = rot
      pin_config["name"]["vjustify"] = "middle"
      pin_config["number"]["vjustify"] = "bottom"
    else:
      # pin number below line
      pin_config["name"]["xoffset"] = length / 2
      pin_config["name"]["yoffset"] = yoffset
      pin_config["name"]["vjustify"] = "bottom"
      pin_config["name"]["justify"] = "middle"
      pin_config["number"]["vjustify"] = "top"
    # pin name should always be top/left, pin number below/right.
    # text should always be facing up/left
    swap_side = False
    if (
      rot in (0, 180)
      and inst_rot in (180, 270)
      or rot in (90, 270)
      and inst_rot in (90, 180)
    ):
      swap_side = True
    if inst_mirror and not (rot + inst_rot) % 180:
      swap_side = not swap_side

    for is_name, part in enumerate(("number", "name")):
      # FIXME: save the metadata
      text = Param(
        lambda t, h: "" if h or t == "~" else t,
        name if is_name else num,
        pin_config[part]["hide"],
      )
      xoffset = (pin_config[part]["xoffset"], 0)
      yoffset = rotated((0, pin_config[part]["yoffset"]), semiunrot)
      args = {
        "justify": pin_config[part]["justify"],
        "vjustify": pin_config[part]["vjustify"],
        "rotate": semiunrot,
        "textcolor": f"pin{part[:3]}",
        "prop": svg.PROP_PIN_NAME if is_name else svg.PROP_PIN_NUMBER,
      }
      svg.gstart(pos=xoffset if swap_side else (0, 0), rotate=180 * swap_side)
      if swap_side:
        if args["justify"] != "middle":
          args["justify"] = (args["justify"] + 180) % 360
        args["pos"] = yoffset
      else:
        args["pos"] = translated(xoffset, yoffset)
      args.update(
        Drawable.svgargs(self[part][0], diffs)
      )  # , context + (self,)))
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
      sym = self._sym(diffs, context)
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
        name, num = pin.name_num(diffs, context)
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
          not pin.hide(diffs)
          and pin.get_type_style(diffs, context)[0] != "no_connect"
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
    cfg = {
      "name": {
        "xoffset": 0.508,
        "hide": False,
      },
      "number": {
        "xoffset": 0,
        "hide": False,
      },
    }
    if "pin_names" in self:
      if "offset" in self["pin_names"][0]:
        cfg["name"]["xoffset"] = float(self["pin_names"][0]["offset"][0][0])
      if self["pin_names"][0].has_yes("hide", diffs).v:
        cfg["name"]["hide"] = True
    if "pin_numbers" in self:
      assert "offset" not in self["pin_numbers"][0]
      if self["pin_numbers"][0].has_yes("hide", diffs).v:
        cfg["number"]["hide"] = True
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
    "diffs": [],
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
