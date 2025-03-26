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

import os
import re
import sys

from . import kicad_sym, kicad_wks, sexp, svg
from .kicad_common import (
  Comparable,
  Drawable,
  Field,
  HasUUID,
  Polyline,
  Variables,
  draw_uc_at,
  instancedata,
  unit_to_alpha,
)
from .netlister import Netlister

# FIXME: check eeschema/schematic.keywords for completeness
#        on last check, there are around 79 unused atoms


@sexp.handler("title_block")
class TitleBlock(Drawable):
  """title_block"""

  @property
  @sexp.uses("title")
  def title(self):
    return self["title"][0][0] if "title" in self else None

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    svg.instantiate_worksheet(draw, context)

  @sexp.uses(
    "company",
    "comment",
    "title",
    "paper",
    "generator",
    "generator_version",
    "rev",
  )
  def fillvars(self, variables, diffs, context):
    # Fill in all variable defaults
    missing_vars = set(kicad_wks.ALL_WKS_VARS)
    for var in self.data:
      name = str(var.type)
      if name == "date":
        name = "ISSUE_DATE"
      if len(var.data) > 1:
        name += "".join(str(s) for s in var.data[:-1])
      name = name.upper()
      missing_vars.remove(name)
      variables.define(context + (self,), name, var.data[-1])
    for name in missing_vars:
      text = ""
      if name == "PAPER":
        for c in reversed(context):
          if c.type == "kicad_sch":
            text = c.get("paper", default=["A4"])[0]
            break
      elif name == "KICAD_VERSION":
        for c in reversed(context):
          if c.type == "kicad_sch":
            text = " ".join(
              (
                c.get("generator", default=["unknown"])[0],
                c.get(
                  "generator_version",
                  default=[
                    str(c.get("version", default=["version unknown"])[0])
                  ],
                )[0],
                "(rendered by kischvidimer)",
              )
            )
            break
      variables.define(context + (self,), name, text)
    # For whatever reason, wks uses REVISION but it references REV
    if variables.resolve(context + (self,), "REVISION") is None:
      variables.define(context + (self,), "REVISION", "${REV}")
    super().fillvars(variables, diffs, context)


@sexp.handler("junction")
class Junction(Drawable):
  """junction"""

  def fillnetlist(self, netlister, diffs, context):
    # TODO: at some point, confirm that there's only one valid net/bus
    self.netbuses = netlister.add_junction(context, self)

  @sexp.uses("at")
  def pts(self, diffs):
    return [self["at"][0].pos(diffs)]

  @sexp.uses("diameter")
  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: this has a false per-page dependency due to netlist queries.
    #        Can this be resolved?
    if not draw & Drawable.DRAW_FG_PG:
      return
    # FIXME: diffs
    pos = self.pts(diffs)[0]
    diameter = sexp.Decimal(0.915)
    if "diameter" in self and self["diameter"][0][0]:
      diameter = self["diameter"][0][0]
    color = "junction"
    try:
      if Netlister.n(context).get_node_count(context, pos, is_bus=True) > 0:
        color = "bus_junction"
    except KeyError:
      pass
    if "color" in self and any(self["color"][0].data):
      color = self["color"][0].data
    svg.circle(
      pos,
      radius=diameter / 2,
      color="none",
      fill=color,
      tag=svg.getuid(self),
    )


@sexp.handler("no_connect")
class NoConnect(Drawable):
  """no_connect"""

  def fillnetlist(self, netlister, diffs, context):
    self.netbuses = netlister.add_nc(context, self)

  @sexp.uses("at")
  def pts(self, diffs):
    return [self["at"][0].pos(diffs)]

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    # FIXME: diffs
    sz = sexp.Decimal(0.6350)
    pos = self.pts(diffs)[0]
    xys = [
      (pos[0] - sz, pos[1] - sz),
      (pos[0] + sz, pos[1] + sz),
      pos,
      (pos[0] + sz, pos[1] - sz),
      (pos[0] - sz, pos[1] + sz),
    ]
    svg.polyline(xys, color="noconnect", tag=svg.getuid(self))


