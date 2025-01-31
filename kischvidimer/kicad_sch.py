#!/usr/bin/env python3
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

from . import kicad_sym
from . import kicad_wks
from .kicad_common import *

# FIXME: check eeschema/schematic.keywords for completeness
#        on last check, there are around 79 unused atoms

@sexp.handler("title_block")
class title_block(Drawable):
  """ title_block """
  @property
  @sexp.uses("title")
  def title(self):
    return self["title"][0][0] if "title" in self else None

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs
    svg.instantiate_worksheet(draw, context)

  @sexp.uses("company", "comment", "title", "paper", "generator",
             "generator_version", "rev")
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
      variables.define(context+(self,), name, var.data[-1])
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
            text = ' '.join((
                c.get("generator", default=["unknown"])[0],
                c.get(
                  "generator_version",
                  default=[
                    str(c.get("version", default=["version unknown"])[0])
                  ],
                )[0],
                "(rendered by kischvidimer)",
                ))
            break
      variables.define(context+(self,), name, text)
    # For whatever reason, wks uses REVISION but it references REV
    if variables.resolve(context+(self,), "REVISION") is None:
      variables.define(context+(self,), "REVISION", "${REV}")
    super().fillvars(variables, diffs, context)

@sexp.handler("junction")
class junction(Drawable):
  """ junction """
  @sexp.uses("diameter")
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    # FIXME: diffs
    pos = self["at"][0].pos(diffs)
    diameter = sexp.Decimal(0.915)
    if "diameter" in self and self["diameter"][0][0]:
      diameter = self["diameter"][0][0]
    # Change color if bus vs wire
    color = None
    # FIXME: replace this with a proper point database
    for c in reversed(context):
      if "bus" in c:
        for bus in c["bus"]:
          if "pts" in bus:
            for pt in bus["pts"][0]["xy"]:
              if pos == pt.pos():
                color = "bus_junction"
            if color: break
        if color: break
    else:
      color = 'junction'
    if "color" in self and any(self["color"][0].data):
      color = self["color"][0].data
    svg.circle(pos,
        radius=diameter/2,
        color='none',
        fill=color,
        )

@sexp.handler("no_connect")
class no_connect(Drawable):
  """ no_connect """
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    # FIXME: diffs
    sz = sexp.Decimal(0.6350)
    pos = self["at"][0].pos(diffs)
    xys = [(pos[0] - sz, pos[1] - sz),
           (pos[0] + sz, pos[1] + sz),
           pos,
           (pos[0] + sz, pos[1] - sz),
           (pos[0] - sz, pos[1] + sz)]
    svg.polyline(xys, color="noconnect")

# FIXME: (wire (pts (xy) (xy)) (stroke) (uuid))
@sexp.handler("wire", "bus")
class wire(polyline, has_uuid):
  """ wire or bus """

