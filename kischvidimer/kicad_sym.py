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

import sys

from . import sexp, svg
from .kicad_common import Comparable, Drawable, rotated, translated


class PlaceholderHandler(sexp.SExp):
  def __init__(self, s):
    raise Exception(f'found "{s[0]}" in an unexpected context')


class PinDef(Drawable):
  """pins in a symbol definition"""

  def name_num(self, diffs, context):
    # FIXME: alternate from context
    alternate = self.alternate(diffs, context)
    return (alternate or self["name"][0][0], self["number"][0][0])

  def get_type_style(self, diffs, context):
    # FIXME: diffs
    # FIXME: alternate can have diffs too
    alternate = self.alternate(diffs, context)
    if alternate is not None:
      for alt in self["alternate"]:
        if alternate == alt[0]:
          return (alt[1], alt[2])
      # TODO: what to do when alternate is not found?
    return (self[0], self[1])

  @sexp.uses("alternate")
  def alternate(self, diffs, context):
    if "alternate" not in self:
      return None
    num = self["number"][0][0]
    for c in reversed(context):
      if hasattr(c, "get_alternates"):
        alternates = c.get_alternates(diffs, context)
        return alternates.get(num)
    return None

  @sexp.uses("at")
  def pts(self, diffs, context):
    pos = self["at"][0].pos(diffs)
    for c in reversed(context):
      if hasattr(c, "transform_pin"):
        pos = c.transform_pin(pos, diffs)
        break
    return [pos]

  @sexp.uses("hide")
  def hide(self, diffs):
    # FIXME: diffs
    return "hide" in self

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
    if self.hide(diffs):
      return  # FIXME: render invisible?

    pos = self["at"][0].pos(diffs)
    rot = self["at"][0].rot(diffs)
    length = float(self.get("length", 0)[0])
    mirror = 1 if rot in (0, 90) else -1

    # Compensate for mirror/rotation in instantiations
    inst_mirror = None
    inst_rot = 0
    for c in context:
      if c.type == "symbol" and "lib_id" in c:
        inst_rot, inst_mirror = c.rot_mirror(diffs)
        break

    # Render line ending
    flipy = 1
    if (inst_mirror, inst_rot) in (
      (None, 180),
      (None, 270),
      ("x", 0),
      ("x", 270),
    ):
      flipy = -1
    style = self.get_type_style(diffs, context)[1]
    dot = "inverted" in style
    end = translated(pos, rotated(length - 1.27 * dot, rot))
    xys = [pos, end]
    if "clock" in style:
      # draw clk carrot
      svg.polyline(
        xys=[
          translated(end, rotated((1.27 * dot, 0.635), rot)),
          translated(end, rotated(1.27 * dot + 0.635, rot)),
          translated(end, rotated((1.27 * dot, -0.635), rot)),
        ],
        color="device",
      )
    elif style == "non_logic":
      # draw X
      xys += [
        (end[0] - 0.635, end[1] - 0.635),
        (end[0] + 0.635, end[1] + 0.635),
        end,
        (end[0] + 0.635, end[1] - 0.635),
        (end[0] - 0.635, end[1] + 0.635),
      ]
    if style.startswith("inverted"):
      # draw dot
      svg.circle(
        pos=translated(end, rotated(0.635, rot)),
        radius=0.635,
        color="device",
      )
    elif style in ("input_low", "clock_low", "edge_clock_high"):
      # draw input-low
      xys += [
        translated(end, rotated((-1.27 * mirror, 1.27 * flipy), rot % 180)),
        translated(end, rotated(-1.27 * mirror, rot % 180)),
      ]
    elif style == "output_low":
      # draw output-low
      xys += [
        translated(end, rotated(-1.27 * mirror, rot % 180)),
        translated(end, rotated((0, 1.27 * flipy), rot % 180)),
      ]
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

    name_num = self.name_num(diffs, context)
    for is_name, part in enumerate(("number", "name")):
      # FIXME: save the metadata
      text = name_num[not is_name]
      if pin_config[part]["hide"] or text == "~":
        text = ""
      xoffset = rotated(pin_config[part]["xoffset"], rot)
      yoffset = rotated((0, pin_config[part]["yoffset"]), rot % 180)
      args = {
        "justify": pin_config[part]["justify"],
        "vjustify": pin_config[part]["vjustify"],
        "rotate": rot % 180,
        "color": f"pin{part[:3]}",
        "prop": svg.PROP_PIN_NAME if is_name else svg.PROP_PIN_NUMBER,
      }
      if swap_side:
        svg.gstart(pos=translated(pos, xoffset), rotate=180)
        if args["justify"] != "middle":
          args["justify"] = (args["justify"] + 180) % 360
        args["pos"] = yoffset
      else:
        args["pos"] = translated(pos, translated(xoffset, yoffset))
      args.update(
        Drawable.svgargs(self[part][0], diffs)
      )  # , context + (self,)))
      svg.text(text, **args)
      if swap_side:
        svg.gend()


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
  Figure out which of the two we're dealing with and instantiate the subclass
  """
  if len(s) >= 2 and isinstance(s[1], sexp.Atom):
    return PinDef(s)
  elif len(s) >= 3 and isinstance(s[2], sexp.Atom):
    return PinSheet(s)
  return PinInst(s)


class SymbolBody(Drawable):
  """The body of a symbol definition"""

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


class SymbolDef(sexp.SExp, Comparable):
  """A single library symbol entity, either in a library or cache"""

  def fillnetlist(self, netlister, diffs, context, unit, variant):
    to_render = {(0, 0), (0, variant), (unit, 0), (unit, variant)}
    sym = self._sym(diffs, context)
    bodies = [b for b in sym["symbol"] if (b.unit, b.variant) in to_render]
    for body in bodies:
      body.fillnetlist(netlister, diffs, context + (self,))

  def fillsvg(self, svg, diffs, draw, context, unit=1, variant=1):
    to_render = {(0, 0), (0, variant), (unit, 0), (unit, variant)}
    sym = self._sym(diffs, context)
    bodies = [b for b in sym["symbol"] if (b.unit, b.variant) in to_render]
    for body in bodies:
      body.fillsvg(svg, diffs, draw, context + (self,))
    if draw & Drawable.DRAW_PROPS:
      properties = {p.name: p for p in sym["property"]}
      if sym is not self:
        properties.update((p.name, p) for p in self["property"])
      for field in properties.values():
        field.fillsvg(svg, diffs, Drawable.DRAW_TEXT, context + (self,))

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
          cacheentry = c.refdes([], context)
          break
      if cacheentry in self._get_pins_cache:
        return self._get_pins_cache[cacheentry]
    pins = {}
    sym = self._sym(diffs, context)
    bodies = [b for b in sym["symbol"] if b.variant in (0, variant)]
    for body in bodies:
      if "pin" not in body:
        continue
      for pin in body["pin"]:
        name, num = pin.name_num(diffs, context)
        pins.setdefault(name, []).append(num)
    if cacheentry is not None:
      self._get_pins_cache[cacheentry] = pins
    return pins

  def get_con_pin_coords(self, diffs, context, unit, variant=1):
    # Returns a list of coordinates where unconnected markers can appear
    pins = set()
    sym = self._sym(diffs, context)
    to_render = {(0, 0), (0, variant), (unit, 0), (unit, variant)}
    bodies = [b for b in sym["symbol"] if (b.unit, b.variant) in to_render]
    for body in bodies:
      if "pin" not in body:
        continue
      for pin in body["pin"]:
        if (
          not pin.hide(diffs)
          and pin.get_type_style(diffs, context)[0] != "no_connect"
        ):
          pins.update(pin.pts(diffs, context))
    return pins

  def show_unit(self, diffs, context):
    sym = self._sym(diffs, context)
    return max(b.unit for b in sym["symbol"]) > 1

  @sexp.uses("pin_names", "offset", "pin_numbers", "hide")
  def pin_config(self, diffs):
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
      if "hide" in self["pin_names"][0]:
        cfg["name"]["hide"] = True
    if "pin_numbers" in self:
      assert "offset" not in self["pin_numbers"][0]
      if "hide" in self["pin_numbers"][0]:
        cfg["number"]["hide"] = True
    return cfg

  @sexp.uses("extends")
  def _sym(self, diffs, context):
    # Returns the true symbol (e.g., if extending, returns that one)
    if "extends" in self:
      for c in reversed(context):
        if isinstance(c, SymLib):
          return c.symbol(self["extends"][0][0])
      raise Exception("extended symbol with no library in context")
    return self

  def __hash__(self):
    return hash(str(self))


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
class SymLib(sexp.SExp, Comparable):
  """Tracks a kicad_sym file or lib_symbols database"""

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
      h = sym._hashcache = hash(str(sym))
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
    data[0].symbols()[argv[2]].fillsvg(**params)
  else:
    import random

    random.choice(list(data[0].symbols().values())).fillsvg(**params)
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