# FIXME: (wire (pts (xy) (xy)) (stroke) (uuid))
@sexp.handler("wire", "bus")
class Wire(Polyline, HasUUID):
  """wire or bus"""

  def fillnetlist(self, netlister, diffs, context):
    self.netbus = netlister.add_wire(context, self)

  def fillsvg(self, svg, diffs, draw, context):
    super().fillsvg(svg, diffs, draw, context, tag=svg.getuid(self))
    if draw & Drawable.DRAW_FG_PG and self.type == "wire":
      for pos in self.pts(diffs):
        if Netlister.n(context).get_node_count(context, pos) == 1:
          draw_uc_at(svg, pos, color="wire")  # FIXME: not the correct color?


# FIXME: (hierarchical_label "x" (shape input) (at) (effects) (uuid) (property))
# FIXME: (label "x" (at) (effects) (uuid) (property))
# FIXME: (global_label "x" (shape input) (at) (effects) (uuid) (property))
@sexp.handler("global_label", "hierarchical_label", "label")
class Label(Drawable, HasUUID):
  """any type of label"""

  BUS_RE = re.compile(r"(?<![_~^$]){(?!slash})(.+)}|\[(\d+)[.][.](\d+)\]")

  def fillnetlist(self, netlister, diffs, context):
    self.netbus = netlister.add_label(context, self)

  def fillvars(self, variables, diffs, context):
    shape = self.shape(diffs)
    if shape is not None:
      variables.define(
        context + (self,),
        "CONNECTION_TYPE",
        "-".join(s.capitalize() for s in shape.split("-")),
      )
    variables.define(context + (self,), "OP", "--")
    """ FIXME:
    ${NET_CLASS} -> net class
    ${NET_NAME} -> connection name
    ${SHORT_NET_NAME} -> local name
    """
    super().fillvars(variables, diffs, context)

  @sexp.uses("shape")
  def shape(self, diffs):
    if self.type == "pin":
      return self[1]
    elif "shape" in self:
      return self["shape"][0][0]
    return None

  def net(self, diffs, context, display=False):
    return self[0].replace("{slash}", "/") if display else self[0]

  def bus(self, diffs, context):
    return Label.BUS_RE.search(self.net(diffs, context))

  def expand_bus(self, diffs, context):
    bus = self.bus(diffs, context)
    if not bus:
      return []
    prefix, suffix = bus.string[: bus.start()], bus.string[bus.end() :]
    # Suffix appears to get dropped silently in KiCad 8
    suffix = ""
    if bus.group(2) is not None:
      indices = (int(bus.group(2)), int(bus.group(3)))
      indices = (min(indices), max(indices) + 1)
      return [(prefix, str(n), f"{prefix}{n}{suffix}") for n in range(*indices)]
    if prefix:
      prefix += "."
    members = [m for m in bus.group(1).split(" ") if m]
    if len(members) == 1:
      for c in reversed(context):
        if "bus_alias" in c:
          for ba in c["bus_alias"]:
            if ba[0] == members[0]:
              members = ba["members"][0].data
              break
          else:
            continue
          break
    return [(prefix, m, f"{prefix}{m}{suffix}") for m in members]

  @sexp.uses("at")
  def pts(self, diffs):
    return (self["at"][0].pos(diffs),)

  @sexp.uses("bidirectional", "input", "output", "passive", "tri_state")
  def fillsvg(self, svg, diffs, draw, context):
    svg.gstart(tag=svg.getuid(self))
    args = pos = None
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_FG_PG):
      # FIXME: diffs
      args = {
        "size": 1.27,
      }
      if self.bus(diffs, context):
        args["color"] = "bus"
      elif self.type == "label":
        args["color"] = "loclabel"
      elif self.type == "pin":
        args["color"] = "sheetlabel"
      else:
        args["color"] = f"{self.type[:4]}label"
      if "color" in self and any(self["color"][0].data):
        args["color"] = self["color"][0].data
      args.update(self.svgargs(diffs, context))
      pos = self["at"][0].pos(diffs)
    if draw & Drawable.DRAW_FG:
      rot = self["at"][0].rot(diffs)
      shape = self.shape(diffs)
      dispnet = self.net(diffs, context, display=True)
      outline = None
      if self.type != "label":
        th = float(args["size"]) * svg.FONT_HEIGHT
        offset = float(th * 0.375)  # DEFAULT_LABEL_SIZE_RATIO
        yoffset = 0
        # Reference: sch_label.cpp: *::CreateGraphicShape
        if self.type == "global_label":
          yoffset = th * 0.0715  # from sch_label.cpp
          w = float(svg.calcwidth(dispnet, args["size"]))
          h = float(th * 1.5)  # from sch_label.cpp
          if shape == "input":
            offset += h / 2
            outline = [(0, 0), (h / 2, h / 2), (h + w, h / 2)]
          elif shape == "output":
            outline = [(0, h / 2), (h / 2 + w, h / 2), (h + w, 0)]
          elif shape in ("bidirectional", "tri_state"):
            offset += h / 2
            outline = [(0, 0), (h / 2, h / 2), (h + w, h / 2), (h * 1.5 + w, 0)]
          elif shape == "passive":
            outline = [(0, h / 2), (w + h / 2, h / 2)]
        elif self.type in ("hierarchical_label", "pin"):
          h = float(args["size"])
          offset += h
          if shape == "input":
            outline = [(0, 0), (h / 2, h / 2), (h, h / 2)]
          elif shape == "output":
            outline = [(0, h / 2), (h / 2, h / 2), (h, 0)]
          elif shape in ("bidirectional", "tri_state"):
            outline = [(0, 0), (h / 2, h / 2), (h, 0)]
          elif shape == "passive":
            outline = [(0, h / 2), (h, h / 2)]
          if self.type == "pin":
            offset *= -1
            for i, p in enumerate(outline):
              outline[i] = (p[0] - h, p[1])
        offset = (offset, yoffset)
      else:
        offset = (0, 0)
      # Outlines are symmetric across X
      if outline:
        for p in reversed(outline):
          if p[1]:
            outline.append((p[0], -p[1]))
        # close the path
        if outline[-1] != outline[0]:
          outline.append(outline[0])
      svg.gstart(pos=pos, rotate=rot)
      if outline:
        ocolor = args["color"]
        if isinstance(ocolor, str):
          ocolor = ocolor.replace("sheet", "hier")
        svg.polyline(
          outline,
          color=ocolor,
          thick=args["size"] / 8,
        )
      args["rotate"] = -180 * (rot >= 180)
      svg.text(dispnet, prop=svg.PROP_LABEL, pos=offset, **args)
      # FIXME: draw unconnected square on ends
      svg.gend()
    if (
      draw & Drawable.DRAW_FG_PG
      and Netlister.n(context).get_node_count(
        context, pos, is_bus=bool(self.bus(diffs, context))
      )
      == 1
    ):
      uc_color = args["color"]
      if isinstance(uc_color, str):
        uc_color = uc_color.replace("sheet", "hier")
      draw_uc_at(svg, pos, color=uc_color)  # FIXME: not the correct color?
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # tag