# FIXME: (hierarchical_label "x" (shape input) (at) (effects) (uuid) (property))
# FIXME: (label "x" (at) (effects) (uuid) (property))
# FIXME: (global_label "x" (shape input) (at) (effects) (uuid) (property))
@sexp.handler("global_label", "hierarchical_label", "label")
class label(Drawable, has_uuid):
  """ any type of label """
  BUS_RE = re.compile(r"(?:^|[^_~^$]){(.+)}|\[(\d+)[.][.](\d+)\]")


  def fillvars(self, variables, diffs, context):
    shape = self.shape(diffs)
    if shape is not None:
      variables.define(context+(self,), "CONNECTION_TYPE",
          "-".join(s.capitalize() for s in shape.split("-")))
    variables.define(context+(self,), "OP", "--")
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

  def net(self, diffs, context):
    return self[0]

  def bus(self, diffs, context):
    return label.BUS_RE.search(self.net(diffs, context))

  @sexp.uses("bidirectional", "input", "output", "passive", "tri_state")
  def fillsvg(self, svg, diffs, draw, context):
    if draw & Drawable.DRAW_FG:
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
      rot = self["at"][0].rot(diffs)
      shape = self.shape(diffs)
      outline = None
      if self.type != "label":
        offset = float(args["size"]/8)
        # Reference: sch_label.cpp: *::CreateGraphicShape
        # FIXME: text width/height calculations are broken
        if self.type == "global_label":
          w = float(len(self[0]) * args["size"])
          h = float(args["size"] * 2)
          if shape == "input":
            offset += h/2
            outline = [(0, 0), (h/2, h/2), (h/2+w, h/2)]
          elif shape == "output":
            outline = [(0, h/2), (w, h/2), (h/2+w, 0)]
          elif shape in ("bidirectional", "tri_state"):
            offset += h/2
            outline = [(0, 0), (h/2, h/2), (h/2+w, h/2), (h+w, 0)]
          elif shape == "passive":
            outline = [(0, h/2), (w, h/2)]
        elif self.type in ("hierarchical_label", "pin"):
          h = float(args["size"])
          offset += h
          if shape == "input":
            outline = [(0, 0), (h/2, h/2), (h, h/2)]
          elif shape == "output":
            outline = [(0, h/2), (h/2, h/2), (h, 0)]
          elif shape in ("bidirectional", "tri_state"):
            outline = [(0, 0), (h/2, h/2), (h, 0)]
          elif shape == "passive":
            outline = [(0, h/2), (h, h/2)]
          if self.type == "pin":
            offset *= -1
            for i, p in enumerate(outline):
              outline[i] = (p[0] - h, p[1])
        offset = (offset, 0)
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
        svg.polyline(outline,
            color=args["color"].replace("sheet", "hier"),
            thick=args["size"]/8)
      args["rotate"] = -180 * (rot >= 180)
      svg.text(self.net(diffs, context),
          prop=svg.PROP_LABEL,
          pos=offset,
          **args
          )
      svg.gend()
    if draw & Drawable.DRAW_TEXT and "property" in self:
      for field in self["property"]:
        field.fillsvg(svg, diffs, Drawable.DRAW_TEXT, context + (self,))


@sexp.handler('bus_entry')
class bus_entry(Drawable):
  """ Instance of a bus entry """
  def fillsvg(self, svg, diffs, draw, context):
    if not draw & Drawable.DRAW_FG:
      return
    # FIXME: diffs!
    pos = self["at"][0].pos(diffs)
    size = self["size"][0].data
    args = {
        "p1": pos,
        "p2": (pos[0]+size[0], pos[1]+size[1]),
        "color": "wire",
        }
    args.update(self.svgargs(diffs, context))
    svg.line(**args)


