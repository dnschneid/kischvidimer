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
Common classes and routines for handling sexp-based KiCad modifier elements
"""

from . import sexp
from .diff import FakeDiff, Param


class HasModifiers(sexp.SExp):
  LITERAL_MAP = {}  # shouldn't have any literals

  @staticmethod
  def _wrap_svgargs_with_diff(apply, item, args, diffs, context):
    new_args = args.copy()
    item.fillsvgargs(new_args, diffs, context)
    for key, newvalue in new_args.items():
      oldvalue = args.get(key)
      if newvalue is not oldvalue:
        args[key] = Param(
          lambda a, o, n: n if a else o, apply, oldvalue, newvalue
        )

  def fillsvgargs(self, args, diffs, context=None):
    if not isinstance(context, tuple):
      context = () if context is None else (context,)
    context = context + (self,)
    added, removed = self.added_and_removed(diffs, (Modifier, HasModifiers))
    for item, add_c in added:
      apply = FakeDiff(add_c, new=True).param()
      self._wrap_svgargs_with_diff(apply, item, args, diffs, context)
    for item in self.data:
      if isinstance(item, (Modifier, HasModifiers)):
        rm_c = removed.get(id(item))
        if rm_c:
          apply = FakeDiff(rm_c, old=True)
          self._wrap_svgargs_with_diff(apply, item, args, diffs, context)
        else:
          item.fillsvgargs(args, diffs, context)


@sexp.handler("font")
class Font(HasModifiers):
  pass


@sexp.handler("effects")
class Effects(HasModifiers):
  """font effects"""

  @sexp.uses("font", "bold", "italic")
  def get_style(self, diffs=None):
    # FIXME: diffs
    if "font" in self:
      bold = "bold" in self["font"][0]
      italic = "italic" in self["font"][0]
      return (bold, italic)
    return (False, False)

  @sexp.uses("href", "mirror", "hide")
  def fillsvgargs(self, args, diffs, context):
    """Returns a dict of arguments to Svg.text"""
    super().fillsvgargs(args, diffs, context)
    args["hidden"] = self.has_yes("hide", diffs, args.get("hidden"))

    # FIXME: bold/italic
    bold, italic = self.get_style(diffs)

    # Handle mirror/rotation causing justify to flip
    # Some nodes have their own implementation, so drop out early
    if self.parent.type in ("pin", "name", "number"):
      return

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
      if "at" in c and "label" not in c.type and c.type != "netclass_flag":
        rot += c["at"][0].rot(diffs, context).v * (-1 if flipy != flipx else 1)
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


class Modifier(sexp.SExp):
  # map of literal name to arg name, if different. if None, will ignore.
  ARG_MAP = {}

  def fillsvgargs(self, args, diffs, context):
    # Grabs the literals and puts them in args
    for key in self.LITERAL_MAP:
      param = self.ARG_MAP.get(key, key)
      if param is not None:
        args[param] = self.param(diffs, key, default=args.get(param))

  @classmethod
  def basic(cls, name, arg=None, istuple=False):
    @sexp.handler(name)
    class BasicModifier(cls):
      LITERAL_MAP = {name: (1, -1) if istuple else 1}
      ARG_MAP = {name: arg or name}

    return BasicModifier


# margins should have 4 entries (left, top, right, bottom).
Margins = Modifier.basic("margins", istuple=True)

Face = Modifier.basic("face", None)  # TODO: support font faces
Thickness = Modifier.basic("thickness")  # TODO: properly support thick fonts
Href = Modifier.basic("href", "url")


@sexp.handler("size")
class Size(Modifier):
  """size can be a textsize or a physical size, and can have 1 or 2 values."""

  LITERAL_MAP = {"size": (1, -1)}

  @property
  def is_textsize(self):
    return self.parent.type == "font"

  def fillsvgargs(self, args, diffs, context):
    p = self.param(diffs)
    if self.is_textsize:
      args["textsize"] = p.map(lambda d: d[0])
    else:
      args["size"] = p

  def reparent(self, new_parent):
    super().reparent(new_parent)
    if self.is_textsize:
      assert len(set(self.data)) == 1, "unexpected multidimensional font size"


@sexp.handler("color")
class Color(Modifier):
  """parameter is different depending on the context of the color."""

  LITERAL_MAP = {"color": (1, -1)}

  def reparent(self, new_parent):
    super().reparent(new_parent)
    if new_parent.type == "font":
      self.ARG_MAP = {"color": "textcolor"}
    elif new_parent.type == "fill":
      self.ARG_MAP = {"color": "fill"}


@sexp.handler("justify")
class Justify(Modifier):
  """justify can specify either or both horizontal and vertical."""

  LITERAL_MAP = {"justify": (1, -1)}

  def param(self, diffs, key, default=None):
    assert key in ("justify", "vjustify")
    v = ("top", "bottom") if key[0] == "v" else ("left", "right")
    return Param(
      lambda j: v[0] if v[0] in j else v[1] if v[1] in j else None,
      super().param(diffs),
      default=default,
    )


@sexp.handler("stroke", "default")
class Stroke(Modifier):
  """stroke effects"""

  @sexp.uses("width", "type", "color")
  def fillsvgargs(self, args, diffs, context):
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


@sexp.handler("fill")
class Fill(Modifier):
  """fill properties"""

  @sexp.uses("background", "color")
  def fillsvgargs(self, args, diffs, context):
    fill = None
    if "type" in self:
      fill = self["type"][0][0]
      if fill == "background":
        fill = "device_background"
    if fill == "color" or "color" in self:
      fill = tuple(self["color"][0].data)
    if any(fill):
      args["fill"] = fill


class HasYes(sexp.SExp):
  """Marker class for easy identification."""


@sexp.handler("hide")
class Hide(HasYes):
  pass


@sexp.handler("show_name")
class ShowName(HasYes):
  pass