@sexp.handler("bus_entry")
class BusEntry(Drawable):
  """Instance of a bus entry"""

  def fillnetlist(self, netlister, diffs, context):
    # TODO: at some point confirm there's exactly one bus and one net
    self.netbuses = netlister.add_busentry(context, self)

  @sexp.uses("at", "size")
  def pts(self, diffs):
    pos = self["at"][0].pos(diffs)
    size = self["size"][0].data
    return [pos, (pos[0] + size[0], pos[1] + size[1])]

  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    # FIXME: diffs!
    pts = self.pts(diffs)
    args = {
      "p1": pts[0],
      "p2": pts[1],
      "color": "wire",
      "tag": svg.getuid(self),
    }
    args.update(self.svgargs(diffs, context))
    svg.line(**args)


# FIXME: (symbol (lib_id "x") (at) (unit 1) (property) (pin)
#          (instances (project "x" (path "y" (reference "z") (unit 1)))))
class SymbolInst(Drawable, HasUUID):
  """An instance of a symbol in a schematic"""

  def fillvars(self, variables, diffs, context):
    variables.define(context + (self,), "UNIT", self.unit(diffs, context, True))
    variables.define(context + (self,), "OP", "--")
    """ FIXME:
    ${ref:DNP} -> "DNP" or ""
    ${ref:EXCLUDE_FROM_BOARD} -> "Excluded from board" or ""
    ${ref:EXCLUDE_FROM_BOM} -> "Excluded from BOM" or ""
    ${ref:EXCLUDE_FROM_SIM} -> "Excluded from simulation" or ""
    ${ref:FOOTPRINT_LIBRARY} -> footprint field (prior to colon if present)
    ${ref:FOOTPRINT_NAME} -> footprint field, after colon. blank if no colon
    ${ref:NET_CLASS(<pin_number>)} -> net class of attached net to pin
    ${ref:NET_NAME(<pin_number>)} -> connection name of net attached to pin
    ${ref:PIN_NAME(<pin_number>)} -> name of the pin
    ${ref:SHORT_NET_NAME(<pin_number>)} -> local name of the net attached to pin
    ${ref:SYMBOL_DESCRIPTION} -> description from the library cache
    ${ref:SYMBOL_KEYWORDS} -> keywords from the library cache
    ${ref:SYMBOL_LIBRARY} -> library name
    ${ref:SYMBOL_NAME} -> symbol name
    """
    super().fillvars(variables, diffs, context)

  @sexp.uses("lib_name", "lib_id")
  def lib_id(self, diffs, context):
    # lib_name specifies page-local overrides of the original library symbol,
    # usually due to out-of-date symbol instances.
    return self.get("lib_name", default=self.get("lib_id"))[0]

  def get_alternates(self, diffs, context):
    if not diffs and hasattr(self, "_get_alternates_cache"):
      return self._get_alternates_cache
    if "pin" not in self:
      return {}
    context = context + (self,)
    alternates = {}
    for pin in self["pin"]:
      alternate = pin.get_alternate(diffs, context)
      if alternate is not None:
        alternates[pin.number] = alternate
    if not diffs:
      self._get_alternates_cache = alternates
    return alternates

  def fillnetlist(self, netlister, diffs, context):
    # Fill in all pins. Use Svg's transformation implementation
    lib = context[-1]["lib_symbols"][0]
    lib_id = self.lib_id(diffs, context)
    sym = lib.symbol(lib_id)
    unit = self.unit(diffs, context)
    convert = self.variant(diffs, context)
    sym.fillnetlist(
      netlister,
      diffs,
      context + (self,),
      unit=unit,
      variant=convert,
    )

  def transform_pin(self, pos, diffs):
    # Reimplementation of what's done in Svg with gstart.
    # This is done to keep things as Decimals. It's also pretty simple.
    # 1. invert y (since symbols have inverted y)
    x, y = pos[0], -pos[1]
    # 2. rotate
    rot = self.rot(diffs)
    if rot == 90:
      x, y = y, -x
    elif rot == 180:
      x, y = -x, -y
    elif rot == 270:
      x, y = -y, x
    else:
      assert not rot
    # 3. apply flip
    mirror = self.mirror(diffs)
    if mirror == "x":
      y = -y
    elif mirror == "y":
      x = -x
    # 4. apply translate
    trans = self["at"][0].pos(diffs)
    return (x + trans[0], y + trans[1])

  def fillsvg(self, svg, diffs, draw, context):
    # Decide what to draw
    subdraw = Drawable.DRAW_BG if draw & Drawable.DRAW_SYMBG else 0
    if draw & Drawable.DRAW_SYMFG:
      subdraw |= Drawable.DRAW_PINS | Drawable.DRAW_FG | Drawable.DRAW_TEXT
    svg.gstart(path=self.uuid(generate=True))
    if subdraw:
      # FIXME: diffs, of course
      lib = context[-1]["lib_symbols"][0]
      lib_id = self.lib_id(diffs, context)
      sym = lib.symbol(lib_id)
      pos = self["at"][0].pos(diffs)
      rot = self.rot(diffs)
      mirror = self.mirror(diffs)
      unit = self.unit(diffs, context)
      convert = self.variant(diffs, context)
      svg.gstart(
        pos=pos,
        rotate=rot,
        mirror=mirror,
        hidden=False,
      )
      svg.instantiate(
        subdraw, lib, lib_id, unit=unit, variant=convert, context=(self,)
      )
      # Draw unconnected circles
      if subdraw & Drawable.DRAW_PINS:  # FIXME: should this be DRAW_FG_PG?
        n = Netlister.n(context)
        # NOTE: context passed to sym (intentionally) does not include self, so
        #       returned pts will be untransformed
        for pos in sym.get_con_pin_coords(diffs, context, unit, convert):
          abs_pos = self.transform_pin(pos, diffs)
          if n.get_net(context, abs_pos).is_floating_sympin():
            pos = (pos[0], -pos[1])
            svg.circle(
              pos=pos,
              radius=sexp.Decimal(0.3175),
              fill="none",
              color="device",  # FIXME: not the correct color
              thick="ui",
            )
      svg.gend()
    super().fillsvg(svg, diffs, draw, context)
    svg.gend()  # path

  def show_unit(self, diffs, context):
    for c in reversed(context):
      if c.type == "kicad_sch":
        lib = c["lib_symbols"][0]
        lib_id = self.lib_id(diffs, context)
        return lib.symbol(lib_id).show_unit(diffs, context)
    return True

  def rot(self, diffs):
    return self["at"][0].rot(diffs)

  @sexp.uses("mirror")
  def mirror(self, diffs):
    return self.get("mirror", default=[None])[0]

  @sexp.uses("x", "y")
  def rot_mirror(self, diffs):
    # Returns a simplified rot + mirror, where mirror is never "y"
    mirror = self.mirror(diffs)
    rot = self.rot(diffs)
    if mirror == "y":
      rot = (rot + 180) % 360
      mirror = "x"
    return (rot, mirror)

  def refdes(self, diffs, context):
    return instancedata(
      "reference",
      diffs,
      context + (self,) if context else None,
      default=Field.getprop(self, "Reference", default="?"),
    )

  @sexp.uses("unit")
  def unit(self, diffs, context, as_alpha=False):
    unit = instancedata(
      "unit",
      diffs,
      context + (self,) if context else None,
      default=self.get("unit", default=[1])[0],
    )
    if not as_alpha:
      return unit
    return unit_to_alpha(unit)

  @sexp.uses("convert")
  def variant(self, diffs, context):
    return self.get("convert", default=[1])[0]

  def as_comp(self, context):
    # returns (refdes, {dict of properties, with chr(1) containing local uuid})
    props = {
      chr(1): self.uuid(generate=True),
      "Reference": self.refdes([], context),
    }
    if "property" not in self:
      return props["Reference"], props
    variables = Variables.v(context)
    for prop in self["property"]:
      name = prop.name
      value = variables.expand(context + (self,), prop.value)
      if (
        name
        and name != "Reference"
        and not name.lower().startswith("sim.")
        and value
        and value != "~"
      ):
        props[name] = value
    return props["Reference"], props


