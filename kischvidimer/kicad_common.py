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
Common classes and routines for handling sexp-based kicad files
"""

import math
import os
import re
import sys
from uuid import uuid4

from . import sexp
from .diff import Comparable

# hack for keyword testing
if __name__ == "__main__":
  sexp.handler._handlers.clear()

# FIXME: (uuid "7d02517e-895f-4725-8c48-050f5414907e")


class HasUUID:
  @sexp.uses("uuid")
  def uuid(self, generate=False):
    if "uuid" in self:
      return self["uuid"][0][0]
    gen = getattr(self, "__uuidcache", None)
    if gen or not generate:
      return gen
    self.__uuidcache = gen = str(uuid4())
    return gen


@sexp.handler("version")
class Version(sexp.SExp, Comparable):
  """File version"""

  MIN_VERSION = 20220000  # kicad 6.99
  MAX_VERSION = 20250114  # kicad 9

  def __init__(self, s):
    super().__init__(s)

  @property
  def is_supported(self):
    return self.MIN_VERSION <= self.data[0] <= self.MAX_VERSION


def unit_to_alpha(unit):
  # FIXME: is this correct?
  alpha = ""
  while unit:
    unit -= 1
    alpha = chr(ord("A") + unit % 26) + alpha
    unit //= 26
  return alpha


def instancedata(field, diffs, context, default=None):
  # FIXME diffs!!!!
  project = None
  path = None
  sheet = None
  for c in reversed(context):
    if c.type == "path":
      path = c
    elif c.type == "sheet":
      sheet = c
    elif c.type == "~project":
      project = c
  if path is None:
    return default
  uuid = path.uuid(sheet)
  for c in reversed(context):
    if "instances" in c:
      inst = c["instances"][0].paths(project).get(uuid)
      if inst and field in inst:
        return inst[field][0][0]
  return default


def draw_uc_at(svg, pos, color):
  # FIXME: handle diffs
  sz = 0.3  # FIXME: number?
  svg.rect(
    pos=(float(pos[0]) - sz, float(pos[1]) - sz),
    width=sz * 2,
    height=sz * 2,
    color=color,
    thick="ui",
  )


def translated(pos, offset):
  if not isinstance(offset, tuple):
    offset = (offset, 0)
  return (float(pos[0]) + float(offset[0]), float(pos[1]) + float(offset[1]))


def rotated(pos, deg=None, rad=None):
  if not isinstance(pos, tuple):
    pos = (float(pos), 0)
  else:
    pos = (float(pos[0]), float(pos[1]))
  if rad is None:
    rad = math.radians(deg)
  cos = math.cos(rad)
  sin = math.sin(rad)
  return (pos[0] * cos - pos[1] * sin, pos[1] * cos + pos[0] * sin)


@sexp.uses("ltcorner", "lbcorner", "rbcorner", "rtcorner")
def rel_coord(gravity, rel=None, pos=None, vect=None):
  """Calculates a relative coordinate or vector based on a gravity and the
  relative pos/size."""
  ret = vect if pos is None else pos
  if gravity[0] == "l":
    if pos is not None:
      ret = (rel[0] + ret[0], ret[1])
  elif gravity[0] == "r":
    ret = (-ret[0], ret[1]) if pos is None else (rel[2] - ret[0], ret[1])
  if gravity[1] == "t":
    if pos is not None:
      ret = (ret[0], rel[1] + ret[1])
  elif gravity[1] == "b":
    ret = (ret[0], -ret[1]) if pos is None else (ret[0], rel[3] - ret[1])
  return ret


@sexp.handler("at", "center", "end", "mid", "offset", "pos", "start", "xy")
class Coord(sexp.SExp, Comparable):
  """A set of offset or coordinates, and sometimes rotation"""

  def pos(self, diffs=None, rel=None, defgravity="lt"):
    # FIXME: diffs
    pos = (self.data[0], self.data[1] if len(self.data) >= 2 else 0)
    if rel is not None:
      return rel_coord(self.gravity(defgravity), rel, pos=pos)
    return pos

  def gravity(self, default="lt"):
    return (
      self.data[2]
      if len(self.data) > 2 and sexp.is_atom(self.data[2])
      else default
    )

  def rot(self, diffs=None, context=None):
    if len(self.data) < 3:
      return 0
    rot = self.data[2]
    # see SCH_IO_KICAD_SEXPR_PARSER::parseText()
    return rot if rot < 360 else rot / 10

  # def rotated(self, rot, diffs=None):
  #  pos = self.pos(diffs)
  #  cos = math.cos(math.radians(rot))
  #  sin = math.sin(math.radians(rot))
  #  rotated = (pos[0]*cos - pos[1]*sin, pos[1]*cos + pos[0]*sin)
  #  #if flip:
  #  #  rotated = (-rotated[0], rotated[1])
  #  return rotated


class Drawable(sexp.SExp, Comparable):
  DRAW_WKS = 1 << 0  # worksheet
  DRAW_WKS_PG = 1 << 1  # page-specific worksheet elements
  DRAW_IMG = 1 << 2
  DRAW_BG = 1 << 3
  DRAW_SYMBG = 1 << 4
  DRAW_PINS = 1 << 5
  DRAW_SYMFG = DRAW_PINS  # context: schematic, includes pins/fg/text
  DRAW_TEXT_PG = 1 << 6  # page-specific text (variables)
  DRAW_PROPS_PG = 1 << 7  # page-specific props (variables, refdes)
  DRAW_FG_PG = 1 << 8  # page-specific foreground elements
  DRAW_TEXT = 1 << 9
  DRAW_PROPS = 1 << 10
  DRAW_FG = 1 << 11
  DRAW_MODES = 12
  DRAW_ALL = (1 << DRAW_MODES) - 1
  DRAW_SEQUENCE = tuple(1 << i for i in range(DRAW_MODES))
  DRAW_STAGE_COMMON_BG = DRAW_WKS | DRAW_IMG | DRAW_BG
  DRAW_STAGE_PAGE_SPECIFIC = (
    DRAW_WKS_PG
    | DRAW_SYMBG
    | DRAW_SYMFG
    | DRAW_TEXT_PG
    | DRAW_PROPS_PG
    | DRAW_FG_PG
  )
  DRAW_STAGE_COMMON_FG = DRAW_TEXT | DRAW_PROPS | DRAW_FG

  def fillsvg(self, svg, diffs, draw=DRAW_ALL, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    for subdraw in Drawable.DRAW_SEQUENCE:
      if draw & subdraw:
        for item in self.data:
          if isinstance(item, Drawable):
            item.fillsvg(svg, diffs, subdraw, context)

  def svgargs(self, diffs, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    args = {}
    for item in self.data:
      if isinstance(item, Modifier):
        args.update(item.svgargs(diffs, context))
    return args

  def fillvars(self, variables, diffs, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    for item in self.data:
      if isinstance(item, Drawable):
        item.fillvars(variables, diffs, context)

  def fillnetlist(self, netlister, diffs, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    for item in self.data:
      if isinstance(item, Drawable):
        item.fillnetlist(netlister, diffs, context)


class Modifier(sexp.SExp, Comparable):
  def svgargs(self, diffs, context):
    return {}


@sexp.handler("effects")
class Effects(Modifier):
  """font effects"""

  @sexp.uses("font", "size")
  def get_size(self, diffs):
    # FIXME: diffs
    if "font" in self and "size" in self["font"][0]:
      size = self["font"][0]["size"][0]
      assert size[0] == size[1]
      return size[0]
    # FIXME: default size?
    return None

  @sexp.uses("font", "color")
  def get_color(self, diffs):
    # FIXME: diffs
    if "font" in self and "color" in self["font"][0]:
      color = self["font"][0]["color"][0]
      return tuple(color.data)
    # FIXME: default size?
    return None

  @sexp.uses("font", "bold", "italic")
  def get_style(self, diffs):
    # FIXME: diffs
    if "font" in self:
      bold = "bold" in self["font"][0]
      italic = "italic" in self["font"][0]
      return (bold, italic)
    return (False, False)

  @sexp.uses("justify", "left", "right", "top", "bottom")
  def get_justify(self, diffs):
    # FIXME: diffs
    lr = "middle"
    tb = "middle"
    if "justify" in self:
      lr = "left" if "left" in self["justify"][0] else lr
      lr = "right" if "right" in self["justify"][0] else lr
      tb = "top" if "top" in self["justify"][0] else tb
      tb = "bottom" if "bottom" in self["justify"][0] else tb
    return (lr, tb)

  @sexp.uses("hide")
  def get_hidden(self, diffs):
    # FIXME: diffs
    return "hide" in self

  @sexp.uses("href", "mirror")
  def svgargs(self, diffs, context):
    """Returns a dict of arguments to Svg.text"""
    args = {}
    args["justify"], args["vjustify"] = self.get_justify(diffs)
    args["size"] = self.get_size(diffs)
    args["color"] = self.get_color(diffs)
    args["hidden"] = self.get_hidden(diffs)
    args["bold"], args["italic"] = self.get_style(diffs)

    if "href" in self:
      args["url"] = self["href"][0][0]

    # Remove defaults so that calling function can easily override if desired
    if args["justify"] == "middle":
      del args["justify"]
    if args["vjustify"] == "middle":
      del args["vjustify"]
    if args["size"] is None:
      del args["size"]
    if args["color"] is None:
      del args["color"]
    if not args["hidden"]:
      del args["hidden"]

    # Handle mirror/rotation causing justify to flip
    # Some nodes have their own implementation, so drop out early
    if context[-1].type in ("pin", "name", "number"):
      return args

    # FIXME: confirm this actually works as intended
    # FIXME: move this into Field, and a modified version for text?
    flipx = flipy = False
    inst_rot = inst_mirror = False
    rot = args.get("rotate", 0)
    for c in context:
      # Special-case for text in symbols, which over-rotate if the instance is
      # included. This works because normally text doesn't have a symbol in its
      # context.
      # FIXME: clean this up.
      if c.type == "symbol" and "lib_id" in c and context[-1].type == "text":
        inst_rot, inst_mirror = c.rot_mirror(diffs)
        continue
      if "mirror" in c:
        if c["mirror"][0][0] == "x":
          flipy = not flipy
        if c["mirror"][0][0] == "y":
          flipx = not flipx
      if "at" in c and "label" not in c.type:
        rot += c["at"][0].rot(diffs, context) * (-1 if flipy != flipx else 1)
    rot = rot % 360
    spin = False
    if (
      rot in (0, 180)
      and inst_rot in (180, 270)
      or rot in (90, 270)
      and inst_rot in (90, 180)
    ):
      spin = True
    if inst_mirror and not (rot + inst_rot) % 180:
      spin = not spin
    if rot in (180, 270) != spin:
      flipx = not flipx
      flipy = not flipy
    if "justify" in args and flipx:
      args["justify"] = "right" if args["justify"] == "left" else "left"
    if "vjustify" in args and flipy:
      args["vjustify"] = "bottom" if args["vjustify"] == "top" else "top"
    args["rotate"] = (rot % 180 + 180 * spin) % 360
    return args


@sexp.handler("stroke", "default")
class Stroke(Modifier):
  """stroke effects"""

  @sexp.uses("width", "type", "color")
  def svgargs(self, diffs, context):
    args = {}
    if "width" in self and self["width"][0][0]:
      args["thick"] = self["width"][0][0]
    if "type" in self:
      args["pattern"] = self["type"][0][0]
    if "color" in self:
      stroke = tuple(self["color"][0].data)
      if any(stroke):
        args["color"] = stroke
    if "type" in self and self["type"][0][0] != "default":
      args["pattern"] = self["type"][0][0]
    return args


@sexp.handler("fill")
class Fill(Modifier):
  """fill properties"""

  @sexp.uses("background", "color")
  def svgargs(self, diffs, context):
    args = {}
    fill = None
    if "type" in self:
      fill = self["type"][0][0]
      if fill == "background":
        fill = "device_background"
    if fill == "color" or "color" in self:
      fill = tuple(self["color"][0].data)
    if any(fill):
      args["fill"] = fill
    return args


@sexp.handler("polyline")
class Polyline(Drawable):
  """Graphical polyline"""

  @sexp.uses("pts")
  def pts(self, diffs):
    # FIXME: diffs
    return [(xy[0], xy[1]) for xy in self["pts"][0]["xy"]]

  def fillsvg(self, svg, diffs, draw, context, tag=None):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    # Don't try to render background only if there are only two points?
    # FIXME: diffs?
    xys = self.pts(diffs)
    if not draw & Drawable.DRAW_FG and len(xys) <= 2:
      return
    default_color = "notes"
    default_thick = "wire"
    if self.type == "wire":
      default_color = "wire"
    if self.type == "bus":
      default_color = "bus"
      default_thick = "bus"
    elif context[-1].type == "symbol":
      default_color = "device"
    args = {
      "xys": xys,
      "color": default_color,
      "thick": default_thick,
      "fill": "none",
    }
    if tag is not None:
      args["tag"] = tag
    args.update(self.svgargs(diffs, context))
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
    if not draw & Drawable.DRAW_BG:
      args["fill"] = "none"
    svg.polyline(**args)


@sexp.handler("arc")
class Arc(Drawable):
  """Graphical arc"""

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "start": self["start"][0].pos(diffs),
      "mid": self["mid"][0].pos(diffs),
      "stop": self["end"][0].pos(diffs),
      "color": "device" if context[-1].type == "symbol" else "notes",
      "thick": "wire",
    }
    args["fill"] = f"{args['color']}_background"
    args.update(self.svgargs(diffs, context))
    if draw & Drawable.DRAW_BG:
      thick = args["thick"]
      args["thick"] = 0
      svg.arc(**args)
      args["thick"] = thick
    if draw & Drawable.DRAW_FG:
      args["fill"] = "none"
      svg.arc(**args)


@sexp.handler("circle")
class Circle(Drawable):
  """Graphical circle"""

  @sexp.uses("radius")
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "pos": self["center"][0].pos(diffs),
      "radius": self["radius"][0][0],
      "color": "device" if context[-1].type == "symbol" else "notes",
    }
    args["fill"] = f"{args['color']}_background"
    args.update(self.svgargs(diffs, context))
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
    if not draw & Drawable.DRAW_BG:
      args["fill"] = "none"
    svg.circle(**args)


@sexp.handler("rectangle")
class Rectangle(Drawable):
  """Graphical rectangle"""

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & (Drawable.DRAW_BG | Drawable.DRAW_FG):
      return
    args = {
      "pos": self["start"][0].pos(diffs),
      "end": self["end"][0].pos(diffs),
      "color": "device" if context[-1].type == "symbol" else "notes",
    }
    args["fill"] = f"{args['color']}_background"
    args.update(self.svgargs(diffs, context))
    if not draw & Drawable.DRAW_FG:
      args["thick"] = 0
    if not draw & Drawable.DRAW_BG:
      args["fill"] = "none"
    svg.rect(**args)


@sexp.handler("text")
class Text(Drawable):
  """Graphical text"""

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    is_pg = "${" in self[0]
    if not draw & (Drawable.DRAW_TEXT_PG if is_pg else Drawable.DRAW_TEXT):
      return
    args = {
      "text": Variables.v(context).expand(context + (self,), self[0]),
      "pos": self["at"][0].pos(diffs),
      "rotate": None,
      "color": "device" if context[-1].type == "symbol" else "notes",
    }
    args.update(self.svgargs(diffs, context))
    svg.text(**args)


@sexp.handler("text_box")
class TextBox(Drawable):
  """Graphical text, but in a box!"""

  @sexp.uses("margins")
  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    is_pg = "${" in self[0]
    args = {
      "rotate": None,
      "color": "notes",
      "fill": "none",
      "thick": "wire",
    }
    args.update(self.svgargs(diffs, context))
    # left, top, right, bottom
    margins = [args["size"] * 4 / 5] * 4
    if "margins" in self:
      margins = self["margins"][0].data
    pos = self["at"][0].pos(diffs)
    size = self["size"][0].data
    if size[0] < 0:
      pos = (pos[0] + size[0], pos[1])
      size = (-size[0], size[1])
    if size[1] < 0:
      pos = (pos[0], pos[1] + size[1])
      size = (size[0], -size[1])
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_BG):
      rargs = {
        x: args[x] for x in ("color", "fill", "thick", "pattern") if x in args
      }
      rargs["pos"] = pos
      rargs["width"], rargs["height"] = size
      # stroke of <0 means no border. stroke of 0 means default
      if not isinstance(rargs["thick"], str) and rargs["thick"] < 0:
        rargs["thick"] = 0
      if not draw & Drawable.DRAW_FG:
        rargs["thick"] = 0
      if not draw & Drawable.DRAW_BG:
        rargs["fill"] = "none"
      svg.rect(**rargs)
    if draw & (Drawable.DRAW_TEXT_PG if is_pg else Drawable.DRAW_TEXT):
      # halve the right margin to account for character spacing
      wrapwidth = size[0] - margins[0] - margins[2] / 2
      text = Variables.v(context).expand(context + (self,), self[0])
      lines = []
      # wrap rules: only wrap on space and don't split words.
      # sequential spaces can cause additional wraps.
      # wrapped lines are trimmed to the first non-space.
      for src in text.split("\n"):
        trim = False
        words = src.split(" ")
        line = words[0]
        for word in words[1:]:
          if svg.calcwidth(f"{line} {word}", args["size"]) > wrapwidth:
            lines.append(line)
            line = word
            trim = True
          elif word and trim:
            line = f"{line} {word}".lstrip(" ")
          else:
            line = f"{line} {word}"
        lines.append(line)
      args["text"] = text = "\n".join(lines)
      tpos = (pos[0] + size[0] / 2, pos[1] + size[1] / 2)
      if args.get("justify") == "left":
        tpos = (pos[0] + margins[0], tpos[1])
      elif args.get("justify") == "right":
        tpos = (pos[0] + size[0] - margins[2], tpos[1])
      if args.get("vjustify") == "top":
        tpos = (tpos[0], pos[1] + margins[1])
      elif args.get("vjustify") == "bottom":
        tpos = (tpos[0], pos[1] + size[1] - margins[3])
      targs = {
        x: args[x]
        for x in (
          "text",
          "size",
          "color",
          "justify",
          "vjustify",
          "rotate",
          "hidden",
        )
        if x in args
      }
      targs["pos"] = tpos
      svg.text(**targs)


@sexp.handler("property")
class Field(Drawable):
  """Properties/fields in labels, sheets, and symbols"""

  @property
  def name(self):
    return self[0]

  @property
  def value(self):
    return self[1]

  def fillvars(self, variables, diffs, context):
    variables.define(context + (self,), self.name, self.value)
    super().fillvars(variables, diffs, context)

  @sexp.uses("show_name", "hide")
  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs...
    prop = self.name
    text = self.value
    is_pg = "${" in text or (prop.lower() in ("reference", "sheetname"))
    if not draw & (Drawable.DRAW_PROPS_PG if is_pg else Drawable.DRAW_PROPS):
      return
    show_name = "show_name" in self  # TODO: is this ever "no"?
    if show_name:
      text = f"{prop}: {text}"
    url = None
    if prop == "Reference":
      color = "referencepart"
      text = instancedata("reference", diffs, context, text)
      if context[-1].show_unit(diffs, context):
        text += unit_to_alpha(instancedata("unit", diffs, context, 0))
    elif prop == "Value":
      color = "valuepart"
    elif prop == "Intersheetrefs":
      color = "intersheet_refs"
    elif prop == "Sheetname":
      color = "sheetname"
      url = Variables.v(context).resolve(context, "SHEETPATH")
      if url:
        url = "#" + url.rstrip("/")
    elif prop == "Sheetfile":
      color = "sheetfilename"
      if not show_name:
        text = f"File: {text}"
    elif all(c.type != "symbol" for c in context):
      color = "sheetfields"
    else:
      color = "fields"
    pos = self["at"][0].pos(diffs)
    # Properties of labels are rendered with offsets defined by the label type
    if hasattr(context[-1], "get_text_offset"):
      pos = translated(
        pos, context[-1].get_text_offset(diffs, context, is_field=True)
      )
    text = Variables.v(context).expand(context + (self,), text)
    if not url and text.startswith(("http://", "https://")):
      url = text.partition(" ")[0]
    args = {
      "text": text,
      "prop": prop,
      "pos": pos,
      "rotate": 0,
      "color": color,
      "url": url,
      "hidden": "hide" in self,
    }
    args.update(self.svgargs(diffs, context))
    svg.text(**args)

  @staticmethod
  def getprop(parent, name, default=None):
    if "property" not in parent:
      return default
    for p in parent["property"]:
      if p.name == name:
        return p.value
    return default


@sexp.handler("image")
class Image(Drawable):
  """An image!"""

  """ (image (at ) (scale x) (uuid ) (data "base64?" "base64?" ) )"""

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_IMG:
      return
    pos = self["at"][0].pos(diffs)
    scale = self.get("scale", default=[1])[0]
    data = "".join(self["data"][0].data)
    svg.image(data, pos, scale)


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
  RE_VAR = re.compile(r"\${([^}:]+:)?([^}]+)}")

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
        return min(c for c in self._contexts if c.endswith(context))
      return context
    elements = [""]
    for c in context:
      if c.type == "path":
        elements = [c.uuid()]
      elif isinstance(c, HasUUID):
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
    return Variables.RE_VAR.sub(
      lambda m: self.resolve(context, m, hist or set()), text
    )

  def resolve(self, context, variable, hist=None):
    """Variable can be x, x:y, or a match object.
    If the variable isn't found, returns None if variable was a string, or the
    full match text if the variable is a match object.
    """
    # FIXME: support querying the netlist
    hist = hist or set()
    orig_variable = None
    if isinstance(variable, re.Match):
      orig_variable = variable[0]
      if variable[1]:
        context = variable[1][:-1]
      variable = variable[2]
    elif ":" in variable:
      context, _, variable = variable.partition(":")
    context = self._resolve_context(context)
    while True:
      hist_entry = (context, variable)
      # If we've cycled, go up a level and continue if possible
      if hist_entry not in hist:
        hist.add(hist_entry)
        vardict = self._contexts.get(context, {})
        resolved = vardict.get(variable, vardict.get(variable.upper()))
        if resolved is not None:
          expanded = self.expand(context, resolved, hist)
          # Ensure the final page list for INTERSHEET_REFS is unique and sorted
          if variable == "INTERSHEET_REFS":
            try:
              return ",".join(sorted(set(expanded.split(",")), key=int))
            except ValueError:
              return ""
          return expanded
      if not context:
        return orig_variable
      context = context.rpartition("/")[0]


def main(argv):
  # Perform keyword checks to ensure all keywords are handled
  sexp.handler._handlers.clear()
  if "wks" in argv:
    from . import kicad_wks  # noqa: F401
  elif "sym" in argv:
    from . import kicad_sym  # noqa: F401
  else:
    # includes kicad_sym and kicad_wks
    from . import kicad_sch  # noqa: F401
  ret = 0
  for kwfile in argv[1:]:
    if not os.path.isfile(kwfile):
      continue
    with open(kwfile) as f:
      kws = sorted(line.strip() for line in f if line.strip())
    print(f"{kwfile}:")
    for kw in kws:
      if kw not in sexp.handler._handlers and kw not in sexp.uses._uses:
        print(f"  {kw}")
        ret = 1
  return ret


if __name__ == "__main__":
  sys.exit(main(sys.argv))
