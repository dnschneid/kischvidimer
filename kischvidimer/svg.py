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

import base64
import math
import operator as op
import os
import re
import subprocess
from xml.sax.saxutils import escape

from . import bmp, jpeg, png, themes
from .diff import DiffParam, Param
from .kicad_common import Drawable


class Svg:
  """Helps generate an SVG image."""

  # SVGs can't handle fractional pixels, and we don't want to specify units all
  # the time. Instead, scale up all of our units. Scaling higher increases
  # precision at the cost of file size. If we have too many trailing zeros, we
  # are no better off than just specifying units all the time.
  # A SCALE of 10000 is needed to be totally accurate (due to line thicknesses)
  # A SCALE of 100 is within 1.6% for min line thicknesses -- not really visible
  SCALE = 100  # multiplies all SVG coordinates by this before rounding

  ANCHOR = {
    "left": "start",
    "middle": "middle",
    "none": "middle",
    "right": "end",
    "0": "start",
    "90": "start",
    "180": "end",
    "270": "end",
  }
  VJUST = {
    "bottom": (0, "text-after-edge"),
    "wks_bottom": (0, "alphabetic"),
    "middle": (0.5, "central"),
    "none": (0.5, "central"),
    "top": (1, "hanging"),
  }
  # Scale the aspect of the SVG stipple pattern by changing the constant
  PATTERN_SCALE = 1
  PATTERNS = {
    "default": (),
    "solid": (),
    "dash": (11, 4),
    "dot": (0.2, 4),
    "dash_dot": (11, 4, 0.2, 4),
    "dash_dot_dot": (11, 4, 0.2, 4, 0.2, 4),
  }
  # SVG thicknesses
  THICKNESS = {
    "ui": 0.0762,  # mm
    "wire": 0.1524,  # mm
    "bus": 0.3048,  # mm
    "dnp": 0.1524 * 3,  # mm
  }
  # It seems a font of size 1.5775 has an em height of 2.54 in KiCad
  FONT_SIZE = 2.54 / 1.5775  # converting KiCad glyph width to em height
  FONT_HEIGHT = 4 / 3  # converting KiCad glyph width to "height"
  FONT_FAMILY = "kicad"
  # Cache for font sizing
  FONT_WIDTH_CACHE = {}
  # Image scale value, above which to treat the image as pixel art
  IMAGE_PIXEL_SCALE_THRESHOLD = 10
  # Transformation types
  TRANSFORM_TYPES = {"translate", "rotate", "scale"}
  # Color types
  COLOR_TYPES = {"fill", "stroke"}
  # Pin name and pin number placeholders for text(prop)
  PROP_PIN_NAME = 1
  PROP_PIN_NUMBER = 2
  PROP_LABEL = 3
  # Properties of text that are indexed outside of generic text
  GENERIC_IGNORE = {PROP_LABEL}
  # Text to render and horiz/vert margin if there is no content
  _PLACEHOLDER = ("Empty file", 5, 10)

  def __init__(
    self,
    bgcolor="SCHEMATIC_BACKGROUND",
    header=True,
    auto_animate=(2, 1),
    theme=None,
  ):
    """Preps an empty SVG.
    header -- include the xml/DOCTYPE header in the output
              Should be False if this SVG is to be nested
    """
    self.data = []
    self.libraries = None
    self.image_dirs = []
    self.symbols = {}
    self.header = header
    self.vars = {}
    self.worksheet = None
    self.datadir = None
    self.uidtable = None
    self.prune = True
    self.glyphs = set()
    self._extend_current_line = 0
    self._invert_y = []
    self._bounds = None
    self._wks_bounds = None
    # Stack of lists of transforms, where each transform is ('op', parameters)
    self._transforms = []
    self._mirrorstate = [(1, 1)]
    self._rotatestate = [0]
    self._animate = []
    self.colormap = themes.get(theme)
    self.bgcolor = self.color(bgcolor)[0]
    self.prop_display = "VALUE"
    # Deal with auto-animations
    self._has_animation = False
    self._auto_animate = []
    self._animate_attrs = []
    if auto_animate:
      self._animate_attrs = [
        f'begin="svg{id(self):X}.begin"',
        f'dur="{auto_animate[0]:d}s"',
        'fill="freeze"',
      ]
      # Looper
      self._auto_animate = [
        "<animate",
        f'id="svg{id(self):X}"',
        f'dur="{sum(auto_animate):d}s"',
        'attributeName="visibility"',
        f'begin="0;svg{id(self):X}.end"/>',
      ]
    # Metadata tracking
    self.metadata_context = None
    # Each entry is a tuple of (self.metadata_context, text)
    self.generic_text = []
    self.pin_text = []

  def getuid(self, obj):
    """Returns an instance-unique ID string for the object.
    If uidtable is set, the ID will be sequential. If multiple SVGs are to be
    used in the same doc, make sure to set uidtable to point to the same list.
    If uidtable is None, returns a non-sequential but likely unique ID.
    """
    objid = hash(obj) if isinstance(obj, str) else id(obj)
    if self.uidtable is None:
      return f"{objid:X}"
    uid = self.uidtable.get(objid)
    if uid is None:
      uid = self.uidtable[objid] = len(self.uidtable) + 1  # avoid 0
    return f"{uid:X}"

  def _apply_transforms(self, pos):
    # Apply the stack of transformations back-to-front
    pos = (float(pos[0]), float(self.y(pos[1])))
    for batch in self._transforms[::-1]:
      for transform in batch[::-1]:
        if transform[0] == "translate":
          pos = (pos[0] + transform[1], pos[1] + transform[2])
        elif transform[0] == "scale":
          pos = (pos[0] * transform[1], pos[1] * transform[2])
        elif transform[0] == "rotate":
          cos = math.cos(math.radians(transform[1]))
          sin = math.sin(math.radians(transform[1]))
          pos = (pos[0] * cos - pos[1] * sin, pos[1] * cos + pos[0] * sin)
        elif transform[0] not in ("hide", "noop"):
          raise Exception(f"unrecognized transform {transform}")
    return pos

  def _update_bounds(self, pos, pos2=None, thick=0):
    if self._prune(force=True):
      return
    pos = self._apply_transforms(pos)
    if self._bounds is None:
      self._bounds = pos * 2
    thick = float(thick)
    self._bounds = (
      min(self._bounds[0], pos[0] - thick / 2),
      min(self._bounds[1], pos[1] - thick / 2),
      max(self._bounds[2], pos[0] + thick / 2),
      max(self._bounds[3], pos[1] + thick / 2),
    )
    if pos2 is not None:
      self._update_bounds(pos2, thick=thick)

  def _prune(self, force=False):
    """Returns true if the element should be pruned."""
    return (self.prune or force) and any(
      t[0][0] == "hide" for t in self._transforms if t
    )

  def add(self, line):
    """Adds one line to the SVG. Lists are combined with spaces.
    Returns self so you can chain with hascontents or nocontents if desired.
    """
    if self._prune():
      return self
    if not isinstance(line, str):
      line = " ".join(line)
    if self._extend_current_line:
      self.data[-1] += line
    else:
      self.data.append(line)
    return self

  def attr_opacity(self, cnt, i, name="opacity"):
    """Generates an opacity attribute, if len(cnt) > 1"""
    if len(cnt) == 1:
      return []
    c = cnt[i].c if i else set().union(*(c.c for c in cnt[1:]))
    return self.attr(name, [DiffParam(i == 0, None), DiffParam(i > 0, c)], True)

  def attr(self, name, value=None, default="", i=0, convert=True):
    """Generates an XML attribute and queues an animation if there is more than
    one value. Skips outputting the attribute if it equals default.
    """
    orig_v = value[0].v
    for new_v, c in value[1:]:
      if orig_v != new_v:
        conv_orig_v = orig_v
        if name in Svg.TRANSFORM_TYPES:
          if not isinstance(orig_v, (tuple, list)):
            conv_orig_v = (orig_v,)
          if not isinstance(new_v, (tuple, list)):
            new_v = (new_v,)
        elif convert:
          conv_orig_v = Svg.tounit(orig_v)
          new_v = Svg.tounit(new_v)
        self._animate.append((name, conv_orig_v, new_v, c))
    val = (value.get(i) if isinstance(value, Param) else value[i]).v
    if name in Svg.TRANSFORM_TYPES:
      if not isinstance(val, (tuple, list)):
        val = (val,)
      return [f'transform="{name}({",".join(map(str, val))})"'] * any(val)
    elif convert:
      val = Svg.tounit(val)
      default = Svg.tounit(default)
    return [f'{name}="{val}"'] * (str(val) != str(default))

  def hascontents(self, contents=""):
    """Ends the last tag without closing it, and flushes animation tags.
    You will need to provide the closing tag yourself.
    Assumes the last tag does not end in >
    Optionally provide contents to be appended to the line
    """
    if self._prune():
      contents = ""
    else:
      self.data[-1] += ">"
    self._flush_animate()
    if contents:
      if not isinstance(contents, str):
        contents = " ".join(contents)
      self.data[-1] += contents

  def nocontents(self):
    """Ends and closes the last tag, flushing animations in the process.
    Assumes the last tag does not end in />
    """
    if not self._animate:
      if not self._prune():
        self.data[-1] += "/>"
      return
    tag = self.data[-1].partition(" ")[0][1:]
    self.hascontents()
    self.add(f"</{tag}>")

  def _flush_animate(self):
    """Outputs all queued animate tags."""
    for name, fromval, toval, c in self._animate:
      self._has_animation = True
      if name in Svg.TRANSFORM_TYPES:
        params = [
          "<animateTransform",
          'attributeName="transform"',
          f'type="{name}"',
          f'from="{",".join(map(str, fromval))}"',
          f'to="{",".join(map(str, toval))}"',
        ]
      elif name in Svg.COLOR_TYPES:
        # Can't animate CSS Variables, so resolve them
        params = [
          "<animate",
          f'attributeName="{name}"',
          f'fromvar="{fromval}"',
          f'tovar="{toval}"',
        ]
      else:
        params = [
          "<animate",
          f'attributeName="{name}"',
          f'from="{fromval}"',
          f'to="{toval}"',
        ]
      class_str = " ".join(sorted(c))
      self.add(params + self._animate_attrs + [f'class="{class_str}"/>'])
    self._animate = []

  def gstart(
    self,
    pos=None,
    rotate=None,
    mirror=None,
    hidden=None,
    filt=None,
    path=None,
    tag=None,
  ):
    """Starts a group, optionally with coordinate offset."""
    transform = []
    hidden = Param.ify(hidden, False)
    path = Param.ify(path, "")
    if hidden.reduce(all):
      # Prune this and all subsequent elements
      transform.append(("hide",))
      # If pruning is off, simplify the g tag.
      prune = ["hidden"]
      opacity = Param(True)
    else:
      prune = []
      opacity = hidden.map(op.not_)
    # adds in this function should be pruned, so append the transform stack now
    self._transforms.append(transform)
    pos = Param.ify(pos, (0, 0))
    rotate = Param.ify(rotate, 0)
    # Mirror is represented by x and y scale factors
    mirror = Param(
      lambda m: (-1 if m == "y" else 1, -1 if m == "x" else 1),
      mirror,
    )
    filt = Param(lambda f: f"url(#{f})" if f else "", filt)
    if not prune and pos.reduce(any, lambda p: p != (0, 0)):
      transform.append(
        ("translate", float(pos[0].v[0]), float(self.y(pos[0].v[1])))
      )
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr(
          "translate",
          Param(lambda p: (Svg.tounit(p[0]), Svg.tounit(self.y(p[1]))), pos),
        )
        + self.attr("opacity", opacity, True)
        + self.attr("filter", filt)
      ).hascontents()
      path = Param("")
      opacity = Param(True)
      filt = Param("")
    if not prune and mirror.reduce(any, lambda m: m != (1, 1)):
      transform.append(("scale",) + mirror[0].v)
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr("scale", mirror)
        + self.attr("opacity", opacity, True)
        + self.attr("filter", filt)
      ).hascontents()
      path = Param.ify("")
      opacity = Param.ify(1)
      filt = Param.ify("")
    if not prune and rotate.reduce(any):
      transform.append(("rotate", -rotate[0].v))
      self.add(
        ["<g"]
        + Svg._tagattr(tag)
        + self.attr("p", path, "")
        + self.attr("rotate", Param(op.neg, rotate))
        + self.attr("opacity", opacity, True)
        + self.attr("filter", filt)
      ).hascontents()
      path = Param.ify("")
      opacity = Param.ify(1)
      filt = Param.ify("")
    if prune or not transform:
      if (
        not prune
        and opacity.reduce(all)
        and not path.reduce(any)
        and not tag
        and not filt.reduce(any)
      ):
        transform.append(("noop",))
      else:
        self.add(
          ["<g"]
          + Svg._tagattr(tag)
          + prune
          + self.attr("p", path, "")
          + self.attr("opacity", opacity, True)
          + self.attr("filter", filt)
        ).hascontents()
    self._rotatestate.append(rotate)
    self._mirrorstate.append(mirror)

  def gend(self):
    """Ends a group started with gstart.
    Returns True if there ended up being content in this tag.
    """
    self._mirrorstate.pop()
    self._rotatestate.pop()
    prune = self._prune()
    transforms = self._transforms.pop()
    if prune or transforms and transforms[0][0] == "noop":
      return not prune
    # If all that's between this end tag and the start tag are a bunch of
    # animations, delete the whole set
    gcount = max(1, len(transforms))
    for d in self.data[-1::-1]:
      if d.startswith("<g"):
        while self.data and not self.data.pop().startswith("<g"):
          pass
        gcount -= 1
        if not gcount:
          return False
      elif not d.startswith("<animate"):
        break
    self.add("</g>" * gcount)
    return True

  def astart(self, target):
    self.add(f'<a href="{target}">')

  def aend(self):
    self.add("</a>")

  def line(
    self, p1=None, p2=None, color=None, thick=None, pattern=None, tag=None
  ):
    p1 = Param.ify(p1, (0, 0))
    p2 = Param.ify(p2, (0, 0))
    # FIXME: don't emit anything if color is none?
    color, opacity = self._color(color, "wire")
    thick = Svg._thick(thick)
    pattern = Param(Svg.pattern, pattern, thick)
    self._update_bounds(p1[0].v, p2[0].v, thick[0].v)
    self.add(
      ["<line"]
      + self.attr("x1", p1.map(self.getx), 0)
      + self.attr("y1", p1.map(self.gety), 0)
      + self.attr("x2", p2.map(self.getx), 0)
      + self.attr("y2", p2.map(self.gety), 0)
      + self.attr("stroke", color)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
      + Svg._tagattr(tag)
    ).nocontents()

  def rect(
    self,
    pos=None,
    width=None,
    height=None,
    end=None,
    color=None,
    fill=None,
    thick=None,
    pattern=None,
    tag=None,
  ):
    pos = Param.ify(pos, (0, 0))
    if end is not None:
      width = Param(lambda e, p: max(abs(e[0] - p[0]), 1 / Svg.SCALE), end, pos)
      height = Param(
        lambda e, p: max(abs(e[1] - p[1]), 1 / Svg.SCALE), end, pos
      )
      x = Param(lambda e, p: min(e[0], p[0]), end, pos)
      y = Param(lambda e, p: min(self.y(e[1]), self.y(p[1])), end, pos)
    else:
      width = Param(width)
      height = Param(height)
      x = pos.map(self.getx)
      y = pos.map(lambda p, h: min(self.y(p[1]), self.y(p[1] + h)), height)
      height = height.map(op.abs)
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param(Svg.pattern, pattern, thick)
    # FIXME: use map/reduce to consider the bounds of all diffs
    self._update_bounds(
      (x[0].v, self.y(y[0].v)),
      (
        float(x[0].v) + float(width[0].v),
        self.y(float(y[0].v) + float(height[0].v)),
      ),
      thick[0].v,
    )
    self.add(
      ["<rect"]
      + self.attr("x", x, 0)
      + self.attr("y", y, 0)
      + self.attr("width", width, 0)
      + self.attr("height", height, 0)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
      + Svg._tagattr(tag)
    ).nocontents()

  def circle(
    self,
    pos=None,
    radius=None,
    color=None,
    fill=None,
    thick=None,
    pattern=None,
    tag=None,
  ):
    pos = Param.ify(pos, (0, 0))
    radius = Param(radius)
    if any(r.v < 0 for r in radius):
      raise Exception("negative radius")
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param(Svg.pattern, pattern, thick)
    self._update_bounds(
      (pos[0].v[0] - radius[0].v, pos[0].v[1] - radius[0].v),
      (pos[0].v[0] + radius[0].v, pos[0].v[1] + radius[0].v),
      thick[0].v,
    )
    self.add(
      ["<circle"]
      + self.attr("cx", pos.map(self.getx), 0)
      + self.attr("cy", pos.map(self.gety), 0)
      + self.attr("r", radius, 0)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
      + Svg._tagattr(tag)
    ).nocontents()

  @staticmethod
  def c_r_la(a, b, c):
    # Returns the center, radius, and largearc flag from a set of three points
    # from https://math.stackexchange.com/a/3503338
    z1, z2, z3 = complex(*a), complex(*b), complex(*c)
    if z1 == z2 or z2 == z3:
      return (z2.real - 1e9, z2.imag - 1e9), 1e9, False, False
    w = (z3 - z1) / (z2 - z1)
    c = (z2 - z1) * (w - abs(w) ** 2) / (2j * w.imag) + z1
    r = abs(z1 - c)
    la = (z3 - z1).real * (c - z1).imag - (z3 - z1).imag * (c - z1).real < 0
    swap = (z1 - c).real * (z2 - c).imag - (z1 - c).imag * (z2 - c).real < 0
    return (c.real, c.imag), r, la != swap, swap

  def arc(
    self,
    start,
    stop,
    mid=None,
    radius=None,
    largearc=None,
    color=None,
    fill=None,
    thick=None,
    pattern=None,
  ):
    start = Param(start)
    stop = Param(stop)
    center = None
    if mid is not None:
      mid = Param(mid)
      center, radius, largearc, reverse = Param.multi(
        4, Svg.c_r_la, start, mid, stop
      )
      # Some callers have reverse ordering of start/mid/stop, which breaks SVGs.
      # We expect it to be consistent regardless of diffs.
      if reverse.reduce(any):
        start, stop = stop, start
    else:
      assert radius and largearc
      radius = Param(radius)
      largearc = Param(largearc)
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param(Svg.pattern, pattern, thick)
    d = Param(
      lambda start, stop, center, radius, largearc, fill: (
        " ".join(
          (
            "M",
            Svg.tounit(start[0]),
            Svg.tounit(self.y(start[1])),
            "A",
            Svg.tounit(radius),
            Svg.tounit(radius),
            "0",
            str(1 * largearc),
            str(int(0.5 + self.y(0.5))),  # flip sweep dir when y is flipped
            Svg.tounit(stop[0]),
            Svg.tounit(self.y(stop[1])),
          )
          + (
            "L",
            Svg.tounit(center[0]),
            Svg.tounit(self.y(center[1])),
            "Z",
          )
          * (center and fill != "none")
        )
      ),
      *(start, stop, center, radius, largearc, fill),
    )
    # FIXME: this is wrong but it doesn't really matter that much
    self._update_bounds(start[0][0], stop[0][0], thick[0][0])
    self.add(
      ["<path"]
      + self.attr("d", d)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
    ).nocontents()

  def lines(
    self,
    xys,
    color=None,
    fill=None,
    thick=None,
    pattern=None,
    tag=None,
  ):
    """Renders a set of disconnected lines.
    xys should be a list of tuples of coordinates, or a Param of such.
    """
    self._path(
      lambda i: "L " if i % 2 else "M ",
      xys=xys,
      color=color,
      fill=fill,
      thick=thick,
      pattern=pattern,
      tag=tag,
    )

  def polyline(
    self,
    xys,
    color=None,
    fill=None,
    thick=None,
    pattern=None,
    close=None,
    tag=None,
  ):
    """Renders a polyline.
    xys should be a list of tuples of coordinates, or a Param of such.
    """
    self._path(
      lambda i: "L " if i else "M ",
      xys=xys,
      color=color,
      fill=fill,
      thick=thick,
      pattern=pattern,
      close=close,
      tag=tag,
    )

  def bezier(self, xys, color=None, fill=None, thick=None, pattern=None):
    """Renders a bezier curve.
    xys should be a list of tuples of coordinates, or a Param of such.
    """
    # TODO: fill isn't right if the bezier loops
    self._path(
      lambda i: "C " if i % 4 == 1 else "" if i else "M",
      xys=xys,
      color=color,
      fill=fill,
      thick=thick,
      pattern=pattern,
    )

  def _path(
    self, ptfunc, xys, color, fill, thick, pattern, close=None, tag=None
  ):
    """Renders a path of various types.
    ptfunc is a func converting point index (i) to svg path prefix with space.
    xys should be a list of tuples of coordinates, or a list of Params of tuples
    of coordinates, or a Param of coordinates
    """
    if not isinstance(xys, Param):
      assert isinstance(xys, (tuple, list))
      # FIXME: NO! handle this better so we don't conflate everything
      xys = Param.array(*xys)
    if len(xys) > 1:
      # Match the number of points in all versions of XYs so animations work
      maxpts = xys.reduce(max, len)
      xys = Param(lambda xys: xys + xys[-1:] * (maxpts - len(xys)), xys)
    d = Param(
      lambda close, pts: (
        " ".join(
          f"{ptfunc(i)}{Svg.tounit(pt[0])} {Svg.tounit(self.y(pt[1]))}"
          for i, pt in enumerate(pts)
        )
        + " Z" * bool(close)
      ),
      close,
      xys,
    )
    # FIXME: don't emit anything if color and fill are none?
    color, opacity = self._color(color, "notes")
    fill, fillopacity = self._fill(fill, color, opacity)
    thick = Svg._thick(thick)
    pattern = Param(Svg.pattern, pattern, thick)
    for xy, _ in xys:
      for pt in xy:
        self._update_bounds(pt, thick=thick[0].v)
    self.add(
      ["<path"]
      + self.attr("d", d)
      + self.attr("fill", fill, "none")
      + self.attr("fill-opacity", fillopacity, 1, convert=False)
      + self.attr("stroke", color)
      + self.attr("stroke-dasharray", pattern)
      + self.attr("stroke-opacity", opacity, 1, convert=False)
      + self.attr("stroke-width", thick, Svg.THICKNESS["wire"])
      + Svg._tagattr(tag)
    ).nocontents()

  def image(self, data, pos=None, scale=None):
    """Adds an image of specified size, centered around pos."""
    data = Param.ify(data)
    pos = Param.ify(pos, (0, 0))
    scale = Param.ify(scale, 1)

    image, width, height = Param.multi(3, self._image, data)
    width = Param(lambda w, s: round(w * float(s)), width, scale)
    height = Param(lambda h, s: round(h * float(s)), height, scale)
    pos = Param(
      lambda p, w, h: (p[0] - w // 2, p[1] - h // 2),
      pos,
      width,
      height,
    )
    # FIXME: this seems really broken for diffs
    for i in range(max(map(len, (data, pos, scale)))):
      self._update_bounds(
        pos[i].v, (pos[i].v[0] + width[i].v, pos[i].v[1] + height[i].v)
      )
      self.add(
        ["<image", f'href="{image.get(i).v}"']
        + self.attr("x", pos.map(self.getx), 0, i)
        + self.attr("y", pos.map(self.gety), 0, i)
        + self.attr("width", width, 0, i)
        + self.attr("height", height, 0, i)
        + self.attr(
          "image-rendering",
          scale.map(
            lambda s: "pixelated" * (s >= self.IMAGE_PIXEL_SCALE_THRESHOLD)
          ),
          "",
          i,
        )
        + self.attr_opacity(data, i=i)
      ).nocontents()

  def text(
    self,
    text,
    prop=None,
    pos=None,
    textsize=None,
    textcolor=None,
    justify=None,
    vjustify=None,
    bold=None,
    italic=None,
    thickness=None,
    rotate=None,
    hidden=None,
    url=None,
    icon=None,
    tag=None,
  ):
    # FIXME: how is thickness different from bold?
    needsgroup = False
    rotate = Param.ify(rotate, 0)
    hidden = Param.ify(hidden, False)
    text = Param(text)
    pos = Param.ify(pos, (0, 0))
    bold = Param(lambda b: "700" if b else "400", bold)
    italic = Param(lambda i: "oblique 14deg" if i else "oblique 0deg", italic)
    kisize = Param(lambda s: self.textsize(s, False), textsize)
    emsize = Param(lambda s: self.textsize(s, True), textsize)
    textcolor, opacity = self._color(textcolor, "notes")
    anchor = Param(lambda j: Svg.ANCHOR[str(j).lower()], justify)
    # Need to unmirror text so it's legible
    mirror = Param(
      lambda *mirrors: bool(sum(m[0] != m[1] for m in mirrors) % 2),
      *self._mirrorstate,
    )
    # Note that we don't directly update rotate with _rotatestate, since
    # _rotatestate is set when instantiating a symbol, and those will already
    # have rotation applied.
    rotate_state = Param(
      # Y mirrors are just X mirrors rotated 180 degrees
      lambda *rotmirrors: (
        sum(
          180 * (rm == (-1, 1)) if isinstance(rm, tuple) else rm or 0
          for rm in rotmirrors
        )
        % 360
      ),
      *self._mirrorstate,
      *self._rotatestate,
      rotate,
    )
    # Spin text so it's always at 0 or 90
    spin = rotate_state.map(lambda r, m: r in (180, 90 if m else 270), mirror)
    rotate = rotate.map(lambda r, s: r - 180 * s, spin)
    anchor = anchor.map(
      lambda a, m: {"start": "end", "end": "start"}.get(a, a) if m else a,
      spin,
    )
    vjustify = Param(
      lambda v, s: {"top": "bottom", "bottom": "top"}.get(v, v) if s else v,
      vjustify,
      spin,
    )
    ### WORKAROUND for crbug/389845192
    vjustmap = dict(Svg.VJUST)
    if text.reduce(any, lambda t: "~{" in t):
      vjustmap["middle"] = (vjustmap["middle"][0], "middle")
    ### end workaround
    vjust = Param(lambda j: vjustmap[str(j).lower()], vjustify)
    url = Param(url)
    icon = Param(icon)
    if (
      rotate.reduce(any)
      or hidden.reduce(any)
      or len(text) > 1
      or text.reduce(any, lambda t: "\n" in t)
      or len(icon) > 1
      or icon.reduce(any)
      or len(url) > 1
    ):
      needsgroup = True
      self.gstart(pos=pos, rotate=rotate, hidden=hidden)
      pos = Param((0, 0))
    ### Calculate bounding box
    # FIXME: get rid of the duplicate calcs by moving it into the render loop
    # FIXME: revamp the positioning to match better
    # FIXME: diffify
    textpos = (float(pos[0].v[0]), float(pos[0].v[1]))
    theight = max(
      (1 + t.v.count("\n")) * emsize.get(i).v for i, t in enumerate(text)
    )
    twidth = max(
      Svg.calcwidth(t.v, kisize.get(i).v) for i, t in enumerate(text)
    )
    # Reference: gr_text.cpp: reg=size/8, demibold=size/6, bold=size/5
    thick = max(s.v * Svg.FONT_HEIGHT / 5 for s in kisize)
    if anchor[0].v == "middle":
      textpos = (textpos[0] - twidth / 2, textpos[1])
    elif anchor[0].v == "end":
      textpos = (textpos[0] - twidth, textpos[1])
    textpos = (
      textpos[0],
      textpos[1]
      + self.y(
        theight * vjust[0].v[0]
        - (kisize[0].v * 1 / 3 if vjust[0].v[1] == "hanging" else 0)
      ),
    )
    if hidden.reduce(any, op.not_) and text.reduce(any):
      # self.rect((textpos[0], textpos[1] - theight), twidth, theight)
      self._update_bounds(
        textpos,
        (textpos[0] + twidth, textpos[1] - self.y(theight)),
        thick=thick,
      )
    ### done with bounding box calculations
    # apply mirroring now, so bbox calculations can ignore it
    anchor = anchor.map(
      lambda a, m: {"start": "end", "end": "start"}.get(a, a) if m else a,
      mirror,
    )
    self.add(
      ["<text", 'stroke="none"']
      + self.attr("x", pos.map(lambda p, m: -p[0] if m else p[0], mirror), 0)
      + self.attr("y", pos.map(self.gety), 0)
      + self.attr("fill", textcolor, "none")
      + self.attr("fill-opacity", opacity, 1, convert=False)
      + self.attr("font-size", emsize, Svg.FONT_SIZE)
      + self.attr("font-style", italic, "oblique 0deg")
      + self.attr("font-weight", bold, "400")
      + self.attr("text-anchor", anchor, "start")
      + self.attr("transform", mirror.map(lambda m: "scale(-1 1)" * m))
      + Svg._tagattr(tag)
      + ([f'prop="{prop}"'] if prop and isinstance(prop, str) else [])
    ).hascontents()
    # It is critical that no extraneous newlines exist within <text>, otherwise
    # textContent will be inaccurate.
    self._extend_current_line = True
    # FIXME: clean up this janky way of collecting pin names/numbers
    if prop and isinstance(prop, int) and prop < Svg.PROP_LABEL:
      pintext = "\n".join(t.v for t in text)
      if not self.pin_text or len(self.pin_text[-1]) == 3:
        self.pin_text.append((self.metadata_context, pintext))
      elif prop == Svg.PROP_PIN_NAME:
        self.pin_text[-1] += (pintext,)
      else:
        self.pin_text[-1] = (
          self.pin_text[-1][0],
          pintext,
          self.pin_text[-1][1],
        )
    elif (
      not tag
      and prop not in self.GENERIC_IGNORE
      and not self._prune(force=True)
    ):
      self.generic_text.append(
        (self.metadata_context, "\n".join(t.v for t in text))
      )
    for i in range(len(text)):
      self.glyphs.update(text.get(i).v)
      if url.get(i).v:
        targ = " target='_blank'" * (not url.get(i).v.startswith("#"))
        self.add([f"<a href='{url.get(i).v}'{targ}>"])
      opacity = self.attr_opacity(text, i=i, name="fill-opacity")
      baseline = self.attr(
        "dominant-baseline", Param(lambda vj: vj[1], vjust), "central"
      )
      # KiCad ignores a single trailing newline
      t = text[i].v[:-1] if text[i].v.endswith("\n") else text[i].v
      # FIXME: render icon separately/accurately and handle multiple types
      if icon.get(i).v:
        t = f"📍{t}"  # FIXME: icons aren't supposed to affect justification...
      splittext = t.split("\n")
      for lineno, line in enumerate(splittext):
        yattr = (
          self.attr(
            "y",
            vjust.map(
              lambda vj, nlines: f"{(nlines - 1) * (vj[0] - 1):g}em",
              len(splittext),
            ),
            "-0em",
          )
          if len(splittext) > 1 and not lineno
          else []
        )
        # Tab calculation reference: stroke_font.cpp
        # The target column is on the charcount/4 * fontwidth boundary
        # If target + one space is less than the existing text width, add
        # additional tabs.
        charcount = 0
        cursorpos = 0
        for colno, t in enumerate(line.split("\t")):
          # FIXME: tab calculation still isn't quite right
          targetpos = 0
          gap_em = 0
          if colno:
            charcount = (charcount // 4 + 1) * 4 - 1
            targetpos = charcount + Svg.calcwidth(" ", 1)
            while targetpos <= cursorpos:
              charcount += 4
              targetpos += 4
          gap_em = (targetpos - cursorpos) / Svg.FONT_HEIGHT
          # stray whitespace in <text> causes misalignment
          self.add(
            ["<tspan"]
            + yattr * (colno == 0)
            + ['x="0"'] * (lineno > 0 or i) * (colno == 0)
            + ['dy="1em"'] * (lineno > 0) * (colno == 0)
            + [f'dx="{gap_em:.4g}em"'] * (colno > 0)
            + baseline
            + opacity,
          ).hascontents(f"{Svg.encode(t or '') or '&#8203;'}</tspan>")
          charcount += Svg.calcwidth(t, 1, font=None)
          cursorpos = targetpos + Svg.calcwidth(t, 1)
      if url.get(i).v:
        self.add("</a>")
    self.add("</text>")
    self._extend_current_line = False
    if needsgroup:
      self.gend()

  def title(self, label):
    self.add(f"<title>{Svg.escape(label)}</title>")

  def instantiate(
    self,
    draw,
    lib,
    lib_id,
    unit=None,
    variant=None,
    context=None,
  ):
    """Instantiates a symbol. lib must contain a definition of lib_id.
    Returns True if the symbol was successfully instantiated; otherwise you
    should draw something yourself.
    """
    # FIXME: this is a terrible hack for alternates, and doesn't support diffs
    alternates = ""
    if context and hasattr(context[-1], "get_alternates"):
      alternates = context[-1].get_alternates(None, context).v
      alternates = "\n".join(f"{n}={a}" for n, a in sorted(alternates.items()))
      alternates = f"{hash(alternates):x}"
    lib = Param(lib)
    lib_id = Param(lib_id)
    unit = Param(unit, default=1)
    variant = Param(variant, default=1)
    mirror = Param(
      lambda *mirrors: bool(sum(m[0] != m[1] for m in mirrors) % 2),
      *self._mirrorstate,
    )
    rotate = Param(
      # Y mirrors are just X mirrors rotated 180 degrees
      lambda *rotmirrors: (
        sum(
          180 * (rm == (-1, 1)) if isinstance(rm, tuple) else rm or 0
          for rm in rotmirrors
        )
        % 360
      ),
      *self._mirrorstate,
      *self._rotatestate,
    )
    name = Param(
      lambda lib, lib_id, unit, variant, rotate, mirror: (
        ":".join(
          (
            "symbol",
            f"{lib.sym_hash(lib_id):x}",
            str(unit),
            str(variant),
            alternates,
            str(rotate // 90),
            "m" * mirror,
            f"{draw:x}",
          )
        )
      ),
      *(lib, lib_id, unit, variant, rotate, mirror),
    )
    for i in range(len(name)):
      if name[i].v in self.symbols:
        continue
      params = name[i].v.split(":")
      sym = lib.get(i).v.hash_lookup(params[1])
      assert sym is not None
      symsvg = Svg(
        self.bgcolor,
        header=False,
        auto_animate=False,
      )
      symsvg._mirrorstate = [(-1 if mirror.get(i).v else 1, 1)]
      symsvg._rotatestate = [rotate.get(i).v]
      symsvg.push_invert_y()
      symsvg.colormap = self.colormap
      # FIXME: diffs
      sym.fillsvg(
        symsvg,
        None,
        draw,
        context or (),
        unit=int(params[2]),
        variant=int(params[3]),
      )
      symsvg.pop_invert_y()
      self.symbols[name[i].v] = symsvg
    return self._instantiate(name)

  def instantiate_worksheet(self, draw, context, worksheet=None):
    """Instantiates a worksheet, based on the context."""
    # FIXME: handle page size/variable/worksheet changes?
    wks = Param(worksheet or self.worksheet)
    context = Param(context)
    name = Param(
      lambda wks, context: wks and f"wks:{wks.wks_hash(context):x}",
      wks,
      context,
    )
    # keep the worksheet bounds separate
    orig_bounds, self._bounds = self._bounds, self._wks_bounds
    if draw & Drawable.DRAW_WKS:
      for i in range(len(name)):
        if not name[i].v or name[i].v in self.symbols:
          continue
        wkssvg = Svg(self.bgcolor, header=False, auto_animate=False)
        wkssvg.colormap = self.colormap
        # FIXME: diffs
        wks.get(i).v.fillsvg(wkssvg, None, Drawable.DRAW_WKS, context.get(i).v)
        self.symbols[name[i].v] = wkssvg
      self._instantiate(name)
    if draw & Drawable.DRAW_WKS_PG:
      for i in range(len(name)):
        if wks.get(i).v:
          # FIXME: diffs
          wks.get(i).v.fillsvg(
            self, None, Drawable.DRAW_WKS_PG, context.get(i).v
          )
    self._wks_bounds, self._bounds = self._bounds, orig_bounds

  def _instantiate(self, name):
    bounds = None
    for i in range(len(name)):
      symsvg = self.symbols.get(name[i].v)
      if not symsvg:
        if i == len(name) - 1:
          return False
        continue
      if symsvg.data:
        self.add(
          ["<use", f'href="#{name[i].v}"'] + self.attr_opacity(name, i=i)
        ).nocontents()
        self.generic_text.extend(
          (self.metadata_context, t) for _, t in symsvg.generic_text
        )
        self.pin_text.extend(
          (self.metadata_context,) + t[1:] for t in symsvg.pin_text
        )
        self.glyphs.update(symsvg.glyphs)
        bounds = symsvg._bounds
        self._update_bounds(
          (bounds[0], self.y(bounds[1])), (bounds[2], self.y(bounds[3]))
        )
    return bounds

  def _image(self, data):
    imagetype, imagedata, w, h = self.imagedata(data)
    b64 = base64.b64encode(imagedata).decode("ascii")
    image = f"data:image/{imagetype};base64,{b64}"
    return (image, w, h)

  @staticmethod
  def imagedata(data, convert_all=False):
    """Returns a tuple of (type, data, width, height) of the specified image.
    Converts bmp files to png. If convert_all is specified, converts jpgs as
    well, although this only works if ImageMagick is installed and in PATH.
    Width and height are in mm, not pixels.
    """
    # FIXME: does KiCad check metadata, or does it use 300 always?
    if isinstance(data, str):
      data = base64.b64decode(data)
    for typ, mod in ("png", png), ("bmp", bmp), ("jpeg", jpeg):
      sz = mod.getsize_mm(data)
      if sz is None:
        continue
      if hasattr(mod, "to_png"):
        return ("png", mod.to_png(data)) + sz
      elif convert_all and typ != "png":
        ret = subprocess.run(
          ["convert", "-", "png:-"], input=data, capture_output=True
        )
        assert ret.returncode == 0
        return ("png", ret.stdout) + sz
      return (typ, data) + sz
    return None

  @staticmethod
  def _thick(thickparam):
    """Processes a thickness parameter to deal with diffs and animation.
    Returns a Param of svg-compatible stroke widths.
    """
    return Param(
      lambda t: Svg.THICKNESS.get("wire" if t is None else t, t),
      thickparam,
    )

  def _color(self, colorparam, default):
    """Processes a color parameter to deal with diffs and animation.
    Returns a tuple of Param of svg-compatible colors and Param of opacities.
    """
    return Param.multi(2, lambda c, d: self.color(c or d), colorparam, default)

  def _fill(self, fillparam, color, opacity):
    """Processes a fill parameter to deal with cases where the fill should equal
    the color, as well as diffs and animations.
    Returns a tuple of Param of svg-compatible colors and Param of opacities.
    """
    return Param.multi(
      2,
      lambda f, c, o: (
        ("none", 1) if not f else (c, o) if f == "outline" else self.color(f)
      ),
      fillparam,
      color,
      opacity,
    )

  @staticmethod
  def _tagattr(tag):
    # Returns a tag attribute in a list
    if not tag:
      return []
    if isinstance(tag, int):
      return [f't="{tag:x}"']
    return [f't="{tag}"']

  def color(self, color):
    """Maps a CAD tool color to an SVG-compatible color."""
    if isinstance(self.colormap, str):
      return self.colormap
    if isinstance(color, str):
      color = color.lower()
    while color in self.colormap:
      color = self.colormap[color]
    opacity = 1
    # Convert color to svg format and split into color + opacity
    if isinstance(color, tuple):
      if len(color) > 3:
        opacity = color[3]
      color = "#" + "".join(f"{c:02X}" for c in color[:3])
    return (color, opacity)

  def textsize(self, textsize, em):
    """Maps a size to the unit size of the font.
    If em is true, scale to SVG size (em)
    """
    textsize = 1.0 if textsize is None else textsize
    if isinstance(textsize, str) and textsize.endswith("%"):
      textsize = float(textsize[:-1]) / 100
    return Svg.FONT_SIZE * float(textsize) if em else float(textsize)

  @staticmethod
  def pattern(pattern, thick):
    """Maps a name or number to an SVG pattern."""
    if pattern is None:
      return ""
    assert pattern in Svg.PATTERNS
    return ",".join(
      Svg.tounit(Svg.PATTERN_SCALE * float(thick) * c)
      for c in Svg.PATTERNS[pattern]
    )

  @staticmethod
  def escape(text):
    """Escapes text such that it can be displayed.
    This is both special character escaping as well as converting leading
    spaces, trailing spaces, and pairs of spaces to use non-breaking spaces (to
    prevent spaces from being collapsed). Trailing spaces need to be handled in
    the case of right- and center-justification."""
    text = escape(text)
    if text.startswith(" "):
      text = "&#160;" + text[1:]
    if text.endswith(" "):
      text = text[:-1] + "&#160;"
    return text.replace("  ", " &#160;")

  _ENCODE_BLOCKS_RE = re.compile(r"[_^~]\{((?:[^{}]|\{[^}]*\})*)\}")

  @staticmethod
  def calcwidth(text, textsize, font="newstroke"):
    # Load the font map
    widthmap = Svg.FONT_WIDTH_CACHE.get(font) if font else {}
    if widthmap is None:
      Svg.FONT_WIDTH_CACHE[font] = widthmap = {}
      try:
        import fontTools.subset as fts

        path = os.path.join(os.path.dirname(__file__), "fonts", f"{font}.woff")
        opts = fts.Options()
        # Scale font metrics such that textsize * widthmap = character advance
        # This is based on FONT_WOFF_SCALE in fontconv.py along with some more
        # reverse tracing of the textsize.
        # FIXME: resolve the magic number 26.5
        metric_scale = 1 / 29.7 * 50 * 0.0254 / 26.5
        with fts.load_font(path, opts, dontLoadGlyphNames=True) as srcfont:
          cmap = srcfont["cmap"].getBestCmap()
          hmtx = srcfont["hmtx"].metrics
          widthmap.update(
            (chr(c), hmtx[g][0] * metric_scale) for c, g in cmap.items()
          )
      except ImportError:
        pass

    # Handle the different contexts (string vs sub-block vs multiline)
    if isinstance(text, re.Match):
      if text[0][0] != "~":
        textsize *= 0.8  # match font-size in _encode_block
      text = text[1]
    elif "\n" in text:
      return max(
        Svg.calcwidth(line, textsize, font) for line in text.split("\n")
      )

    # Process formatted blocks and then remove from the string
    width = 0
    for m in Svg._ENCODE_BLOCKS_RE.finditer(text):
      width += Svg.calcwidth(m, 1, font)
    text = Svg._ENCODE_BLOCKS_RE.sub("", text)

    # Handle the remaining string
    width += sum(widthmap.get(c, 1) for c in text)

    return width * float(textsize)

  @staticmethod
  def encode(text):
    """adds <tspan> elements for any embedded formatting"""
    # NOTE: remember to update diffui.js's generic text matcher to reflect this
    # XML is OK with _^~{}, so it's safe (and required) to escape first
    text = Svg.escape(text)
    text = Svg._ENCODE_BLOCKS_RE.sub(Svg._encode_block, text)
    return text

  @staticmethod
  def _encode_block(text):
    """Encodes a single formatting block in a string"""
    # Handle a little bit of nesting (not full support)
    innertext = Svg._ENCODE_BLOCKS_RE.sub(Svg._encode_block, text[1])
    # Reference: STROKE_FONT::GetTextAsGlyphs
    if text[0][0] == "_":  # subscript
      return f'<tspan font-size="80%" baseline-shift="-15%">{innertext}</tspan>'
    elif text[0][0] == "^":  # superscript
      return f'<tspan font-size="80%" baseline-shift="35%">{innertext}</tspan>'
    elif text[0][0] == "~":  # overbar
      return f'<tspan text-decoration="overline">{innertext}</tspan>'
    raise AssertionError()  # regex is bad

  @staticmethod
  def tomm(coord):
    return f"{coord:.4f}mm"

  @staticmethod
  def tounit(mm):
    if isinstance(mm, str):
      return mm
    elif isinstance(mm, bool):
      return "1" if mm else "0"
    return str(round(mm * Svg.SCALE))

  def y(self, mm):
    return -mm if self._invert_y and self._invert_y[-1] else mm

  def push_invert_y(self, invert=True):
    self._invert_y.append(invert)

  def pop_invert_y(self):
    return self._invert_y.pop()

  @staticmethod
  def getx(coord):
    return coord[0]

  def gety(self, coord):
    return self.y(coord[1])

  @staticmethod
  def _get_placeholder():
    if not isinstance(Svg._PLACEHOLDER, Svg):
      placeholder = Svg()
      placeholder.text(Svg._PLACEHOLDER[0], textcolor="device")
      placeholder._bounds = (
        -(Svg._PLACEHOLDER[1] + 0) * placeholder._bounds[2],
        (Svg._PLACEHOLDER[2] + 0) * placeholder._bounds[1],
        (Svg._PLACEHOLDER[1] + 1) * placeholder._bounds[2],
        -(Svg._PLACEHOLDER[2] + 1) * placeholder._bounds[1],
      )
      Svg._PLACEHOLDER = placeholder
    return Svg._PLACEHOLDER

  def get_viewbox(self, convert=True, with_wks=True):
    orig_bounds = self._bounds
    if with_wks and self._wks_bounds:
      self._update_bounds(self._wks_bounds[:2], self._wks_bounds[2:])
    if not self._bounds:
      return Svg._get_placeholder().get_viewbox(convert)
    box = (
      self._bounds[0],
      self._bounds[1],
      self._bounds[2] - self._bounds[0],
      self._bounds[3] - self._bounds[1],
    )
    if convert:
      box = tuple(map(Svg.tounit, box))
    self._bounds = orig_bounds
    return box

  def __repr__(self):
    """Returns a string of the SVG"""
    svg = []
    if self.header:
      svg.append('<?xml version="1.0"?>')
      svg.append(
        '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN"'
        + ' "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
      )
    viewbox = self.get_viewbox(convert=False)
    svg.append(
      " ".join(
        (
          '<svg xmlns="http://www.w3.org/2000/svg"',
          'xmlns:xlink="http://www.w3.org/1999/xlink"',
          f'viewBox="{",".join(tuple(map(Svg.tounit, viewbox)))}"',
          f'width="{Svg.tomm(viewbox[2])}" height="{Svg.tomm(viewbox[3])}"',
          'fill="none"',
          f'font-family="{Svg.FONT_FAMILY}"',
          f'font-size="{Svg.tounit(Svg.FONT_SIZE)}"',
          f'dominant-baseline="{Svg.VJUST["middle"][1]}"',
          f'stroke-width="{Svg.tounit(Svg.THICKNESS["wire"])}"',
          'stroke-linecap="round"',
          'stroke-linejoin="round"',
          f'style="background-color:{self.bgcolor}"',
        )
      )
      + ">"
    )
    if self._has_animation:
      svg += self._auto_animate
    # Add all symbols
    for name, symsvg in self.symbols.items():
      if symsvg is None or not symsvg.data:
        continue
      svg.append(f'<symbol id="{name}" overflow="visible">')
      svg += symsvg.data
      svg.append("</symbol>")
    svg += self.data or Svg._get_placeholder().data
    svg.append("</svg>\n")
    return "\n".join(svg)