kicad_sym.SymbolInst = SymbolInst


class PinInst(sexp.SExp, Comparable, HasUUID):
  """pins in a symbol instance"""

  @property
  def number(self):
    return self[0]

  @sexp.uses("alternate")
  def get_alternate(self, diffs, context):
    return self.get("alternate", default=[None])[0]


kicad_sym.PinInst = PinInst


class PinSheet(Label):
  """A pin on a sheet instance"""

  def fillnetlist(self, netlister, diffs, context):
    self.netbus = netlister.add_sheetpin(context, self)


kicad_sym.PinSheet = PinSheet


def fakesheet(uuid):
  """Creates a fake sheet element for the purposes of UUIDs"""
  if not isinstance(uuid, str):
    uuid = uuid["uuid"][0][0]
  return sexp.SExp.init(
    [sexp.Atom("sheet"), sexp.SExp.init([sexp.Atom("uuid"), uuid])]
  )


@sexp.handler("sheet")
class Sheet(Drawable, HasUUID):
  """Sheet instance"""

  def fillvars(self, variables, diffs, context):
    super().fillvars(variables, diffs, context)
    context = context + (self,)
    variables.define(context, "FILENAME", os.path.basename(self.file))
    variables.define(context, "FILEPATH", self.file)
    # Define SHEETPATH using the parent sheetpath and just-now-defined sheetname
    # The resolver handles the recursion by going up in the hierarchy
    variables.define(context, "SHEETPATH", "${SHEETPATH}${SHEETNAME}/")

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs, of course
    pos = self["at"][0].pos(diffs)
    size = self["size"][0].data

    svg.gstart(path=self.uuid(generate=True))

    # Draw the rectangle
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_BG):
      args = {
        "pos": pos,
        "width": size[0],
        "height": size[1],
        "color": "sheet",
        "fill": "sheet_background",
      }
      args.update(self.svgargs(diffs, context))
      if not draw & Drawable.DRAW_FG:
        args["thick"] = 0
      if not draw & Drawable.DRAW_BG:
        args["fill"] = "none"
      svg.rect(**args)

    # Draw the rest of the owl
    super().fillsvg(svg, diffs, draw, context)

    svg.gend()  # path

  def paths(self, project=None):
    """Returns a list of path elements for a project"""
    if "instances" not in self:
      return []
    return list(self["instances"][0].paths(project).values())

  @property
  def name(self):
    return Field.getprop(self, "Sheetname")

  @property
  def file(self):
    return Field.getprop(self, "Sheetfile")