# FIXME: (symbol (lib_id "x") (at) (unit 1) (property) (pin)
#          (instances (project "x" (path "y" (reference "z") (unit 1)))))
class symbol_inst(Drawable, has_uuid):
  """ An instance of a symbol in a schematic """
  def fillvars(self, variables, diffs, context):
    variables.define(context+(self,), "UNIT", self.unit(diffs, context, True))
    variables.define(context+(self,), "OP", "--")
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

  @sexp.uses("lib_id")
  def fillsvg(self, svg, diffs, draw, context):
    # Decide what to draw
    subdraw = Drawable.DRAW_BG if draw & Drawable.DRAW_SYMBG else 0
    if draw & Drawable.DRAW_SYMFG:
      subdraw |= Drawable.DRAW_PINS | Drawable.DRAW_FG | Drawable.DRAW_TEXT
    if subdraw:
      # FIXME: diffs, of course
      lib = context[-1]["lib_symbols"][0]
      lib_id = self["lib_id"][0][0]
      pos = self["at"][0].pos(diffs)
      rot = self.rot(diffs)
      mirror = self.mirror(diffs)
      unit = self.unit(diffs, context)
      convert = self.get("convert", default=[1])[0]
      svg.gstart(
          pos=pos,
          rotate=rot,
          mirror=mirror,
          hidden=False,
          path=self.uuid(generate=True),
          tag=svg.getuid(self),
          )
      svg.instantiate(subdraw, lib, lib_id, unit=unit, variant=convert,
                      context=(self,))
      svg.gend()
    super().fillsvg(svg, diffs, draw, context)

  def show_unit(self, diffs, context):
    for c in reversed(context):
      if c.type == 'kicad_sch':
        lib = c["lib_symbols"][0]
        lib_id = self["lib_id"][0][0]
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
    return instancedata("reference", diffs,
                        context + (self,) if context else None,
                        default=self.get("reference", default=[1])[0])

  def unit(self, diffs, context, as_alpha=False):
    unit = instancedata("unit", diffs,
                        context + (self,) if context else None,
                        default=self.get("unit", default=[1])[0])
    if not as_alpha:
      return unit
    return unit_to_alpha(unit)

  def as_comp(self, context):
    # returns (refdes, {dict of properties, with chr(1) containing local uuid})
    ref = self.refdes([], context)
    props = {
        chr(1): self.uuid(generate=True)
        }
    if not "property" in self:
      return ref, props
    variables = Variables.v(context)
    for prop in self["property"]:
      name = prop.name
      value = variables.expand(context+(self,), prop.value)
      if name and not name.lower().startswith('sim.') and value and value != "~":
        props[name] = value
    return ref, props


kicad_sym.symbol_inst = symbol_inst

class pin_inst(sexp.sexp, Comparable, has_uuid):
  """ pins in a symbol instance """
  """
		(pin "1"
			(uuid "91e8ed47-d04f-4b18-94c6-d8c70729bd8c")
			(alternate "pwr_in")
		)
                """
kicad_sym.pin_inst = pin_inst

class pin_sheet(label):
  """ A pin on a sheet instance """
kicad_sym.pin_sheet = pin_sheet

def fakesheet(uuid):
  """ Creates a fake sheet element for the purposes of UUIDs """
  if not isinstance(uuid, str):
    uuid = uuid["uuid"][0][0]
  return sexp.sexp.init([sexp.atom("sheet"),
                           sexp.sexp.init([sexp.atom("uuid"), uuid])
                        ])

@sexp.handler("sheet")
class sheet(Drawable, has_uuid):
  """ Sheet instance """

  def fillvars(self, variables, diffs, context):
    super().fillvars(variables, diffs, context)
    context = context + (self,)
    variables.define(context, "FILENAME", os.path.basename(self.file))
    variables.define(context, "FILEPATH", self.file)
    # Define SHEETPATH using the parent sheetpath and just-now-defined sheetname
    variables.define(context, "SHEETPATH",
        variables.expand(context, "${SHEETPATH}${SHEETNAME}/"))

  def fillsvg(self, svg, diffs, draw, context):
    # FIXME: diffs, of course
    pos = self["at"][0].pos(diffs)
    size = self["size"][0].data

    # Draw the rectangle
    if draw & (Drawable.DRAW_FG | Drawable.DRAW_BG):
      args = {
          "pos": pos,
          "width": size[0],
          "height": size[1],
          "color": "sheet",
          "fill": "sheet_background",
          "tag": svg.getuid(self),
          }
      if not draw & Drawable.DRAW_FG:
        args["thick"] = 0
      if not draw & Drawable.DRAW_BG:
        args["fill"] = 'none'
      args.update(self.svgargs(diffs, context))
      svg.rect(**args)

    # Draw the rest of the owl
    super().fillsvg(svg, diffs, draw, context)

  def paths(self, project=None):
    """ Returns a list of path elements for a project """
    if not "instances" in self:
      return []
    return list(self["instances"][0].paths(project).values())

  @property
  def name(self):
    return field.getprop(self, "Sheetname")
  @property
  def file(self):
    return field.getprop(self, "Sheetfile")