@sexp.handler("instances")
class Instances(sexp.SExp, Comparable):
  """Tracks instances of a sheet or symbol"""

  @sexp.uses("project")
  def paths(self, project=None):
    """Returns a dict of instance to path elements"""
    if "project" not in self:
      return {}
    if isinstance(project, sexp.SExp):
      project = project[0]
    ret = {}
    for proj in self["project"]:
      if not project:
        project = proj[0]
      if proj[0] not in ("", project) or "path" not in proj:
        continue
      for inst in proj["path"]:
        assert inst[0] not in ret
        ret[inst[0]] = inst
    return ret


@sexp.handler("path")
class Path(sexp.SExp, Comparable):
  def uuid(self, ref=None, generate=False):
    if ref and not isinstance(ref, (tuple, list)):
      ref = [ref]
    if not ref:
      return self[0]
    return f"{self[0]}/{'/'.join(r.uuid(generate=generate) for r in ref)}"


def fakepath(path):
  """Creates a fake path element for the purposes of tracking instances"""
  return sexp.SExp.init([sexp.Atom("path"), path])


@sexp.handler("kicad_sch")
class KicadSch(Drawable):  # ignore the uuid for the most part
  """A schematic page"""

  @property
  def paper(self):
    if "paper" in self:
      return self["paper"][0][0]
    return "A4"

  # Stuff for diffui
  def initsch(self, fname=None):
    self._fname = fname

  def relpath(self, fname):
    d = os.path.dirname(self._fname)
    return f"{d}/{fname}" if d else fname

  @property
  def title(self):
    return self["title_block"][0].title if "title_block" in self else None

  @property
  @sexp.uses("sheet_instances")
  def root_path(self):
    # Returns the path element if this is a root sheet; None otherwise
    # NOTE: if this ever stops working (eg sheet_instances is removed), a
    # potential heuristic is to look at the various instances fields on the
    # page and see if the page's UUID is featured first.
    if (
      "sheet_instances" in self
      and "path" in self["sheet_instances"][0]
      and self["sheet_instances"][0]["path"][0][0] == "/"
    ):
      return self["sheet_instances"][0]["path"][0]
    return None

  def fillvars(self, variables, diffs, context=None):
    if not context or self.root_path:
      variables.define(context, "FILENAME", os.path.basename(self._fname))
      variables.define(context, "FILEPATH", self._fname)
      variables.define(context, "SHEETPATH", "/")
    super().fillvars(variables, diffs, context)

  def inferred_instances(self, project=None):
    """If operating on a standalone file, we won't have any context on
    instances. So come up with the different instance views."""
    instances = set()
    # Instances can be inferred from sheet and symbol instantiations
    for typ in "sheet", "symbol":
      if typ in self:
        for obj in self[typ]:
          if "instances" in obj:
            instances.update(obj["instances"][0].paths(project))
    if not instances:
      instances.add("/" + HasUUID.uuid(self, generate=True))
    return [
      (fakepath(i.rpartition("/")[0]), fakesheet(i.rpartition("/")[2]))
      for i in instances
    ]

  def get_sheets(self, project=None):
    """Returns a list of tuples of (path, sheetref)"""
    if "sheet" not in self:
      return []
    sheets = []
    for sheet in self["sheet"]:
      sheets.extend((p, sheet) for p in sheet.paths(project))
    return sheets

  def get_components(self, context, instance):
    # returns a dict mapping refdes to dict of properties
    # Context should include project and variables, ideally
    if "symbol" not in self:
      return {}
    context += (fakepath(instance), self)
    comps = {}
    for sym in self["symbol"]:
      ref, data = sym.as_comp(context)
      if not ref.startswith("#"):
        comps.setdefault(ref, []).append(data)
    return comps

  # def get_nets(self, instance, variables, include_power=True):
  #  # FIXME: include_power -> include symbols with invisible power_input pins
  #  #        I think these can be variable-defined, unfortunately
  #  # Just get local nets for now
  #  # FIXME: properly return connection names, in addition to somehow indexing
  #  #        local nets
  #  variables = variables or Variables()
  #  nets = set()
  #  for labtyp in "global_label", "hierarchical_label", "label":
  #    if labtyp not in self:
  #      continue
  #    for label in self[labtyp]:
  #      nets.add(label.net([], instance))
  #  return nets


def kicad_sch(f, fname=None):
  data = f.read()
  if isinstance(data, bytes):
    data = data.decode()
  data = sexp.parse(data)
  if isinstance(data[0], KicadSch):
    data[0].initsch(fname)
    if "version" in data[0] and data[0]["version"][0].is_supported:
      return data[0]
  return None


def main(argv):
  """USAGE: kicad_sch.py [kicad_sch [instance]]
  Reads a kicad_sch from stdin or symfile and renders a random instance or the
  specified instance as an svg to stdout.
  """
  s = svg.Svg(theme="default")
  path = argv[1] if len(argv) > 1 else None
  with open(path) if path else sys.stdin as f:
    data = sexp.parse(f.read())
  variables = Variables()
  data[0].fillvars(variables, [], None)
  data[0].fillsvg(s, [], Drawable.DRAW_ALL, variables.context())
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