@sexp.handler("instances")
class instances(sexp.sexp, Comparable):
  """ Tracks instances of a sheet or symbol """
  @sexp.uses("project")
  def paths(self, project=None):
    """ Returns a dict of instance to path elements """
    if not "project" in self:
      return {}
    if isinstance(project, sexp.sexp):
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
class path(sexp.sexp, Comparable):
  def uuid(self, ref=None, generate=False):
    if ref and not isinstance(ref, (tuple, list)):
      ref = [ref]
    if not ref:
      return self[0]
    return f"{self[0]}/{'/'.join(r.uuid(generate=generate) for r in ref)}"

def fakepath(path):
  """ Creates a fake path element for the purposes of tracking instances """
  return sexp.sexp.init([sexp.atom("path"), path])


@sexp.handler("kicad_sch")
class sch(Drawable):  # ignore the uuid for the most part
  """ A schematic page """
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
    if ("sheet_instances" in self and
        "path" in self["sheet_instances"][0] and
        self["sheet_instances"][0]["path"][0][0] == "/"):
      return self["sheet_instances"][0]["path"][0]
    return None

  def fillvars(self, variables, diffs, context=None):
    if not context or self.root_path:
      variables.define(context, "FILENAME", os.path.basename(self._fname))
      variables.define(context, "FILEPATH", self._fname)
      variables.define(context, "SHEETPATH", "/")
    super().fillvars(variables, diffs, context)

  def inferred_instances(self, project=None):
    """ If operating on a standalone file, we won't have any context on
    instances. So come up with the different instance views. """
    instances = set()
    # Instances can be inferred from sheet and symbol instantiations
    for typ in "sheet", "symbol":
      if typ in self:
        for obj in self[typ]:
          if "instances" in obj:
            instances.update(obj["instances"][0].paths(project))
    return [(fakepath(i.rpartition('/')[0]), fakesheet(i.rpartition('/')[2]))
            for i in instances]

  def get_sheets(self, project=None):
    """ Returns a list of tuples of (path, sheetref) """
    if not "sheet" in self:
      return []
    sheets = []
    for sheet in self["sheet"]:
      sheets.extend((p, sheet) for p in sheet.paths(project))
    return sheets

  def get_components(self, instance, variables):
    # returns a dict mapping refdes to dict of properties
    if not "symbol" in self:
      return {}
    variables = variables or Variables()
    context = variables.context() + (fakepath(instance), self)
    comps = {}
    for sym in self["symbol"]:
      ref, data = sym.as_comp(context)
      if not ref.startswith("#"):
        comps.setdefault(ref, []).append(data)
    return comps

  def get_nets(self, instance, variables, include_power=True):
    # FIXME: include_power -> include symbols with invisible power_input pins
    #        I think these can be variable-defined, unfortunately
    # Just get local nets for now
    # FIXME: properly return connection names, in addition to somehow indexing
    #        local nets
    variables = variables or Variables()
    nets = set()
    for labtyp in "global_label", "hierarchical_label", "label":
      if not labtyp in self:
        continue
      for label in self[labtyp]:
        nets.add(label.net([], instance))
    return nets


def kicad_sch(f, fname=None):
  data = f.read()
  if isinstance(data, bytes):
    data = data.decode()
  data = sexp.parse(data)
  if isinstance(data[0], sch):
    data[0].initsch(fname)
    return data[0]
  return None


def main(argv):
  """USAGE: kicad_sch.py [kicad_sch [instance]]
  Reads a kicad_sch from stdin or symfile and renders a random instance or the
  specified instance as an svg to stdout.
  """
  s = svg.Svg(theme="default")
  path = argv[1] if len(argv) > 1 else None
  with open(path, "r") if path else sys.stdin as f:
    data = sexp.parse(f.read())
  variables = Variables()
  data[0].fillvars(variables, [], None)
  data[0].fillsvg(s, [], Drawable.DRAW_ALL, variables.context())
  print(str(s))


if __name__ == "__main__":
  sys.exit(main(sys.argv))
